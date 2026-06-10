"""
Session calibration — ET-aligned trading phases + per-session gate thresholds.

Phases (America/New_York, weekdays):
  P1  08:30-09:30  Pre-open early trend (BTC/ETH/SOL)
  P2  09:30-11:00  US cash open chop — NO TRADING
  P1b 11:00-12:30  Post-open trend (BTC/ETH/SOL)
  P3  12:30-15:00  Midday trend (all coins)
  PM  15:00-18:00  Afternoon main engine
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except (TypeError, ValueError):
        return default


def _parse_hm(s: str, default: Tuple[int, int]) -> Tuple[int, int]:
    try:
        h, m = s.strip().split(":")
        return int(h), int(m)
    except Exception:
        return default


def _hm_now() -> Tuple[int, int]:
    return datetime.now(NY).hour, datetime.now(NY).minute


def _hm_ge(hm: Tuple[int, int], ref: Tuple[int, int]) -> bool:
    return hm[0] > ref[0] or (hm[0] == ref[0] and hm[1] >= ref[1])


def _hm_lt(hm: Tuple[int, int], ref: Tuple[int, int]) -> bool:
    return hm[0] < ref[0] or (hm[0] == ref[0] and hm[1] < ref[1])


# ET phase boundaries (env-overridable)
P_PRE_START = _parse_hm(os.getenv("SESSION_PRE_OPEN_START", "8:30"), (8, 30))
P_OPEN_START = _parse_hm(os.getenv("SESSION_US_OPEN_START", "9:30"), (9, 30))
P_OPEN_END = _parse_hm(os.getenv("SESSION_US_OPEN_END", "11:00"), (11, 0))
P_POST_END = _parse_hm(os.getenv("SESSION_POST_OPEN_END", "12:30"), (12, 30))
P_MIDDAY_END = _parse_hm(os.getenv("SESSION_MIDDAY_END", "15:00"), (15, 0))
P_DAY_END = _parse_hm(os.getenv("SESSION_DAY_END", "18:00"), (18, 0))


@dataclass
class SessionGates:
    name: str
    phase: Optional[int]
    min_trend: float
    min_dist: float
    min_prob: float
    min_edge: float
    choppy_min_trend: float
    allow_trade: bool
    allowed_coins: Optional[set]


def _allowed(env_key: str, default: str) -> set:
    raw = os.getenv(env_key, default)
    return {c.strip().upper() for c in raw.split(",") if c.strip()}


def get_session() -> SessionGates:
    """Return current session gates (ET clock)."""
    now = datetime.now(NY)
    hm = (now.hour, now.minute)
    wd = now.weekday()

    base_trend = _env_float("MIN_TREND_SCORE", _env_float("MIN_TREND_ABS", "0.30"))
    base_dist = _env_float("MIN_DIST_UP_PCT", "0.0010")
    base_prob = _env_float("MIN_WIN_PROB", "0.62")
    base_edge = _env_float("MIN_EDGE_THRESHOLD", "0.08")
    chop_trend = _env_float("CHOPPY_MIN_TREND_ABS", "0.38")

    if wd >= 5:
        return SessionGates("WEEKEND", None, base_trend, base_dist, base_prob, base_edge, chop_trend, False, None)

    if _hm_lt(hm, P_PRE_START) or _hm_ge(hm, P_DAY_END):
        return SessionGates("OFF", None, base_trend, base_dist, base_prob, base_edge, chop_trend, False, None)

    # P2 — US open chop blackout
    if _hm_ge(hm, P_OPEN_START) and _hm_lt(hm, P_OPEN_END):
        return SessionGates(
            "US_OPEN_CHOP", 2,
            base_trend, base_dist, base_prob, base_edge, chop_trend,
            False, None,
        )

    # P1 pre-open
    if _hm_ge(hm, P_PRE_START) and _hm_lt(hm, P_OPEN_START):
        return SessionGates(
            "PRE_OPEN", 1,
            _env_float("SESSION_P1_MIN_TREND", str(base_trend)),
            _env_float("SESSION_P1_MIN_DIST", str(base_dist)),
            _env_float("SESSION_P1_MIN_PROB", "0.60"),
            _env_float("SESSION_P1_MIN_EDGE", "0.07"),
            _env_float("SESSION_P1_CHOPPY_TREND", str(chop_trend)),
            True, _allowed("MORNING_P1_ALLOWED", "BTC,ETH,SOL"),
        )

    # P1 post-open
    if _hm_ge(hm, P_OPEN_END) and _hm_lt(hm, P_POST_END):
        return SessionGates(
            "POST_OPEN", 1,
            _env_float("SESSION_P1_MIN_TREND", str(base_trend)),
            _env_float("SESSION_P1_MIN_DIST", str(base_dist * 0.9)),
            _env_float("SESSION_P1_MIN_PROB", "0.60"),
            _env_float("SESSION_P1_MIN_EDGE", "0.07"),
            _env_float("SESSION_P1_CHOPPY_TREND", str(chop_trend)),
            True, _allowed("MORNING_P1_ALLOWED", "BTC,ETH,SOL"),
        )

    # P3 midday
    if _hm_ge(hm, P_POST_END) and _hm_lt(hm, P_MIDDAY_END):
        return SessionGates(
            "MIDDAY", 3,
            _env_float("SESSION_P3_MIN_TREND", str(base_trend * 0.95)),
            _env_float("SESSION_P3_MIN_DIST", str(base_dist * 0.9)),
            _env_float("SESSION_P3_MIN_PROB", "0.58"),
            _env_float("SESSION_P3_MIN_EDGE", "0.07"),
            _env_float("SESSION_P3_CHOPPY_TREND", str(chop_trend * 0.95)),
            True, _allowed("MORNING_P3_ALLOWED", "BTC,ETH,SOL"),
        )

    # Afternoon — tradeable chop (was 0 signals 3pm-close)
    return SessionGates(
        "AFTERNOON", None,
        _env_float("SESSION_AFTERNOON_MIN_TREND", "0.20"),
        _env_float("SESSION_AFTERNOON_MIN_DIST", "0.0006"),
        _env_float("SESSION_AFTERNOON_MIN_PROB", "0.54"),
        _env_float("SESSION_AFTERNOON_MIN_EDGE", "0.05"),
        _env_float("SESSION_AFTERNOON_CHOPPY_TREND", "0.22"),
        True, None,
    )


def is_morning_session() -> bool:
    s = get_session()
    return s.name in ("PRE_OPEN", "POST_OPEN", "MIDDAY")


def is_afternoon_session() -> bool:
    return get_session().name == "AFTERNOON"


def can_trade_now() -> Tuple[bool, str]:
    s = get_session()
    if s.name == "WEEKEND":
        return False, "[WEEKEND] no trading Sat/Sun ET"
    if s.name == "OFF":
        hm = _hm_now()
        return False, f"[OFF HOURS] {hm[0]:02d}:{hm[1]:02d} ET — window 8:30am-6pm ET"
    if s.name == "US_OPEN_CHOP":
        return False, f"[US OPEN] 9:30-11:00 ET chop blackout (scanning active)"
    return True, ""


def get_regime_label(is_choppy: bool) -> str:
    return "CHOPPY" if is_choppy else "TRENDING"


def book_agrees(direction: str, book_up: float, gap: float = 0.04) -> bool:
    if direction == "UP":
        return book_up >= 0.50 + gap
    return book_up <= 0.50 - gap


def session_expensive_down_max_ask(book_agrees_dir: bool) -> float:
    base = _env_float("EXPENSIVE_DOWN_MAX_ASK", "0.62")
    if book_agrees_dir:
        return _env_float("EXPENSIVE_DOWN_BOOK_AGREE_MAX", "0.78")
    return base


def session_expensive_down_min_dist(book_agrees_dir: bool) -> float:
    base = _env_float("EXPENSIVE_DOWN_MIN_DIST", "0.0018")
    if book_agrees_dir:
        return _env_float("EXPENSIVE_DOWN_BOOK_AGREE_DIST", "0.0012")
    return base
