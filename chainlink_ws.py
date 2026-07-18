"""
Polymarket RTDS — Chainlink crypto prices (resolution oracle for 15m Up/Down).
"""
import json
import os
import random
import threading
import time
from typing import Dict, Optional

from loguru import logger

_RTDS_URL = "wss://ws-live-data.polymarket.com"
_COIN_SYMBOL = {
    "BTC": "btc/usd",
    "ETH": "eth/usd",
    "SOL": "sol/usd",
    "XRP": "xrp/usd",
}

_latest: Dict[str, float] = {}
_updated: Dict[str, float] = {}
# jun12: rolling RTDS tick buffer per coin — RTDS is the real-time Chainlink
# stream (the settlement feed family). Momentum computed from it beats 2.5s
# polls of the deviation-gated on-chain aggregator (~5bp quantized, heartbeat
# lag). ~40min at 1 tick/s.
_ticks: Dict[str, list] = {}
# v1.55/v1.57: denser buffer; RTDS is still sparse (~1/15s) so get_ticks merges on-chain
_TICK_MAX = int(os.getenv("CHAINLINK_WS_TICK_MAX", "5400"))
# Min seconds between stored RTDS samples (was 1.0). Lower = denser when RTDS chatters.
_TICK_MIN_DT = float(os.getenv("CHAINLINK_WS_TICK_MIN_DT", "0.25"))
_lock = threading.Lock()
_connected = False
_thread: Optional[threading.Thread] = None
_last_data_ts: float = 0.0       # last time we received a real price
_last_err_429: bool = False      # was the most recent failure a rate-limit?
_merge_onchain = os.getenv("CHAINLINK_MERGE_ONCHAIN", "on").lower() in ("1", "true", "yes", "on")


def _coin_from_symbol(sym: str) -> Optional[str]:
    s = (sym or "").lower()
    for coin, cs in _COIN_SYMBOL.items():
        if cs == s:
            return coin
    return None


def _on_message(ws, message):
    global _connected, _last_data_ts
    try:
        data = json.loads(message)
        if data.get("topic") != "crypto_prices_chainlink":
            return
        payload = data.get("payload") or {}
        sym = payload.get("symbol", "")
        val = float(payload.get("value", 0) or 0)
        if val <= 0:
            return
        coin = _coin_from_symbol(sym)
        if coin:
            _connected = True
            now = time.time()
            _last_data_ts = now
            with _lock:
                _latest[coin] = val
                _updated[coin] = now
                buf = _ticks.setdefault(coin, [])
                # Keep sample if price moved OR enough time passed (v1.57: 0.25s min dt)
                if (not buf or buf[-1][1] != val
                        or (now - buf[-1][0]) >= _TICK_MIN_DT):
                    buf.append((now, val))
                    if len(buf) > _TICK_MAX:
                        del buf[: len(buf) - _TICK_MAX]
    except Exception:
        pass


def _on_error(ws, e):
    global _last_err_429
    msg = str(e)
    _last_err_429 = "429" in msg or "Too Many Requests" in msg
    logger.debug(f"[CHAINLINK-WS] error: {msg[:80]}")


def _on_open_factory(subs):
    def _on_open(ws):
        ws.send(subs)

        def _pinger():
            # RTDS docs: send app-level "PING" every 5s to stay alive.
            while getattr(ws, "keep_running", False):
                try:
                    ws.send("PING")
                except Exception:
                    break
                time.sleep(5)

        threading.Thread(target=_pinger, daemon=True,
                         name="chainlink-ping").start()
    return _on_open


def _run():
    global _last_err_429
    import websocket

    subs = json.dumps({
        "action": "subscribe",
        "subscriptions": [{
            "topic": "crypto_prices_chainlink",
            "type": "*",
            "filters": "",
        }],
    })
    _on_open = _on_open_factory(subs)

    # Exponential backoff with jitter. A fixed 2s retry turned a transient
    # Polymarket blip into a sustained 429 ban (Jun 10 2026 incident). Now:
    #   • backoff doubles each failure: base → cap
    #   • 429 (rate-limit) starts at a much longer floor to let the ban expire
    #   • reset to base only after a SUSTAINED good connection (got data)
    base = float(os.getenv("CHAINLINK_BACKOFF_BASE", "3"))
    cap = float(os.getenv("CHAINLINK_BACKOFF_CAP", "120"))
    rl_floor = float(os.getenv("CHAINLINK_RATELIMIT_FLOOR", "30"))
    good_secs = float(os.getenv("CHAINLINK_GOOD_RESET_SEC", "20"))
    delay = base

    while True:
        _last_err_429 = False
        conn_start = time.time()
        try:
            ws = websocket.WebSocketApp(
                _RTDS_URL,
                on_message=_on_message,
                on_error=_on_error,
                on_close=lambda ws, *a: logger.debug("[CHAINLINK-WS] closed"),
                on_open=_on_open,
                header={
                    "User-Agent": os.getenv(
                        "CHAINLINK_WS_UA",
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36",
                    ),
                    "Origin": "https://polymarket.com",
                },
            )
            ws.run_forever(ping_interval=8, ping_timeout=6)
        except Exception as e:
            logger.debug(f"[CHAINLINK-WS] reconnect: {str(e)[:80]}")

        # If we held a healthy connection (received data recently and stayed up
        # a while), reset backoff to base. Otherwise grow it.
        held = time.time() - conn_start
        got_recent = (time.time() - _last_data_ts) < good_secs
        if held >= good_secs and got_recent:
            delay = base
        else:
            delay = min(cap, max(delay * 2.0, rl_floor if _last_err_429 else base))

        # jitter ±25% to avoid synchronized retry storms
        sleep_for = delay * (0.75 + 0.5 * random.random())
        if _last_err_429:
            logger.warning(
                f"[CHAINLINK-WS] rate-limited (429) — backing off {sleep_for:.0f}s"
            )
        time.sleep(sleep_for)


