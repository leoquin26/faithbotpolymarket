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
import os
import time
from dataclasses import dataclass
from typing import Optional, Dict, List, Tuple
from loguru import logger

import config
import session_calibration as sess_cal
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
    trend_score: float = 0.0
    book_up_mid: float = 0.5
    dir_votes_up: int = 0
    dir_votes_down: int = 0
    dist_pct: float = 0.0


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

    def __init__(self, lam: float = None):
        import os as _os
        # jun10 sigma fix: raw Binance ticks arrive in sub-second bursts;
        # r2=log_ret^2/dt with tiny dt inflated per-sec sigma ~25x (implied
        # 15m move 7%+ vs real ~0.3%), making Black-Scholes N(d2) pure noise.
        self._lambda = float(_os.getenv("EWMA_LAMBDA", "0.97")) if lam is None else lam
        self._dt_floor = float(_os.getenv("EWMA_DT_FLOOR", "1.0"))
        self._sigma_cap = float(_os.getenv("EWMA_SIGMA_CAP", "5.0e-4"))
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

        dt = max(ts - self._last_ts, self._dt_floor)

        if price == self._last_price:
            self._last_ts = ts
            return self.get_sigma()

        log_ret = math.log(price / self._last_price) if self._last_price > 0 else 0.0
        r2_per_sec = (log_ret * log_ret) / dt

        self._variance = self._lambda * self._variance + (1.0 - self._lambda) * r2_per_sec
        self._variance = max(self._variance, 1e-10)
        # impossible-move guard: cap per-second sigma (sigma_cap^2 is var cap)
        _var_cap = self._sigma_cap * self._sigma_cap
        if self._variance > _var_cap:
            self._variance = _var_cap

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
    """Track recent window directions to detect choppy vs trending markets.

    Per-coin histories (audit M1, Jun 10 2026): mixing BTC/ETH/SOL directions
    into one shared list made "BTC UP then SOL DOWN" look like a flip, polluting
    is_choppy() and FLIP GUARD. Each coin now keeps its own history. The legacy
    global `_history` is retained for backward compatibility (run_bot afternoon
    reset, persistence) and represents the most recent cross-asset commits.
    """
    _PERSIST_FILE = "/home/ubuntu/v3-bot/chop_state.json"

    def __init__(self, lookback: int = 6):
        self._history: List[str] = []          # legacy global (kept)
        self._by_coin: Dict[str, List[str]] = {}
        self._max = lookback
        self._load()

    def _hist(self, coin: Optional[str]) -> List[str]:
        if not coin:
            return self._history
        return self._by_coin.setdefault(coin, [])

    def _load(self):
        try:
            import json
            with open(self._PERSIST_FILE, "r") as f:
                data = json.load(f)
            self._history = data.get("history", [])[-self._max:]
            for c, h in (data.get("by_coin", {}) or {}).items():
                self._by_coin[c] = list(h)[-self._max:]
            logger.debug(f"[CHOP] Loaded state: {self.summary()}")
        except Exception:
            pass

    def _save(self):
        try:
            import json
            with open(self._PERSIST_FILE, "w") as f:
                json.dump({"history": self._history, "by_coin": self._by_coin}, f)
        except Exception:
            pass

    def record_direction(self, direction: str, coin: Optional[str] = None):
        # Always update the legacy global so run_bot reset + old readers work.
        self._history.append(direction)
        if len(self._history) > self._max:
            self._history.pop(0)
        if coin:
            h = self._hist(coin)
            h.append(direction)
            if len(h) > self._max:
                h.pop(0)
        self._save()

    def is_choppy(self, coin: Optional[str] = None) -> bool:
        h = self._hist(coin)
        if len(h) < 3:
            return False
        flips = sum(1 for i in range(1, len(h)) if h[i] != h[i - 1])
        return flips >= 2

    def chop_score(self, coin: Optional[str] = None) -> float:
        h = self._hist(coin)
        if len(h) < 2:
            return 0.0
        flips = sum(1 for i in range(1, len(h)) if h[i] != h[i - 1])
        return flips / (len(h) - 1)

    def summary(self, coin: Optional[str] = None) -> str:
        h = self._hist(coin)
        return "->".join(h[-4:]) if h else "empty"


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
        self._window_directions: Dict[str, str] = {}  # per-coin dir lock
        self._engine_conviction: Dict[str, str] = {}  # mom+book agreed direction
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

    def _get_cl_momentum(self, coin: str) -> MomentumAnalyzer:
        """Separate analyzer fed ONLY Chainlink ticks, for directional ROC
        that matches the level feed (does not affect EWMA/sigma)."""
        if not hasattr(self, '_cl_momentum'):
            self._cl_momentum = {}
        if coin not in self._cl_momentum:
            self._cl_momentum[coin] = MomentumAnalyzer(600)
        return self._cl_momentum[coin]

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
        _spot_src = "binance"
        try:
            import chainlink_ws as _cl_spot
            _cl_px = _cl_spot.get_price(coin)
            if not _cl_px or _cl_px <= 0:
                try:
                    import chainlink_onchain as _cl_oc
                    _cl_px = _cl_oc.get_price(coin)
                except Exception:
                    pass
            if _cl_px and _cl_px > 0:
                current_price = _cl_px
                _spot_src = "chainlink"
        except Exception:
            _spot_src = "binance"
        strike = info.threshold_price
        _strike_src = getattr(info, "strike_source", "") or "unknown"
        now_ts = int(time.time())
        window_start = info.window_start or 0
        window_end = window_start + 900
        time_remaining = max(1.0, window_end - now_ts)
        window_age = max(0, now_ts - window_start)

        if current_price <= 0 or strike <= 0:
            return None

        # Warmup: env-driven (was hardcoded 75s — blocked first 75s every window)
        warmup = int(os.getenv("HARD_WARMUP_15M", os.getenv("WARMUP_SEC", "90")))
        if window_age < warmup:
            self._diag_log(
                f"warmup-{coin}",
                f"[WARMUP] {coin}: {window_age}s < {warmup}s min",
                30.0,
            )
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
        SIGMA_FLOOR = float(os.getenv("SIGMA_FLOOR_MIN", "2.5e-4"))
        if sigma < SIGMA_FLOOR:
            self._diag_log(
                f"lowvol-{coin}",
                f"[LOW VOL] {coin}: sigma={sigma:.2e} < {SIGMA_FLOOR:.2e} — abstaining",
                15.0,
            )
            return None
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

        # Cold streak check is in morning_predictor only — afternoon has proven 80%+ WR
        # Morning losses should never block afternoon trading
        pass  # accuracy tracking still active for morning_predictor to read

        # ── Step 1: Trend score (confidence only — direction set in Step 2 settlement) ──
        momentum_raw = mom.get_momentum()
        roc_60 = mom._roc(60)
        roc_120 = mom._roc(120)
        # Hybrid: prefer Chainlink-derived ROC for direction (matches level).
        _cl_ticks_in = kwargs.get('chainlink_ticks') or None
        _cl_mom = None
        if _cl_ticks_in and len(_cl_ticks_in) >= 3:
            _cl_mom = self._get_cl_momentum(coin)
            _cl_last = self._cl_last_fed.get(coin, 0.0) if hasattr(self, '_cl_last_fed') else 0.0
            if not hasattr(self, '_cl_last_fed'):
                self._cl_last_fed = {}
            for _ts, _p in _cl_ticks_in:
                if _ts > _cl_last:
                    _cl_mom.add_tick(_ts, _p)
            self._cl_last_fed[coin] = _cl_ticks_in[-1][0]
            _r60 = _cl_mom._roc(60)
            if _r60 != 0.0:
                roc_60 = _r60
            _r120 = _cl_mom._roc(120)
            if _r120 != 0.0:
                roc_120 = _r120

        # Cold-start guard: need 60s+ of tick data (not momentum values, which can be zero in flat markets)
        mom_ticks = mom.tick_count
        mom_span = (mom._ticks[-1][0] - mom._ticks[0][0]) if mom_ticks >= 2 else 0.0
        if mom_span < 60.0:
            self._diag_log(f"cold-start-{coin}", f"[COLD START] {coin}: only {mom_span:.0f}s of tick data (need 60s+, have {mom_ticks} ticks)", 30.0)
            return None

        # Distance from strike as percentage
        dist_pct = (current_price - strike) / strike if strike > 0 else 0.0

        # Multi-timeframe ROC for direction confirmation
        roc_300 = mom._roc(300)  # 5-minute trend (big picture)
        if _cl_mom is not None:
            _r300 = _cl_mom._roc(300)
            if _r300 != 0.0:
                roc_300 = _r300

        # Trend score: early window favors dist + 5m ROC over noisy 60s
        early_window = window_age < int(os.getenv("ACCURACY_EARLY_SEC", "300"))
        if early_window:
            w_dist, w_r60, w_r120, w_r300, w_mom = 250.0, 400.0, 300.0, 350.0, 300.0
        else:
            w_dist, w_r60, w_r120, w_r300, w_mom = 200.0, 400.0, 350.0, 300.0, 300.0
        trend_score = (
            dist_pct * w_dist + roc_60 * w_r60 + roc_120 * w_r120
            + roc_300 * w_r300 + momentum_raw * w_mom
        )

        # Direction disagreement filter: if 5-min trend strongly opposes 1-min signal,
        # the signal is likely a bounce, not a reversal. Dampen the trend score.
        if roc_300 != 0.0 and roc_60 != 0.0:
            short_dir = 1 if roc_60 > 0 else -1
            long_dir = 1 if roc_300 > 0 else -1
            if short_dir != long_dir and abs(roc_300) > abs(roc_60) * 0.3:
                dampen = 0.50  # cut trend score in half when timeframes disagree
                old_ts = trend_score
                trend_score *= dampen
                self._diag_log(
                    f"disagree-{coin}",
                    f"[TF DISAGREE] {coin}: roc60={roc_60*10000:+.1f}bps vs roc300={roc_300*10000:+.1f}bps "
                    f"— dampened trend {old_ts:+.2f} -> {trend_score:+.2f}",
                    15.0,
                )

        # Regime detection: choppy vs trending (per-coin history — audit M1)
        chop = self._chop_detector
        is_chop = chop.is_choppy(coin)

        if is_chop:
            reversion = mom.get_reversion()
            if abs(trend_score) < 0.20 and abs(reversion) < 0.003:
                self._diag_log(
                    f"chop-{coin}",
                    f"[CHOPPY] {coin}: chop={chop.chop_score(coin):.1f} ({chop.summary(coin)}) "
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
        # Session-calibrated trend gates
        _session = sess_cal.get_session()
        if is_chop:
            _min_tr = _session.choppy_min_trend
            _dist_clear = abs(dist_pct) >= float(os.getenv("CHOPPY_DIST_BYPASS", "0.0012"))
            _trend_strong = abs(trend_score) >= float(os.getenv("CHOPPY_TREND_BYPASS", "0.32"))
            if abs(trend_score) < _min_tr and not (_dist_clear and _trend_strong):
                self._diag_log(
                    f"chopstrict-{coin}",
                    f"[CHOPPY STRICT] {coin}: |trend|={abs(trend_score):.3f} < {_min_tr} "
                    f"session={_session.name} — skip",
                    15.0,
                )
                return None
        else:
            _min_trend = _session.min_trend
            if abs(trend_score) < _min_trend:
                self._diag_log(
                    f"dead-{coin}",
                    f"[WEAK TREND] {coin}: trend={trend_score:+.3f} dist={dist_pct*100:+.4f}% "
                    f"session={_session.name} need {_min_trend:.2f}+ — skip",
                    15.0,
                )
                return None

        # ── Step 2: Settlement-first direction (level vs strike at expiry) ──
        base_up_prob = _bs_binary_prob(current_price, strike, sigma, time_remaining)
        raw_prob = _sigmoid(trend_score * 3.0)

        _ua_b, _da_b = float(up_ask or 0), float(down_ask or 0)
        if _ua_b > 0.02 and _da_b > 0.02:
            book_up = _ua_b / (_ua_b + _da_b)
        elif up_mid > 0.01 and down_mid > 0.01:
            book_up = up_mid / (up_mid + down_mid)
        elif up_mid > 0.01:
            book_up = up_mid
        elif down_mid > 0.01:
            book_up = 1.0 - down_mid
        else:
            book_up = 0.5
        book_up = max(0.01, min(0.99, book_up))

        _near_dist = float(os.getenv("SETTLEMENT_NEAR_DIST", "0.0018"))
        _min_roc = float(os.getenv("SETTLEMENT_MIN_ROC300", "0.00003"))
        _book_edge = float(os.getenv("SETTLEMENT_BOOK_EDGE", "0.02"))
        _bs_edge = float(os.getenv("SETTLEMENT_BS_EDGE", "0.02"))

        def _dir_from_sign(val: float, edge: float = 0.0) -> Optional[str]:
            if val > edge:
                return "UP"
            if val < -edge:
                return "DOWN"
            return None

        settlement_dir: Optional[str] = None
        if abs(dist_pct) < _near_dist:
            level_dir = _dir_from_sign(dist_pct, 0.0)
            if not level_dir:
                self._diag_log(
                    f"settle-atstrike-{coin}",
                    f"[SETTLEMENT] {coin}: at strike dist={dist_pct*100:+.4f}% — abstain",
                    12.0,
                )
                return None

            # ── Near-strike accuracy gate (Jun 10 2026) ───────────────────────
            # The BTC loss was dist=-0.093% with 14m left, trusted purely because
            # momentum agreed — but at <0.10% with lots of time, level is noise.
            # Policy (user-tuned, NOT block-everything):
            #   • hard floor: never trade inside ABS_FLOOR
            #   • grey band [ABS_FLOOR, _near_dist): trade only if BOOK agrees
            #   • time-scaled: require more cushion when more time remains
            _abs_floor = float(os.getenv("NEAR_DIST_HARD_FLOOR", "0.0010"))   # 0.10%
            # time scaling: at full window add up to TIME_BPS extra cushion,
            # decaying to 0 near expiry (9bps@14m risky, 9bps@2m fine).
            _time_bps = float(os.getenv("NEAR_DIST_TIME_BPS", "0.0008"))      # +0.08%
            _frac = max(0.0, min(1.0, (time_remaining - 120.0) / 780.0))
            _req_floor = _abs_floor + _time_bps * _frac
            _book_dir_now = "UP" if book_up >= (0.50 + _book_edge) else (
                "DOWN" if book_up <= (0.50 - _book_edge) else None
            )
            _book_confirms = (_book_dir_now == level_dir)
            if abs(dist_pct) < _req_floor and not _book_confirms:
                self._diag_log(
                    f"near-floor-{coin}",
                    f"[NEAR FLOOR] {coin} {level_dir}: dist={dist_pct*100:+.3f}% "
                    f"< req={_req_floor*100:.3f}% (T={time_remaining:.0f}s) and book "
                    f"not confirming (book_up={book_up:.2f}) — skip",
                    12.0,
                )
                return None

            # Dead-momentum block (Jun 10 2026 r2): a near-strike level with
            # lots of time left and NO live short-term push (ROC60 flat or
            # against the level) is a coin flip that tends to revert. Require
            # live ROC60 to agree unless the book strongly confirms.
            _dm_time = float(os.getenv("NEAR_DEADMOM_MIN_T", "480"))   # 8 min
            _dm_roc = float(os.getenv("NEAR_DEADMOM_MIN_ROC60", "0.00003"))
            if time_remaining >= _dm_time and not _book_confirms:
                _roc60_dir = _dir_from_sign(roc_60, _dm_roc)
                if _roc60_dir != level_dir:
                    self._diag_log(
                        f"near-deadmom-{coin}",
                        f"[NEAR DEADMOM] {coin} {level_dir}: dist={dist_pct*100:+.3f}% "
                        f"roc60={roc_60*10000:+.1f}bps not confirming with "
                        f"{time_remaining:.0f}s left — coin-flip, skip",
                        12.0,
                    )
                    return None

            _roc_veto = float(os.getenv("SETTLEMENT_ROC_VETO", "0.00008"))
            if abs(roc_300) >= _roc_veto:
                roc_dir = _dir_from_sign(roc_300, _min_roc)
                if roc_dir and roc_dir != level_dir:
                    self._diag_log(
                        f"settle-roc-{coin}",
                        f"[SETTLEMENT] {coin}: dist→{level_dir} roc300→{roc_dir} "
                        f"(dist={dist_pct*100:+.3f}% roc300={roc_300*10000:+.1f}bps) — abstain",
                        12.0,
                    )
                    return None
            book_dir = "UP" if book_up >= (0.50 + _book_edge) else (
                "DOWN" if book_up <= (0.50 - _book_edge) else None
            )
            if book_dir and book_dir != level_dir:
                self._diag_log(
                    f"settle-book-{coin}",
                    f"[SETTLEMENT] {coin}: dist→{level_dir} book→{book_dir} "
                    f"(book_up={book_up:.2f}) — abstain",
                    12.0,
                )
                return None
            bs_dir = "UP" if base_up_prob >= (0.50 + _bs_edge) else (
                "DOWN" if base_up_prob <= (0.50 - _bs_edge) else None
            )
            if bs_dir and bs_dir != level_dir:
                self._diag_log(
                    f"settle-bs-{coin}",
                    f"[SETTLEMENT] {coin}: dist→{level_dir} BS→{bs_dir} "
                    f"(N(d2)={base_up_prob:.1%}) — abstain",
                    12.0,
                )
                return None
            settlement_dir = level_dir
            combined_prob = 0.50 * base_up_prob + 0.30 * book_up + 0.20 * raw_prob
        else:
            # Far from strike: level (dist) leads; BS only vetoes strong disagreement
            dist_dir = "UP" if dist_pct > 0 else "DOWN"
            _bs_veto = float(os.getenv("SETTLEMENT_FAR_BS_VETO", "0.05"))
            if dist_dir == "UP" and base_up_prob < (0.50 - _bs_veto):
                self._diag_log(
                    f"settle-far-{coin}",
                    f"[SETTLEMENT] {coin}: dist→UP vetoed by BS={base_up_prob:.1%} "
                    f"(dist={dist_pct*100:+.3f}%) — abstain",
                    12.0,
                )
                return None
            if dist_dir == "DOWN" and base_up_prob > (0.50 + _bs_veto):
                self._diag_log(
                    f"settle-far-{coin}",
                    f"[SETTLEMENT] {coin}: dist→DOWN vetoed by BS={base_up_prob:.1%} "
                    f"(dist={dist_pct*100:+.3f}%) — abstain",
                    12.0,
                )
                return None
            settlement_dir = dist_dir
            combined_prob = 0.45 * base_up_prob + 0.25 * raw_prob + 0.20 * book_up + (
                0.10 * (base_up_prob if dist_dir == "UP" else (1.0 - base_up_prob))
            )

        combined_prob = max(0.01, min(0.99, combined_prob))

        DIST_THRESHOLD = float(os.getenv("ACCURACY_DIST_PENALTY", "0.0008"))
        if abs(dist_pct) < DIST_THRESHOLD:
            dist_factor = abs(dist_pct) / DIST_THRESHOLD
            penalty = 0.40 * (1.0 - dist_factor)
            combined_prob = combined_prob * (1.0 - penalty) + 0.50 * penalty

        direction = settlement_dir
        is_up = direction == "UP"
        win_prob = combined_prob if is_up else (1.0 - combined_prob)
        # Book is a CONFIDENCE nudge, not the probability. Heavy book weight
        # (was 0.60) collapsed edge = win_prob - ask toward ~0 whenever the book
        # was efficient, locking out correct settlement calls with [LOW EDGE]
        # (audit C2, Jun 10 2026). Default 0.20 book / 0.80 model; env-tunable.
        book_side = book_up if is_up else (1.0 - book_up)
        _book_w = float(os.getenv("WIN_PROB_BOOK_WEIGHT", "0.20"))
        _book_w = max(0.0, min(0.6, _book_w))
        win_prob = max(0.01, min(0.99, _book_w * book_side + (1.0 - _book_w) * win_prob))

        votes_up = votes_down = 0
        if abs(dist_pct) >= float(os.getenv("ACCURACY_VOTE_MIN_DIST", "0.00005")):
            votes_up += 1 if dist_pct > 0 else 0
            votes_down += 1 if dist_pct < 0 else 0
        if abs(roc_300) >= _min_roc:
            votes_up += 1 if roc_300 > 0 else 0
            votes_down += 1 if roc_300 < 0 else 0
        if book_up >= 0.52:
            votes_up += 1
        elif book_up <= 0.48:
            votes_down += 1
        # Far from strike, settlement intentionally lets dist LEAD and book only
        # vetoes via BS — re-applying a book-weighted vote veto here would undo
        # that and kill correct trending calls on a laggy/expensive book
        # (audit H2, Jun 10 2026). Skip DIR VOTE once dist is clearly meaningful.
        _vote_skip_dist = float(os.getenv("ACCURACY_VOTE_SKIP_DIST", "0.0006"))
        _vote_active = abs(dist_pct) < _vote_skip_dist
        vote_dir = "UP" if votes_up >= 2 else ("DOWN" if votes_down >= 2 else None)
        if _vote_active and vote_dir and vote_dir != direction:
            self._diag_log(
                f"dirvote-{coin}",
                f"[DIR VOTE] {coin}: settlement={direction} vote={vote_dir} "
                f"(dist={dist_pct*100:+.3f}% roc300={roc_300*10000:+.1f}bps book={book_up:.2f} "
                f"{votes_up}UP/{votes_down}DN) — skip",
                12.0,
            )
            return None
        ask = up_ask if is_up else down_ask
        mid = up_mid if is_up else down_mid
        depth = up_depth if is_up else down_depth
        token_id = info.up_token_id if is_up else info.down_token_id

        # ── Engine conviction: mom + book agree → trust engine, no dist-bounce flip ──
        _conv_on = os.getenv("ENGINE_CONVICTION_ON", "on").lower() not in ("off", "0", "false")
        if _conv_on:
            _bd_gap = float(os.getenv("BOOK_DIRECTION_GAP", "0.04"))
            _roc60_min = float(os.getenv("MOM_LOCK_MIN_ROC60", "0.00003"))
            _roc300_min = float(os.getenv("MOM_LOCK_MIN_ROC300", "0.00003"))
            _mom_down = roc_60 < -_roc60_min and roc_300 < -_roc300_min
            _mom_up = roc_60 > _roc60_min and roc_300 > _roc300_min
            _ua, _da = float(up_ask or 0), float(down_ask or 0)
            _book_screams_down = (
                book_up <= (0.50 - _bd_gap)
                or (_ua > 0.05 and _da > 0.05 and _ua + _bd_gap <= _da)
            )
            _book_screams_up = (
                book_up >= (0.50 + _bd_gap)
                or (_ua > 0.05 and _da > 0.05 and _da + _bd_gap <= _ua)
            )
            _forced = False
            if _mom_down and _book_screams_down and direction == "UP":
                self._diag_log(
                    f"engine-conv-{coin}",
                    f"[ENGINE CONFLICT] {coin}: settlement UP vs mom+book DOWN — skip",
                    12.0,
                )
                return None
            elif _mom_up and _book_screams_up and direction == "DOWN":
                self._diag_log(
                    f"engine-conv-{coin}",
                    f"[ENGINE CONFLICT] {coin}: settlement DOWN vs mom+book UP — skip",
                    12.0,
                )
                return None
            elif _mom_down and _book_screams_down:
                self._engine_conviction[coin] = "DOWN"
            elif _mom_up and _book_screams_up:
                self._engine_conviction[coin] = "UP"

            # Engine lock: only block weak flips (strong trend can override)
            _prior_conv = self._engine_conviction.get(coin)
            _eng_lock_on = os.getenv("ENGINE_LOCK_ON", "off").lower() == "on"
            if (_eng_lock_on and not _forced and _prior_conv and direction != _prior_conv
                    and abs(trend_score) < float(os.getenv("ENGINE_FLIP_MIN_TREND", "1.2"))):
                self._diag_log(
                    f"engine-lock-{coin}",
                    f"[ENGINE LOCK] {coin} {direction}: committed {_prior_conv} "
                    f"trend={trend_score:+.2f} too weak to flip — skip",
                    12.0,
                )
                return None

        # ── Minimum distance: thin dist = coin flip, not a real edge ──
        _sg_dist = sess_cal.get_session()
        _min_dist_up = _sg_dist.min_dist
        _min_dist_dn = _sg_dist.min_dist
        if direction == "UP" and dist_pct < _min_dist_up:
            self._diag_log(
                f"thin-dist-{coin}",
                f"[THIN DIST] {coin} UP: dist={dist_pct*100:+.3f}% < {_min_dist_up*100:.2f}% above strike — skip",
                12.0,
            )
            return None
        if direction == "DOWN" and dist_pct > -_min_dist_dn:
            self._diag_log(
                f"thin-dist-{coin}",
                f"[THIN DIST] {coin} DOWN: dist={dist_pct*100:+.3f}% > -{_min_dist_dn*100:.2f}% below strike — skip",
                12.0,
            )
            return None

        # ── Overshoot cap (jun11): price too far from strike mean-reverts ──
        # Backtest (90 trades): |dist|>=0.30% won only 40% (4/10, -$12) vs
        # sweet spot 0.10-0.25% at 70%. Far-from-strike = the move already
        # happened and tends to round-trip back through strike before expiry
        # (today's ETH UP +0.339% -> finished DOWN). Skip unless momentum is
        # STILL accelerating in our favor (genuine breakout, not exhaustion).
        _overshoot = float(os.getenv("OVERSHOOT_MAX_DIST", "0.0030"))
        if abs(dist_pct) >= _overshoot:
            # data (90 trades): overshoot trades 40% WR regardless of momentum.
            # "still pushing" subset was WORSE (33%) — momentum at overshoot is
            # exhaustion, not breakout. Clean cap, no exception.
            self._diag_log(
                f"overshoot-{coin}",
                f"[OVERSHOOT] {coin} {direction}: dist={dist_pct*100:+.3f}% "
                f">= {_overshoot*100:.2f}% — price overshot, mean-revert risk, skip",
                12.0,
            )
            return None

        # ── Bounce guard: roc60 positive + thin dist below strike = dead cat, not DOWN ──
        _bounce_roc = float(os.getenv("BOUNCE_ROC60_MIN", "0.00005"))
        _bounce_dist = float(os.getenv("BOUNCE_DIST_MAX", "0.0025"))
        if (direction == "DOWN" and roc_60 > _bounce_roc
                and dist_pct > -_bounce_dist):
            self._diag_log(
                f"bounce-{coin}",
                f"[BOUNCE] {coin} DOWN: roc60={roc_60*10000:+.1f}bps dist={dist_pct*100:+.3f}% — bounce, skip",
                12.0,
            )
            return None
        # Symmetric guard: roc60 negative + thin dist above strike = failing
        # bounce, not a real UP (audit H4 — UP errors in chop were unguarded).
        if (direction == "UP" and roc_60 < -_bounce_roc
                and dist_pct < _bounce_dist):
            self._diag_log(
                f"bounce-{coin}",
                f"[BOUNCE] {coin} UP: roc60={roc_60*10000:+.1f}bps dist={dist_pct*100:+.3f}% — failing bounce, skip",
                12.0,
            )
            return None

        # ── Expensive DOWN needs deep dist (56-64c DOWN on -0.2% dist = today's loss) ──
        _bk_agree = sess_cal.book_agrees(direction, book_up)
        _exp_dn_ask = sess_cal.session_expensive_down_max_ask(_bk_agree and direction == "DOWN")
        _exp_dn_dist = sess_cal.session_expensive_down_min_dist(_bk_agree and direction == "DOWN")
        if direction == "DOWN" and ask >= _exp_dn_ask and abs(dist_pct) < _exp_dn_dist:
            self._diag_log(
                f"exp-dn-{coin}",
                f"[EXPENSIVE DOWN] {coin}: ask={ask*100:.0f}c dist={dist_pct*100:+.3f}% "
                f"need {_exp_dn_dist*100:.2f}%+ cushion — skip",
                12.0,
            )
            return None

        # Spot vs strike: never buy DOWN above strike / UP below strike
        try:
            if os.getenv("STRIKE_DIRECTION_ENFORCE", "on").lower() == "on":
                _sd_min = float(os.getenv("STRIKE_DIRECTION_MIN_DIST", "0.00015"))
                if dist_pct >= _sd_min and direction == "DOWN":
                    self._diag_log(
                        f"strike-dir-{coin}",
                        f"[STRIKE CONFLICT] {coin} DOWN: price {dist_pct*100:+.3f}% above strike — skip",
                        15.0,
                    )
                    return None
                if dist_pct <= -_sd_min and direction == "UP":
                    self._diag_log(
                        f"strike-dir-{coin}",
                        f"[STRIKE CONFLICT] {coin} UP: price {dist_pct*100:+.3f}% below strike — skip",
                        15.0,
                    )
                    return None
        except Exception as _e_sd:
            logger.debug(f"[STRIKE CONFLICT] check failed: {_e_sd}")

        # Book ask gate: UP token cheap = market expects DOWN (and vice versa)
        try:
            if os.getenv("BOOK_DIRECTION_ENFORCE", "on").lower() == "on":
                _bd_gap = float(os.getenv("BOOK_DIRECTION_GAP", "0.04"))
                _ua, _da = float(up_ask or 0), float(down_ask or 0)
                if direction == "UP" and _ua > 0.05 and _da > 0.05 and _ua + _bd_gap <= _da:
                    self._diag_log(
                        f"book-conflict-{coin}",
                        f"[BOOK CONFLICT] {coin} UP: UP ask={_ua*100:.0f}c cheaper than "
                        f"DOWN={_da*100:.0f}c — market says DOWN, skip UP",
                        12.0,
                    )
                    return None
                if direction == "DOWN" and _ua > 0.05 and _da > 0.05 and _da + _bd_gap <= _ua:
                    self._diag_log(
                        f"book-conflict-{coin}",
                        f"[BOOK CONFLICT] {coin} DOWN: DOWN ask={_da*100:.0f}c cheaper than "
                        f"UP={_ua*100:.0f}c — market says UP, skip DOWN",
                        12.0,
                    )
                    return None
        except Exception as _e_bc:
            logger.debug(f"[BOOK CONFLICT] check failed: {_e_bc}")

        # Cross-asset direction consistency
        if window_start != self._window_start_ts:
            self._window_direction = None
            self._window_directions.clear()
            self._engine_conviction.clear()
            self._window_start_ts = window_start
            self._window_trends.clear()
        
        # Record this coin's trend for consensus
        self._window_trends[coin] = direction
        
        # Per-coin DIR LOCK: only if prior commit was strong trend
        _commit_min = float(os.getenv("DIR_COMMIT_MIN_TREND", "0.55"))
        prior_dir = self._window_directions.get(coin)
        if prior_dir is not None and direction != prior_dir:
            _prior_strong = self._window_directions.get(f"{coin}_strength", 0) >= _commit_min
            if _prior_strong and abs(trend_score) < float(os.getenv("DIR_FLIP_MIN_TREND", "1.0")):
                self._diag_log(
                    f"dirlock-{coin}",
                    f"[DIR LOCK] {coin} {direction}: committed to {prior_dir} "
                    f"(|trend|={abs(trend_score):.2f} < flip min) — skipping",
                    15.0,
                )
                return None
        
        # Consensus check: if 2+ coins have signals, check majority.
        # Count only OTHER coins' COMMITTED directions (self._window_directions),
        # not self._window_trends which includes this coin's not-yet-committed
        # provisional dir and coins that later abstained (audit H1, Jun 10 2026).
        _consensus_on = os.getenv("CONSENSUS_GATE_ON", "on").lower() not in ("off", "0", "false")
        _committed_other = [
            d for c, d in self._window_directions.items()
            if c != coin and not c.endswith("_strength") and d in ("UP", "DOWN")
        ]
        if _consensus_on and len(_committed_other) >= 2:
            up_count = sum(1 for d in _committed_other if d == "UP")
            down_count = sum(1 for d in _committed_other if d == "DOWN")
            majority = "UP" if up_count > down_count else "DOWN" if down_count > up_count else None

            if majority and direction != majority:
                # Bypass: a high-conviction, clearly-positioned call should not be
                # blocked by two weak opposite coins (env knobs were never read).
                _cb_dist = float(os.getenv("CONSENSUS_BYPASS_MIN_DIST", "0.0012"))
                _cb_prob = float(os.getenv("CONSENSUS_BYPASS_MIN_PROB", "0.72"))
                _bypass = abs(dist_pct) >= _cb_dist and win_prob >= _cb_prob
                if not _bypass:
                    self._diag_log(
                        f"consensus-{coin}",
                        f"[CONSENSUS] {coin} {direction}: market consensus is {majority} "
                        f"({up_count}UP/{down_count}DOWN) — blocking minority bet",
                        15.0,
                    )
                    return None
                self._diag_log(
                    f"consensus-bypass-{coin}",
                    f"[CONSENSUS BYPASS] {coin} {direction}: dist={dist_pct*100:+.3f}% "
                    f"prob={win_prob:.0%} overrides {majority} consensus",
                    15.0,
                )

        # ── FLIP GUARD (peak): block weak direction flips (per-coin — audit M1) ──
        try:
            recent_hist = list(self._chop_detector._hist(coin)[-4:])
        except Exception:
            recent_hist = []
        if len(recent_hist) >= 3:
            opposite = sum(1 for d in recent_hist if d and d != direction)
            FLIP_TREND_MIN = float(os.getenv("FLIP_TREND_MIN_15M", "0.55"))
            _flip_bypass_dist = float(os.getenv("FLIP_GUARD_BYPASS_DIST", "0.0012"))
            _dist_agrees = (
                (direction == "UP" and dist_pct >= _flip_bypass_dist)
                or (direction == "DOWN" and dist_pct <= -_flip_bypass_dist)
            )
            if opposite >= 3 and abs(trend_score) < FLIP_TREND_MIN and not _dist_agrees:
                self._diag_log(
                    f"flipguard-{coin}",
                    f"[FLIP GUARD] {coin} {direction}: recent={'->'.join(recent_hist)} "
                    f"trend={trend_score:+.2f} dist={dist_pct*100:+.3f}% — need |trend|>={FLIP_TREND_MIN}",
                    12.0,
                )
                return None

        # ── Momentum must agree with direction ──
        _mom_align = os.getenv("MOMENTUM_ALIGN_ON", "on").lower() not in ("off", "0", "false")
        if _mom_align:
            _mm = float(os.getenv("MOM_ALIGN_MIN_ROC", "0.00003"))
            # UP: both ROC must be negative to block (keep — prevents dead-cat UP)
            if direction == "UP" and roc_60 < -_mm and roc_300 < -_mm:
                self._diag_log(
                    f"mom-conflict-{coin}",
                    f"[MOM CONFLICT] {coin} UP: roc60={roc_60*10000:+.1f}bps "
                    f"roc300={roc_300*10000:+.1f}bps both negative — skip",
                    12.0,
                )
                return None
            # DOWN: only block if roc_300 positive AND trend weak (allow dump bounces)
            if (direction == "DOWN" and roc_300 > _mm
                    and abs(trend_score) < float(os.getenv("MOM_DOWN_MIN_TREND", "0.80"))):
                self._diag_log(
                    f"mom-conflict-{coin}",
                    f"[MOM CONFLICT] {coin} DOWN: roc300={roc_300*10000:+.1f}bps "
                    f"positive + weak trend={trend_score:+.2f} — skip",
                    12.0,
                )
                return None

        # ── Expensive UP needs real distance (Tier 1) ──
        _exp_up_max = float(os.getenv("EXPENSIVE_UP_MAX_ASK", "0.58"))
        _exp_up_dist = float(os.getenv("EXPENSIVE_UP_MIN_DIST", "0.0015"))
        if direction == "UP" and ask >= _exp_up_max and abs(dist_pct) < _exp_up_dist:
            self._diag_log(
                f"exp-up-{coin}",
                f"[EXPENSIVE UP] {coin}: ask={ask*100:.0f}c dist={dist_pct*100:+.3f}% "
                f"< {_exp_up_dist*100:.2f}% — skip",
                12.0,
            )
            return None

        # Entry price filters — direction-aware (DOWN 90c is normal in dumps)
        if direction == "DOWN":
            entry_max = float(os.getenv("ENTRY_MAX_DOWN", os.getenv("ENTRY_MAX", "0.72")))
        else:
            entry_max = float(os.getenv("ENTRY_MAX_UP", "0.62"))
        if early_window:
            entry_min = float(os.getenv("EARLY_ENTRY_MIN", "0.35"))
        else:
            entry_min = getattr(config, "ENTRY_MIN", 0.10)

        if ask <= 0.01:
            self._diag_log(f"noask-{coin}-{direction}", f"[NO ASK] {coin} {direction}: ask=0", 30.0)
            return None

        if ask < entry_min:
            self._diag_log(
                f"cheap-{coin}-{direction}",
                f"[CHEAP] {coin} {direction}: ask={ask*100:.0f}c < {entry_min*100:.0f}c — skip",
                30.0)
            return None

        if ask > entry_max:
            self._diag_log(
                f"exp-{coin}-{direction}",
                f"[EXPENSIVE] {coin} {direction}: ask={ask*100:.0f}c > {entry_max*100:.0f}c", 30.0)
            return None

        # Edge = our probability minus cost
        edge = win_prob - ask

        # Live probability calibration (regime + chop + late window)
        _cal_live = os.getenv("CALIBRATION_LIVE", "off").lower() in ("on", "1", "true")
        _cal_shadow = os.getenv("CALIBRATION_SHADOW", "off").lower() in ("on", "1", "true")
        if _cal_live or _cal_shadow:
            try:
                from regime_aware.confidence_calibrator import calibrate as _calibrate
                from regime_aware.confidence_calibrator import format_log_line as _cal_fmt
                _regime = sess_cal.get_regime_label(is_chop)
                # Cross-asset breadth from this window's committed per-coin dirs.
                # Previously xasset_features=None, so the only directional
                # *confirming* calibration factor was dead while shrink factors
                # ran — biasing every prob down (audit C3, Jun 10 2026).
                _xa_feats = None
                try:
                    _dirs = [d for c, d in self._window_trends.items()
                             if c != coin and d in ("UP", "DOWN")]
                    if _dirs:
                        _up_n = sum(1 for d in _dirs if d == "UP")
                        _dn_n = len(_dirs) - _up_n
                        _breadth = (_up_n - _dn_n) / max(1, len(_dirs))
                        _xa_feats = {
                            "breadth": _breadth,
                            "dominant_age_sec": float(window_age),
                        }
                except Exception:
                    _xa_feats = None
                _cal_res = _calibrate(
                    raw_prob=win_prob,
                    regime=_regime,
                    trend_abs=abs(trend_score),
                    bucket_stats=None,
                    microstructure_features=None,
                    reversion_risk=0.0,
                    T_sec=float(time_remaining),
                    xasset_features=_xa_feats,
                    direction=direction,
                )
                _mode = "LIVE" if _cal_live else "SHADOW"
                logger.debug(_cal_fmt(coin, direction, _cal_res, mode=_mode))
                if _cal_live:
                    win_prob = float(_cal_res["calibrated_prob"])
                    edge = win_prob - ask
            except Exception as _cal_e:
                logger.debug(f"[CALIBRATION] skip {coin}: {_cal_e}")

        _sg = sess_cal.get_session()
        min_edge = max(getattr(config, "MIN_EDGE", 0.05), _sg.min_edge)
        min_prob = max(getattr(config, "MIN_WIN_PROB", 0.65), _sg.min_prob)
        if win_prob < min_prob:
            self._diag_log(f"lowprob-{coin}", f"[LOW PROB] {coin} {direction}: prob={win_prob*100:.0f}% < {min_prob*100:.0f}% session={_sg.name}", 15.0)
            return None

        if edge < min_edge:
            self._diag_log(
                f"lowedge-{coin}-{direction}",
                f"[LOW EDGE] {coin} {direction}: prob={win_prob:.1%} ask={ask*100:.0f}c edge={edge*100:.1f}% < {min_edge*100:.0f}%",
                15.0,
            )
            return None

        # Expensive entry needs more edge (Jun-3 audit: 66-72c @ 8% edge = -EV)
        _hi_ask = float(os.getenv("HIGH_ASK_EDGE_MIN_ASK", "0.58"))
        _hi_edge = float(os.getenv("HIGH_ASK_EDGE_MIN_EDGE", "0.18"))
        if ask >= _hi_ask and edge < _hi_edge:
            self._diag_log(
                f"thin-{coin}-{direction}",
                f"[THIN EDGE] {coin} {direction}: ask={ask*100:.0f}c edge={edge*100:.1f}% < {_hi_edge*100:.0f}% needed at {_hi_ask*100:.0f}c+",
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
            f"ROC60={roc_60*10000:+.1f}bps ROC300={roc_300*10000:+.1f}bps "
            f"σ={sigma:.2e} T={time_remaining:.0f}s spot={_spot_src} strike={_strike_src}"
        )

        self._window_direction = direction  # legacy global
        self._window_directions[coin] = direction
        self._chop_detector.record_direction(direction, coin)
        # ChopDetector feeds FLIP GUARD history (per-coin — audit M1)
        regime = "CHOPPY" if self._chop_detector.is_choppy(coin) else "TRENDING"
        logger.debug(f"[COMMIT] {coin} {direction} | {regime} | history={self._chop_detector.summary(coin)} | trends={dict(self._window_trends)}")

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
            trend_score=trend_score,
            book_up_mid=book_up,
            dir_votes_up=votes_up,
            dir_votes_down=votes_down,
            dist_pct=dist_pct,
        )
