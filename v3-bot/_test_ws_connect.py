"""Mimic exactly what bybit_ws does to find the real connection bug.

This version imports force_tor FIRST (like the bot does) to get exact env.
"""
import sys
sys.path.insert(0, "/home/ubuntu/v3-bot")
import force_tor  # sets up exact same env the bot uses

import os
import time
import threading

print("== env after force_tor import ==")
for k in sorted(os.environ):
    if "proxy" in k.lower():
        print(f"  {k}={os.environ[k]}")

import websocket

print(f"websocket version: {websocket.__version__}")
print(f"env HTTPS_PROXY={os.environ.get('HTTPS_PROXY')}")
print(f"env NO_PROXY={os.environ.get('NO_PROXY')}")

events = []

def _on_open(ws):
    events.append(("open", time.time()))
    print("  >> OPEN")

def _on_message(ws, msg):
    events.append(("msg", time.time()))
    print(f"  >> MSG: {msg[:80]}")
    ws.close()

def _on_error(ws, err):
    events.append(("err", str(err)))
    print(f"  >> ERR: {err}")

def _on_close(ws, code, msg):
    events.append(("close", code, msg))
    print(f"  >> CLOSE: {code} {msg}")


# Test 1: Bybit with http_no_proxy (what we tried)
print("\n=== Test 1: bybit with http_no_proxy=['stream.bybit.com', '*.bybit.com'] ===")
ws = websocket.WebSocketApp(
    "wss://stream.bybit.com/v5/public/spot",
    on_open=_on_open, on_message=_on_message, on_error=_on_error, on_close=_on_close,
)
t = threading.Thread(target=lambda: ws.run_forever(
    proxy_type=None,
    http_proxy_host=None,
    http_proxy_port=None,
    http_no_proxy=['stream.bybit.com', '*.bybit.com'],
    ping_interval=10,
), daemon=True)
t.start()
t.join(timeout=8)
print(f"  events: {events[-3:]}")
events.clear()

# Test 2: Bybit with proxy_type='socks5h' (explicit Tor)
print("\n=== Test 2: bybit with proxy_type='socks5h' explicit + http_no_proxy ===")
ws = websocket.WebSocketApp(
    "wss://stream.bybit.com/v5/public/spot",
    on_open=_on_open, on_message=_on_message, on_error=_on_error, on_close=_on_close,
)
t = threading.Thread(target=lambda: ws.run_forever(
    proxy_type='socks5h',
    http_proxy_host='127.0.0.1',
    http_proxy_port=9050,
    http_no_proxy=['stream.bybit.com'],
    ping_interval=10,
), daemon=True)
t.start()
t.join(timeout=8)
print(f"  events: {events[-3:]}")
events.clear()

# Test 3: Binance with same explicit socks5h pattern
print("\n=== Test 3: binance with proxy_type='socks5h' + http_no_proxy ===")
ws = websocket.WebSocketApp(
    "wss://stream.binance.us:9443/ws/btcusdt@aggTrade",
    on_open=_on_open, on_message=_on_message, on_error=_on_error, on_close=_on_close,
)
t = threading.Thread(target=lambda: ws.run_forever(
    proxy_type='socks5h',
    http_proxy_host='127.0.0.1',
    http_proxy_port=9050,
    http_no_proxy=['stream.binance.us'],
    ping_interval=10,
), daemon=True)
t.start()
t.join(timeout=8)
print(f"  events: {events[-3:]}")
