"""
EXHAUST AUDIT: For a sample of ABSTAIN events, query Polymarket Gamma
to see if the blocked direction would have won. Uses the same slug format
as the live bot's resolver (run_bot.py: '{coin}-updown-15m-{ws}').
"""
import json, time, random, sys, ast
from collections import defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/home/ubuntu/v3-bot")
import force_tor
import order_manager  # for _get_direct_http
import requests

PATH = "/home/ubuntu/v3-bot/data/trade_events.jsonl"
DAYS = 7
SAMPLE_SIZE = 200

cutoff = (datetime.now(timezone.utc) - timedelta(days=DAYS)).timestamp()

trades = {}
with open(PATH) as f:
    for line in f:
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("ts_epoch", 0) < cutoff:
            continue
        tid = e.get("trade_id")
        if not tid:
            continue
        ev = e.get("event", "?")
        rec = trades.setdefault(tid, {})
        if ev == "SIGNAL":
            rec.update({
                "coin": e.get("coin"), "side": e.get("side"),
                "entry": e.get("entry"), "prob": e.get("prob"),
                "edge": e.get("edge"), "trend": e.get("trend_score"),
                "ws": e.get("window_start"), "tok": e.get("token_id"),
                "conf": e.get("confidence"), "ts": e.get("ts_epoch"),
            })
        elif ev == "EXHAUST":
            rec["action"] = e.get("action")
            rec["score"] = e.get("score")
        elif ev == "FIRED":
            rec["fired"] = True
        elif ev == "RESOLVED":
            rec["actual_outcome"] = e.get("outcome")

abstains = [r for r in trades.values()
            if r.get("action") == "ABSTAIN" and r.get("ws") and r.get("tok")
            and r.get("coin") and r.get("side") and r.get("ts")]
unique = {}
for r in abstains:
    k = (r["coin"], r["side"], r["ws"])
    if k not in unique or r["ts"] < unique[k]["ts"]:
        unique[k] = r

now_ts = datetime.now(timezone.utc).timestamp()
unique_list = [r for r in unique.values() if r["ws"] + 900 + 60 < now_ts]
print(f"Total ABSTAIN events: {len(abstains)}")
print(f"Unique (coin,side,window) ABSTAIN with resolved windows: {len(unique_list)}")

random.seed(42)
sample = random.sample(unique_list, min(SAMPLE_SIZE, len(unique_list)))
print(f"Sampling {len(sample)} for resolution...")
print("")

# Re-use the bot's outbound HTTP session (Tor proxy already configured)
_om = order_manager.OrderManager.__new__(order_manager.OrderManager)
http = _om._get_direct_http() if hasattr(_om, "_get_direct_http") else requests
del _om

# Try to make a generic http session if helper not available
try:
    http = order_manager._get_direct_http()  # if module-level helper
except Exception:
    pass
if http is requests:
    s = requests.Session()
    s.proxies = {"http": "socks5h://127.0.0.1:9050", "https": "socks5h://127.0.0.1:9050"}
    http = s

# Coin slug variants — bot uses lowercase short ticker
COIN_TO_SLUG = {"BTC": "btc", "ETH": "eth", "SOL": "sol", "XRP": "xrp"}

def query_won(coin, side, window_start):
    slug_coin = COIN_TO_SLUG.get(coin.upper(), coin.lower())
    slug = f"{slug_coin}-updown-15m-{window_start}"
    try:
        r = http.get(f"https://gamma-api.polymarket.com/events?slug={slug}", timeout=8)
        if r.status_code != 200:
            return None
        d = r.json() or []
        if not (d and isinstance(d, list) and d[0].get("markets")):
            return None
        m = d[0]["markets"][0]
        op = m.get("outcomePrices", [])
        if isinstance(op, str): op = ast.literal_eval(op)
        outs = m.get("outcomes", [])
        if isinstance(outs, str): outs = ast.literal_eval(outs)
        target = "Up" if str(side).upper() == "UP" else "Down"
        if target in outs and len(op) == 2:
            idx = outs.index(target)
            price = float(op[idx])
            if price >= 0.98: return True
            if price <= 0.02: return False
        return None
    except Exception:
        return None

resolved = []
not_resolved = 0
for i, r in enumerate(sample):
    if i and i % 20 == 0:
        print(f"  ... {i}/{len(sample)}  resolved={len(resolved)} pending={not_resolved}")
    won = query_won(r["coin"], r["side"], r["ws"])
    if won is None:
        not_resolved += 1
        continue
    resolved.append({**r, "won": won})
    time.sleep(0.05)

print("")
print(f"Resolved: {len(resolved)} / {len(sample)}  ({not_resolved} unresolved)")

if not resolved:
    print("No resolutions retrieved.")
    sys.exit(0)

wins = sum(1 for r in resolved if r["won"])
hit_rate = wins / len(resolved)
print("")
print("=" * 60)
print("HYPOTHETICAL PERFORMANCE OF EXHAUST-BLOCKED SIGNALS")
print("=" * 60)
print(f"  Trades:    {len(resolved)}")
print(f"  Wins:      {wins}")
print(f"  Losses:    {len(resolved)-wins}")
print(f"  Hit rate:  {hit_rate*100:.1f}%")
print("")

