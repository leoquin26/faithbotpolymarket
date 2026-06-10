"""Latency-arbitrage fast path (Phase 1).

Watches Binance ticks for sudden moves (> LATENCY_ARB_MIN_ROC_BPS in the last
LATENCY_ARB_WINDOW_SEC seconds).  When detected, fires a small directional
bet on Polymarket *before* the orderbook fully reprices.

Designed for the 3-15 second Polymarket lag after Binance moves.

Bypasses: morning_strategy, regime_strategy, exhaust, calibration, reversion.
Respects: daily loss limit, window dedup, ASK validity, ENTRY range, cooldown.

Env knobs:
  LATENCY_ARB_ENABLED          on/off          default on
  LATENCY_ARB_MIN_ROC_BPS      basis points    default 25   (0.25 % in window)
  LATENCY_ARB_WINDOW_SEC       seconds         default 10
  LATENCY_ARB_MAX_ENTRY        decimal price   default 0.65
  LATENCY_ARB_MIN_ENTRY        decimal price   default 0.20
  LATENCY_ARB_BET_USD          dollars         default 1.50
  LATENCY_ARB_COOLDOWN_SEC     seconds         default 60   (per coin+side)
  LATENCY_ARB_REQUIRE_DIST_PCT decimal pct     default 0.02 (need 0.02 % from strike)
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Dict, Optional

from loguru import logger


# ---------- Env helpers ----------
def _env_on(name: str, default: str = "on") -> bool:
    return os.getenv(name, default).strip().lower() == "on"


def _env_f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


# ---------- State ----------
_last_fire: Dict[str, float] = {}   # key = "COIN:DIR" -> ts


def _cooldown_remaining(coin: str, direction: str) -> float:
    cd = _env_f("LATENCY_ARB_COOLDOWN_SEC", 60.0)
    key = f"{coin}:{direction}"
    last = _last_fire.get(key, 0.0)
    return max(0.0, last + cd - time.time())


def _mark_fired(coin: str, direction: str) -> None:
    _last_fire[f"{coin}:{direction}"] = time.time()


# ---------- Core ----------
@dataclass
class FastSignal:
    coin: str
    direction: str            # UP / DOWN
    binance_roc_bps: float    # signed (+ up, - down)
    poly_ask: float
    bet_usd: float
    reason: str


def evaluate(coin: str, info, binance_ws, up_ask: float, down_ask: float) -> Optional[FastSignal]:
    """Return a FastSignal if latency-arb conditions are met, else None.

    Pure read-only; caller is responsible for placement.
    """
    if not _env_on("LATENCY_ARB_ENABLED", "on"):
        return None

    win_sec = _env_f("LATENCY_ARB_WINDOW_SEC", 10.0)
    min_roc_bps = _env_f("LATENCY_ARB_MIN_ROC_BPS", 25.0)
    max_entry = _env_f("LATENCY_ARB_MAX_ENTRY", 0.65)
    min_entry = _env_f("LATENCY_ARB_MIN_ENTRY", 0.20)
    bet_usd = _env_f("LATENCY_ARB_BET_USD", 1.50)
    min_dist_pct = _env_f("LATENCY_ARB_REQUIRE_DIST_PCT", 0.02)

    # --- 1. Need a strong recent Binance move ---
    ticks = binance_ws.get_tick_history(coin, int(win_sec) + 2)
    if len(ticks) < 4:
        return None
    t0, p0 = ticks[0]
    t1, p1 = ticks[-1]
    if p0 <= 0 or (t1 - t0) < win_sec * 0.5:
        return None
    roc_bps = (p1 - p0) / p0 * 10000.0  # basis points
    if abs(roc_bps) < min_roc_bps:
        return None

    direction = "UP" if roc_bps > 0 else "DOWN"

    # --- 1b. ByBit consensus (opt-in via env) ---
    if _env_on("LATENCY_ARB_REQUIRE_CONSENSUS", "off"):
        try:
            import bybit_ws as _bw
            _bb_roc = _bw.get_short_roc_bps(coin, win_sec)
            if _bb_roc is None:
                # ByBit hasn't seen ticks yet — fail closed when consensus required
                return None
            # Must agree in sign and magnitude (within 50% of binance roc)
            _agree_sign = (roc_bps > 0) == (_bb_roc > 0)
            _agree_mag  = abs(_bb_roc) >= abs(roc_bps) * 0.5
            if not (_agree_sign and _agree_mag):
                return None
        except Exception:
            return None  # fail closed

    # --- 2. Cooldown gate ---
    cd_rem = _cooldown_remaining(coin, direction)
    if cd_rem > 0:
        return None

    # --- 3. Need distance from strike (skip near-strike noise) ---
    try:
        strike = float(getattr(info, "strike_price", 0) or 0)
        cur = float(getattr(info, "current_crypto_price", 0) or 0)
        if strike <= 0 or cur <= 0:
            return None
        dist_pct = abs(cur - strike) / strike * 100.0
        if dist_pct < min_dist_pct:
            return None
        # Direction sanity: move should be AWAY from strike for this side to win
        moving_above = roc_bps > 0
        if direction == "UP" and cur < strike:
            return None   # going up but still below strike — risky
        if direction == "DOWN" and cur > strike:
            return None   # going down but still above strike — risky
    except Exception:
        return None

    # --- 4. Polymarket ask sanity ---
    ask = up_ask if direction == "UP" else down_ask
    if not ask or ask < min_entry or ask > max_entry:
        return None

    return FastSignal(
        coin=coin,
        direction=direction,
        binance_roc_bps=roc_bps,
        poly_ask=ask,
        bet_usd=bet_usd,
        reason=f"binance_roc={roc_bps:+.0f}bps/{win_sec:.0f}s dist={dist_pct:.2f}% ask={ask*100:.0f}c",
    )


def confirm_fired(coin: str, direction: str) -> None:
    """Caller invokes after FOK fill (or even after attempt) to set cooldown."""
    _mark_fired(coin, direction)


def diag() -> Dict[str, float]:
    """Return latest fire timestamps for telemetry."""
    return dict(_last_fire)


# ---------------------------------------------------------------------------
# Phase 2: Polymarket-vs-Binance implied-probability gap detector
# ---------------------------------------------------------------------------
import math


def _norm_cdf(x: float) -> float:
    a1, a2, a3, a4, a5 = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429
    p = 0.3275911
    sign = -1.0 if x < 0 else 1.0
    ax = abs(x) / math.sqrt(2.0)
    t = 1.0 / (1.0 + p * ax)
    y = 1.0 - ((((a5 * t + a4) * t + a3) * t + a2) * t + a1) * t * math.exp(-ax * ax)
    return 0.5 * (1.0 + sign * y)


def _bs_up_prob(current_price: float, strike: float, sigma_per_sec: float, T_sec: float) -> float:
    """P(price > strike at T seconds)."""
    if T_sec <= 0:
        return 1.0 if current_price > strike else 0.0
    if sigma_per_sec <= 0 or current_price <= 0 or strike <= 0:
        return 0.5
    sqrt_T = math.sqrt(T_sec)
    d2 = (math.log(current_price / strike) + (-0.5 * sigma_per_sec ** 2) * T_sec) / (sigma_per_sec * sqrt_T)
    return _norm_cdf(d2)


def evaluate_gap(coin: str, info, binance_ws,
                 up_ask: float, down_ask: float) -> Optional[FastSignal]:
    """Pure implied-probability-gap detector.

    Computes Black-Scholes fair UP probability from live Binance price +
    realized vol + time-to-expiry, then compares to Polymarket asks.

    If the GAP (fair - implied) exceeds GAP_DETECTOR_MIN_PP, fires.

    This is independent of the predictor's pipeline and only checks the raw
    pricing gap.  It catches stale Polymarket asks the predictor may reject
    for other reasons (regime, exhaust, etc.).

    Env knobs:
      GAP_DETECTOR_ENABLED       on/off       default on
      GAP_DETECTOR_MIN_PP        percent      default 8.0   (gap >= 8pp to fire)
      GAP_DETECTOR_MAX_ENTRY     decimal      default 0.70
      GAP_DETECTOR_MIN_ENTRY     decimal      default 0.20
      GAP_DETECTOR_BET_USD       dollars      default 1.50
      GAP_DETECTOR_COOLDOWN_SEC  seconds      default 90
      GAP_DETECTOR_MIN_T         seconds      default 120   (skip near-expiry)
    """
    if not _env_on("GAP_DETECTOR_ENABLED", "on"):
        return None

    min_gap_pp = _env_f("GAP_DETECTOR_MIN_PP", 8.0)
    max_entry = _env_f("GAP_DETECTOR_MAX_ENTRY", 0.70)
    min_entry = _env_f("GAP_DETECTOR_MIN_ENTRY", 0.20)
    bet_usd = _env_f("GAP_DETECTOR_BET_USD", 1.50)
    min_T = _env_f("GAP_DETECTOR_MIN_T", 120.0)

    try:
        cur = float(getattr(info, "current_crypto_price", 0) or 0)
        strike = float(getattr(info, "strike_price", 0) or 0)
        T = float(getattr(info, "time_remaining", 0) or 0)
        if cur <= 0 or strike <= 0 or T < min_T:
            return None

        # Realized volatility from last 180s of ticks (per-second)
        sigma = float(binance_ws.get_realized_vol(coin, 180) or 0)
        if sigma <= 0:
            return None

        fair_up = _bs_up_prob(cur, strike, sigma, T)
        fair_down = 1.0 - fair_up

        # ----- Check UP gap -----
        up_gap_pp = (fair_up - up_ask) * 100.0
        down_gap_pp = (fair_down - down_ask) * 100.0

        # Pick the larger positive gap
        if up_gap_pp >= min_gap_pp and up_gap_pp >= down_gap_pp:
            direction = "UP"
            ask = up_ask
            gap_pp = up_gap_pp
            fair = fair_up
        elif down_gap_pp >= min_gap_pp:
            direction = "DOWN"
            ask = down_ask
            gap_pp = down_gap_pp
            fair = fair_down
        else:
            return None

        # Entry range sanity
        if not ask or ask < min_entry or ask > max_entry:
            return None

        # Separate cooldown bucket
        cd_key = f"GAP:{coin}:{direction}"
        cd_sec = _env_f("GAP_DETECTOR_COOLDOWN_SEC", 90.0)
        last = _last_fire.get(cd_key, 0.0)
        if last + cd_sec > time.time():
            return None

        return FastSignal(
            coin=coin,
            direction=direction,
            binance_roc_bps=0.0,  # not used here
            poly_ask=ask,
            bet_usd=bet_usd,
            reason=(f"gap-detector: fair={fair*100:.0f}% ask={ask*100:.0f}c "
                    f"gap={gap_pp:+.1f}pp sigma={sigma:.5f} T={T:.0f}s"),
        )
    except Exception as exc:  # noqa
        return None


def confirm_gap_fired(coin: str, direction: str) -> None:
    _last_fire[f"GAP:{coin}:{direction}"] = time.time()
