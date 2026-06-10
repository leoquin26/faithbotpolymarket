"""
V8 Predictor — Empirical Trend + Momentum Model.

Core changes from V7:
- Predictor is STATELESS (no trade tracking — run_bot owns dedup)
- Replaces risk-neutral GBM Monte Carlo with empirical trend model
- Requires price moving AWAY from threshold (momentum gate)
- Blocks recent threshold crossings (reversal risk)
- Fixes CLOB mid-price gate for DOWN tokens
- Returns (direction, win_probability) — edge computed by caller with fresh ask
"""

import math
import time
import random
from dataclasses import dataclass, field
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


class Predictor:

    def __init__(self):
        self._diag_last: Dict[str, float] = {}

    def _diag_log(self, key: str, msg: str, interval: float = 30.0):
        now = time.time()
        if now - self._diag_last.get(key, 0) >= interval:
            logger.debug(msg)
            self._diag_last[key] = now

    # ------------------------------------------------------------------
    # Trend analysis from tick history
    # ------------------------------------------------------------------
    @staticmethod
    def _linear_slope(ticks: List[Tuple[float, float]]) -> float:
        """Least-squares slope of price vs time. Returns pct-change-per-second."""
        n = len(ticks)
        if n < 5:
            return 0.0
        t0 = ticks[0][0]
        p0 = ticks[0][1]
        if p0 <= 0:
            return 0.0
        sum_x = sum_y = sum_xy = sum_xx = 0.0
        for t, p in ticks:
            x = t - t0
            y = (p - p0) / p0
            sum_x += x
            sum_y += y
            sum_xy += x * y
            sum_xx += x * x
        denom = n * sum_xx - sum_x * sum_x
        if abs(denom) < 1e-12:
            return 0.0
        return (n * sum_xy - sum_x * sum_y) / denom

    @staticmethod
    def _trend_consistency(ticks: List[Tuple[float, float]], is_up: bool) -> float:
        """Fraction of consecutive tick-pairs moving in the expected direction."""
        if len(ticks) < 3:
            return 0.0
        correct = 0
        total = 0
        for i in range(1, len(ticks)):
            diff = ticks[i][1] - ticks[i - 1][1]
            if abs(diff) < 1e-10:
                continue
            total += 1
            if is_up and diff > 0:
                correct += 1
            elif not is_up and diff < 0:
                correct += 1
        return correct / total if total > 0 else 0.0

    @staticmethod
    def _last_cross_age(ticks: List[Tuple[float, float]], threshold: float) -> float:
        """Seconds since price last crossed the threshold. Large = safer."""
        if len(ticks) < 2:
            return 0.0
        now = ticks[-1][0]
        for i in range(len(ticks) - 1, 0, -1):
            p_cur = ticks[i][1]
            p_prev = ticks[i - 1][1]
            if (p_cur >= threshold) != (p_prev >= threshold):
                return now - ticks[i][0]
        return now - ticks[0][0]

    def _tick_stability(self, ticks: List[Tuple[float, float]],
                        threshold: float, is_up: bool,
                        lookback_sec: float = 30) -> float:
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

    # ------------------------------------------------------------------
    # Empirical win probability (replaces GBM Monte Carlo)
    # ------------------------------------------------------------------
    def _empirical_win_prob(self, ticks: List[Tuple[float, float]],
                            current_price: float, threshold: float,
                            is_up: bool, time_left: float,
                            realized_vol: float) -> Tuple[float, dict]:
        """
        Compute win probability from observable market signals:
        1. Distance from threshold (further = more likely to hold)
        2. Short-term trend slope (last 60s)
        3. Medium-term trend slope (last 180s)
        4. Trend consistency (are ticks mostly moving our way?)
        5. Time since last threshold cross (longer = more stable)
        """
        signals = {}

        rel_distance = abs(current_price - threshold) / threshold
        signals["distance_pct"] = rel_distance * 100

        now = time.time()
        short_ticks = [(t, p) for t, p in ticks if t > now - 60]
        short_slope = self._linear_slope(short_ticks) if len(short_ticks) >= 5 else 0.0
        signals["short_slope"] = short_slope

        med_ticks = [(t, p) for t, p in ticks if t > now - 180]
        med_slope = self._linear_slope(med_ticks) if len(med_ticks) >= 5 else 0.0
        signals["med_slope"] = med_slope

        consistency = self._trend_consistency(short_ticks, is_up) if len(short_ticks) >= 5 else 0.0
        signals["consistency"] = consistency

        cross_age = self._last_cross_age(ticks, threshold)
        signals["cross_age"] = cross_age

        stability = self._tick_stability(ticks, threshold, is_up, 30)
        signals["stability"] = stability

        # Base: distance-driven. Further from threshold = higher base prob.
        # At 0.1% distance, base ~ 53%. At 0.5%, base ~ 65%. At 1%, base ~ 80%.
        dist_score = min(0.85, 0.50 + rel_distance * 30)

        # Slope bonus/penalty: is price moving in our direction?
        slope_for_direction = short_slope if is_up else -short_slope
        med_slope_for_direction = med_slope if is_up else -med_slope

        slope_bonus = 0.0
        if slope_for_direction > 0.000005:
            slope_bonus = min(0.10, slope_for_direction * 5000)
        elif slope_for_direction < -0.000002:
            slope_bonus = max(-0.15, slope_for_direction * 8000)
        signals["slope_bonus"] = slope_bonus

        med_bonus = 0.0
        if med_slope_for_direction > 0.000003:
            med_bonus = min(0.05, med_slope_for_direction * 3000)
        elif med_slope_for_direction < -0.000002:
            med_bonus = max(-0.10, med_slope_for_direction * 5000)
        signals["med_bonus"] = med_bonus

        consistency_bonus = 0.0
        if consistency > 0.55:
            consistency_bonus = (consistency - 0.50) * 0.20
        elif consistency < 0.40:
            consistency_bonus = (consistency - 0.50) * 0.30
        signals["consistency_bonus"] = consistency_bonus

        cross_bonus = 0.0
        if cross_age < 30:
            cross_bonus = -0.10
        elif cross_age < 60:
            cross_bonus = -0.05
        elif cross_age > 180:
            cross_bonus = 0.05
        signals["cross_bonus"] = cross_bonus

        stab_bonus = 0.0
        if stability > 0.80:
            stab_bonus = 0.05
        elif stability < 0.60:
            stab_bonus = -0.10
        signals["stab_bonus"] = stab_bonus

        win_prob = dist_score + slope_bonus + med_bonus + consistency_bonus + cross_bonus + stab_bonus
        win_prob = max(0.30, min(0.95, win_prob))
        signals["raw_prob"] = win_prob

        return win_prob, signals

    # ------------------------------------------------------------------
    # Evaluate one side (UP or DOWN)
    # ------------------------------------------------------------------
    def _evaluate_side(self, coin: str, direction: str, is_up: bool,
                       info: MarketInfo, bp: float, tp: float,
                       window_age: int, time_left: float,
                       realized_vol: float,
                       ask_price: float, mid_price: float,
                       depth: float,
                       ticks: List[Tuple[float, float]]) -> Tuple[float, Optional[Prediction]]:
        """Evaluate one side. Returns (win_prob, prediction) or (0, None)."""

        if ask_price < config.ENTRY_MIN:
            self._diag_log(
                f"cheap-{coin}-{direction}",
                f"[CHEAP] {coin} {direction}: ask={ask_price*100:.0f}c < {config.ENTRY_MIN*100:.0f}c",
                30.0,
            )
            return 0, None

        if ask_price > config.ENTRY_MAX:
            self._diag_log(
                f"exp-{coin}-{direction}",
                f"[EXPENSIVE] {coin} {direction}: ask={ask_price*100:.0f}c > {config.ENTRY_MAX*100:.0f}c",
                30.0,
            )
            return 0, None

        if ticks and len(ticks) >= 5:
            stability = self._tick_stability(ticks, tp, is_up, 30)
            if stability < 0.65:
                self._diag_log(
                    f"unstable-{coin}-{direction}",
                    f"[UNSTABLE] {coin} {direction}: stab={stability:.0%} < 65%",
                    30.0,
                )
                return 0, None
        else:
            self._diag_log(f"noticks-{coin}", f"[NO TICKS] {coin}: insufficient tick data", 30.0)
            return 0, None

        # CLOB mid-price gate (FIX 4: each side's own mid, no inversion)
        if mid_price > 0.01 and mid_price < 0.52:
            self._diag_log(
                f"clob-{coin}-{direction}",
                f"[CLOB DISAGREE] {coin} {direction}: mid={mid_price*100:.0f}c < 52c",
                30.0,
            )
            return 0, None

        # Momentum / Trend gate (FIX 3)
        now = time.time()
        short_ticks = [(t, p) for t, p in ticks if t > now - 60]
        if len(short_ticks) >= 5:
            slope = self._linear_slope(short_ticks)
            slope_for_dir = slope if is_up else -slope

            if slope_for_dir < -0.000001:
                self._diag_log(
                    f"counter-{coin}-{direction}",
                    f"[COUNTER TREND] {coin} {direction}: slope={slope:.8f} moving AGAINST us",
                    15.0,
                )
                return 0, None

            cross_age = self._last_cross_age(ticks, tp)
            min_cross_age = getattr(config, "MIN_CROSS_AGE", 45)
            if cross_age < min_cross_age:
                self._diag_log(
                    f"recentcross-{coin}-{direction}",
                    f"[RECENT CROSS] {coin} {direction}: crossed {cross_age:.0f}s ago < {min_cross_age}s",
                    15.0,
                )
                return 0, None

        # Empirical win probability (FIX 2)
        win_prob, signals = self._empirical_win_prob(
            ticks, bp, tp, is_up, time_left, realized_vol
        )

        min_prob = getattr(config, "MIN_WIN_PROB", 0.68)
        if win_prob < min_prob:
            self._diag_log(
                f"lowprob-{coin}-{direction}",
                f"[LOW PROB] {coin} {direction}: prob={win_prob:.0%} < {min_prob:.0%} "
                f"(dist={signals['distance_pct']:.2f}% slope={signals['short_slope']:.7f} "
                f"cons={signals['consistency']:.0%} cross={signals['cross_age']:.0f}s)",
                15.0,
            )
            return 0, None

        # Build prediction (FIX 5: edge is preliminary, caller recomputes)
        preliminary_edge = win_prob - ask_price
        token_id = info.up_token_id if is_up else info.down_token_id
        confidence = "HIGH" if win_prob >= 0.78 and preliminary_edge >= 0.12 else "MEDIUM"

        reasoning = (
            f"dist={signals['distance_pct']:.2f}% | prob={win_prob:.0%} "
            f"({time_left:.0f}s left) | ask={ask_price*100:.0f}c | "
            f"slope={signals['short_slope']:.7f} | cons={signals['consistency']:.0%} | "
            f"cross={signals['cross_age']:.0f}s | stab={signals['stability']:.0%} | "
            f"depth={depth:.1f}x | age={window_age}s"
        )

        pred = Prediction(
            coin=coin,
            direction=direction,
            probability=win_prob,
            poly_price=ask_price,
            edge=preliminary_edge,
            confidence=confidence,
            reasoning=reasoning,
            token_id=token_id,
            market_info=info,
            entry_price=ask_price,
            mc_prob=win_prob,
            depth_ratio=depth,
            directional_edge=win_prob - 0.50,
        )
        return win_prob, pred

    # ------------------------------------------------------------------
    # Main predict entry point (STATELESS — no trade tracking)
    # ------------------------------------------------------------------
    def predict(self, info: MarketInfo, *,
                ws_price: float = 0.0,
                realized_vol: float = 0.0,
                up_ask: float = 0.0,
                down_ask: float = 0.0,
                up_mid: float = 0.0,
                down_mid: float = 0.0,
                up_depth: float = 0.0,
                down_depth: float = 0.0,
                ticks: Optional[List[Tuple[float, float]]] = None,
                clob_ask: float = 0.0,
                clob_mid: float = 0.0,
                depth_ratio: float = 0.0,
                ) -> Optional[Prediction]:
        coin = info.coin
        bp = ws_price if ws_price > 0 else info.current_crypto_price
        tp = info.threshold_price
        now_ts = int(time.time())
        window_start = info.window_start or 0
        window_age = max(0, now_ts - window_start) if window_start else 0
        time_left = max(1.0, (window_start + 900) - now_ts)

        if bp <= 0 or tp <= 0:
            return None

        warmup = getattr(config, "WARMUP_SEC", 90)
        if window_age < warmup:
            self._diag_log(f"warmup-{coin}", f"[WARMUP] {coin}: {window_age}s < {warmup}s", 30.0)
            return None

        max_age = getattr(config, "MAX_WINDOW_AGE", 840)
        if window_age > max_age:
            return None

        abs_distance = abs((bp - tp) / tp)
        if abs_distance < config.MIN_DISTANCE_PCT:
            self._diag_log(
                f"close-{coin}",
                f"[TOO CLOSE] {coin}: {abs_distance*100:.2f}% < {config.MIN_DISTANCE_PCT*100:.2f}%",
                15.0,
            )
            return None

        if not ticks or len(ticks) < 10:
            self._diag_log(f"noticks-{coin}", f"[NO TICKS] {coin}: need 10+ ticks", 30.0)
            return None

        best_prob = 0
        best_pred = None

        up_prob, up_pred = self._evaluate_side(
            coin, "UP", True, info, bp, tp,
            window_age, time_left, realized_vol,
            up_ask, up_mid, up_depth, ticks,
        )
        if up_prob > best_prob:
            best_prob = up_prob
            best_pred = up_pred

        # FIX 4: pass down_mid directly — no 1-mid inversion
        down_prob, down_pred = self._evaluate_side(
            coin, "DOWN", False, info, bp, tp,
            window_age, time_left, realized_vol,
            down_ask, down_mid, down_depth, ticks,
        )
        if down_prob > best_prob:
            best_prob = down_prob
            best_pred = down_pred

        if best_pred:
            logger.info(
                f"[SIGNAL] {best_pred.coin} {best_pred.direction} | "
                f"Prob={best_pred.probability:.0%} | Ask={best_pred.entry_price*100:.0f}c | "
                f"PrelimEdge={best_pred.edge*100:.1f}%"
            )

        return best_pred
