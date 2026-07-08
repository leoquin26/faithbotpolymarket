"""
V11 Binance Price Feed — aggTrade WebSocket (tick-level, sub-100ms latency).

Key upgrades from V10:
- Uses aggTrade stream (every trade, not sampled) instead of @trade
- Tick buffer expanded to 1200 for deeper history
- EWMA volatility computed inline for instant access
- REST fallback preserved for geo-blocked regions
"""

import json
import math
import time
import threading
from typing import Dict, Optional, List, Tuple
from loguru import logger

import config

_WS_URL = "wss://stream.binance.us:9443/ws"
_REST_URL = config.BINANCE_API

_latest_prices: Dict[str, float] = {}
_price_lock = threading.Lock()
_tick_history: Dict[str, List[Tuple[float, float]]] = {}
_MAX_TICKS = 1200
# order flow: every aggTrade's (ts, quantity, is_aggressive_buy) — buy vs sell PRESSURE,
# a real-time volume-direction signal (leads price). Captured from the same message, no
# extra latency. Only from the WS aggTrade stream (REST fallback has no per-trade volume).
_flow_history: Dict[str, List[Tuple[float, float, bool]]] = {}
_MAX_FLOW = 6000
# top-of-book size imbalance from @bookTicker (updates on EVERY book change — high-frequency and
# reliable even when trades are sparse). coin -> (bid_px, bid_qty, ask_px, ask_qty, ts). Resting
# bid-vs-ask SIZE imbalance is a short-term directional-pressure signal that leads the price.
_book: Dict[str, Tuple[float, float, float, float, float]] = {}
_ws_connected = False
_ws_thread: Optional[threading.Thread] = None
_rest_thread: Optional[threading.Thread] = None
_ws_gave_up = False


def _on_message(ws, message):
    global _ws_connected
    _ws_connected = True
    try:
        data = json.loads(message)
        symbol = data.get("s", "").upper()
        coin = None
        for c, sym in config.SYMBOLS.items():
            if sym == symbol:
                coin = c
                break
        if not coin:
            return
        # @bookTicker message: best bid/ask + their SIZES (fields b/B/a/A, no 'p' price). Store
        # top-of-book imbalance. Fully isolated + returns early so it can't disturb tick/flow.
        if "B" in data and "A" in data and "b" in data:
            try:
                bpx = float(data.get("b", 0)); bq = float(data.get("B", 0))
                apx = float(data.get("a", 0)); aq = float(data.get("A", 0))
                if bq > 0 and aq > 0:
                    with _price_lock:
                        _book[coin] = (bpx, bq, apx, aq, time.time())
            except Exception:
                pass
            return
        # @aggTrade message: price + volume/flow (existing path, unchanged)
        price = float(data.get("p", 0))
        ts = data.get("T", 0) / 1000.0 if data.get("T") else time.time()
        if price <= 0:
            return
        _store_tick(coin, price, ts)
        # order flow: m = "is buyer the maker?" → True means the AGGRESSOR was a seller
        # (sell trade); False means an aggressive buyer (buy trade). Two field reads, no delay.
        qty = float(data.get("q", 0) or 0)
        if qty > 0:
            _store_flow(coin, qty, not data.get("m", False), ts)
    except Exception:
        pass


def _store_tick(coin: str, price: float, ts: float = None):
    if ts is None:
        ts = time.time()
    with _price_lock:
        _latest_prices[coin] = price
        if coin not in _tick_history:
            _tick_history[coin] = []
        _tick_history[coin].append((ts, price))
        if len(_tick_history[coin]) > _MAX_TICKS:
            _tick_history[coin] = _tick_history[coin][-_MAX_TICKS:]


def get_book_imbalance(coin: str, max_age: float = 15.0) -> Optional[float]:
    """Top-of-book size imbalance from @bookTicker: (bid_qty - ask_qty)/(bid_qty + ask_qty) in
    [-1,+1]. Positive = more resting BID size (buy-side pressure) = short-term bullish lean.
    None if no fresh book (older than max_age). A leading microstructure signal — resting book
    pressure tends to precede the price move that settles the market."""
    with _price_lock:
        b = _book.get(coin)
    if not b:
        return None
    bpx, bq, apx, aq, ts = b
    if time.time() - ts > max_age or (bq + aq) <= 0:
        return None
    return (bq - aq) / (bq + aq)


