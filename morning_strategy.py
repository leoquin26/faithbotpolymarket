"""
Morning Strategy (9am-2pm Lima) — Stricter filters layered on top of the main Predictor.

This module does NOT replace the 2pm engine. It wraps it with tighter
requirements that vary by phase:

  Phase 1  09:00-10:29  "Early Trend"   — trade only BTC/ETH, strong trend, half Kelly
  Phase 2  10:30-11:59  "US Open Chop"  — no trading (highest reversal risk)
  Phase 3  12:00-13:59  "Midday Trend"  — trade all coins, moderate filters, half Kelly

All signals still come from the main Predictor.predict() — this module
just adds an extra filter layer and controls sizing.
"""

import time
from typing import Optional, List, Tuple
from loguru import logger
from dataclasses import replace

from predictor import Prediction
from market_data import MarketInfo


# Phase boundaries in Lima hour/minute
PHASE_1_START = (9, 0)
PHASE_1_END = (10, 30)
PHASE_2_START = (10, 30)
PHASE_2_END = (12, 0)
PHASE_3_START = (12, 0)
PHASE_3_END = (14, 0)

# Phase 1: early trend — only strongest signals on the most liquid coins
P1_ALLOWED_COINS = {"BTC", "ETH"}
P1_MIN_WIN_PROB = 0.80
P1_MIN_EDGE = 0.10
P1_MIN_TREND = 0.60

# Phase 3: midday trend — all coins, moderate filters
P3_ALLOWED_COINS = {"BTC", "ETH", "SOL", "XRP"}
P3_MIN_WIN_PROB = 0.78
P3_MIN_EDGE = 0.08
P3_MIN_TREND = 0.50


def _lima_hm():
    """Return (hour, minute) in Lima time."""
    from zoneinfo import ZoneInfo
    from datetime import datetime
    now = datetime.now(ZoneInfo("America/Lima"))
    return now.hour, now.minute


def _hm_ge(hm, ref):
    return hm[0] > ref[0] or (hm[0] == ref[0] and hm[1] >= ref[1])


def _hm_lt(hm, ref):
    return hm[0] < ref[0] or (hm[0] == ref[0] and hm[1] < ref[1])


def get_morning_phase() -> Optional[int]:
    """Return 1, 2, 3, or None if outside morning window."""
    hm = _lima_hm()
    if _hm_ge(hm, PHASE_1_START) and _hm_lt(hm, PHASE_1_END):
        return 1
    if _hm_ge(hm, PHASE_2_START) and _hm_lt(hm, PHASE_2_END):
        return 2
    if _hm_ge(hm, PHASE_3_START) and _hm_lt(hm, PHASE_3_END):
        return 3
    return None


def is_morning_hour() -> bool:
    """True if we're in any morning phase (9am-2pm Lima)."""
    hm = _lima_hm()
    return _hm_ge(hm, PHASE_1_START) and _hm_lt(hm, PHASE_3_END)


def filter_morning_signal(pred: Prediction, trend_score: float) -> Optional[Prediction]:
    """
    Apply morning-specific filters to a prediction from the main engine.
    Returns the prediction (possibly adjusted) or None if filtered out.
    
    The trend_score must be passed in from the caller since it's computed
    inside predictor.predict() and not stored on the Prediction object.
    """
    phase = get_morning_phase()
    if phase is None:
        return None

    coin = pred.coin

    # Phase 2: NO trading during US market open chop
    if phase == 2:
        logger.debug(
            f"[MORNING P2] {coin}: 10:30-12:00 Lima (US open) — no trading"
        )
        return None

    # Phase 1 filters
    if phase == 1:
        if coin not in P1_ALLOWED_COINS:
            logger.debug(f"[MORNING P1] {coin}: only {P1_ALLOWED_COINS} in early session")
            return None
        if pred.probability < P1_MIN_WIN_PROB:
            logger.debug(
                f"[MORNING P1] {coin}: prob={pred.probability:.0%} < {P1_MIN_WIN_PROB:.0%}"
            )
            return None
        if pred.edge < P1_MIN_EDGE:
            logger.debug(
                f"[MORNING P1] {coin}: edge={pred.edge*100:.1f}% < {P1_MIN_EDGE*100:.0f}%"
            )
            return None
        if abs(trend_score) < P1_MIN_TREND:
            logger.debug(
                f"[MORNING P1] {coin}: |trend|={abs(trend_score):.2f} < {P1_MIN_TREND}"
            )
            return None

    # Phase 3 filters
    if phase == 3:
        if coin not in P3_ALLOWED_COINS:
            return None
        if pred.probability < P3_MIN_WIN_PROB:
            logger.debug(
                f"[MORNING P3] {coin}: prob={pred.probability:.0%} < {P3_MIN_WIN_PROB:.0%}"
            )
            return None
        if pred.edge < P3_MIN_EDGE:
            logger.debug(
                f"[MORNING P3] {coin}: edge={pred.edge*100:.1f}% < {P3_MIN_EDGE*100:.0f}%"
            )
            return None
        if abs(trend_score) < P3_MIN_TREND:
            logger.debug(
                f"[MORNING P3] {coin}: |trend|={abs(trend_score):.2f} < {P3_MIN_TREND}"
            )
            return None

    logger.info(
        f"[MORNING P{phase}] {coin} {pred.direction} APPROVED | "
        f"Prob={pred.probability:.0%} Edge={pred.edge*100:.1f}% "
        f"|Trend|={abs(trend_score):.2f}"
    )
    return pred
