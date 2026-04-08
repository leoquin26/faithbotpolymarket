"""
V11 Predictor — Black-Scholes Binary Option + EWMA Volatility + Momentum in Logit-Space.

This is NOT a technical indicator model. It solves a MATH problem:
"What is the probability that price stays above/below threshold?"

Pipeline:
1. EWMA volatility from tick-level WebSocket data (per-second sigma)
2. Black-Scholes d2 → base probability N(d2)
3. Momentum adjustment in logit-space (10s/30s/60s weighted ROC)
4. Mean-reversion adjustment when price stretched from SMA
5. Abstention when model has no edge
6. Compare probability vs Polymarket ask → only trade when edge > fee + margin
"""

import math
import time
from dataclasses import dataclass
from typing import Optional, Dict, List, Tuple
from loguru import logger

import config
from market_data import MarketInfo


@dataclass
class Prediction:
    coin: str
    direction: str
    probability: float
    poly_price: float
    edge: float
    confidence: str
    reasoning: str
    token_id: str
    market_info: MarketInfo
    entry_price: float = 0.0
    mc_prob: float = 0.0
    depth_ratio: float = 0.0
    directional_edge: float = 0.0
    stale_price: bool = False
    stale_gap: float = 0.0
    conviction_strength: Optional[str] = None
    force_fok: bool = False


# ── Normal CDF (Abramowitz & Stegun approximation, max error 1.5e-7) ──
def _norm_cdf(x: float) -> float:
    a1, a2, a3, a4, a5 = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429
    p = 0.3275911
    sign = -1.0 if x < 0 else 1.0
    ax = abs(x) / math.sqrt(2.0)
    t = 1.0 / (1.0 + p * ax)
    y = 1.0 - ((((a5 * t + a4) * t + a3) * t + a2) * t + a1) * t * math.exp(-ax * ax)
    return 0.5 * (1.0 + sign * y)


# ── Black-Scholes binary call probability ──
def _bs_binary_prob(current_price: float, strike: float, sigma: float, T: float) -> float:
    """P(price > strike at expiry) = N(d2)"""
    if T <= 0:
        return 1.0 if current_price > strike else 0.0
    if sigma <= 0 or current_price <= 0 or strike <= 0:
        return 0.5
    sqrt_T = math.sqrt(T)
    d2 = (math.log(current_price / strike) + (-0.5 * sigma * sigma) * T) / (sigma * sqrt_T)
    return _norm_cdf(d2)


# ── Logit / Sigmoid transforms ──
def _logit(p: float) -> float:
    p = max(0.001, min(0.999, p))
    return math.log(p / (1.0 - p))

def _sigmoid(x: float) -> float:
    if x > 20: return 0.999
    if x < -20: return 0.001
    return 1.0 / (1.0 + math.exp(-x))


class EWMAVolatility:
    """Tick-level EWMA volatility estimator (per-second sigma)."""

    def __init__(self, lam: float = 0.94):
        self._lambda = lam
        self._variance = 0.0
        self._last_price = 0.0
        self._last_ts = 0.0
        self._initialized = False
        self._tick_count = 0
        self._sigma_history: List[float] = []

    def update(self, price: float, ts: float):
        self._tick_count += 1
        if self._last_price <= 0:
            self._last_price = price
            self._last_ts = ts
            self._initialized = True
            self._variance = 1e-08
            return self.get_sigma()

        dt = max(ts - self._last_ts, 0.001)

        if price == self._last_price:
            self._last_ts = ts
            return self.get_sigma()

        log_ret = math.log(price / self._last_price) if self._last_price > 0 else 0.0
        r2_per_sec = (log_ret * log_ret) / dt

        self._variance = self._lambda * self._variance + (1.0 - self._lambda) * r2_per_sec
        self._variance = max(self._variance, 1e-10)

        self._last_price = price
        self._last_ts = ts

        sigma = math.sqrt(self._variance) if self._variance > 0 else 0.0
        self._sigma_history.append(sigma)
        if len(self._sigma_history) > 100:
            self._sigma_history.pop(0)
        return sigma

    def get_sigma(self) -> float:
        return math.sqrt(self._variance) if self._variance > 0 else 0.0

    def get_mean_sigma(self) -> float:
        if not self._sigma_history:
            return 0.0
        return sum(self._sigma_history) / len(self._sigma_history)

    @property
    def tick_count(self) -> int:
        return self._tick_count

    def reset(self):
        self._variance = 0.0
        self._last_price = 0.0
        self._last_ts = 0.0
        self._initialized = False
        self._tick_count = 0
        self._sigma_history.clear()



