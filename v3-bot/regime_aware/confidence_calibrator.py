"""
Confidence Calibrator — adjusts the predictor's raw `probability` based on
context the predictor itself doesn't see (regime, per-bucket WR, microstructure,
reversion-risk).

Designed as an *additive shadow* layer:

  • Pure function, no I/O, no logging.
  • Returns a calibrated probability + a breakdown of *why* the calibration
    moved the number.
  • The caller decides whether to use the calibrated prob (LIVE mode) or just
    log it next to the raw prob (SHADOW mode, for offline grading).

Five independent multipliers, each in [0.7, 1.1] roughly, applied to the
raw probability after re-centering on 0.5:

  1. regime_factor      — REVERTING / CHOPPY shrink confidence on trend trades
  2. bucket_factor      — per-(coin, ask, trend) empirical WR drives shrink/lift
  3. microstructure_factor
                        — wide spread, adverse depth-skew, severe ask velocity
                          → shrink confidence
  4. reversion_factor   — the reversion_risk score itself: high risk → shrink
  5. late_window_factor — under T<300s, shrink slightly (15m markets revert
                          hardest in the last 5 min)

Final prob = 0.5 + (raw_prob − 0.5) × ∏ factors, clamped to [0.05, 0.95].

The thresholds and weights are intentionally conservative — the goal is to
*tighten* sizing on shaky setups, not to flip directions (the regime engine
+ reversion-risk are the flippers).

Env knobs:
  CALIBRATION_REGIME_REVERTING_FACTOR  (default 0.85)
  CALIBRATION_REGIME_CHOPPY_FACTOR     (default 0.85)
  CALIBRATION_BUCKET_WR_FLOOR          (default 0.40)
  CALIBRATION_SPREAD_BPS_PENALTY       (default 250)  bps above which spread starts shrinking
"""
from __future__ import annotations

import os
from typing import Dict, Optional


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except (TypeError, ValueError):
        return default


def _regime_factor(regime: str, trend_abs: float) -> float:
    """Shrink confidence for trend-following trades in REVERTING/CHOPPY."""
    if regime == "REVERTING":
        # Strong trends in reverting regimes are most likely to fail.
        if trend_abs >= 1.5:
            return _env_float("CALIBRATION_REGIME_REVERTING_FACTOR", 0.85)
        return 0.92
    if regime == "CHOPPY":
        return _env_float("CALIBRATION_REGIME_CHOPPY_FACTOR", 0.85)
    if regime == "TRENDING":
        # Slight boost when regime confirms our direction
        if trend_abs >= 1.5:
            return 1.05
        return 1.02
    return 1.00


def _bucket_factor(bucket_stats: Optional[dict]) -> float:
    """Use empirical bucket WR to shift confidence.
    No samples → 1.0 (no info, no shift).
    Low WR → shrink. High WR → lift.
    """
    if not bucket_stats or bucket_stats.get("n", 0) < 5:
        return 1.00
    wr = bucket_stats["wr"]
    floor = _env_float("CALIBRATION_BUCKET_WR_FLOOR", 0.40)
    if wr <= floor:
        return 0.80                         # bucket is structurally bad
    if wr <= 0.50:
        return 0.92
    if wr >= 0.70:
        return 1.07
    return 1.00


def _microstructure_factor(features: Optional[dict]) -> float:
    """Microstructure-driven shrink for wide spread / adverse depth_skew /
    severe ask velocity (all of which the predictor doesn't see directly)."""
    if not features:
        return 1.00
    factor = 1.00
    spread = features.get("spread_bps")
    if spread is not None:
        spread_thresh = _env_float("CALIBRATION_SPREAD_BPS_PENALTY", 250.0)
        if spread > spread_thresh:
            # Linear shrink: every 100bps over threshold = 3% shrink, max 12%
            extra = min(400.0, spread - spread_thresh)
            factor *= 1.0 - 0.03 * (extra / 100.0)

    skew = features.get("depth_skew", 0.0)
    # depth_skew is from the side we're buying; if it's < 0 the OTHER side has
    # more liquidity (smart money sitting on the other side).
    if skew <= -0.4:
        factor *= 0.92
    elif skew <= -0.2:
        factor *= 0.97

    adv = features.get("ask_adverse_cpm", 0.0)
    if adv >= 30.0:
        factor *= 0.90
    elif adv >= 15.0:
        factor *= 0.95

    return max(0.70, factor)


def _reversion_factor(reversion_risk: float) -> float:
    """Reversion-risk above 0.25 starts compounding shrink."""
    if reversion_risk <= 0.20:
        return 1.00
    if reversion_risk <= 0.40:
        return 0.95
    return 0.88


