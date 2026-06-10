"""
Exhaustion Detector - Shadow Mode (v2: crowd-gated)

Detects when a directional signal is chasing an exhausted move.

Produces a score in [0, 1] from 5 independent signals:
  1. Momentum deceleration (roc_30 vs roc_300 ratio)
  2. Polymarket crowd overextension (poly moved more than Binance justifies)
  3. Session-range position (at top/bottom of recent range)
  4. Cross-asset breadth at high entry (everyone on same side)
  5. Over-tick ratio (upticks/downticks climax)

Actions (by score threshold):
  < 0.30  -> CLEAN     no exhaustion
  < 0.50  -> DAMPEN    reduce probability by 15%
  < 0.70  -> ABSTAIN   skip trade
  >= 0.70 -> FLIP      reverse direction

v2 CROWD GATE:
  A non-CLEAN action only stands when at least one "unique exhaustion"
  signal fires (breadth >= GATE_BREADTH or poly >= GATE_POLY). Trend-topping
  signals alone (range + decel) are not enough to flag a trade, because
  every healthy uptrend naturally sits near range-high with momentum decay.

  This is calibrated from Apr-17 shadow data:
    - All 4 morning losses had breadth >= 0.67 -> would flag
    - All 15 afternoon wins had breadth = 0.00 and poly = 0.00 -> would pass

SHADOW_MODE = True means actions are logged but NOT applied.
"""

import time
import os
from collections import deque
from typing import Dict, List, Optional, Tuple
from loguru import logger


SHADOW_MODE = False

W_DECEL = 0.30
W_POLY_OVEREXT = 0.25
W_RANGE_POS = 0.20
W_BREADTH = 0.15
W_OVERTICK = 0.10

TH_DAMPEN = 0.30
TH_ABSTAIN = 0.50
TH_FLIP = float(os.getenv("EXHAUST_TH_FLIP", "0.70"))  # was 0.70; lowered via env after Jun-1 backtest (311 trades, 71% flip-WR in 0.50-0.70 zone)

# Crowd gate: require one of these unique-exhaustion signals to fire
# before a non-CLEAN action is allowed. Prevents over-firing on normal
# healthy trends where range=1.00 and decel=1.00 are the default state.
GATE_BREADTH = 0.33
GATE_POLY = 0.40

# Fix E (apr21): sticky breadth memory. Breadth is computed per-tick
# and can flicker when a coin's ask briefly crosses the 55c threshold.
# Remembering the highest breadth for each (coin,direction) over the
# last 30s prevents "one bad tick downgrades ABSTAIN -> CLEAN".
_BREADTH_MEM: Dict[Tuple[str, str], Tuple[float, float]] = {}
_BREADTH_MEM_WINDOW = 30.0

_POLY_HISTORY: Dict[Tuple[str, int], deque] = {}
_POLY_MAX_LEN = 120


def record_poly_price(coin: str, window_start: int, poly_price: float) -> None:
    if poly_price <= 0 or poly_price >= 1:
        return
    key = (coin, int(window_start))
    hist = _POLY_HISTORY.get(key)
    if hist is None:
        hist = deque(maxlen=_POLY_MAX_LEN)
        _POLY_HISTORY[key] = hist
    hist.append((time.time(), poly_price))
    if len(_POLY_HISTORY) > 200:
        oldest_keys = sorted(_POLY_HISTORY.keys(), key=lambda k: k[1])[:50]
        for k in oldest_keys:
            _POLY_HISTORY.pop(k, None)


def _roc_from_ticks(ticks: List[Tuple[float, float]], seconds: float) -> float:
    if not ticks or len(ticks) < 2:
        return 0.0
    now_ts = ticks[-1][0]
    cutoff = now_ts - seconds
    old_price = None
    for ts, p in ticks:
        if ts >= cutoff:
            old_price = p
            break
    if old_price is None or old_price <= 0:
        return 0.0
    return (ticks[-1][1] - old_price) / old_price


def _score_decel(ticks: List[Tuple[float, float]], direction: str) -> float:
    r30 = _roc_from_ticks(ticks, 30)
    r300 = _roc_from_ticks(ticks, 300)
    if abs(r300) < 1e-6:
        return 0.0
    dir_sign = 1.0 if direction == "UP" else -1.0
    if r30 * dir_sign < 0:
        return 1.0
    ratio = abs(r30) / abs(r300)
    if ratio >= 1.0:
        return 0.0
    return max(0.0, 1.0 - ratio)


