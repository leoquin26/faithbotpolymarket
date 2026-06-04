"""ByBit V5 Public WebSocket — secondary price source for consensus.

Provides the same surface as `binance_ws`:
  - get_price(coin) -> Optional[float]
  - get_tick_history(coin, seconds) -> List[(ts, price)]
  - is_connected() -> bool
  - start() -> None

Subscribes to `publicTrade.<SYMBOL>` for BTC/ETH/SOL/XRP USDT spot.
Runs in a daemon thread.  Uses websocket-client (already installed for
polymarket_ws.py).

Bypasses the Tor proxy (similar to polymarket_ws): WebSockets don't tunnel
cleanly through SOCKS.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from typing import Dict, List, Optional, Tuple

import websocket
from loguru import logger

# Bypass Tor proxy for ByBit WS (set in environment at module load).
os.environ["NO_PROXY"] = os.environ.get("NO_PROXY", "") + ",stream.bybit.com"

# ---------------------------------------------------------------------------
WS_URL = "wss://stream.bybit.com/v5/public/spot"

SYMBOL_MAP = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
    "XRP": "XRPUSDT",
}
REV_SYMBOL_MAP = {v: k for k, v in SYMBOL_MAP.items()}

# ---------------------------------------------------------------------------
_lock = threading.RLock()
_latest_prices: Dict[str, float] = {}
_tick_history: Dict[str, List[Tuple[float, float]]] = {coin: [] for coin in SYMBOL_MAP}
_ws_connected = False
_ws_app: Optional[websocket.WebSocketApp] = None
_thread_started = False

MAX_TICKS = 600   # ~10 min at 1s granularity

# ---------------------------------------------------------------------------
def _on_message(_, msg: str) -> None:
    global _ws_connected
    try:
        data = json.loads(msg)
        if data.get("topic", "").startswith("publicTrade."):
            sym = data["topic"].split(".", 1)[1]
            coin = REV_SYMBOL_MAP.get(sym)
            if not coin:
                return
            trades = data.get("data") or []
            now = time.time()
            with _lock:
                for t in trades:
                    p = float(t.get("p") or 0)
                    if p <= 0:
                        continue
                    _latest_prices[coin] = p
                    hist = _tick_history.setdefault(coin, [])
                    hist.append((now, p))
                    if len(hist) > MAX_TICKS:
                        del hist[: len(hist) - MAX_TICKS]
        elif data.get("op") == "pong":
            pass
        elif data.get("success") is True and data.get("op") == "subscribe":
            logger.info(f"[BYBIT-WS] subscribed: {data.get('ret_msg','ok')}")
    except Exception as e:  # noqa
        logger.debug(f"[BYBIT-WS] parse error: {e}")


def _on_open(ws) -> None:
    global _ws_connected
    _ws_connected = True
    logger.info("[BYBIT-WS] connected")
    args = [f"publicTrade.{sym}" for sym in SYMBOL_MAP.values()]
    ws.send(json.dumps({"op": "subscribe", "args": args}))


def _on_error(_, err) -> None:
    logger.warning(f"[BYBIT-WS] error: {err}")


def _on_close(_, code, msg) -> None:
    global _ws_connected
    _ws_connected = False
    logger.info(f"[BYBIT-WS] closed ({code}): {msg}")


def _ping_loop() -> None:
    while True:
        try:
            if _ws_app and _ws_connected:
                _ws_app.send(json.dumps({"op": "ping"}))
        except Exception:
            pass
        time.sleep(20)


def _run_forever() -> None:
    """Run Bybit WS using http_no_proxy to bypass Tor SOCKS.

    Jun-2 PM: simplified — http_no_proxy kwarg is correctly honored by
    websocket-client (verified via standalone test that opened successfully
    even with ALL_PROXY/HTTPS_PROXY/HTTP_PROXY all set to socks5h://Tor).
    """
    global _ws_app
    while True:
        try:
            _ws_app = websocket.WebSocketApp(
                WS_URL,
                on_open=_on_open,
                on_message=_on_message,
                on_error=_on_error,
                on_close=_on_close,
            )
            _ws_app.run_forever(
                ping_interval=25,
                ping_timeout=10,
                proxy_type=None,
                http_proxy_host=None,
                http_proxy_port=None,
                http_no_proxy=['stream.bybit.com', 'api.bybit.com'],
            )
        except Exception as e:  # noqa
            logger.warning(f"[BYBIT-WS] run_forever crashed: {e}")
        time.sleep(5)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def start() -> None:
    global _thread_started
    if _thread_started:
        return
    _thread_started = True
    t = threading.Thread(target=_run_forever, daemon=True, name="bybit-ws")
    t.start()
    p = threading.Thread(target=_ping_loop, daemon=True, name="bybit-ws-ping")
    p.start()
    logger.info("[BYBIT-WS] start() launched")


def get_price(coin: str) -> Optional[float]:
    with _lock:
        return _latest_prices.get(coin)


def get_tick_history(coin: str, seconds: int = 300) -> List[Tuple[float, float]]:
    cutoff = time.time() - seconds
    with _lock:
        ticks = _tick_history.get(coin, [])
        return [(t, p) for t, p in ticks if t > cutoff]


def is_connected() -> bool:
    return _ws_connected or bool(_latest_prices)


def get_short_roc_bps(coin: str, window_sec: float = 10.0) -> Optional[float]:
    """Signed basis-points return over the last `window_sec` seconds."""
    ticks = get_tick_history(coin, int(window_sec) + 2)
    if len(ticks) < 4:
        return None
    t0, p0 = ticks[0]
    t1, p1 = ticks[-1]
    if p0 <= 0 or (t1 - t0) < window_sec * 0.5:
        return None
    return (p1 - p0) / p0 * 10000.0
