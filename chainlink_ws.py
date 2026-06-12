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
_TICK_MAX = int(os.getenv("CHAINLINK_WS_TICK_MAX", "2400"))
_lock = threading.Lock()
_connected = False
_thread: Optional[threading.Thread] = None
_last_data_ts: float = 0.0       # last time we received a real price
_last_err_429: bool = False      # was the most recent failure a rate-limit?


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
                # dedupe equal prices but keep a >=1s cadence so ROC spans are real
                if not buf or buf[-1][1] != val or (now - buf[-1][0]) >= 1.0:
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


def get_ticks(coin: str, seconds: float = 300.0) -> list:
    """(ts, price) RTDS ticks for the last `seconds`, oldest first."""
    cutoff = time.time() - seconds
    with _lock:
        buf = list(_ticks.get(coin.upper(), []))
    return [(ts, p) for ts, p in buf if ts >= cutoff]


def is_connected() -> bool:
    return _connected and bool(_latest)
