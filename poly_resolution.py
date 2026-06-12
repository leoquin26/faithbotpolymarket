"""
Polymarket Gamma market data: strike cache + official resolution outcome.
"""
import ast
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import httpx
from loguru import logger

import config

_STRIKE_CACHE = Path(__file__).resolve().parent / "data" / "strike_cache.json"
_http = httpx.Client(timeout=httpx.Timeout(8.0, connect=3.0))


def _load_strike_cache() -> dict:
    try:
        if _STRIKE_CACHE.exists():
            return json.loads(_STRIKE_CACHE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_strike_cache(data: dict):
    _STRIKE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    _STRIKE_CACHE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def fetch_market_by_slug(slug: str) -> Optional[dict]:
    try:
        r = _http.get(f"{config.GAMMA_API}/markets/slug/{slug}", timeout=8.0)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logger.debug(f"[GAMMA] market slug fetch failed {slug}: {e}")
    return None


def _parse_json_list(val) -> list:
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        try:
            return ast.literal_eval(val)
        except Exception:
            return json.loads(val)
    return []


def event_start_unix(market: dict) -> int:
    raw = market.get("eventStartTime") or market.get("startTime")
    if not raw:
        return 0
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return int(dt.timestamp())
    except Exception:
        return 0


def resolved_winner(market: dict) -> Optional[str]:
    """Return UP or DOWN when Gamma shows resolved outcome, else None."""
    if not market.get("closed"):
        return None
    status = str(market.get("umaResolutionStatus") or "").lower()
    if status and status not in ("resolved", "confirmed"):
        if not market.get("automaticallyResolved"):
            return None
    outcomes = _parse_json_list(market.get("outcomes"))
    prices = _parse_json_list(market.get("outcomePrices"))
    if len(outcomes) != 2 or len(prices) != 2:
        return None
    for label, price in zip(outcomes, prices):
        try:
            if float(price) >= 0.99:
                lab = str(label).strip().upper()
                if lab in ("UP", "DOWN"):
                    return lab
                if lab.startswith("UP"):
                    return "UP"
                if lab.startswith("DOWN"):
                    return "DOWN"
        except Exception:
            continue
    return None


def market_slug(coin: str, window_start: int, timeframe: str = "15m") -> str:
    return f"{coin.lower()}-updown-{timeframe}-{window_start}"


def get_strike(
    coin: str,
    slug: str,
    event_start_unix_ts: int,
    timeframe: str = "15m",
) -> Tuple[float, str]:
    """
    Price-to-beat aligned with Polymarket (Chainlink at window open).
    Returns (strike_price, source_tag).
    """
    cache = _load_strike_cache()
    hit = cache.get(slug)
    if hit and float(hit.get("strike", 0) or 0) > 0:
        return float(hit["strike"]), str(hit.get("source", "cache"))

    strike = 0.0
    source = "unknown"
    window_age = max(0.0, time.time() - float(event_start_unix_ts or 0))

    # PRIMARY: Chainlink window-open snapshot — Polymarket resolves Chainlink
    # spot vs strike, and the predictor reads Chainlink for live spot, so the
    # strike MUST come from the same feed or dist_pct carries a cross-feed basis
    # that flips near-strike direction (audit C1, Jun 10 2026).
    _cl_max_age = float(__import__("os").getenv("STRIKE_CHAINLINK_MAX_AGE", "20"))
    if window_age <= _cl_max_age:
        try:
            import chainlink_ws
            cl = chainlink_ws.get_price(coin)
            if cl and cl > 0:
                strike = cl
                source = "chainlink_window_open"
        except Exception:
            pass

    # TIER 2: on-chain Chainlink aggregator (same oracle family as the Data
    # Stream Polymarket settles on). Used when the RTDS WS is unavailable so
    # we still avoid the cross-feed Binance basis. Only at/near window open.
    if strike <= 0 and window_age <= _cl_max_age:
        try:
            import chainlink_onchain
            co = chainlink_onchain.get_price(coin)
            if co and co > 0:
                strike = co
                source = "chainlink_onchain"
        except Exception:
            pass

    # FALLBACK: 1m candle OPEN at window start (only if no Chainlink snapshot —
    # e.g. bot started mid-window or Chainlink WS was down at open).
    if strike <= 0:
        from market_data import get_threshold_from_binance
        b = get_threshold_from_binance(coin, event_start_unix_ts, timeframe)
        if b and b > 0:
            strike = b
            source = "binance_kline_open"
            if window_age > _cl_max_age:
                logger.warning(
                    f"[STRIKE] {coin} {slug}: mid-window cache miss "
                    f"(age={window_age:.0f}s) — Binance kline open as strike "
                    f"(may carry small Chainlink basis)"
                )

    if strike > 0:
        cache[slug] = {
            "coin": coin,
            "strike": strike,
            "source": source,
            "event_start": event_start_unix_ts,
            "ts": int(time.time()),
        }
        _save_strike_cache(cache)
        logger.info(f"[STRIKE] {coin} {slug}: ${strike:,.2f} ({source})")
    return strike, source


def resolve_position(coin: str, side: str, window_start: int, timeframe: str = "15m") -> Optional[dict]:
    """
    Official Polymarket outcome when market is closed; else None (caller may fallback).
    Returns dict: winner UP|DOWN, source, slug.
    """
    slug = market_slug(coin, window_start, timeframe)
    market = fetch_market_by_slug(slug)
    if not market:
        return None
    winner = resolved_winner(market)
    if not winner:
        return None
    return {
        "winner": winner,
        "slug": slug,
        "source": "gamma",
        "closed": True,
    }
