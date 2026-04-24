"""
Background resolver: joins FIRED events with their Gamma API outcomes.

Runs as a daemon thread started from run_bot.py (when ENABLE_ANALYTICS=true).
For every FIRED event without a matching RESOLVED event, it polls the Gamma
market once the window has closed, and emits a RESOLVED event.

Idempotent: safe to restart; picks up where it left off by scanning the events
file for the most recent RESOLVED record per (trade_id).

Rate limiting: polls every 60s; for each unresolved fire that's past window
close + 30s, queries Gamma once per poll cycle (max 4 calls / cycle).
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Optional

import requests

from .event_logger import EVENTS_PATH, is_enabled, log_resolved


POLL_INTERVAL_S = 60
_STOP = threading.Event()
_THREAD: Optional[threading.Thread] = None


def start_background(interval_s: int = POLL_INTERVAL_S) -> Optional[threading.Thread]:
    """Spawn the resolver thread once. Returns the Thread or None if disabled."""
    global _THREAD
    if not is_enabled():
        return None
    if _THREAD is not None and _THREAD.is_alive():
        return _THREAD
    _STOP.clear()
    _THREAD = threading.Thread(
        target=_loop,
        args=(interval_s,),
        name="analytics-resolver",
        daemon=True,
    )
    _THREAD.start()
    return _THREAD


def stop() -> None:
    _STOP.set()


# ------------------------------------------------------------------ main loop

def _loop(interval_s: int) -> None:
    while not _STOP.is_set():
        try:
            _tick()
        except Exception as e:
            _warn(f"resolver tick error: {e}")
        # interruptible sleep
        for _ in range(interval_s):
            if _STOP.is_set():
                return
            time.sleep(1)


def _tick() -> None:
    """
    Load the events file, find FIRED events older than (window_close + 30s)
    that don't have a RESOLVED counterpart yet, and resolve up to 4 of them
    per cycle to stay under Gamma rate limits.
    """
    events = _load_events()
    pending = _unresolved_fires(events)
    if not pending:
        return

    now_epoch = int(time.time())
    resolvable = [
        e for e in pending
        if (e.get("window_start") or 0) + 900 + 30 < now_epoch
    ]
    if not resolvable:
        return

    # Sort oldest-first, cap at 4 per tick.
    resolvable.sort(key=lambda e: e.get("window_start") or 0)
    for e in resolvable[:4]:
        _resolve_one(e)


# ----------------------------------------------------------------- helpers

def _load_events() -> list[dict]:
    try:
        out = []
        if not os.path.exists(EVENTS_PATH):
            return out
        with open(EVENTS_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
        return out
    except Exception as e:
        _warn(f"load events failed: {e}")
        return []


def _unresolved_fires(events: list[dict]) -> list[dict]:
    resolved_ids = set()
    # A (trade_id) OR (coin, window_start) pair identifies a resolution.
    resolved_pairs = set()
    for e in events:
        if e.get("event") == "RESOLVED":
            tid = e.get("trade_id")
            if tid:
                resolved_ids.add(tid)
            c, ws = e.get("coin"), e.get("window_start")
            if c and ws is not None:
                resolved_pairs.add((c, int(ws)))

    out = []
    for e in events:
        if e.get("event") != "FIRED":
            continue
        tid = e.get("trade_id")
        c, ws = e.get("coin"), e.get("window_start")
        if tid and tid in resolved_ids:
            continue
        if c and ws is not None and (c, int(ws)) in resolved_pairs:
            continue
        out.append(e)
    return out


def _resolve_one(fired: dict) -> None:
    coin = fired.get("coin")
    side = fired.get("side")
    window_start = fired.get("window_start")
    token_id = fired.get("token_id")
    cost = float(fired.get("cost") or 0.0)
    shares = float(fired.get("shares") or 0.0)

    outcome = _query_gamma(coin, side, window_start, token_id)
    if outcome is None:
        return  # not resolved yet; try next tick
    won, source = outcome

    payout = shares if won else 0.0
    pnl = payout - cost

    log_resolved(
        trade_id=fired.get("trade_id"),
        coin=coin,
        side=side,
        window_start=int(window_start),
        won=won,
        cost=cost,
        payout=payout,
        pnl=pnl,
        phase=fired.get("phase"),
        resolution_source=source,
    )


def _query_gamma(
    coin: Optional[str],
    side: Optional[str],
    window_start: Optional[int],
    token_id: Optional[str],
) -> Optional[tuple[bool, str]]:
    """
    Query Gamma for the 15-min market that matches (coin, window_start).

    Returns (won, source_description) if the market resolved, else None.

    `won` is derived by matching `side` (UP/DOWN) against `outcomes`/`outcomePrices`,
    the same robust logic as the live resolver (apr23 fix).
    """
    if not coin or not side or not window_start:
        return None

    try:
        slug_guess = _slug_for(coin, window_start)
        if not slug_guess:
            return None
        r = requests.get(
            "https://gamma-api.polymarket.com/markets",
            params={"slug": slug_guess},
            timeout=8,
        )
        if not r.ok:
            return None
        data = r.json() or []
        if not data:
            return None
        m = data[0] if isinstance(data, list) else data

        import ast as _ast
        op = m.get("outcomePrices", [])
        if isinstance(op, str):
            op = _ast.literal_eval(op)
        outs = m.get("outcomes", [])
        if isinstance(outs, str):
            outs = _ast.literal_eval(outs)

        target_label = "Up" if str(side).upper() == "UP" else "Down"
        idx = outs.index(target_label) if target_label in outs else -1
        if idx < 0 or len(op) < 2:
            return None
        price = float(op[idx])
        if price >= 0.98:
            return True, "gamma"
        if price <= 0.02:
            return False, "gamma"
        return None
    except Exception as e:
        _warn(f"gamma query failed for {coin} @ {window_start}: {e}")
        return None


def _slug_for(coin: str, window_start: int) -> Optional[str]:
    """
    Best-effort slug reconstruction. Polymarket's 15-min market slugs look like:
      bitcoin-up-or-down-15m-1745432100
    We query by slug; Gamma returns 0 or 1 match.
    """
    coin_to_slug_base = {
        "BTC": "bitcoin-up-or-down-15m",
        "ETH": "ethereum-up-or-down-15m",
        "SOL": "solana-up-or-down-15m",
        "XRP": "xrp-up-or-down-15m",
    }
    base = coin_to_slug_base.get(str(coin).upper())
    if not base:
        return None
    return f"{base}-{int(window_start)}"


def _warn(msg: str) -> None:
    try:
        import sys
        print(f"[ANALYTICS resolver] {msg}", file=sys.stderr, flush=True)
    except Exception:
        pass