# Slice by exhaust score
print("=== Slice by exhaust score band ===")
buckets = defaultdict(lambda: [0, 0])
for r in resolved:
    s = r.get("score") or 0
    if s < 0.55: b = "0.50-0.55"
    elif s < 0.60: b = "0.55-0.60"
    elif s < 0.65: b = "0.60-0.65"
    elif s < 0.70: b = "0.65-0.70 (just-below-FLIP)"
    else: b = ">=0.70 (FLIP zone)"
    buckets[b][0] += 1
    if r["won"]: buckets[b][1] += 1
for b in ["0.50-0.55","0.55-0.60","0.60-0.65","0.65-0.70 (just-below-FLIP)",">=0.70 (FLIP zone)"]:
    n, w = buckets[b]
    if n: print(f"  {b:30s}  n={n:3d}  WR={(w/n)*100:.1f}%")

print("")
print("=== Slice by coin ===")
buckets = defaultdict(lambda: [0, 0])
for r in resolved:
    buckets[r["coin"]][0] += 1
    if r["won"]: buckets[r["coin"]][1] += 1
for c, (n, w) in sorted(buckets.items(), key=lambda x: -x[1][0]):
    print(f"  {c:5s}  n={n:3d}  WR={(w/n)*100:.1f}%")

print("")
print("=== Slice by entry band ===")
buckets = defaultdict(lambda: [0, 0])
for r in resolved:
    e = r.get("entry") or 0
    if e < 0.50: b = "<50c"
    elif e < 0.55: b = "50-54c"
    elif e < 0.60: b = "55-59c"
    elif e < 0.63: b = "60-62c"
    elif e < 0.70: b = "63-69c"
    else: b = ">=70c"
    buckets[b][0] += 1
    if r["won"]: buckets[b][1] += 1
for b in ["<50c","50-54c","55-59c","60-62c","63-69c",">=70c"]:
    n, w = buckets[b]
    if n: print(f"  {b:7s}  n={n:3d}  WR={(w/n)*100:.1f}%")

print("")
print("=== Slice by hour (Lima, UTC-5) ===")
buckets = defaultdict(lambda: [0, 0])
for r in resolved:
    ts = r.get("ts") or 0
    h_lima = (datetime.fromtimestamp(ts, tz=timezone.utc) - timedelta(hours=5)).hour
    buckets[h_lima][0] += 1
    if r["won"]: buckets[h_lima][1] += 1
for h in sorted(buckets.keys()):
    n, w = buckets[h]
    print(f"  {h:02d}:00  n={n:3d}  WR={(w/n)*100:.1f}%")

print("")
print("=== Slice by signal probability ===")
buckets = defaultdict(lambda: [0, 0])
for r in resolved:
    p = r.get("prob") or 0
    if p < 0.65: b = "<65% (weak)"
    elif p < 0.75: b = "65-75% (mid)"
    elif p < 0.85: b = "75-85% (strong)"
    else: b = ">=85% (very strong)"
    buckets[b][0] += 1
    if r["won"]: buckets[b][1] += 1
for b in ["<65% (weak)","65-75% (mid)","75-85% (strong)",">=85% (very strong)"]:
    n, w = buckets[b]
    if n: print(f"  {b:25s}  n={n:3d}  WR={(w/n)*100:.1f}%")

print("")
print("=== Slice by trend_score ===")
buckets = defaultdict(lambda: [0, 0])
for r in resolved:
    t = r.get("trend") or 0
    if abs(t) < 0.3: b = "low (|t|<0.3)"
    elif abs(t) < 0.6: b = "mid (0.3-0.6)"
    elif abs(t) < 0.9: b = "high (0.6-0.9)"
    else: b = "very high (>=0.9)"
    buckets[b][0] += 1
    if r["won"]: buckets[b][1] += 1
for b in ["low (|t|<0.3)","mid (0.3-0.6)","high (0.6-0.9)","very high (>=0.9)"]:
    n, w = buckets[b]
    if n: print(f"  {b:25s}  n={n:3d}  WR={(w/n)*100:.1f}%")

print("")
print("=== Hypothetical PnL ($8/trade, payout=(1/entry-1)*size, no fees) ===")
total_pnl = 0
for r in resolved:
    size = 8.0
    e = r.get("entry") or 0.6
    if r["won"]: total_pnl += size * (1.0/e - 1.0)
    else: total_pnl -= size
print(f"  Net PnL on ${len(resolved)*8} turnover: ${total_pnl:+.2f}")
print(f"  Avg per trade: ${total_pnl/len(resolved):+.2f}")
print("")
print(f"=== Compare to actual fired+resolved trades same period ===")
fired = [r for r in trades.values()
         if r.get("fired") and r.get("actual_outcome") in ("WIN", "LOSS")]
if fired:
    actual_wr = sum(1 for r in fired if r["actual_outcome"] == "WIN") / len(fired)
    print(f"  n={len(fired)}, WR={actual_wr*100:.1f}%")

print("")
print("INTERPRETATION:")
print(f"  - blocked WR >> fired WR  -> EXHAUST is killing winners (BAD)")
print(f"  - blocked WR << fired WR  -> EXHAUST is filtering losers (GOOD)")
print(f"  - blocked WR ~ fired WR   -> EXHAUST is noise, no signal")
