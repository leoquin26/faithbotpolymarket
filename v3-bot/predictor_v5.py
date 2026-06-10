"""
V5 Predictor — Confirmed Direction + Monte Carlo.

Trades anytime in the window (after 90s warmup) when direction is
confirmed by 3 independent signals:
  1. Binance price on one side (distance > 0.1%)
  2. CLOB market agrees (mid > 55% on our side)
  3. Tick stability (70%+ ticks on our side for 30s)

Monte Carlo validates the probability, Kelly sizes the bet.
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
        self._rng = random.Random(42)

    def _diag_log(self, key: str, msg: str, interval: float = 30.0):
        now = time.time()
        if now - self._diag_last.get(key, 0) >= interval:
            logger.debug(msg)
            self._diag_last[key] = now

    def _monte_carlo(self, current_price: float, threshold: float,
                     vol_per_sec: float, time_left_sec: float,
                     n_paths: int = 1000) -> float:
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
            final_price = current_price * math.exp(drift + diffusion * z)
            if final_price >= threshold:
                above_count += 1

        return above_count / n_paths

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
        now_ts = int(time.time())
        window_age = max(0, now_ts - info.window_start) if info.window_start else 0
        time_left_actual = max(1.0, (info.window_start + 900) - now_ts)

        if bp <= 0 or tp <= 0:
            return None

        # ── GATE 1: Warmup — need 90s of data ──
        if window_age < 90:
            self._diag_log(
                f"warmup-{coin}",
                f"[WARMUP] {coin}: {window_age}s < 90s",
                30.0,
            )
            return None

        # ── GATE 2: Not too late — need 1 min for execution ──
        if window_age > 840:
            return None

        # ── SIGNAL 1: Distance from threshold ──
        distance_pct = (bp - tp) / tp
        abs_distance = abs(distance_pct)
        is_up = bp >= tp
        direction = "UP" if is_up else "DOWN"

        if abs_distance < config.MIN_DISTANCE_PCT:
            self._diag_log(
                f"close-{coin}",
                f"[TOO CLOSE] {coin}: {abs_distance*100:.2f}% < "
                f"{config.MIN_DISTANCE_PCT*100:.1f}% — coinflip",
                15.0,
            )
            return None

        # ── SIGNAL 2: Tick stability (70%+ on our side last 30s) ──
        stability = 0.0
        if ticks:
            stability = self._tick_stability(ticks, tp, is_up, 30)
            if stability < 0.70:
                self._diag_log(
                    f"unstable-{coin}",
                    f"[UNSTABLE] {coin} {direction}: {stability:.0%} < 70%",
                    15.0,
                )
                return None

        # ── SIGNAL 3: CLOB mid-price agrees ──
        if clob_mid > 0.01:
            our_side_pct = clob_mid if is_up else (1.0 - clob_mid)
            if our_side_pct < 0.55:
                self._diag_log(
                    f"clob-{coin}",
                    f"[CLOB DISAGREE] {coin} {direction}: market={our_side_pct:.0%} "
                    f"on our side < 55%",
                    15.0,
                )
                return None

        # ── MONTE CARLO: quantify probability ──
        mc_prob = self._monte_carlo(
            current_price=bp,
            threshold=tp,
            vol_per_sec=realized_vol,
            time_left_sec=time_left_actual,
            n_paths=config.MC_PATHS,
        )
        win_prob = mc_prob if is_up else (1.0 - mc_prob)

        # MC threshold: scales with window age (stricter early, looser late)
        # At 90s: need 75%. At 720s+: need 65%.
        mc_threshold = max(0.65, 0.80 - (window_age / 900) * 0.20)
        if win_prob < mc_threshold:
            self._diag_log(
                f"mc-{coin}",
                f"[MC LOW] {coin} {direction}: MC={win_prob:.0%} < "
                f"{mc_threshold:.0%} ({time_left_actual:.0f}s left, "
                f"dist={abs_distance*100:.2f}%)",
                15.0,
            )
            return None

        # ── Entry price from CLOB ──
        entry_price = clob_ask if clob_ask > 0.01 else 0.0
        if entry_price <= 0.01 and clob_mid > 0.01:
            entry_price = clob_mid if is_up else (1.0 - clob_mid)

        if entry_price < config.ENTRY_MIN:
            logger.info(
                f"[CHEAP] {coin} {direction}: ask={entry_price*100:.0f}c "
                f"< {config.ENTRY_MIN*100:.0f}c"
            )
            return None

        if entry_price > config.ENTRY_MAX:
            logger.info(
                f"[EXPENSIVE] {coin} {direction}: ask={entry_price*100:.0f}c "
                f"> {config.ENTRY_MAX*100:.0f}c"
            )
            return None

        # ── Edge: MC prob minus entry price ──
        edge = win_prob - entry_price
        min_edge = config.MIN_EDGE
        if edge < min_edge:
            self._diag_log(
                f"edge-{coin}",
                f"[LOW EDGE] {coin} {direction}: MC={win_prob:.0%} - "
                f"ask={entry_price*100:.0f}c = {edge*100:.1f}% < "
                f"{min_edge*100:.0f}%",
                15.0,
            )
            return None

        # ── ALL CONFIRMED — trade ──
        token_id = info.up_token_id if is_up else info.down_token_id

        if edge >= 0.15 and win_prob >= 0.85 and depth_ratio >= 1.5:
            confidence = "HIGH"
        else:
            confidence = "MEDIUM"

        reasoning = (
            f"dist={abs_distance*100:.2f}% | MC={win_prob:.0%} "
            f"({time_left_actual:.0f}s left) | ask={entry_price*100:.0f}c | "
            f"edge={edge*100:.1f}% | stab={stability:.0%} | "
            f"depth={depth_ratio:.1f}x | age={window_age}s"
        )

        logger.info(
            f"[TRADE] {coin} {direction} | MC={win_prob:.0%} | "
            f"Ask={entry_price*100:.0f}c | Edge={edge*100:.1f}% | "
            f"Dist={abs_distance*100:.2f}% | Stab={stability:.0%} | "
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
