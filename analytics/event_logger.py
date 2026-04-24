"""
Append-only event logger for rigorous trade analysis.

Design principles
-----------------
1. NEVER crash the bot. All writes wrapped in broad try/except.
2. Feature-flagged: `ENABLE_ANALYTICS=false` makes every call a no-op.
3. Append-only JSONL. Append is atomic on POSIX for small writes (<PIPE_BUF=4KB).
4. Threadsafe via a single module-level lock.
5. Zero dependencies beyond stdlib.

Event shape
-----------
{
  "ts":        "2026-04-23T17:48:02+00:00",  # ISO 8601 UTC
  "ts_epoch":  1745432882,                   # int seconds
  "event":     "SIGNAL" | "BLOCKED" | "FIRED" | "RESOLVED" | "EXHAUST" | ...,
  ...arbitrary fields: coin, side, entry, prob, edge, trend_score,
                       exhaust_score, breadth, session_range,
                       time_remaining_s, kelly_tier, kelly_size,
                       blocked_by, window_start, outcome, pnl, ...
}

A single trade's lifecycle produces multiple events correlated by `trade_id`:
  SIGNAL       -> candidate emitted by predictor
  EXHAUST      -> EXHAUST detector verdict (CLEAN/DAMPEN/FLIP/ABSTAIN)
  BLOCKED      -> a filter rejected it (includes `blocked_by` reason)
  FIRED        -> order placed (or MISS on FOK reject)
  RESOLVED     -> window closed, outcome known

Downstream analytics join these events by `trade_id` to reconstruct the
full decision chain, or by (coin, window_start) for counterfactuals on blocks.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

# -- config --------------------------------------------------------------------

EVENTS_PATH = os.getenv(
    "ANALYTICS_EVENTS_PATH",
    "/home/ubuntu/v3-bot/data/trade_events.jsonl",
)

_FLAG = os.getenv("ENABLE_ANALYTICS", "true").strip().lower()
_ENABLED = _FLAG not in ("false", "0", "no", "off", "")

_LOCK = threading.Lock()
_WARNED_ONCE = False


def is_enabled() -> bool:
    return _ENABLED


def new_trade_id() -> str:
    """Short id used to correlate SIGNAL → EXHAUST → BLOCKED/FIRED → RESOLVED."""
    return uuid.uuid4().hex[:12]


def log(event: str, **fields: Any) -> None:
    """
    Append one event to the events JSONL. Swallows all errors.

    Always adds `ts` (ISO8601 UTC) and `ts_epoch` (int seconds).
    """
    if not _ENABLED:
        return

    row: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ts_epoch": int(time.time()),
        "event": event,
    }
    row.update(fields)

    line = _safe_dumps(row)
    if line is None:
        return

    try:
        # Ensure the parent directory exists exactly once per process.
        _ensure_dir()
        with _LOCK:
            with open(EVENTS_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception as e:
        _warn_once(f"[ANALYTICS] write failed: {e}")


# -- helpers -------------------------------------------------------------------

_DIR_OK = False


def _ensure_dir() -> None:
    global _DIR_OK
    if _DIR_OK:
        return
    try:
        os.makedirs(os.path.dirname(EVENTS_PATH), exist_ok=True)
        _DIR_OK = True
    except Exception as e:
        _warn_once(f"[ANALYTICS] mkdir failed: {e}")


def _safe_dumps(obj: dict) -> Optional[str]:
    try:
        return json.dumps(obj, default=_fallback, separators=(",", ":"))
    except Exception as e:
        _warn_once(f"[ANALYTICS] json dump failed: {e}")
        return None


def _fallback(x: Any) -> Any:
    # Convert anything non-serializable into its repr. Defensive: never blocks a write.
    try:
        if hasattr(x, "__dict__"):
            return {k: _fallback(v) for k, v in vars(x).items()
                    if not k.startswith("_") and not callable(v)}
        return str(x)
    except Exception:
        return "<unserializable>"


def _warn_once(msg: str) -> None:
    global _WARNED_ONCE
    if _WARNED_ONCE:
        return
    _WARNED_ONCE = True
    try:
        import sys
        print(msg, file=sys.stderr, flush=True)
    except Exception:
        pass


# -- convenience wrappers for common event types ------------------------------

def log_signal(trade_id: str, pred, trend_score: float, **extra: Any) -> None:
    """Candidate signal emitted by the predictor."""
    log(
        "SIGNAL",
        trade_id=trade_id,
        coin=getattr(pred, "coin", None),
        side=getattr(pred, "direction", None),
        entry=_fnum(getattr(pred, "entry_price", None)),
        prob=_fnum(getattr(pred, "probability", None)),
        edge=_fnum(getattr(pred, "edge", None)),
        trend_score=_fnum(trend_score),
        window_start=_inum(
            getattr(getattr(pred, "market_info", None), "window_start", None)
        ),
        token_id=getattr(pred, "token_id", None),
        confidence=getattr(pred, "confidence", None),
        poly_price=_fnum(getattr(pred, "poly_price", None)),
        **extra,
    )


def log_blocked(
    trade_id: str,
    coin: str,
    side: str,
    blocked_by: str,
    **extra: Any,
) -> None:
    """A filter rejected the signal. `blocked_by` is the filter name."""
    log(
        "BLOCKED",
        trade_id=trade_id,
        coin=coin,
        side=side,
        blocked_by=blocked_by,
        **extra,
    )


def log_exhaust(
    trade_id: str,
    coin: str,
    side: str,
    action: str,
    score: float,
    **extra: Any,
) -> None:
    """EXHAUST detector verdict: CLEAN / DAMPEN / FLIP / ABSTAIN."""
    log(
        "EXHAUST",
        trade_id=trade_id,
        coin=coin,
        side=side,
        action=action,
        score=_fnum(score),
        **extra,
    )


def log_fired(
    trade_id: str,
    coin: str,
    side: str,
    entry: float,
    shares: float,
    cost: float,
    kelly_tier: Optional[str] = None,
    kelly_size: Optional[float] = None,
    phase: Optional[str] = None,
    **extra: Any,
) -> None:
    """Order actually filled."""
    log(
        "FIRED",
        trade_id=trade_id,
        coin=coin,
        side=side,
        entry=_fnum(entry),
        shares=_fnum(shares),
        cost=_fnum(cost),
        kelly_tier=kelly_tier,
        kelly_size=_fnum(kelly_size),
        phase=phase,
        **extra,
    )


def log_resolved(
    trade_id: Optional[str],
    coin: str,
    side: str,
    window_start: int,
    won: bool,
    cost: float,
    payout: float,
    pnl: float,
    phase: Optional[str] = None,
    resolution_source: Optional[str] = None,
    **extra: Any,
) -> None:
    """Window closed, outcome known."""
    log(
        "RESOLVED",
        trade_id=trade_id,
        coin=coin,
        side=side,
        window_start=_inum(window_start),
        won=bool(won),
        cost=_fnum(cost),
        payout=_fnum(payout),
        pnl=_fnum(pnl),
        phase=phase,
        resolution_source=resolution_source,
        **extra,
    )


# -- numeric coercion ----------------------------------------------------------

def _fnum(v):
    if v is None:
        return None
    try:
        return float(v)
    except Exception:
        return None


def _inum(v):
    if v is None:
        return None
    try:
        return int(v)
    except Exception:
        return None
