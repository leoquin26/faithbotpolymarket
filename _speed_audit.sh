#!/bin/bash
cd /home/ubuntu/v3-bot

echo "==========================================================="
echo "  SPEED AUDIT — BOT $(date +%H:%M:%S)"
echo "==========================================================="

echo ""
echo "=== 1. SCAN INTERVAL ==="
grep -E "^SCAN_INTERVAL" config.py

echo ""
echo "=== 2. ROUND-TRIP TO BINANCE (5 samples) ==="
python3 <<'PYEOF'
import httpx, time, statistics
c = httpx.Client(timeout=3.0)
samples = []
for _ in range(5):
    t0 = time.perf_counter()
    r = c.get("https://api.binance.us/api/v3/ticker/price?symbol=BTCUSDT")
    samples.append((time.perf_counter() - t0)*1000)
print(f"  min={min(samples):.0f}ms  med={statistics.median(samples):.0f}ms  max={max(samples):.0f}ms")
print(f"  samples: {[round(s,0) for s in samples]}")
PYEOF

echo ""
echo "=== 3. ROUND-TRIP TO POLYMARKET CLOB BOOK (5 samples) ==="
python3 <<'PYEOF'
import httpx, time, statistics
# Use a known live token id (any one of today's markets)
TOKEN = "112946011929935833676574108412017063632257018180195910313839058817486925583989"
c = httpx.Client(timeout=5.0, follow_redirects=True)
samples = []
for _ in range(5):
    t0 = time.perf_counter()
    r = c.get(f"https://clob.polymarket.com/book?token_id={TOKEN}")
    samples.append((time.perf_counter() - t0)*1000)
    time.sleep(0.3)
print(f"  status={r.status_code}")
print(f"  min={min(samples):.0f}ms  med={statistics.median(samples):.0f}ms  max={max(samples):.0f}ms")
print(f"  samples: {[round(s,0) for s in samples]}")
PYEOF

echo ""
echo "=== 4. WS PRICE FRESHNESS (live bot memory) ==="
python3 <<'PYEOF'
import sys; sys.path.insert(0, "/home/ubuntu/v3-bot")
import binance_ws, time
binance_ws.start()
time.sleep(8)
print(f"  WS connected: {binance_ws.is_connected()}")
import config
for coin in config.SYMBOLS:
    p = binance_ws.get_price(coin)
    ticks = binance_ws.get_tick_history(coin, 60)
    age = time.time() - ticks[-1][0] if ticks else -1
    print(f"  {coin}: price={p}  ticks/60s={len(ticks)}  newest={age:.2f}s ago")
PYEOF

echo ""
echo "=== 5. END-TO-END FIRE LATENCY (today's APPROVED -> FILLED) ==="
python3 <<'PYEOF'
import re
from datetime import datetime
ts_re = re.compile(r"^(\d{2}:\d{2}:\d{2})")
events = []
with open("v3_bot.log") as f:
    for line in f.readlines()[-30000:]:
        m = ts_re.match(line)
        if not m: continue
        if "APPROVED" in line or "FILLED" in line:
            events.append((m.group(1), line.rstrip()))
# walk through pairs
pairs = []
last_approved = None
for ts, line in events:
    if "APPROVED" in line:
        last_approved = (ts, line)
    elif "FILLED" in line and last_approved:
        a_ts = last_approved[0]
        f_ts = ts
        a_dt = datetime.strptime(a_ts, "%H:%M:%S")
        f_dt = datetime.strptime(f_ts, "%H:%M:%S")
        delta = (f_dt - a_dt).total_seconds()
        if 0 <= delta < 60:
            pairs.append((a_ts, f_ts, delta, line.split("|")[1].strip() if "|" in line else line))
            last_approved = None
print(f"  pairs found: {len(pairs)}")
for ats, fts, d, what in pairs[-15:]:
    print(f"    {ats} APPROVED -> {fts} FILLED  ({d:.0f}s)  {what}")
PYEOF

echo ""
echo "=== 6. NUMBER OF CLOB BOOK CALLS PER MINUTE (sampled) ==="
# count how many [PRICE] (book hits) appear in last 5 min
python3 <<'PYEOF'
import re, time
from datetime import datetime
now = datetime.now()
cutoff_min = (now.hour * 60 + now.minute) - 5
def line_min(ts):
    h, m, s = ts.split(":")
    return int(h)*60 + int(m)
hits = 0
with open("v3_bot.log") as f:
    for line in f.readlines()[-30000:]:
        m = re.match(r"^(\d{2}:\d{2}:\d{2})", line)
        if not m: continue
        if line_min(m.group(1)) < cutoff_min: continue
        if "[PRICE]" in line or "CLOB ask" in line or "[PM ENTRY CAP]" in line:
            hits += 1
print(f"  CLOB-fetch markers in last ~5 min: {hits}")
print(f"  (each scan @ 3s = ~100 scans/5min; each candidate signal triggers 1 book fetch)")
PYEOF

echo ""
echo "=== 7. PROXY ROUTE FOR CLOB BOOK ==="
grep -nE "_get_direct_http|httpx.Client.*proxy" order_manager.py | head -5

echo ""
echo "=== 8. CPU/MEM ==="
ps -p $(pgrep -f "python3.*run_bot.py" | head -1) -o pid,%cpu,%mem,etime,rss,cmd 2>/dev/null

echo ""
echo "=== 9. ANALYTICS RESOLVER LATENCY (writes per minute) ==="
ls -la data/trade_events.jsonl 2>/dev/null
wc -l data/trade_events.jsonl 2>/dev/null
