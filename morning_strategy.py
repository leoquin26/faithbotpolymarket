"""
Morning Strategy (9am-2pm Lima) - 3-phase filter
Runs alongside main predictor but with its own gates.
Isolated from the 2pm afternoon engine - does NOT affect predictor state.
"""
from typing import Optional
from datetime import datetime
from zoneinfo import ZoneInfo
from loguru import logger

LIMA = ZoneInfo("America/Lima")

# Phase boundaries (Lima time)
# Phase 1: 9:00-10:29 "Early Trend" - conservative, liquid coins only
# Phase 2: 10:30-11:59 "US Open Chop" - NO TRADING
# Phase 3: 12:00-13:59 "Midday Trend" - moderate filters, all coins
P1_ALLOWED = {"BTC", "ETH"}
P1_MIN_PROB = 0.80
P1_MIN_EDGE = 0.10
P1_MIN_TREND = 0.60

P3_ALLOWED = {"BTC", "ETH", "SOL", "XRP"}
P3_MIN_PROB = 0.78
P3_MIN_EDGE = 0.08
P3_MIN_TREND = 0.50

def get_morning_phase():
    """Return 1, 2, 3, or None (not in morning window)."""
    now = datetime.now(LIMA)
    h, m = now.hour, now.minute

    if h < 9 or h >= 14:
        return None
    if h < 10 or (h == 10 and m < 30):
        return 1
    if (h == 10 and m >= 30) or h == 11:
        return 2
    if 12 <= h < 14:
        return 3
    return None

def filter_morning_signal(pred, trend_score: float):
    """
    Apply phase-specific filters to a prediction.
    Returns the prediction if it passes, or None.
    """
    phase = get_morning_phase()
    if phase is None:
        return None

    if phase == 2:
        logger.debug(f"[MORNING P2] {pred.coin}: 10:30-12:00 Lima (US open chop) - no trading")
        return None

    coin = pred.coin
    prob = pred.probability
    edge = pred.edge
    abs_trend = abs(trend_score)

    if phase == 1:
        if coin not in P1_ALLOWED:
            logger.debug(f"[MORNING P1] {coin} not in allowed ({P1_ALLOWED})")
            return None
        if prob < P1_MIN_PROB:
            logger.debug(f"[MORNING P1] {coin} prob {prob:.0%} < {P1_MIN_PROB:.0%}")
            return None
        if edge < P1_MIN_EDGE:
            logger.debug(f"[MORNING P1] {coin} edge {edge*100:.1f}% < {P1_MIN_EDGE*100:.0f}%")
            return None
        if abs_trend < P1_MIN_TREND:
            logger.debug(f"[MORNING P1] {coin} |trend| {abs_trend:.2f} < {P1_MIN_TREND}")
            return None
        logger.info(
            f"[MORNING P1] {coin} {pred.direction} APPROVED | "
            f"Prob={prob:.0%} Edge={edge*100:.1f}% |Trend|={abs_trend:.2f}"
        )
        return pred

    if phase == 3:
        if coin not in P3_ALLOWED:
            return None
        if prob < P3_MIN_PROB:
            logger.debug(f"[MORNING P3] {coin} prob {prob:.0%} < {P3_MIN_PROB:.0%}")
            return None
        if edge < P3_MIN_EDGE:
            logger.debug(f"[MORNING P3] {coin} edge {edge*100:.1f}% < {P3_MIN_EDGE*100:.0f}%")
            return None
        if abs_trend < P3_MIN_TREND:
            logger.debug(f"[MORNING P3] {coin} |trend| {abs_trend:.2f} < {P3_MIN_TREND}")
            return None
        logger.info(
            f"[MORNING P3] {coin} {pred.direction} APPROVED | "
            f"Prob={prob:.0%} Edge={edge*100:.1f}% |Trend|={abs_trend:.2f}"
        )
        return pred

    return None
