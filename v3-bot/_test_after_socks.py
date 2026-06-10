"""Test: load socks proxy modules and see what changes."""
import sys
sys.path.insert(0, "/home/ubuntu/v3-bot")
import force_tor
import os, threading

def ws_test_open():
    import websocket
    events = []
    def _on_open(ws): events.append("open"); ws.close()
    def _on_error(ws, err): events.append(("err", str(err)))
    ws = websocket.WebSocketApp(
        "wss://stream.binance.us:9443/ws/btcusdt@aggTrade",
        on_open=_on_open, on_error=_on_error, on_close=lambda *a: events.append("close"),
    )
    t = threading.Thread(target=lambda: ws.run_forever(
        ping_interval=20,
        proxy_type=None, http_proxy_host=None, http_proxy_port=None,
        http_no_proxy=['stream.binance.us'],
    ), daemon=True)
    t.start(); t.join(timeout=6)
    return any(e == "open" for e in events)


print("=== Test BEFORE socks import ===")
print(f"  ws opens: {ws_test_open()}")

# Now import just the suspect modules
print("\n=== Importing httpcore SOCKS modules ===")
import httpcore._async.socks_proxy
import httpcore._sync.socks_proxy
import socksio

print(f"  ws opens after import: {ws_test_open()}")

# Try explicit empty-string trick
print("\n=== With http_proxy_host='' (empty string) ===")
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
    proxy_type="",  # try empty
    http_proxy_host="",  # try empty
    http_proxy_port=0,
    http_no_proxy=['stream.binance.us'],
), daemon=True)
t.start(); t.join(timeout=6)
print(f"  events: {events[:2]}")

# Try clearing proxy env vars right before run_forever
print("\n=== Cleared env right before run_forever ===")
_saved = {}
for k in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
    if k in os.environ:
        _saved[k] = os.environ.pop(k)
events = []
ws = websocket.WebSocketApp(
    "wss://stream.binance.us:9443/ws/btcusdt@aggTrade",
    on_open=_open, on_error=_err, on_close=lambda *a: events.append("close"),
)
t = threading.Thread(target=lambda: ws.run_forever(
    ping_interval=20,
), daemon=True)
t.start(); t.join(timeout=6)
print(f"  events: {events[:2]}")
# restore
for k, v in _saved.items():
    os.environ[k] = v