class ChopDetector:
    """Track recent window directions to detect choppy vs trending markets."""
    _PERSIST_FILE = "/home/ubuntu/v3-bot/chop_state.json"

    def __init__(self, lookback: int = 6):
        self._history: List[str] = []
        self._max = lookback
        self._load()

    def _load(self):
        try:
            import json
            with open(self._PERSIST_FILE, "r") as f:
                data = json.load(f)
            self._history = data.get("history", [])[-self._max:]
            logger.debug(f"[CHOP] Loaded state: {self.summary()}")
        except Exception:
            pass

    def _save(self):
        try:
            import json
            with open(self._PERSIST_FILE, "w") as f:
                json.dump({"history": self._history}, f)
        except Exception:
            pass

    def record_direction(self, direction: str):
        self._history.append(direction)
        if len(self._history) > self._max:
            self._history.pop(0)
        self._save()

    def is_choppy(self) -> bool:
        if len(self._history) < 3:
            return False
        flips = sum(1 for i in range(1, len(self._history))
                    if self._history[i] != self._history[i - 1])
        return flips >= 2

    def chop_score(self) -> float:
        if len(self._history) < 2:
            return 0.0
        flips = sum(1 for i in range(1, len(self._history))
                    if self._history[i] != self._history[i - 1])
        return flips / (len(self._history) - 1)

    def summary(self) -> str:
        return "->".join(self._history[-4:]) if self._history else "empty"


class MomentumAnalyzer:
    """Multi-timeframe rate-of-change from tick buffer."""

    def __init__(self, max_ticks: int = 600):
        self._ticks: List[Tuple[float, float]] = []
        self._max = max_ticks

    def add_tick(self, ts: float, price: float):
        self._ticks.append((ts, price))
        if len(self._ticks) > self._max:
            self._ticks.pop(0)

    def _roc(self, seconds: float) -> float:
        """Rate of change over last N seconds."""
        if len(self._ticks) < 2:
            return 0.0
        now_ts = self._ticks[-1][0]
        cutoff = now_ts - seconds
        old_price = None
        for ts, p in self._ticks:
            if ts >= cutoff:
                old_price = p
                break
        if old_price is None or old_price <= 0:
            return 0.0
        return (self._ticks[-1][1] - old_price) / old_price

    def get_momentum(self) -> float:
        """Weighted ROC: 50% 10s + 30% 30s + 20% 60s"""
        r10 = self._roc(10)
        r30 = self._roc(30)
        r60 = self._roc(60)
        return 0.50 * r10 + 0.30 * r30 + 0.20 * r60

    def get_reversion(self) -> float:
        """Mean-reversion signal: deviation from 2-minute SMA."""
        if len(self._ticks) < 10:
            return 0.0
        now_ts = self._ticks[-1][0]
        cutoff = now_ts - 120
        recent = [p for ts, p in self._ticks if ts >= cutoff]
        if len(recent) < 5:
            return 0.0
        sma = sum(recent) / len(recent)
        current = self._ticks[-1][1]
        if sma <= 0:
            return 0.0
        deviation = (current - sma) / sma
        if abs(deviation) < 0.003:
            return 0.0
        return -deviation

    def clear(self):
        self._ticks.clear()

    @property
    def tick_count(self) -> int:
        return len(self._ticks)


