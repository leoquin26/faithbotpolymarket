"""
Morning Strategy — session-calibrated filters on top of main Predictor.

Phases use America/New_York (ET) via session_calibration.py:
  P1  08:30-09:30 + 11:00-12:30  early/post-open trend
  P2  09:30-11:00                 US cash open — NO TRADING
  P3  12:30-15:00                 midday trend
"""
import os
from typing import Optional
from loguru import logger

from predictor import Prediction
import session_calibration as sess


def get_morning_phase() -> Optional[int]:
    s = sess.get_session()
    return s.phase if s.name in ("PRE_OPEN", "POST_OPEN", "MIDDAY", "US_OPEN_CHOP") else None


def is_morning_hour() -> bool:
    return sess.is_morning_session()


def filter_morning_signal(pred: Prediction, trend_score: float) -> Optional[Prediction]:
    s = sess.get_session()
    if not s.allow_trade or s.phase is None:
        if s.name == "US_OPEN_CHOP":
            logger.debug(
                f"[MORNING P2] {pred.coin}: 9:30-11:00 ET US open chop — no trading"
            )
        return None

    coin = pred.coin
    if s.allowed_coins and coin not in s.allowed_coins:
        logger.debug(f"[MORNING P{s.phase}] {coin}: only {s.allowed_coins}")
        return None

    if pred.probability < s.min_prob:
        logger.debug(
            f"[MORNING P{s.phase}] {coin}: prob={pred.probability:.0%} < {s.min_prob:.0%}"
        )
        return None
    if pred.edge < s.min_edge:
        logger.debug(
            f"[MORNING P{s.phase}] {coin}: edge={pred.edge*100:.1f}% < {s.min_edge*100:.0f}%"
        )
        return None
    if abs(trend_score) < s.min_trend:
        logger.debug(
            f"[MORNING P{s.phase}] {coin}: |trend|={abs(trend_score):.2f} < {s.min_trend}"
        )
        return None

    logger.info(
        f"[MORNING P{s.phase}] {coin} {pred.direction} APPROVED | "
        f"session={s.name} Prob={pred.probability:.0%} Edge={pred.edge*100:.1f}% "
        f"|Trend|={abs(trend_score):.2f}"
    )
    return pred
