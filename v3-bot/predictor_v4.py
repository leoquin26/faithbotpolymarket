"""
V4 Predictor — Late-Window Monte Carlo Sniper.

Replaces the GBM/Bayesian engine with a fundamentally different approach:
- Only evaluates in the final 3 minutes (720-840s into 900s window)
- Uses Monte Carlo simulation with realized volatility
- Requires CLOB depth imbalance confirmation
- Requires meaningful distance from threshold (0.3%+)
- Minimum 8% edge over CLOB ask price

The edge: with 2-3 minutes left, if BTC is 0.5% above threshold,
a reversal large enough to flip the outcome is statistically rare.
Monte Carlo quantifies this. We only trade when the math says 80%+.
"""

import math
import time
import random
from dataclasses import dataclass
from typing import Optional, Dict, List, Tuple
from loguru import logger

import config
from market_data import MarketInfo


SECS_PER_YEAR = 365.25 * 24 * 3600
FALLBACK_VOL = {"BTC": 0.55, "ETH": 0.65, "SOL": 0.85, "XRP": 0.90}


@dataclass
class Prediction:
    coin: str
    direction: str
    probability: float       # MC probability (used by Kelly in order_manager)
    poly_price: float        # CLOB ask price for our side
    edge: float              # mc_prob - clob_ask
    confidence: str          # HIGH / MEDIUM
    reasoning: str
    token_id: str
    market_info: MarketInfo
    entry_price: float = 0.0
    mc_prob: float = 0.0     # Monte Carlo win probability
    depth_ratio: float = 0.0
    directional_edge: float = 0.0
    stale_price: bool = False
    stale_gap: float = 0.0
    conviction_strength: Optional[str] = None
    force_fok: bool = False