def _score_poly_overextension(coin: str, window_start: int,
                              ticks: List[Tuple[float, float]],
                              direction: str, current_poly: float) -> float:
    key = (coin, int(window_start))
    hist = _POLY_HISTORY.get(key)
    if not hist or len(hist) < 5:
        return 0.0
    now_ts = time.time()
    cutoff = now_ts - 300
    poly_old = None
    for ts, p in hist:
        if ts >= cutoff:
            poly_old = p
            break
    if poly_old is None:
        poly_old = hist[0][1]
    poly_delta_c = (current_poly - poly_old) * 100.0
    binance_roc_300 = _roc_from_ticks(ticks, 300)
    binance_move_pct = abs(binance_roc_300) * 100.0
    dir_sign = 1.0 if direction == "UP" else -1.0
    if poly_delta_c * dir_sign < 4.0:
        return 0.0
    justified_c = binance_move_pct * 66.0
    excess = abs(poly_delta_c) - justified_c
    if excess <= 3.0:
        return 0.0
    return min(1.0, excess / 15.0)


def _score_range_position(ticks: List[Tuple[float, float]], direction: str,
                          current_price: float) -> float:
    if not ticks or len(ticks) < 10 or current_price <= 0:
        return 0.0
    now_ts = ticks[-1][0]
    cutoff = now_ts - 300
    recent = [p for ts, p in ticks if ts >= cutoff]
    if len(recent) < 5:
        return 0.0
    hi, lo = max(recent), min(recent)
    if hi - lo < 1e-6:
        return 0.0
    if direction == "UP":
        pos = (current_price - lo) / (hi - lo)
    else:
        pos = (hi - current_price) / (hi - lo)
    if pos < 0.70:
        return 0.0
    return min(1.0, (pos - 0.70) / 0.30)


def _score_breadth(window_trends: Dict[str, str], direction: str,
                   poly_prices: Dict[str, float]) -> float:
    if not window_trends:
        return 0.0
    # Fix D (apr21): threshold 0.60 -> 0.55 so mid-price pile-ups at 55-59c
    # register as breadth. Old 0.60 missed today's DOWN pile-up (SOL/XRP/BTC
    # all at 58c) -> breadth=0.00 -> GATED->CLEAN -> both trades placed -> lost.
    same_dir_high = sum(
        1 for c, d in window_trends.items()
        if d == direction and poly_prices.get(c, 0.0) >= 0.55
    )
    if same_dir_high < 2:
        return 0.0
    return min(1.0, (same_dir_high - 1) / 3.0)


def _score_overtick(ticks: List[Tuple[float, float]], direction: str) -> float:
    if not ticks or len(ticks) < 5:
        return 0.0
    now_ts = ticks[-1][0]
    cutoff = now_ts - 30
    recent = [(t, p) for t, p in ticks if t >= cutoff]
    if len(recent) < 5:
        return 0.0
    up_count = 0
    dn_count = 0
    for i in range(1, len(recent)):
        dp = recent[i][1] - recent[i - 1][1]
        if dp > 0:
            up_count += 1
        elif dp < 0:
            dn_count += 1
    total = up_count + dn_count
    if total < 5:
        return 0.0
    if direction == "UP":
        ratio = up_count / total
    else:
        ratio = dn_count / total
    if ratio < 0.70:
        return 0.0
    return min(1.0, (ratio - 0.70) / 0.30)