def start():
    global _thread
    if _thread and _thread.is_alive():
        return
    _thread = threading.Thread(target=_run, daemon=True, name="chainlink-ws")
    _thread.start()
    logger.info("[CHAINLINK-WS] RTDS subscriber started")


def get_price(coin: str, max_age: Optional[float] = None) -> Optional[float]:
    """Latest RTDS price, or None when STALE (default 30s) so callers fall
    back to on-chain/Binance instead of computing dist_pct off a frozen
    level. CHAINLINK_WS_MAX_AGE=0 disables the check (old behavior)."""
    c = coin.upper()
    if max_age is None:
        max_age = float(os.getenv("CHAINLINK_WS_MAX_AGE", "30"))
    with _lock:
        p = _latest.get(c)
        ts = _updated.get(c, 0.0)
    if not p or p <= 0:
        return None
    if max_age > 0 and ts > 0 and (time.time() - ts) > max_age:
        return None
    return p


def get_ticks(coin: str, seconds: float = 300.0, merge_onchain: Optional[bool] = None) -> list:
    """(ts, price) Chainlink-family ticks for the last `seconds`, oldest first.

    v1.57: merge RTDS stream + Polygon on-chain aggregator polls when enabled.
    On-chain is same oracle family (not Binance). On-chain RPC is direct (no proxy)
    by design; CLOB route depends on env (direct native IP on Ireland since Jul 2026).
    """
    cutoff = time.time() - seconds
    c = coin.upper()
    with _lock:
        buf = list(_ticks.get(c, []))
    out = [(ts, p) for ts, p in buf if ts >= cutoff]
    do_merge = _merge_onchain if merge_onchain is None else merge_onchain
    if do_merge:
        try:
            import chainlink_onchain as _oc
            for ts, p in _oc.tick_history(c, seconds):
                if ts >= cutoff and p and p > 0:
                    out.append((ts, p))
        except Exception:
            pass
        if out:
            out.sort(key=lambda x: x[0])
            # Collapse near-duplicates within 0.2s (same sample from both sources)
            merged = [out[0]]
            for ts, p in out[1:]:
                if ts - merged[-1][0] < 0.2 and abs(p - merged[-1][1]) / max(p, 1e-12) < 1e-8:
                    merged[-1] = (ts, p)  # prefer later sample
                else:
                    merged.append((ts, p))
            out = merged
    return out


def get_realized_vol(coin: str, lookback_sec: int = 180) -> float:
    """Per-second log-return vol from CL-family ticks (no Binance)."""
    import math
    ticks = get_ticks(coin, lookback_sec + 30)
    if len(ticks) < 10:
        return 0.0
    total_var = total_dt = 0.0
    for i in range(1, len(ticks)):
        t0, p0 = ticks[i - 1]
        t1, p1 = ticks[i]
        dt = t1 - t0
        if dt <= 0 or p0 <= 0:
            continue
        lr = math.log(p1 / p0)
        total_var += lr * lr
        total_dt += dt
    if total_dt <= 0:
        return 0.0
    return math.sqrt(total_var / total_dt)


def tick_count(coin: str, seconds: float = 120.0) -> int:
    return len(get_ticks(coin, seconds))


def tick_density_report(coins=("ETH", "SOL", "BTC"), seconds: float = 120.0) -> str:
    """Human line for heartbeats: how dense is CL path data."""
    parts = []
    for c in coins:
        n = tick_count(c, seconds)
        parts.append(f"{c}={n}/{int(seconds)}s")
    return " ".join(parts)


def is_connected() -> bool:
    return _connected and bool(_latest)
