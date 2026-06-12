"""
Test theory: Cloudflare 429 on WS upgrade because we never send the __cf_bm
cookie. Step 1: GET to obtain __cf_bm. Step 2: WS upgrade WITH that cookie.
"""
import json
import ssl
import threading
import time
import urllib.request
import http.cookiejar

import websocket

BASE = "https://ws-live-data.polymarket.com/"
WS = "wss://ws-live-data.polymarket.com"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")

# ---- Step 1: get the cloudflare cookie via a normal request ----
cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
req = urllib.request.Request(BASE, headers={
    "User-Agent": UA,
    "Origin": "https://polymarket.com",
    "Accept-Language": "en-US,en;q=0.9",
})
cookie_hdr = ""
try:
    opener.open(req, timeout=10)
except Exception as e:
    # 426 Upgrade Required is expected and still sets the cookie
    print(f"step1 GET raised (expected for 426): {str(e)[:60]}")

cookies = []
for c in cj:
    cookies.append(f"{c.name}={c.value}")
    print(f"  got cookie: {c.name}")
cookie_hdr = "; ".join(cookies)
print(f"step1 cookie header: {cookie_hdr[:60]}{'...' if len(cookie_hdr)>60 else ''}")

# ---- Step 2: WS upgrade WITH the cookie ----
SUB = json.dumps({
    "action": "subscribe",
    "subscriptions": [{
        "topic": "crypto_prices_chainlink", "type": "*", "filters": "",
    }],
})
state = {"opened": False, "msgs": 0, "first": None, "err": None}


def on_open(ws):
    state["opened"] = True
    print(f"[{time.strftime('%H:%M:%S')}] OPEN -> subscribe")
    ws.send(SUB)

    def ping():
        while getattr(ws, "keep_running", False):
            try:
                ws.send("PING")
            except Exception:
                break
            time.sleep(5)
    threading.Thread(target=ping, daemon=True).start()


def on_message(ws, m):
    state["msgs"] += 1
    if state["msgs"] <= 2:
        print(f"[{time.strftime('%H:%M:%S')}] MSG: {m[:120]}")
    try:
        d = json.loads(m)
        if d.get("topic") == "crypto_prices_chainlink":
            p = d.get("payload") or {}
            if p.get("symbol") and float(p.get("value", 0) or 0) > 0 and not state["first"]:
                state["first"] = (p["symbol"], p["value"])
                print(f"[{time.strftime('%H:%M:%S')}] FIRST PRICE {p['symbol']}={p['value']}")
    except Exception:
        pass


def on_error(ws, e):
    state["err"] = str(e)[:100]
    print(f"[{time.strftime('%H:%M:%S')}] ERROR: {state['err']}")


hdrs = {"User-Agent": UA, "Origin": "https://polymarket.com"}
if cookie_hdr:
    hdrs["Cookie"] = cookie_hdr

print(f"[{time.strftime('%H:%M:%S')}] WS upgrade WITH cookie...")
ws = websocket.WebSocketApp(WS, on_open=on_open, on_message=on_message,
                            on_error=on_error, header=hdrs)
threading.Thread(
    target=lambda: ws.run_forever(ping_interval=8, ping_timeout=6,
                                  sslopt={"cert_reqs": ssl.CERT_NONE}),
    daemon=True,
).start()
time.sleep(15)
ws.close()
time.sleep(1)
print("---- RESULT ----")
print("opened:", state["opened"], "| msgs:", state["msgs"],
      "| first:", state["first"], "| err:", state["err"])
