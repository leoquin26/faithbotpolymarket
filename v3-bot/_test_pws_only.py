"""Does importing just polymarket_ws break it?"""
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

print("\n>>> import polymarket_ws")
import polymarket_ws
print("  result:", ws_test())

# Now also call get_singleton (starts the actual WS thread)
print("\n>>> polymarket_ws.get_singleton() (starts WS thread)")
pws = polymarket_ws.get_singleton()
print(f"  connected? {pws.is_connected()}")
import time
time.sleep(3)  # let it connect
print(f"  connected? {pws.is_connected()}")
print("  result:", ws_test())
