"""
V4 Predictor — Late-Window Monte Carlo Sniper.

Replaces the GBM d2 / Bayesian engine with a fundamentally different approach:
- Only evaluates in the final 3 minutes of each 15-min window (720s-840s)
- Uses Monte Carlo simulation (1000 GBM paths) with realized volatility
- Requires price to be 0.3%+ away from threshold (meaningful gap)
- Requires CLOB depth imbalance confirmation (smart money agrees)
- Requires 8%+ edge (MC probability vs CLOB ask price)

The edge: at T-180s, the crypto price is very close to its final value.
A large reversal in 2-3 minutes is statistically rare. When the price is
far from threshold AND the orderbook confirms direction, we have real edge.
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
FALLBACK_VOL = {"BTC": 0.50, "ETH": 0.65, "SOL": 0.85, "XRP": 0.90}


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
    stale_price: bool = False
    stale_gap: float = 0.0
    conviction_strength: Optional[str] = None
    force_fok: bool = False
    entry_price: float = 0.0
    directional_edge: float = 0.0
    mc_prob: float = 0.0
    depth_ratio: float = 0.0


class Predictor:
    def __init__(self):
        self._diag_last: Dict[str, float] = {}
        self._rng = random.Random(42)

    def _diag(self, key: str, msg: str, interval: float = 30.0):
        now = time.time()
        if now - self._diag_last.get(key, 0) >= interval:
            logger.debug(msg)
            self._diag_last[key] = now

    def _monte_carlo(self, current: float, threshold: float,
                     vol_per_sec: float, time_left_sec: float,
                     n_paths: int = 1000) -> float:
        """Simulate n_paths GBM price paths. Return fraction ending above threshold."""
        if current <= 0 or threshold <= 0 or time_left_sec <= 0:
            return 0.5

        dt = time_left_sec
        drift = -0.5 * vol_per_sec * vol_per_sec * dt
        diffusion = vol_per_sec * math.sqrt(dt)

        above = 0
        rng = self._rng
        for _ in range(n_paths):
            z = rng.gauss(0, 1)
            final_price = current * math.exp(drift + diffusion * z)
            if final_price >= threshold:
                above += 1

        return above / n_paths

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

        if bp <= 0 or tp <= 0:
            return None

        now_ts = int(time.time())
        window_age = max(0, now_ts - info.window_start) if info.window_start else 0
        time_left_sec = max(0, (info.window_start + 900) - now_ts)

        # ── GATE 1: late window only (720s - 840s) ──
        if window_age < config.LATE_WINDOW_START:
            if window_age > 0 and window_age % 60 < config.SCAN_INTERVAL + 1:
                wait = config.LATE_WINDOW_START - window_age
                self._diag(f"wait-{coin}", f"[WAIT] {coin}: {window_age}s / {config.LATE_WINDOW_START}s — sniping in {wait}s", 60.0)
            return None

        if window_age > config.LATE_WINDOW_END:
            self._diag(f"late-{coin}", f"[TOO LATE] {coin}: {window_age}s > {config.LATE_WINDOW_END}s — window closing", 30.0)
            return None

        # ── GATE 2: distance from threshold ──
        distance_pct = (bp - tp) / tp
        abs_distance = abs(distance_pct)

        if abs_distance < config.MIN_DISTANCE_PCT:
            self._diag(
                f"dist-{coin}",
                f"[CLOSE] {coin}: dist={distance_pct*100:+.3f}% < {config.MIN_DISTANCE_PCT*100:.1f}% — coinflip zone",
                15.0,
            )
            return None

        is_up = bp > tp
        direction = "UP" if is_up else "DOWN"

        # ── GATE 3: tick stability — check last 60s of ticks agree on direction ──
        if ticks and len(ticks) >= 10:
            recent_60 = [(t, p) for t, p in ticks if t > time.time() - 60]
            if len(recent_60) >= 5:
                if is_up:
                    agree = sum(1 for _, p in recent_60 if p > tp)
                else:
                    agree = sum(1 for _, p in recent_60 if p < tp)
                stability = agree / len(recent_60)
                if stability < 0.75:
                    self._diag(
                        f"stab-{coin}",
                        f"[UNSTABLE] {coin} {direction}: {stability:.0%} of ticks agree < 75% in last 60s",
                        15.0,
                    )
                    return None

        # ── GATE 4: CLOB depth imbalance — smart money must agree ──
        if depth_ratio > 0 and depth_ratio < config.DEPTH_IMBALANCE_MIN:
            self._diag(
                f"depth-{coin}",
                f"[THIN] {coin} {direction}: depth ratio={depth_ratio:.2f}x < {config.DEPTH_IMBALANCE_MIN:.1f}x — no consensus",
                15.0,
            )
            return None

        # ── GATE 5: CLOB mid-price must agree with direction ──
        if clob_mid > 0.01:
            if is_up and clob_mid < 0.45:
                logger.info(f"[CLOB DISAGREE] {coin} UP: CLOB mid={clob_mid:.2f} — market says DOWN, skip")
                return None
            if not is_up and clob_mid > 0.55:
                logger.info(f"[CLOB DISAGREE] {coin} DOWN: CLOB mid={clob_mid:.2f} — market says UP, skip")
                return None

        # ── MONTE CARLO SIMULATION ──
        if realized_vol > 0:
            vol_sec = realized_vol
        else:
            ann = FALLBACK_VOL.get(coin, 0.70)
            vol_sec = ann / math.sqrt(SECS_PER_YEAR)

        p_up = self._monte_carlo(bp, tp, vol_sec, time_left_sec, config.MC_PATHS)
        mc_prob = p_up if is_up else (1.0 - p_up)

        # ── GATE 6: Monte Carlo confidence threshold ──
        if mc_prob < config.MC_WIN_THRESHOLD:
            self._diag(
                f"mc-{coin}",
                f"[MC LOW] {coin} {direction}: mc={mc_prob:.0%} < {config.MC_WIN_THRESHOLD:.0%} | "
                f"dist={distance_pct*100:+.2f}% vol={vol_sec:.6f} t_left={time_left_sec:.0f}s",
                15.0,
            )
            return None

        # ── ENTRY PRICE: use CLOB ask if available, fall back to mid ──
        entry_price = clob_ask if clob_ask > 0.01 else clob_mid
        if entry_price <= 0.01:
            entry_price = mc_prob

        # ── GATE 7: entry price zone ──
        if entry_price < config.ENTRY_MIN:
            logger.info(f"[CHEAP] {coin} {direction}: ask={entry_price*100:.0f}c < {config.ENTRY_MIN*100:.0f}c — coinflip zone")
            return None
        if entry_price > config.ENTRY_MAX:
            logger.info(f"[EXPENSIVE] {coin} {direction}: ask={entry_price*100:.0f}c > {config.ENTRY_MAX*100:.0f}c — payout too low")
            return None

        # ── GATE 8: edge (MC probability - entry price) ──
        edge = mc_prob - entry_price
        if edge < config.MIN_EDGE:
            self._diag(
                f"edge-{coin}",
                f"[LOW EDGE] {coin} {direction}: mc={mc_prob:.0%} ask={entry_price*100:.0f}c edge={edge*100:.1f}% < {config.MIN_EDGE*100:.0f}%",
                15.0,
            )
            return None

        # ── PASSED ALL GATES — build prediction ──
        token_id = info.up_token_id if is_up else info.down_token_id

        confidence = "HIGH" if mc_prob >= 0.90 and edge >= 0.12 else "MEDIUM"

        reasons = []
        reasons.append(f"MC={mc_prob:.0%}")
        reasons.append(f"dist={distance_pct*100:+.2f}%")
        reasons.append(f"ask={entry_price*100:.0f}c")
        reasons.append(f"edge={edge*100:.1f}%")
        reasons.append(f"depth={depth_ratio:.1f}x")
        reasons.append(f"t_left={time_left_sec:.0f}s")
        reasons.append(f"vol={vol_sec:.6f}/s")
        reasoning = " | ".join(reasons)

        logger.info(
            f"[SNIPE] {coin} {direction} | MC={mc_prob:.0%} | Ask={entry_price*100:.0f}c | "
            f"Edge={edge*100:.1f}% | Dist={distance_pct*100:+.2f}% | Depth={depth_ratio:.1f}x | "
            f"T-{time_left_sec:.0f}s | {confidence}"
        )

        return Prediction(
            coin=coin,
            direction=direction,
            probability=mc_prob,
            poly_price=entry_price,
            edge=edge,
            confidence=confidence,
            reasoning=reasoning,
            token_id=token_id,
            market_info=info,
            entry_price=entry_price,
            directional_edge=mc_prob - 0.50,
            mc_prob=mc_prob,
            depth_ratio=depth_ratio,
        )
