"""
ONE single RTDS connection attempt, logs the result, exits. Designed to be run
hourly by cron so we detect recovery WITHOUT hammering the rate limiter.

Writes a one-line status to logs/rtds_health.log. On the first SUCCESS it also
writes data/rtds_recovered.flag so the operator (or bot) knows it's safe to
re-enable CHAINLINK_WS_ENABLED.
"""
import json
import os
import ssl
import threading
import time
from datetime import datetime, timezone

import websocket

WS = "wss://ws-live-data.polymarket.com"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")
SUB = json.dumps({
    "action": "subscribe",
    "subscriptions": [{
        "topic": "crypto_prices_chainlink", "type": "*", "filters": "",
    }],
})

BASE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(BASE, "logs", "rtds_health.log")
FLAG = os.path.join(BASE, "data", "rtds_recovered.flag")

state = {"opened": False, "price": None, "err": None}


def on_open(ws):
    state["opened"] = True
    ws.send(SUB)


def on_message(ws, m):
    try:
        d = json.loads(m)
        if d.get("topic") == "crypto_prices_chainlink":
            p = d.get("payload") or {}
            if p.get("symbol") and float(p.get("value", 0) or 0) > 0:
                state["price"] = (p["symbol"], p["value"])
                ws.close()
    except Exception:
        pass


def on_error(ws, e):
    state["err"] = str(e).split("-+-+-")[0].strip()[:60]


def main():
    ws = websocket.WebSocketApp(
        WS, on_open=on_open, on_message=on_message, on_error=on_error,
        header={"User-Agent": UA, "Origin": "https://polymarket.com"},
    )
    t = threading.Thread(
        target=lambda: ws.run_forever(ping_interval=8, ping_timeout=6,
                                      sslopt={"cert_reqs": ssl.CERT_NONE}),
        daemon=True,
    )
    t.start()
    deadline = time.time() + 12
    while time.time() < deadline and state["price"] is None:
        time.sleep(0.5)
    try:
        ws.close()
    except Exception:
        pass

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    if state["price"]:
        line = f"{ts} | RECOVERED | {state['price'][0]}={state['price'][1]}\n"
        try:
            with open(FLAG, "w") as f:
                f.write(line)
        except Exception:
            pass
    elif state["opened"]:
        line = f"{ts} | CONNECTED_NO_DATA (opened but no price in 12s)\n"
    else:
        line = f"{ts} | STILL_BLOCKED | {state['err'] or 'no data / timeout'}\n"

    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as f:
        f.write(line)
    print(line.strip())


if __name__ == "__main__":
    main()
