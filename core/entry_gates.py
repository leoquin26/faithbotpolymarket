"""
Single-source-of-truth entry-gate evaluator.

Replaces the duplicated gate cascade that today lives partly in
`run_bot.main()` (afternoon path) and partly in `OrderManager.place_bet()`.
Both currently re-check trap-band, PM coin block, entry range, real-edge,
PM_ENTRY_MAX — silently, with subtle differences in order.

Goal:
  • One function: `evaluate_for_entry(pred, ctx) -> EvaluationResult`
  • Pure-ish: reads config + a small context object; no I/O except a fresh
    CLOB ask lookup (passed in as a callable).
  • All decisions logged in a single, parseable `[GATE-V2]` line.
  • Initial deployment is SHADOW only — bot still uses the old path. We log
    side-by-side and only promote when log analysis shows the new evaluator
    agrees with the old in ≥ 99% of decisions.

This module is intentionally minimal and dependency-free; it imports config
at call-time so unit tests can monkey-patch.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class GateContext:
    """Bag-of-data passed into evaluate_for_entry."""
    is_pm_session: bool = False
    is_morning_session: bool = False
    daily_losses: float = 0.0
    daily_loss_limit: float = 15.0
    active_positions: int = 0
    max_concurrent: int = 2
    consec_losses: int = 0
    is_window_locked: bool = False
    is_window_traded_already: bool = False
    same_dir_count_this_window: int = 0
    same_dir_max: int = 3
    trap_band_tainted: bool = False
    real_ask: Optional[float] = None        # fresh CLOB ask, if available
    ask_drift_vs_signal: float = 0.0        # cents; positive = ask jumped up


@dataclass
class EvaluationResult:
    allow: bool
    reason: str
    detail: dict = field(default_factory=dict)
    modified_ask: Optional[float] = None    # may be overwritten by CLOB lookup
    modified_edge: Optional[float] = None


# ──────────────────────────────────────────────────────────────────────────────
def _is_inverted_confidence(confidence: str) -> bool:
    return confidence in ("REGIME-INVERT", "BTC-INVERT", "TRAP-INVERT",
                          "REVERSION-INVERT")


def evaluate_for_entry(pred, ctx: GateContext) -> EvaluationResult:
    """Run the full entry-gate cascade and return a single decision.

    Order is deliberate — cheaper / harder filters first:
      1. window-locked / already-traded (instant)
      2. daily stop-loss
      3. concurrency cap
      4. consec-loss breaker
      5. correlation limit (same-direction count)
      6. trap-band memory taint (window-scoped)
      7. session coin block (PM / morning)
      8. raw entry range
      9. (CLOB ask drift, if real_ask provided)
      10. real-edge floor (recompute against real ask)
      11. session entry caps (PM, ETH UP, choppy)
      12. trap band (with A-tier override)
    """
    import config  # late import so tests can monkey-patch

    coin = pred.coin
    direction = pred.direction
    confidence = pred.confidence or ""
    is_inverted = _is_inverted_confidence(confidence)
    poly_price = float(pred.poly_price)
    prob = float(pred.probability)
    edge = float(pred.edge or 0.0)

    # 1. Window lock / already traded
    if ctx.is_window_locked:
        return EvaluationResult(False, "window-locked")
    if ctx.is_window_traded_already:
        return EvaluationResult(False, "window-already-traded")

    # 2. Daily stop-loss
    if ctx.daily_losses >= ctx.daily_loss_limit:
        return EvaluationResult(False, "daily-stop-loss",
                                {"losses": ctx.daily_losses,
                                 "limit": ctx.daily_loss_limit})

    # 3. Concurrency cap
    if ctx.active_positions >= ctx.max_concurrent:
        return EvaluationResult(False, "max-concurrent",
                                {"active": ctx.active_positions,
                                 "cap": ctx.max_concurrent})

    # 4. Consecutive-loss breaker
    if ctx.consec_losses >= 2:
        return EvaluationResult(False, "consec-loss-breaker",
                                {"consec": ctx.consec_losses})

    # 5. Correlation: max N same-direction bets per window
    if ctx.same_dir_count_this_window >= ctx.same_dir_max:
        return EvaluationResult(False, "correlation-limit",
                                {"same_dir": ctx.same_dir_count_this_window})

    # 6. Trap-band memory (window-scoped)
    if ctx.trap_band_tainted:
        ovr_p = float(getattr(config, "TRAP_BAND_OVERRIDE_PROB", 0.85) or 0.85)
        ovr_e = float(getattr(config, "TRAP_BAND_OVERRIDE_EDGE", 0.18) or 0.18)
        if prob >= ovr_p and edge >= ovr_e:
            pass  # A-tier override allowed
        else:
            return EvaluationResult(False, "trap-band-tainted")

    # 7. Session coin block
    if ctx.is_pm_session and coin in getattr(config, "PM_BLOCKED_COINS", set()):
        if not is_inverted:
            return EvaluationResult(False, "pm-coin-block",
                                    {"coin": coin})

    # 8. Raw entry range — inverted trades intentionally buy cheap opposite side
    entry_min = float(getattr(config, "ENTRY_MIN", 0.45) or 0.45)
    entry_max = float(getattr(config, "ENTRY_MAX", 0.78) or 0.78)
    effective_min = 0.20 if is_inverted else entry_min
    if poly_price < effective_min or poly_price > entry_max:
        return EvaluationResult(False, "entry-range",
                                {"ask": poly_price,
                                 "min": effective_min, "max": entry_max})

    # 9. Real CLOB ask drift
    real_ask = ctx.real_ask
    if real_ask is not None:
        max_drift = float(getattr(config, "ASK_MAX_DRIFT", 0.02) or 0.02)
        if ctx.ask_drift_vs_signal > max_drift:
            return EvaluationResult(False, "ask-drift",
                                    {"drift": ctx.ask_drift_vs_signal,
                                     "max": max_drift})

    actual_entry = real_ask if real_ask is not None else poly_price

    # 10. Real-edge floor
    real_edge = prob - actual_entry
    if real_edge < 0.02:
        return EvaluationResult(False, "low-real-edge",
                                {"prob": prob, "ask": actual_entry,
                                 "real_edge": real_edge})

    # 11. Session entry caps
    pm_cap = float(getattr(config, "PM_ENTRY_MAX", 0.64) or 0.64)
    if ctx.is_pm_session and not is_inverted and actual_entry > pm_cap:
        return EvaluationResult(False, "pm-entry-cap",
                                {"ask": actual_entry, "cap": pm_cap})

    eth_up_max = float(getattr(config, "ETH_UP_MAX", 0.60) or 0.60)
    if coin == "ETH" and direction == "UP" and actual_entry > eth_up_max:
        return EvaluationResult(False, "eth-up-cap",
                                {"ask": actual_entry, "cap": eth_up_max})

    choppy_cap = float(getattr(config, "CHOPPY_ENTRY_MAX", 0.58) or 0.58)
    if getattr(pred, "market_regime", "TRENDING") == "CHOPPY" and actual_entry > choppy_cap:
        return EvaluationResult(False, "choppy-entry-cap",
                                {"ask": actual_entry, "cap": choppy_cap})

    # 12. Trap band — with A-tier override
    trap_min = float(getattr(config, "TRAP_BAND_MIN", 0.60) or 0.60)
    trap_max = float(getattr(config, "TRAP_BAND_MAX", 0.63) or 0.63)
    if trap_min <= actual_entry <= trap_max:
        ovr_p = float(getattr(config, "TRAP_BAND_OVERRIDE_PROB", 0.85) or 0.85)
        ovr_e = float(getattr(config, "TRAP_BAND_OVERRIDE_EDGE", 0.20) or 0.20)
        if prob >= ovr_p and real_edge >= ovr_e:
            pass  # A-tier override
        else:
            return EvaluationResult(False, "trap-band",
                                    {"ask": actual_entry,
                                     "band": (trap_min, trap_max)})

    return EvaluationResult(
        allow=True,
        reason="ok",
        detail={"ask": actual_entry, "real_edge": real_edge},
        modified_ask=actual_entry,
        modified_edge=real_edge,
    )


def format_log_line(coin: str, direction: str, res: EvaluationResult) -> str:
    """Single-line log helper for [GATE-V2]."""
    tag = "ALLOW" if res.allow else "BLOCK"
    extras = ""
    if res.detail:
        extras = " | " + ", ".join(f"{k}={v}" for k, v in res.detail.items())
    return f"[GATE-V2] {coin} {direction} {tag} reason={res.reason}{extras}"
