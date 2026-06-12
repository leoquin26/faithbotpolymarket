import os
import re

# ---- Patch run_bot.py to honor a CHAINLINK_WS_ENABLED gate ----
rb = "run_bot.py"
rs = open(rb).read()
old_gate = "    if _CHAINLINK_OK and _chainlink_ws is not None:\n        try:\n            _chainlink_ws.start()"
new_gate = (
    "    if (_CHAINLINK_OK and _chainlink_ws is not None\n"
    "            and os.getenv(\"CHAINLINK_WS_ENABLED\", \"1\") == \"1\"):\n"
    "        try:\n"
    "            _chainlink_ws.start()"
)
if "CHAINLINK_WS_ENABLED" in rs:
    print("run_bot.py already has gate")
elif old_gate in rs:
    rs = rs.replace(old_gate, new_gate)
    open(rb, "w").write(rs)
    print("patched run_bot.py gate OK")
else:
    print("WARN: run_bot.py gate block not found - skipping (manual review)")

f = "chainlink_ws.py"
s = open(f).read()

old = (
'            ws = websocket.WebSocketApp(\n'
'                _RTDS_URL,\n'
'                on_message=_on_message,\n'
'                on_error=_on_error,\n'
'                on_close=lambda ws, *a: logger.debug("[CHAINLINK-WS] closed"),\n'
'                on_open=lambda ws: ws.send(subs),\n'
'            )\n'
'            ws.run_forever(ping_interval=15, ping_timeout=10)\n'
)
new = (
'            ws = websocket.WebSocketApp(\n'
'                _RTDS_URL,\n'
'                on_message=_on_message,\n'
'                on_error=_on_error,\n'
'                on_close=lambda ws, *a: logger.debug("[CHAINLINK-WS] closed"),\n'
'                on_open=lambda ws: ws.send(subs),\n'
'                header={\n'
'                    "User-Agent": os.getenv(\n'
'                        "CHAINLINK_WS_UA",\n'
'                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "\n'
'                        "AppleWebKit/537.36 (KHTML, like Gecko) "\n'
'                        "Chrome/124.0.0.0 Safari/537.36",\n'
'                    ),\n'
'                    "Origin": "https://polymarket.com",\n'
'                },\n'
'            )\n'
'            ws.run_forever(ping_interval=15, ping_timeout=10)\n'
)

if new in s:
    print("already patched")
elif old in s:
    s = s.replace(old, new)
    open(f, "w").write(s)
    print("patched headers OK")
else:
    raise SystemExit("handshake block not found - manual review needed")