class Predictor:

    def __init__(self):
        self._diag_last: Dict[str, float] = {}
        self._rng = random.Random(42)

    def _diag_log(self, key: str, msg: str, interval: float = 30.0):
        now = time.time()
        if now - self._diag_last.get(key, 0) >= interval:
            logger.debug(msg)
            self._diag_last[key] = now

    def _monte_carlo(self, current_price: float, threshold: float,
                     vol_per_sec: float, time_left_sec: float,
                     n_paths: int = 1000) -> float:
        """
        Simulate n_paths GBM price paths from current_price over time_left_sec.
        Returns fraction of paths that end above threshold.

        Each path: S_T = S_0 * exp((-0.5*sigma^2)*T + sigma*sqrt(T)*Z)
        where Z ~ N(0,1), sigma = annualized vol, T = time in years.
        """
        if current_price <= 0 or threshold <= 0 or time_left_sec <= 0:
            return 0.5

        if vol_per_sec > 0:
            ann_vol = vol_per_sec * math.sqrt(SECS_PER_YEAR)
            ann_vol = max(0.10, min(ann_vol, 5.0))
        else:
            coin_guess = "BTC"
            if threshold < 10:
                coin_guess = "XRP"
            elif threshold < 500:
                coin_guess = "SOL"
            elif threshold < 5000:
                coin_guess = "ETH"
            ann_vol = FALLBACK_VOL.get(coin_guess, 0.70)

        t_years = time_left_sec / SECS_PER_YEAR
        drift = -0.5 * ann_vol * ann_vol * t_years
        diffusion = ann_vol * math.sqrt(t_years)

        above_count = 0
        for _ in range(n_paths):
            z = self._rng.gauss(0, 1)
            log_return = drift + diffusion * z
            final_price = current_price * math.exp(log_return)
            if final_price >= threshold:
                above_count += 1

        return above_count / n_paths

    def _check_tick_stability(self, ticks: List[Tuple[float, float]],
                              threshold: float, is_up: bool,
                              lookback_sec: float = 60) -> float:
        """What % of recent ticks are on our predicted side?"""
        if not ticks or len(ticks) < 5:
            return 0.0
        cutoff = time.time() - lookback_sec
        recent = [p for t, p in ticks if t > cutoff]
        if len(recent) < 5:
            return 0.0
        if is_up:
            return sum(1 for p in recent if p >= threshold) / len(recent)
        else:
            return sum(1 for p in recent if p < threshold) / len(recent)

    def predict(self, info: MarketInfo, *,
                ws_price: float = 0.0,
                realized_vol: float = 0.0,
                clob_ask: float = 0.0,
                clob_mid: float = 0.0,
                depth_ratio: float = 0.0,
                ticks: Optional[List[Tuple[float, float]]] = None,
                ) -> Optional[Prediction]:
        coin = info.coin
        bp = ws_price if ws_price > 0 else info.current_crypto_price
        tp = info.threshold_price
        time_left_sec = info.time_remaining * 60.0

        if bp <= 0 or tp <= 0:
            return None

        now_ts = int(time.time())
        window_age = max(0, now_ts - info.window_start) if info.window_start else 0

        # ── GATE 1: Late window only (720s-840s) ──
        if window_age < config.LATE_WINDOW_START:
            if window_age % 60 < config.SCAN_INTERVAL + 1:
                self._diag_log(
                    f"wait-{coin}",
                    f"[WAIT] {coin}: {window_age}s / {config.LATE_WINDOW_START}s "
                    f"({config.LATE_WINDOW_START - window_age}s until snipe window)",
                    60.0,
                )
            return None

        if window_age > config.LATE_WINDOW_END:
            self._diag_log(
                f"late-{coin}",
                f"[TOO LATE] {coin}: {window_age}s > {config.LATE_WINDOW_END}s — window closing",
                30.0,
            )
            return None

        # ── GATE 2: Distance from threshold ──
        distance_pct = (bp - tp) / tp
        abs_distance = abs(distance_pct)
        is_up = bp >= tp
        direction = "UP" if is_up else "DOWN"

        if abs_distance < config.MIN_DISTANCE_PCT:
            self._diag_log(
                f"close-{coin}",
                f"[TOO CLOSE] {coin}: {abs_distance*100:.2f}% < {config.MIN_DISTANCE_PCT*100:.1f}% "
                f"from threshold — coinflip territory",
                15.0,
            )
            return None

        # ── GATE 3: Tick stability (70%+ on our side in last 60s) ──
        if ticks:
            stability = self._check_tick_stability(ticks, tp, is_up, 60)
            if stability < 0.70:
                self._diag_log(
                    f"unstable-{coin}",
                    f"[UNSTABLE] {coin} {direction}: {stability:.0%} ticks on side "
                    f"< 70% — direction not stable",
                    15.0,
                )
                return None

        # ── GATE 4: CLOB depth imbalance ──
        if depth_ratio < config.DEPTH_IMBALANCE_MIN:
            self._diag_log(
                f"depth-{coin}",
                f"[THIN DEPTH] {coin} {direction}: bid/ask={depth_ratio:.1f}x "
                f"< {config.DEPTH_IMBALANCE_MIN:.1f}x — smart money not confirming",
                15.0,
            )
            return None

        # ── GATE 5: CLOB mid-price consensus ──
        if clob_mid > 0.01:
            clob_up_pct = clob_mid
            if is_up and clob_up_pct < 0.55:
                logger.info(
                    f"[CLOB DISAGREE] {coin} UP: market={clob_up_pct:.0%} UP — "
                    f"not enough consensus, skip"
                )
                return None
            if not is_up and clob_up_pct > 0.45:
                logger.info(
                    f"[CLOB DISAGREE] {coin} DOWN: market={clob_up_pct:.0%} UP — "
                    f"not enough consensus, skip"
                )
                return None

        # ── MONTE CARLO SIMULATION ──
        time_left_actual = max(1.0, (info.window_start + 900) - now_ts)
        mc_prob = self._monte_carlo(
            current_price=bp,
            threshold=tp,
            vol_per_sec=realized_vol,
            time_left_sec=time_left_actual,
            n_paths=config.MC_PATHS,
        )

        win_prob = mc_prob if is_up else (1.0 - mc_prob)

        if win_prob < config.MC_WIN_THRESHOLD:
            self._diag_log(
                f"mc-{coin}",
                f"[MC LOW] {coin} {direction}: MC={win_prob:.0%} < {config.MC_WIN_THRESHOLD:.0%} "
                f"({config.MC_PATHS} paths, {time_left_actual:.0f}s left, "
                f"dist={abs_distance*100:.2f}%)",
                15.0,
            )
            return None

        # ── GATE 6: Entry price range ──
        entry_price = clob_ask if clob_ask > 0.01 else (clob_mid if clob_mid > 0.01 else 0.0)
        if not is_up and clob_mid > 0.01:
            entry_price = clob_ask if clob_ask > 0.01 else (1.0 - clob_mid)

        if entry_price < config.ENTRY_MIN:
            logger.info(
                f"[CHEAP] {coin} {direction}: ask={entry_price*100:.0f}c "
                f"< {config.ENTRY_MIN*100:.0f}c — too cheap, suspicious"
            )
            return None

        if entry_price > config.ENTRY_MAX:
            logger.info(
                f"[EXPENSIVE] {coin} {direction}: ask={entry_price*100:.0f}c "
                f"> {config.ENTRY_MAX*100:.0f}c — payout too low"
            )
            return None

        # ── GATE 7: Edge vs CLOB ask ──
        edge = win_prob - entry_price
        if edge < config.MIN_EDGE:
            self._diag_log(
                f"edge-{coin}",
                f"[LOW EDGE] {coin} {direction}: MC={win_prob:.0%} - ask={entry_price*100:.0f}c "
                f"= {edge*100:.1f}% < {config.MIN_EDGE*100:.0f}% needed",
                15.0,
            )
            return None

        # ── ALL GATES PASSED — BUILD PREDICTION ──
        token_id = info.up_token_id if is_up else info.down_token_id

        if edge >= 0.15 and win_prob >= 0.90 and depth_ratio >= 3.0:
            confidence = "HIGH"
        else:
            confidence = "MEDIUM"

        reasoning = (
            f"dist={abs_distance*100:.2f}% | MC={win_prob:.0%} ({config.MC_PATHS}p, "
            f"{time_left_actual:.0f}s) | ask={entry_price*100:.0f}c | "
            f"edge={edge*100:.1f}% | depth={depth_ratio:.1f}x | "
            f"stab={'OK' if ticks else 'N/A'} | vol={'WS' if realized_vol > 0 else 'FB'}"
        )

        logger.info(
            f"[SNIPE] {coin} {direction} | MC={win_prob:.0%} | "
            f"Ask={entry_price*100:.0f}c | Edge={edge*100:.1f}% | "
            f"Dist={abs_distance*100:.2f}% | Depth={depth_ratio:.1f}x | "
            f"{time_left_actual:.0f}s left | {confidence}"
        )

        return Prediction(
            coin=coin,
            direction=direction,
            probability=win_prob,
            poly_price=entry_price,
            edge=edge,
            confidence=confidence,
            reasoning=reasoning,
            token_id=token_id,
            market_info=info,
            entry_price=entry_price,
            mc_prob=win_prob,
            depth_ratio=depth_ratio,
            directional_edge=win_prob - 0.50,
        )
