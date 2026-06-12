"""Test EXACTLY what binance_ws does in the bot."""
import sys
sys.path.insert(0, "/home/ubuntu/v3-bot")
import force_tor

import os
import time
import threading
import websocket
import config

print("config.SYMBOLS =", config.SYMBOLS)

combined = "/".join(f"{sym.lower()}@aggTrade" for sym in config.SYMBOLS.values())
url = f"wss://stream.binance.us:9443/ws/{combined}"
print("URL =", url)

events = []
def _on_open(ws):
    events.append("open")
    print("  >> OPEN")

def _on_message(ws, msg):
    events.append("msg")
    print(f"  >> MSG: {msg[:120]}")
    if len(events) >= 5:
        ws.close()

def _on_error(ws, err):
    events.append(("err", str(err)))
    print(f"  >> ERR: {err}")

def _on_close(ws, code, msg):
    events.append("close")
    print(f"  >> CLOSE: {code} {msg}")

ws = websocket.WebSocketApp(url, on_open=_on_open, on_message=_on_message,
                            on_error=_on_error, on_close=_on_close)

print("\n=== Running run_forever with http_no_proxy (binance exact) ===")
def go():
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

t = threading.Thread(target=go, daemon=True)
t.start()
t.join(timeout=15)

print(f"\n  total events: {len(events)}")
print(f"  first 5: {events[:5]}")
