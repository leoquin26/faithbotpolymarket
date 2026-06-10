"""
Polymarket RTDS — Chainlink crypto prices (resolution oracle for 15m Up/Down).
"""
import json
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
_lock = threading.Lock()
_connected = False
_thread: Optional[threading.Thread] = None


def _coin_from_symbol(sym: str) -> Optional[str]:
    s = (sym or "").lower()
    for coin, cs in _COIN_SYMBOL.items():
        if cs == s:
            return coin
    return None


def _on_message(ws, message):
    global _connected
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
            with _lock:
                _latest[coin] = val
    except Exception:
        pass


def _run():
    import websocket

    subs = json.dumps({
        "action": "subscribe",
        "subscriptions": [{
            "topic": "crypto_prices_chainlink",
            "type": "*",
            "filters": "",
        }],
    })
    while True:
        try:
            ws = websocket.WebSocketApp(
                _RTDS_URL,
                on_message=_on_message,
                on_error=lambda ws, e: logger.debug(f"[CHAINLINK-WS] error: {e}"),
                on_close=lambda ws, *a: logger.warning("[CHAINLINK-WS] closed"),
                on_open=lambda ws: ws.send(subs),
            )
            ws.run_forever(ping_interval=5, ping_timeout=3)
        except Exception as e:
            logger.debug(f"[CHAINLINK-WS] reconnect: {e}")
        time.sleep(2)


def start():
    global _thread
    if _thread and _thread.is_alive():
        return
    _thread = threading.Thread(target=_run, daemon=True, name="chainlink-ws")
    _thread.start()
    logger.info("[CHAINLINK-WS] RTDS subscriber started")


def get_price(coin: str) -> Optional[float]:
    with _lock:
        p = _latest.get(coin.upper())
    return p if p and p > 0 else None


def is_connected() -> bool:
    return _connected and bool(_latest)