def _store_flow(coin: str, qty: float, is_buy: bool, ts: float = None):
    if ts is None:
        ts = time.time()
    with _price_lock:
        buf = _flow_history.setdefault(coin, [])
        buf.append((ts, qty, is_buy))
        if len(buf) > _MAX_FLOW:
            del buf[:-_MAX_FLOW]


def get_order_flow(coin: str, seconds: int = 60) -> Optional[float]:
    """Real-time buy/sell PRESSURE over the last `seconds`: (buy_vol - sell_vol) / total_vol,
    in [-1, +1]. Positive = net aggressive BUYING (bullish lean), negative = net selling.
    None if no trades in the window (e.g. REST-fallback mode). Volume leads price, so this is
    a faster directional read than price drift."""
    cutoff = time.time() - seconds
    with _price_lock:
        buf = _flow_history.get(coin, [])
        buy = sell = 0.0
        for t, q, is_buy in reversed(buf):
            if t < cutoff:
                break
            if is_buy:
                buy += q
            else:
                sell += q
    tot = buy + sell
    return (buy - sell) / tot if tot > 0 else None


def _on_error(ws, error):
    global _ws_connected
    _ws_connected = False
    logger.debug(f"[WS] Binance error: {error}")


def _on_close(ws, close_status_code, close_msg):
    global _ws_connected
    _ws_connected = False
    logger.warning("[WS] Binance connection closed")


def _run_ws():
    global _ws_gave_up
    import websocket
    failures = 0
    while not _ws_gave_up:
        try:
            # aggTrade = every trade (tick + flow); bookTicker = best bid/ask + sizes (book imbalance)
            streams = []
            for sym in config.SYMBOLS.values():
                streams.append(f"{sym.lower()}@aggTrade")
                streams.append(f"{sym.lower()}@bookTicker")
            combined = "/".join(streams)
            url = f"{_WS_URL}/{combined}"
            ws = websocket.WebSocketApp(
                url,
                on_message=_on_message,
                on_error=_on_error,
                on_close=_on_close,
            )
            # Jun-2: bypass Tor SOCKS for Binance hosts (proven via standalone test).
            ws.run_forever(
                ping_interval=20,
                ping_timeout=10,
                proxy_type=None,
                http_proxy_host=None,
                http_proxy_port=None,
                http_no_proxy=[
                    'stream.binance.us', 'api.binance.us',
                    'stream.binance.com', 'fstream.binance.com',
                    'ws-api.binance.com',
                ],
            )
            failures += 1
            if failures >= 3:
                _ws_gave_up = True
                logger.warning("[WS] WebSocket blocked after 3 attempts — using REST fallback")
                return
        except Exception:
            failures += 1
            if failures >= 3:
                _ws_gave_up = True
                logger.warning("[WS] WebSocket failed — using REST fallback")
                return
        time.sleep(2)


def _run_rest_poller():
    """Poll Binance ticker/price every 0.5s for reliable price snapshots."""
    import httpx
    client = httpx.Client(timeout=3.0)
    logger.info("[REST] Binance price poller started (0.5s interval)")
    while True:
        now = time.time()
        for coin, symbol in config.SYMBOLS.items():
            try:
                r = client.get(f"{_REST_URL}/ticker/price?symbol={symbol}")
                if r.status_code == 200:
                    data = r.json()
                    price = float(data.get("price", 0))
                    if price > 0:
                        _store_tick(coin, price, ts=now)
            except Exception:
                pass
        time.sleep(0.5)


def start():
    global _ws_thread, _rest_thread
    if _ws_thread is None or not _ws_thread.is_alive():
        _ws_thread = threading.Thread(target=_run_ws, daemon=True, name="binance-ws")
        _ws_thread.start()

    time.sleep(5)

    if _rest_thread is None or not _rest_thread.is_alive():
        _rest_thread = threading.Thread(target=_run_rest_poller, daemon=True, name="binance-rest")
        _rest_thread.start()


def get_price(coin: str) -> Optional[float]:
    with _price_lock:
        return _latest_prices.get(coin)


def get_tick_history(coin: str, seconds: int = 300) -> List[Tuple[float, float]]:
    cutoff = time.time() - seconds
    with _price_lock:
        ticks = _tick_history.get(coin, [])
        return [(t, p) for t, p in ticks if t > cutoff]


def get_realized_vol(coin: str, lookback_sec: int = 180) -> float:
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
    return _ws_connected or bool(_latest_prices)
