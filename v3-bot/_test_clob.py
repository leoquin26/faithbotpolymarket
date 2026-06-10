"""Test: import just py_clob_client_v2 and see if WS breaks."""
import sys
sys.path.insert(0, "/home/ubuntu/v3-bot")
import force_tor
import os, threading

def ws_test():
    import websocket
    events = []
    def _open(ws): events.append("open"); ws.close()
    def _err(ws, err): events.append(("err", str(err)))
    ws = websocket.WebSocketApp(
        "wss://stream.binance.us:9443/ws/btcusdt@aggTrade",
        on_open=_open, on_error=_err, on_close=lambda *a: events.append("close"),
    )
    t = threading.Thread(target=lambda: ws.run_forever(
        ping_interval=20,
        proxy_type=None, http_proxy_host=None, http_proxy_port=None,
        http_no_proxy=['stream.binance.us'],
    ), daemon=True)
    t.start(); t.join(timeout=6)
    return any(e == "open" for e in events), events[:2]

print("baseline:", ws_test())

# Now incrementally import
print("\n>>> from py_clob_client_v2.client import ClobClient")
from py_clob_client_v2.client import ClobClient
print("  result:", ws_test())

print("\n>>> from py_clob_client_v2.clob_types import OrderArgs")
from py_clob_client_v2.clob_types import OrderArgs
print("  result:", ws_test())

print("\n>>> from py_clob_client_v2.order_builder.constants import BUY")
from py_clob_client_v2.order_builder.constants import BUY
print("  result:", ws_test())

# Maybe httpx Client instantiation matters
print("\n>>> Creating httpx.Client with proxy=None")
import httpx
_cli = httpx.Client(timeout=5, follow_redirects=True, proxy=None)
print("  result:", ws_test())

# What about http_no_proxy with the actual httpx Client open?
print("\n>>> While httpx Client open, test")
print("  result:", ws_test())