def evaluate(pred, ticks: List[Tuple[float, float]],
             window_trends: Dict[str, str],
             poly_prices: Dict[str, float]) -> Dict:
    coin = pred.coin
    direction = pred.direction
    entry = pred.entry_price if pred.entry_price > 0.05 else pred.poly_price
    window_start = int(pred.market_info.window_start or 0)

    record_poly_price(coin, window_start, entry)

    current_price = pred.market_info.current_crypto_price or 0.0

    s_decel = _score_decel(ticks, direction)
    s_poly = _score_poly_overextension(coin, window_start, ticks, direction, entry)
    s_range = _score_range_position(ticks, direction, current_price)
    s_breadth_raw = _score_breadth(window_trends, direction, poly_prices)
    # Fix E (apr21): take max of current and any breadth seen in last 30s
    # for this (coin, direction). Prevents one bad tick from flipping
    # ABSTAIN -> CLEAN when the pile-up is still ongoing.
    _mem_key = (coin, direction)
    _now_ts = time.time()
    _mem = _BREADTH_MEM.get(_mem_key)
    if _mem is not None and (_now_ts - _mem[0]) <= _BREADTH_MEM_WINDOW:
        s_breadth = max(s_breadth_raw, _mem[1])
    else:
        s_breadth = s_breadth_raw
    # Only remember NON-zero breadth (avoid resetting memory on zero ticks)
    if s_breadth_raw > 0.0:
        _BREADTH_MEM[_mem_key] = (_now_ts, s_breadth_raw)
    elif _mem is not None and (_now_ts - _mem[0]) > _BREADTH_MEM_WINDOW:
        _BREADTH_MEM.pop(_mem_key, None)
    s_tick = _score_overtick(ticks, direction)

    total = (
        W_DECEL * s_decel
        + W_POLY_OVEREXT * s_poly
        + W_RANGE_POS * s_range
        + W_BREADTH * s_breadth
        + W_OVERTICK * s_tick
    )

    if total >= TH_FLIP:
        raw_action = "FLIP"
    elif total >= TH_ABSTAIN:
        raw_action = "ABSTAIN"
    elif total >= TH_DAMPEN:
        raw_action = "DAMPEN"
    else:
        raw_action = "CLEAN"

    # Fix D (apr21) + Fix G (apr21): strong breadth alone (>=0.66, i.e.
    # 3+ coins same direction at 55c+) is by itself sufficient evidence
    # of crowd overextension. Upgrade DAMPEN -> ABSTAIN in that case.
    # NOTE: 2/3 = 0.6666... < 0.67, so the original 0.67 threshold
    # silently missed ALL 3-coin pile-ups. Threshold lowered to 0.66.
    if s_breadth >= 0.66 and raw_action in ("DAMPEN", "CLEAN"):
        raw_action = "ABSTAIN"

    # Crowd gate: require a unique-exhaustion signal (breadth or poly) to
    # stand behind any non-CLEAN action. Trend-topping alone is not enough.
    gated = False
    action = raw_action
    if raw_action != "CLEAN" and s_breadth < GATE_BREADTH and s_poly < GATE_POLY:
        action = "CLEAN"
        gated = True

    if total >= TH_DAMPEN:
        tag = "[EXHAUST-SHADOW]" if SHADOW_MODE else "[EXHAUST]"
        gate_tag = " (GATED->CLEAN)" if gated else ""
        logger.info(
            f"{tag} {coin} {direction} @ {entry*100:.0f}c | score={total:.2f} "
            f"raw={raw_action}{gate_tag} action={action} | "
            f"decel={s_decel:.2f} poly={s_poly:.2f} "
            f"range={s_range:.2f} breadth={s_breadth:.2f} tick={s_tick:.2f}"
        )

    return {
        "action": action,
        "raw_action": raw_action,
        "gated": gated,
        "score": total,
        "signals": {
            "decel": s_decel,
            "poly": s_poly,
            "range": s_range,
            "breadth": s_breadth,
            "tick": s_tick,
        },
        "entry": entry,
        "direction": direction,
        "coin": coin,
    }


def apply(pred, ticks, window_trends, poly_prices):
    """Apply exhaustion-adjusted decision. In SHADOW_MODE, returns pred unchanged."""
    result = evaluate(pred, ticks, window_trends, poly_prices)
    if SHADOW_MODE:
        return pred
    if result["action"] == "ABSTAIN":
        return None
    if result["action"] == "FLIP":
        pred.direction = "DOWN" if pred.direction == "UP" else "UP"
        pred.probability = 1.0 - pred.probability
        entry = pred.entry_price if pred.entry_price > 0.05 else pred.poly_price
        pred.edge = pred.probability - entry
        return pred
    if result["action"] == "DAMPEN":
        pred.probability = max(0.01, pred.probability * 0.85)
        entry = pred.entry_price if pred.entry_price > 0.05 else pred.poly_price
        pred.edge = pred.probability - entry
        return pred
    return pred
