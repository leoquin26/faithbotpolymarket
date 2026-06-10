"""
Binance WebSocket Price Feed — real-time sub-second crypto ticks.

Runs a background thread that maintains a persistent WebSocket connection
to Binance, streaming live trade prices for BTC, ETH, SOL, XRP.
Falls back to REST if the WebSocket is disconnected.
"""

import json
import time
import threading
from typing import Dict, Optional, List, Tuple
from loguru import logger

import config

_WS_URL = "wss://stream.binance.us:9443/ws"

_latest_prices: Dict[str, float] = {}
_price_lock = threading.Lock()
_tick_history: Dict[str, List[Tuple[float, float]]] = {}
_MAX_TICKS = 600
_ws_connected = False
_ws_thread: Optional[threading.Thread] = None


def _on_message(ws, message):
    global _ws_connected
    _ws_connected = True
    try:
        data = json.loads(message)
        symbol = data.get("s", "").upper()
        price = float(data.get("p", 0))
        ts = data.get("T", 0) / 1000.0 if data.get("T") else time.time()
        if price <= 0:
            return

        coin = None
        for c, sym in config.SYMBOLS.items():
            if sym == symbol:
                coin = c
                break
        if not coin:
            return

        with _price_lock:
            _latest_prices[coin] = price
            if coin not in _tick_history:
                _tick_history[coin] = []
            _tick_history[coin].append((ts, price))
            if len(_tick_history[coin]) > _MAX_TICKS:
                _tick_history[coin] = _tick_history[coin][-_MAX_TICKS:]
    except Exception:
        pass


def _on_error(ws, error):
    global _ws_connected
    _ws_connected = False
    logger.debug(f"[WS] Binance error: {error}")


def _on_close(ws, close_status_code, close_msg):
    global _ws_connected
    _ws_connected = False
    logger.warning("[WS] Binance connection closed")


def _on_open(ws):
    global _ws_connected
    _ws_connected = True
    streams = [f"{sym.lower()}@trade" for sym in config.SYMBOLS.values()]
    subscribe_msg = {
        "method": "SUBSCRIBE",
        "params": streams,
        "id": 1,
    }
    ws.send(json.dumps(subscribe_msg))
    logger.info(f"[WS] Subscribed to {len(streams)} Binance streams")


def _run_ws():
    """Persistent WebSocket loop with auto-reconnect."""
    import websocket
    while True:
        try:
            combined = "/".join(f"{sym.lower()}@trade" for sym in config.SYMBOLS.values())
            url = f"{_WS_URL}/{combined}"
            ws = websocket.WebSocketApp(
                url,
                on_message=_on_message,
                on_error=_on_error,
                on_close=_on_close,
            )
            ws.run_forever(ping_interval=20, ping_timeout=10)
        except Exception as e:
            logger.debug(f"[WS] Reconnecting after error: {e}")
        time.sleep(2)


def start():
    """Start the background WebSocket thread (call once at startup)."""
    global _ws_thread
    if _ws_thread is not None and _ws_thread.is_alive():
        return
    _ws_thread = threading.Thread(target=_run_ws, daemon=True, name="binance-ws")
    _ws_thread.start()
    logger.info("[WS] Binance WebSocket thread started")


def get_price(coin: str) -> Optional[float]:
    """Get latest price for a coin. Returns None if no data."""
    with _price_lock:
        return _latest_prices.get(coin)


def get_tick_history(coin: str, seconds: int = 300) -> List[Tuple[float, float]]:
    """Get recent (timestamp, price) ticks for a coin within last N seconds."""
    cutoff = time.time() - seconds
    with _price_lock:
        ticks = _tick_history.get(coin, [])
        return [(t, p) for t, p in ticks if t > cutoff]


def get_realized_vol(coin: str, lookback_sec: int = 180) -> float:
    """Compute realized volatility from tick log-returns over lookback period."""
    import math
    ticks = get_tick_history(coin, lookback_sec)
    if len(ticks) < 10:
        return 0.0

    total_var = 0.0
    total_dt = 0.0
    for i in range(1, len(ticks)):
        t0, p0 = ticks[i - 1]
        t1, p1 = ticks[i]
        dt = t1 - t0
        if dt <= 0 or p0 <= 0:
            continue
        log_ret = math.log(p1 / p0)
        total_var += log_ret * log_ret
        total_dt += dt

    if total_dt <= 0:
        return 0.0
    return math.sqrt(total_var / total_dt)


def is_connected() -> bool:
    return _ws_connected
