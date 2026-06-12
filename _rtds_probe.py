"""
Standalone RTDS connection probe. Single attempt, no retry loop, proper PING.
Reports exactly what happens on ONE clean connection so we can diagnose the 429.
"""
import json
import ssl
import sys
import threading
import time

import websocket  # websocket-client

URL = "wss://ws-live-data.polymarket.com"
SUB = json.dumps({
    "action": "subscribe",
    "subscriptions": [{
        "topic": "crypto_prices_chainlink",
        "type": "*",
        "filters": "",
    }],
})

state = {"opened": False, "msgs": 0, "first_price": None, "err": None, "closed": None}


def on_open(ws):
    state["opened"] = True
    print(f"[{time.strftime('%H:%M:%S')}] OPEN -> sending subscribe")
    ws.send(SUB)

    def pinger():
        # Docs: send PING every 5s to keep alive
        while ws.keep_running:
            try:
                ws.send("PING")
            except Exception:
                break
            time.sleep(5)
    threading.Thread(target=pinger, daemon=True).start()


def on_message(ws, message):
    state["msgs"] += 1
    if state["msgs"] <= 3:
        print(f"[{time.strftime('%H:%M:%S')}] MSG#{state['msgs']}: {message[:140]}")
    try:
        d = json.loads(message)
        if d.get("topic") == "crypto_prices_chainlink":
            p = d.get("payload") or {}
            if p.get("symbol") and float(p.get("value", 0) or 0) > 0:
                if state["first_price"] is None:
                    state["first_price"] = (p["symbol"], p["value"])
                    print(f"[{time.strftime('%H:%M:%S')}] FIRST PRICE: {p['symbol']}={p['value']}")
    except Exception:
        pass


def on_error(ws, e):
    state["err"] = str(e)[:120]
    print(f"[{time.strftime('%H:%M:%S')}] ERROR: {state['err']}")


def on_close(ws, code, msg):
    state["closed"] = (code, msg)
    print(f"[{time.strftime('%H:%M:%S')}] CLOSE code={code} msg={msg}")


def on_ping(ws, data):
    print(f"[{time.strftime('%H:%M:%S')}] <- server PING")


def on_pong(ws, data):
    print(f"[{time.strftime('%H:%M:%S')}] <- server PONG")


ua = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")

print(f"[{time.strftime('%H:%M:%S')}] connecting (single attempt)...")
ws = websocket.WebSocketApp(
    URL,
    on_open=on_open,
    on_message=on_message,
    on_error=on_error,
    on_close=on_close,
    on_ping=on_ping,
    on_pong=on_pong,
    header={"User-Agent": ua, "Origin": "https://polymarket.com"},
)
run_secs = int(sys.argv[1]) if len(sys.argv) > 1 else 20
t = threading.Thread(
    target=lambda: ws.run_forever(ping_interval=5, ping_timeout=4,
                                  sslopt={"cert_reqs": ssl.CERT_NONE}),
    daemon=True,
)
t.start()
time.sleep(run_secs)
ws.close()
time.sleep(1)
print("---- RESULT ----")
print("opened:", state["opened"], "| msgs:", state["msgs"],
      "| first_price:", state["first_price"],
      "| err:", state["err"], "| closed:", state["closed"])