def _late_window_factor(T_sec: float) -> float:
    """Below 5 min remaining, shrink slightly. The 15m → 5min reversion
    risk is what the late_score in reversion_risk handles for the *score*;
    here we mirror it on the *probability*."""
    if T_sec >= 300:
        return 1.00
    if T_sec >= 180:
        return 0.97
    return 0.92


def _xasset_factor(direction: str,
                   xasset: Optional[dict]) -> float:
    """Cross-asset breadth alignment factor.

    May 27 v3 — env-driven; default amplification raised after research
    showed cross-market consensus is the highest-quality directional signal
    we have (paper arXiv:2508.03474). Maps the multi-coin snapshot to a
    probability multiplier:

      • confirms direction AND |b| ≥ 0.75 AND age ≥ 60s
            → XASSET_BOOST_STRONG (default 1.15)   ← was 1.05
      • confirms direction AND |b| ≥ 0.5 (or fresh confirm)
            → XASSET_BOOST_FRESH (default 1.05)    ← was 1.02
      • |breadth| < 0.4 (no consensus)
            → XASSET_SHRINK_NEUTRAL (default 0.85)
      • contradicts our direction, |b| ≥ 0.5
            → XASSET_SHRINK_CONTRADICT (default 0.70) ← was 0.75
      • else                                       → 1.00

    "Direction confirms" means:
        we want UP   and breadth > 0   (more coins UP than DOWN), OR
        we want DOWN and breadth < 0
    """
    if not xasset:
        return 1.00
    try:
        breadth = float(xasset.get("breadth", 0.0))
        age = float(xasset.get("dominant_age_sec", 0.0))
    except (TypeError, ValueError):
        return 1.00

    abs_b = abs(breadth)
    confirms_up = direction == "UP" and breadth > 0
    confirms_down = direction == "DOWN" and breadth < 0
    confirms = confirms_up or confirms_down

    boost_strong = _env_float("XASSET_BOOST_STRONG", 1.15)
    boost_fresh = _env_float("XASSET_BOOST_FRESH", 1.05)
    shrink_neutral = _env_float("XASSET_SHRINK_NEUTRAL", 0.85)
    shrink_contradict = _env_float("XASSET_SHRINK_CONTRADICT", 0.70)
    confirm_age_secs = _env_float("XASSET_CONFIRM_AGE_SECS", 60.0)
    confirm_strong_breadth = _env_float("XASSET_STRONG_BREADTH", 0.75)
    confirm_fresh_breadth = _env_float("XASSET_FRESH_BREADTH", 0.5)
    neutral_breadth = _env_float("XASSET_NEUTRAL_BREADTH", 0.4)

    if confirms and abs_b >= confirm_strong_breadth and age >= confirm_age_secs:
        return boost_strong
    if confirms and abs_b >= confirm_fresh_breadth:
        return boost_fresh
    if abs_b < neutral_breadth:
        return shrink_neutral
    if not confirms and abs_b >= confirm_fresh_breadth:
        return shrink_contradict
    return 1.00


def calibrate(*,
              raw_prob: float,
              regime: str,
              trend_abs: float,
              bucket_stats: Optional[dict],
              microstructure_features: Optional[dict],
              reversion_risk: float = 0.0,
              T_sec: float = 900.0,
              xasset_features: Optional[dict] = None,
              direction: str = "UP") -> Dict:
    """Return the calibrated probability and a feature breakdown.

    Result keys:
      raw_prob, calibrated_prob, factors{...}, delta_pct
    """
    factors = {
        "regime": _regime_factor(regime, trend_abs),
        "bucket": _bucket_factor(bucket_stats),
        "micro": _microstructure_factor(microstructure_features),
        "reversion": _reversion_factor(reversion_risk),
        "late": _late_window_factor(T_sec),
        "xasset": _xasset_factor(direction, xasset_features),
    }
    multiplier = 1.0
    for f in factors.values():
        multiplier *= f
    centered = raw_prob - 0.5
    cal = 0.5 + centered * multiplier
    cal = max(0.05, min(0.95, cal))

    return {
        "raw_prob": round(raw_prob, 3),
        "calibrated_prob": round(cal, 3),
        "multiplier": round(multiplier, 3),
        "factors": {k: round(v, 3) for k, v in factors.items()},
        "delta_pct": round((cal - raw_prob) * 100, 1),
    }


def format_log_line(coin: str, direction: str, result: Dict, *, mode: str) -> str:
    f = result["factors"]
    xa = f.get("xasset", 1.00)
    return (
        f"[CALIBRATION {mode}] {coin} {direction} "
        f"raw={result['raw_prob']*100:.0f}% cal={result['calibrated_prob']*100:.0f}% "
        f"({result['delta_pct']:+.1f}pp) | "
        f"reg={f['regime']:.2f} bkt={f['bucket']:.2f} "
        f"mic={f['micro']:.2f} rev={f['reversion']:.2f} late={f['late']:.2f} "
        f"xa={xa:.2f}"
    )