class Predictor:
    """V11: Black-Scholes + EWMA + Momentum predictor."""

    # Logit-space weights (from research on profitable bots)
    MOMENTUM_WEIGHT = 150.0
    REVERSION_WEIGHT = 80.0
    NEAR_EXPIRY_GUARD = 30  # skip momentum adjustments under 30s

    # Abstention thresholds
    MIN_TICKS = 30
    DEAD_ZONE = 0.04       # abstain if |prob - 0.5| < this
    SIGMA_SPIKE = 3.0      # abstain if sigma > 3x mean
    MIN_ACCURACY = 0.45    # abstain if recent accuracy < 35%
    ACCURACY_WINDOW = 8

    def __init__(self):
        self._ewma: Dict[str, EWMAVolatility] = {}
        self._momentum: Dict[str, MomentumAnalyzer] = {}
        self._outcomes: List[bool] = []
        self._load_outcomes()
        self._diag_last: Dict[str, float] = {}
        self._last_fed_ts: Dict[str, float] = {}
        self._window_direction: Optional[str] = None
        self._window_start_ts: int = 0
        self._window_trends: Dict[str, str] = {}
        self._chop_detector = ChopDetector(lookback=4)
        self._boot_ts = time.time()

    def _get_ewma(self, coin: str) -> EWMAVolatility:
        if coin not in self._ewma:
            self._ewma[coin] = EWMAVolatility(lam=0.94)
        return self._ewma[coin]

    def _get_momentum(self, coin: str) -> MomentumAnalyzer:
        if coin not in self._momentum:
            self._momentum[coin] = MomentumAnalyzer(600)
        return self._momentum[coin]

    def _diag_log(self, key: str, msg: str, interval: float = 15.0):
        now = time.time()
        if now - self._diag_last.get(key, 0) >= interval:
            logger.debug(msg)
            self._diag_last[key] = now

    def feed_ticks(self, coin: str, ticks: List[Tuple[float, float]]):
        """Feed tick history into EWMA and momentum analyzers."""
        ewma = self._get_ewma(coin)
        mom = self._get_momentum(coin)
        last_ts = self._last_fed_ts.get(coin, 0.0)
        new_count = 0
        for ts, price in ticks:
            if ts > last_ts:
                ewma.update(price, ts)
                mom.add_tick(ts, price)
                new_count += 1
        if ticks:
            self._last_fed_ts[coin] = ticks[-1][0]

    def _load_outcomes(self):
        try:
            import json
            with open("/home/ubuntu/v3-bot/outcomes_state.json", "r") as f:
                self._outcomes = json.load(f).get("outcomes", [])[-self.ACCURACY_WINDOW:]
            logger.debug(f"[OUTCOMES] Loaded: {len(self._outcomes)} results, accuracy={self._recent_accuracy():.0%}")
        except Exception:
            pass

    def _save_outcomes(self):
        try:
            import json
            with open("/home/ubuntu/v3-bot/outcomes_state.json", "w") as f:
                json.dump({"outcomes": self._outcomes}, f)
        except Exception:
            pass

    def record_outcome(self, correct: bool):
        self._outcomes.append(correct)
        if len(self._outcomes) > self.ACCURACY_WINDOW:
            self._outcomes.pop(0)
        self._save_outcomes()
        logger.info(f"[OUTCOME] {'WIN' if correct else 'LOSS'} | Recent: {sum(self._outcomes)}/{len(self._outcomes)} = {self._recent_accuracy():.0%}")

    def _recent_accuracy(self) -> float:
        if len(self._outcomes) < 5:
            return 1.0
        return sum(1 for o in self._outcomes if o) / len(self._outcomes)

    def predict(self, info: MarketInfo, *,
                ws_price: float = 0.0,
                ticks: Optional[List[Tuple[float, float]]] = None,
                up_ask: float = 0.0, down_ask: float = 0.0,
                up_mid: float = 0.0, down_mid: float = 0.0,
                up_depth: float = 0.0, down_depth: float = 0.0,
                realized_vol: float = 0.0,
                **kwargs) -> Optional[Prediction]:

        coin = info.coin
        current_price = ws_price if ws_price > 0 else info.current_crypto_price
        strike = info.threshold_price
        now_ts = int(time.time())
        window_start = info.window_start or 0
        window_end = window_start + 900
        time_remaining = max(1.0, window_end - now_ts)
        window_age = max(0, now_ts - window_start)

        if current_price <= 0 or strike <= 0:
            return None

        # Warmup: need at least 30s of data
        warmup = getattr(config, "WARMUP_SEC", 45)
        if window_age < 75:
            self._diag_log(f"warmup-{coin}", f"[WARMUP] {coin}: {window_age}s < 75s hard min", 30.0)
            return None



        # Don't trade last 60s (can't exit + resolution risk)
        if time_remaining < 120:
            self._diag_log(f"late-{coin}", f"[TOO LATE] {coin}: only {time_remaining:.0f}s left — need 120s+", 30.0)
            return None

        # Feed ticks into analyzers
        if ticks and len(ticks) > 0:
            self.feed_ticks(coin, ticks)

        ewma = self._get_ewma(coin)
        mom = self._get_momentum(coin)

        # ── Abstention checks ──
        if ewma.tick_count < self.MIN_TICKS:
            self._diag_log(f"ticks-{coin}", f"[FEW TICKS] {coin}: {ewma.tick_count} < {self.MIN_TICKS}", 30.0)
            return None

        sigma = ewma.get_sigma()
        # Floor sigma at typical crypto minimum to prevent decay to zero
        # during low-tick periods (REST polling with identical prices)
        SIGMA_FLOOR = 1e-05
        if sigma < SIGMA_FLOOR:
            sigma = SIGMA_FLOOR
        if not ewma._initialized:
            self._diag_log(f"nosigma-{coin}", f"[NO VOL] {coin}: not initialized", 30.0)
            return None

        mean_sigma = ewma.get_mean_sigma()
        if mean_sigma > 0 and sigma > self.SIGMA_SPIKE * mean_sigma:
            self._diag_log(
                f"spike-{coin}",
                f"[VOL SPIKE] {coin}: sigma={sigma:.8f} > {self.SIGMA_SPIKE}x mean={mean_sigma:.8f} — abstaining",
                15.0,
            )
            return None

        if self._recent_accuracy() < self.MIN_ACCURACY:
            self._diag_log(f"cold-{coin}", f"[COLD STREAK] accuracy={self._recent_accuracy():.0%} — abstaining", 30.0)
            return None

        # ── Step 1: Trend-based direction (primary signal) ──
        # Use actual price movement to determine direction, not BS math
        momentum_raw = mom.get_momentum()
        roc_60 = mom._roc(60)
        roc_120 = mom._roc(120)

        # Cold-start guard: need real 2-min price history before trading
        if roc_60 == 0.0 and roc_120 == 0.0 and momentum_raw == 0.0:
            self._diag_log(f"cold-start-{coin}", f"[COLD START] {coin}: no momentum data yet — waiting for 2min+ history", 30.0)
            return None

        # Distance from strike as percentage
        dist_pct = (current_price - strike) / strike if strike > 0 else 0.0

        # Trend score: combines short-term momentum with position relative to strike
        # Positive = price moving UP / above strike, Negative = DOWN / below strike
        trend_score = 0.0
        trend_score += dist_pct * 200.0        # position vs strike (strongest signal)
        trend_score += roc_60 * 500.0          # 60s momentum
        trend_score += roc_120 * 300.0         # 2min trend
        trend_score += momentum_raw * 400.0    # weighted momentum (10s/30s/60s)

        # Regime detection: choppy vs trending
        chop = self._chop_detector
        is_chop = chop.is_choppy()

        if is_chop:
            reversion = mom.get_reversion()
            if abs(trend_score) < 0.20 and abs(reversion) < 0.003:
                self._diag_log(
                    f"chop-{coin}",
                    f"[CHOPPY] {coin}: chop={chop.chop_score():.1f} ({chop.summary()}) "
                    f"trend={trend_score:+.2f} rev={reversion*10000:+.1f}bps — need stronger signal",
                    15.0,
                )
                return None
            if abs(reversion) > 0.003 and abs(reversion) > abs(trend_score) * 0.5:
                old_ts = trend_score
                trend_score = reversion * -300.0
                self._diag_log(
                    f"fade-{coin}",
                    f"[FADE] {coin}: choppy market, fading trend={old_ts:+.2f} -> reversion={trend_score:+.2f}",
                    15.0,
                )
        else:
            if abs(trend_score) < 0.40:
                self._diag_log(
                    f"dead-{coin}",
                    f"[WEAK TREND] {coin}: trend={trend_score:+.3f} dist={dist_pct*100:+.4f}% "
                    f"roc60={roc_60*10000:+.1f}bps roc120={roc_120*10000:+.1f}bps — need 0.40+",
                    15.0,
                )
                return None

        # ── Step 2: Convert trend to probability using sigmoid ──
        # Steepness controls how quickly trend translates to confidence
        base_up_prob = _bs_binary_prob(current_price, strike, sigma, time_remaining)
        raw_prob = _sigmoid(trend_score * 3.0)

        # Blend: 70% trend-based, 30% BS mathematical
        combined_prob = 0.70 * raw_prob + 0.30 * base_up_prob
        combined_prob = max(0.01, min(0.99, combined_prob))

        # ── Step 3: Decide direction and evaluate ──
        is_up = combined_prob >= 0.5
        direction = "UP" if is_up else "DOWN"
        win_prob = combined_prob if is_up else (1.0 - combined_prob)
        ask = up_ask if is_up else down_ask
        mid = up_mid if is_up else down_mid
        depth = up_depth if is_up else down_depth
        token_id = info.up_token_id if is_up else info.down_token_id

        # Cross-asset direction consistency
        if window_start != self._window_start_ts:
            self._window_direction = None
            self._window_start_ts = window_start
            self._window_trends.clear()
        
        # Record this coin's trend for consensus
        self._window_trends[coin] = direction
        
        # If we already committed to a direction, block contradictions
        if self._window_direction is not None and direction != self._window_direction:
            self._diag_log(
                f"dirlock-{coin}",
                f"[DIR LOCK] {coin} {direction}: committed to {self._window_direction} — skipping",
                15.0,
            )
            return None
        
        # Consensus check: if 2+ coins have signals, check majority
        if len(self._window_trends) >= 2:
            up_count = sum(1 for d in self._window_trends.values() if d == "UP")
            down_count = sum(1 for d in self._window_trends.values() if d == "DOWN")
            majority = "UP" if up_count > down_count else "DOWN" if down_count > up_count else None
            
            if majority and direction != majority:
                self._diag_log(
                    f"consensus-{coin}",
                    f"[CONSENSUS] {coin} {direction}: market consensus is {majority} "
                    f"({up_count}UP/{down_count}DOWN) — blocking minority bet",
                    15.0,
                )
                return None

        # Entry price filters
        entry_min = getattr(config, "ENTRY_MIN", 0.10)
        entry_max = getattr(config, "ENTRY_MAX", 0.75)

        if ask <= 0.01:
            self._diag_log(f"noask-{coin}-{direction}", f"[NO ASK] {coin} {direction}: ask=0", 30.0)
            return None

        if ask < entry_min:
            self._diag_log(
                f"cheap-{coin}-{direction}",
                f"[CHEAP] {coin} {direction}: ask={ask*100:.0f}c < {entry_min*100:.0f}c", 30.0)
            return None

        if ask > entry_max:
            self._diag_log(
                f"exp-{coin}-{direction}",
                f"[EXPENSIVE] {coin} {direction}: ask={ask*100:.0f}c > {entry_max*100:.0f}c", 30.0)
            return None

        # Edge = our probability minus cost
        edge = win_prob - ask
        min_edge = getattr(config, "MIN_EDGE", 0.05)

        min_prob = getattr(config, "MIN_WIN_PROB", 0.65)
        if win_prob < min_prob:
            self._diag_log(f"lowprob-{coin}", f"[LOW PROB] {coin} {direction}: prob={win_prob*100:.0f}% < {min_prob*100:.0f}%", 15.0)
            return None

        if edge < min_edge:
            self._diag_log(
                f"lowedge-{coin}-{direction}",
                f"[LOW EDGE] {coin} {direction}: prob={win_prob:.1%} ask={ask*100:.0f}c edge={edge*100:.1f}% < {min_edge*100:.0f}%",
                15.0,
            )
            return None

        confidence = "HIGH" if win_prob >= 0.75 and edge >= 0.12 else "MEDIUM"

        reasoning = (
            f"BS={base_up_prob:.1%} sigma={sigma:.2e} T={time_remaining:.0f}s | "
            f"mom={mom.get_momentum()*100:.3f}% rev={mom.get_reversion()*100:.3f}% | "
            f"final={combined_prob:.1%} dir={direction} win={win_prob:.1%} | "
            f"ask={ask*100:.0f}c edge={edge*100:.1f}% depth={depth:.1f}x"
        )

        logger.info(
            f"[SIGNAL] {coin} {direction} | Prob={win_prob:.0%} | Ask={ask*100:.0f}c | "
            f"Edge={edge*100:.1f}% | Trend={trend_score:+.2f} Dist={dist_pct*100:+.3f}% "
            f"ROC60={roc_60*10000:+.1f}bps σ={sigma:.2e} T={time_remaining:.0f}s"
        )

        self._window_direction = direction
        self._chop_detector.record_direction(direction)
        regime = "CHOPPY" if self._chop_detector.is_choppy() else "TRENDING"
        logger.debug(f"[COMMIT] {coin} {direction} | {regime} | history={self._chop_detector.summary()} | trends={dict(self._window_trends)}")

        return Prediction(
            coin=coin,
            direction=direction,
            probability=win_prob,
            poly_price=ask,
            edge=edge,
            confidence=confidence,
            reasoning=reasoning,
            token_id=token_id,
            market_info=info,
            entry_price=ask,
            mc_prob=win_prob,
            depth_ratio=depth,
            directional_edge=win_prob - 0.50,
        )
