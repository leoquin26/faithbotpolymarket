"""CLOB API adapter for Dashboard v3.

Ground truth for trades, positions, and P&L.
Per-endpoint TTL cache so we don't hammer the CLOB API.
"""
from __future__ import annotations

import os
import time
import logging
import threading
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Any

logger = logging.getLogger("dash_v3.clob")

try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds
except Exception as e:  # pragma: no cover
    ClobClient = None  # type: ignore
    ApiCreds = None  # type: ignore
    logger.warning(f"py_clob_client not available: {e}")


# ─────────────────────────────────────────────────────────────────
# Client construction (cached singleton)
# ─────────────────────────────────────────────────────────────────
_client_lock = threading.Lock()
_client: Any | None = None


def get_client():
    global _client
    with _client_lock:
        if _client is not None:
            return _client
        if ClobClient is None:
            return None
        try:
            host = os.getenv("POLYMARKET_HOST") or "https://clob.polymarket.com"
            creds = ApiCreds(
                api_key=os.getenv("POLYMARKET_API_KEY"),
                api_secret=os.getenv("POLYMARKET_API_SECRET"),
                api_passphrase=os.getenv("POLYMARKET_PASSPHRASE"),
            )
            _client = ClobClient(
                host,
                key=os.getenv("POLYMARKET_PRIVATE_KEY"),
                chain_id=int(os.getenv("POLYMARKET_CHAIN_ID", "137")),
                creds=creds,
                signature_type=int(os.getenv("POLYMARKET_SIGNATURE_TYPE", "2")),
                funder=os.getenv("POLYMARKET_FUNDER_ADDRESS"),
            )
            logger.info("CLOB client initialized")
            return _client
        except Exception as e:
            logger.error(f"CLOB client init failed: {e}")
            return None


# ─────────────────────────────────────────────────────────────────
# TTL cache
# ─────────────────────────────────────────────────────────────────
class _TtlCache:
    def __init__(self):
        self._data: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str, ttl: float):
        with self._lock:
            item = self._data.get(key)
            if item and time.time() - item[0] < ttl:
                return item[1]
        return None

    def set(self, key: str, value: Any):
        with self._lock:
            self._data[key] = (time.time(), value)


_cache = _TtlCache()


# ─────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────
def get_all_trades(limit: int = 500, ttl: float = 20.0) -> list[dict]:
    """Return last N on-chain trades for this wallet.

    Each item is a dict. Important fields we rely on:
      - match_time or matchTime (unix seconds)
      - side ("BUY" / "SELL")
      - outcome ("Up" / "Down" / "Yes" / "No")
      - size (tokens)
      - price (decimal, e.g. 0.55 = 55c)
      - status ("CONFIRMED" when settled)
      - market / question / title (for display)
    """
    key = f"trades:{limit}"
    cached = _cache.get(key, ttl)
    if cached is not None:
        return cached

    client = get_client()
    if client is None:
        return []
    try:
        raw = client.get_trades() or []
        # Keep only CONFIRMED (real on-chain) trades.
        trades = []
        for t in raw:
            status = str(t.get("status", "")).upper()
            if status and status != "CONFIRMED":
                continue
            trades.append(t)
        trades.sort(
            key=lambda x: int(x.get("match_time") or x.get("matchTime") or 0),
            reverse=True,
        )
        trades = trades[:limit]
        _cache.set(key, trades)
        return trades
    except Exception as e:
        logger.error(f"get_trades failed: {e}")
        return []


def get_trades_since(since_ts: float) -> list[dict]:
    trades = get_all_trades(limit=500)
    return [
        t for t in trades
        if int(t.get("match_time") or t.get("matchTime") or 0) >= since_ts
    ]


def pnl_for_period(start_ts: float, end_ts: float | None = None) -> dict:
    """Compute realized P&L from CLOB trades in [start_ts, end_ts).

    A BUY spends USDC; the position resolves later as a Redeem (not
    in trades). Without the gamma outcome-resolution feed we cannot
    match a buy to a redeem here, so P&L is approximated as net USDC
    flow: sum(redeems) - sum(buys). For the dashboard we instead rely
    on the bot's [WIN]/[LOSS] log events which already know outcome.
    This helper just returns buy totals so we can show "gross risked".
    """
    end_ts = end_ts or time.time()
    trades = get_all_trades(limit=500)
    buys = 0.0
    sells = 0.0
    n_buys = 0
    n_sells = 0
    for t in trades:
        ts = int(t.get("match_time") or t.get("matchTime") or 0)
        if ts < start_ts or ts >= end_ts:
            continue
        side = str(t.get("side", "")).upper()
        size = float(t.get("size", 0) or 0)
        price = float(t.get("price", 0) or 0)
        notional = size * price
        if side == "BUY":
            buys += notional
            n_buys += 1
        elif side == "SELL":
            sells += notional
            n_sells += 1
    return {
        "risked": round(buys, 2),
        "sold": round(sells, 2),
        "n_buys": n_buys,
        "n_sells": n_sells,
    }


def get_active_positions(ttl: float = 10.0) -> list[dict]:
    """Return currently-open positions from the CLOB.

    py_clob_client exposes get_positions via the data-api proxy.
    Each position has: asset (token_id), size, avgPrice, market, outcome.
    """
    key = "positions"
    cached = _cache.get(key, ttl)
    if cached is not None:
        return cached
    client = get_client()
    if client is None:
        return []
    positions: list[dict] = []
    try:
        if hasattr(client, "get_positions"):
            positions = client.get_positions() or []  # type: ignore
        elif hasattr(client, "get_balances"):
            positions = client.get_balances() or []  # type: ignore
    except Exception as e:
        logger.debug(f"get_positions not available via CLOB: {e}")
    # Fallback: derive open positions from trades - not yet redeemed.
    if not positions:
        positions = _derive_open_positions_from_trades()
    _cache.set(key, positions)
    return positions


def _derive_open_positions_from_trades() -> list[dict]:
    """Derive approximate open positions from trades.

    Sums BUYs minus SELLs per token_id. Positions with residual > 0
    are still "open" until the market resolves and the user redeems.
    """
    trades = get_all_trades(limit=500, ttl=20.0)
    agg: dict[str, dict] = defaultdict(
        lambda: {"size": 0.0, "cost": 0.0, "market": "", "outcome": "", "last_ts": 0}
    )
    for t in trades:
        token = t.get("asset") or t.get("tokenId") or t.get("asset_id")
        if not token:
            continue
        side = str(t.get("side", "")).upper()
        size = float(t.get("size", 0) or 0)
        price = float(t.get("price", 0) or 0)
        if side == "BUY":
            agg[token]["size"] += size
            agg[token]["cost"] += size * price
        elif side == "SELL":
            agg[token]["size"] -= size
            agg[token]["cost"] -= size * price
        agg[token]["market"] = (
            t.get("market") or t.get("title") or t.get("question") or ""
        )
        agg[token]["outcome"] = t.get("outcome") or ""
        ts = int(t.get("match_time") or t.get("matchTime") or 0)
        if ts > agg[token]["last_ts"]:
            agg[token]["last_ts"] = ts
    out = []
    for token, v in agg.items():
        if v["size"] > 0.01:
            avg_price = v["cost"] / v["size"] if v["size"] > 0 else 0
            out.append({
                "asset": token,
                "size": round(v["size"], 4),
                "avg_price": round(avg_price, 4),
                "cost": round(v["cost"], 2),
                "market": v["market"],
                "outcome": v["outcome"],
                "last_ts": v["last_ts"],
            })
    out.sort(key=lambda x: x["last_ts"], reverse=True)
    return out
