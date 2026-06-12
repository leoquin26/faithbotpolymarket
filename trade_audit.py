"""
Per-trade gate audit — one log line: signal → gates → decision.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def start(coin: str, window_start: int = 0) -> Dict[str, Any]:
    return {"coin": coin, "window": int(window_start or 0), "gates": {}}


def set_gate(audit: Optional[Dict], key: str, value: str) -> None:
    if audit is not None:
        audit["gates"][key] = value


def attach(pred: Any, audit: Dict[str, Any]) -> None:
    setattr(pred, "audit", audit)


def get_audit(pred: Any) -> Dict[str, Any]:
    a = getattr(pred, "audit", None)
    if a is None:
        a = start(getattr(pred, "coin", "?"))
        attach(pred, a)
    return a


def record_signal(
    pred: Any,
    *,
    prob: float,
    edge: float,
    ask: float,
    trend: float = 0.0,
    dist_pct: float = 0.0,
    regime: str = "",
    regime_action: str = "",
    regime_reason: str = "",
    sigma: float = 0.0,
    T_sec: float = 0.0,
) -> None:
    a = get_audit(pred)
    parts = [
        f"prob={prob:.0%}",
        f"edge={edge * 100:.1f}%",
        f"ask={ask * 100:.0f}c",
        f"trend={trend:+.2f}",
        f"dist={dist_pct * 100:+.3f}%",
    ]
    if sigma > 0:
        parts.append(f"sigma={sigma:.2e}")
    if T_sec > 0:
        parts.append(f"T={T_sec:.0f}s")
    set_gate(a, "signal", " ".join(parts))
    if regime:
        ra = regime_action or "—"
        rr = regime_reason or ""
        set_gate(a, "regime", f"{regime}/{ra}" + (f"({rr})" if rr else ""))


def record_block(coin: str, gate: str, reason: str, window_start: int = 0) -> None:
    audit = start(coin, window_start)
    set_gate(audit, gate, f"BLOCK:{reason}")
    _emit(audit, "SKIP")


def record_exhaust(pred: Any, action: str, score: float = 0.0, override: str = "") -> None:
    a = get_audit(pred)
    s = f"{action}"
    if score:
        s += f" score={score:.2f}"
    if override:
        s += f" [{override}]"
    set_gate(a, "exhaust", s)


def record_morning(pred: Any, phase: int, status: str, detail: str = "") -> None:
    a = get_audit(pred)
    set_gate(a, "morning", f"P{phase}-{status}" + (f" {detail}" if detail else ""))


def record_clob(pred: Any, ask: float, edge: float, note: str = "") -> None:
    a = get_audit(pred)
    s = f"ask={ask * 100:.0f}c edge={edge * 100:.1f}%"
    if note:
        s += f" {note}"
    set_gate(a, "clob", s)


def log_decision(
    pred: Any,
    decision: str,
    *,
    extra: str = "",
    cost: float = 0.0,
    shares: float = 0.0,
    fill_c: float = 0.0,
) -> None:
    a = get_audit(pred)
    coin = getattr(pred, "coin", a.get("coin", "?"))
    direction = getattr(pred, "direction", "?")
    ask = getattr(pred, "entry_price", 0) or getattr(pred, "poly_price", 0)
    gates = " → ".join(f"{k}={v}" for k, v in a.get("gates", {}).items())
    tail = ""
    if fill_c > 0 and shares > 0:
        tail = f" fill={fill_c * 100:.0f}c x{shares:.1f} cost=${cost:.2f}"
    elif cost > 0:
        tail = f" cost=${cost:.2f}"
    if extra:
        tail += f" {extra}"
    logger.info(
        f"[TRADE AUDIT] {coin} {direction}@{ask * 100:.0f}c | "
        f"{gates} | DECISION={decision}{tail}"
    )


def _emit(audit: Dict[str, Any], decision: str) -> None:
    coin = audit.get("coin", "?")
    gates = " → ".join(f"{k}={v}" for k, v in audit.get("gates", {}).items())
    logger.info(f"[TRADE AUDIT] {coin} | {gates} | DECISION={decision}")
