import re, glob
from collections import defaultdict

days = defaultdict(lambda: defaultdict(int))
blocks = defaultdict(int)
for f in sorted(
    glob.glob("logs/bot_2026-04-2*.log")
    + glob.glob("logs/bot_2026-04-3*.log")
    + glob.glob("logs/bot_2026-05-0*.log")
):
    if "_5m_" in f:
        continue
    d = re.search(r"(\d{4}-\d{2}-\d{2})", f).group(1)
    try:
        with open(f, errors="ignore") as fh:
            for line in fh:
                m = re.search(r"\[FILLED\]\s+(BTC|ETH|SOL|XRP)", line)
                if m:
                    days[d][m.group(1)] += 1
                for tag in [
                    "CONSENSUS",
                    "EXHAUST BLOCK",
                    "FLIP GUARD",
                    "RECENT FLIP",
                    "TRAP BAND",
                    "PM COIN BLOCK",
                    "MORNING P1",
                    "MORNING P3",
                    "ETH UP BLOCK",
                    "EXPENSIVE",
                    "CHEAP",
                    "LATE WHIPSAW",
                    "WEAK TREND",
                ]:
                    if "[" + tag + "]" in line:
                        blocks[tag] += 1
                        break
    except Exception:
        pass

print("day         BTC ETH SOL XRP BTC+SOL total")
print("-" * 45)
totals = {"BTC": 0, "ETH": 0, "SOL": 0, "XRP": 0}
btc_sol_dist = []
for d in sorted(days):
    row = days[d]
    btc = row.get("BTC", 0)
    eth = row.get("ETH", 0)
    sol = row.get("SOL", 0)
    xrp = row.get("XRP", 0)
    bs = btc + sol
    t = btc + eth + sol + xrp
    btc_sol_dist.append(bs)
    totals["BTC"] += btc
    totals["ETH"] += eth
    totals["SOL"] += sol
    totals["XRP"] += xrp
    print("{}  {:>3} {:>3} {:>3} {:>3}   {:>5}   {:>4}".format(d, btc, eth, sol, xrp, bs, t))
print("-" * 45)
print("TOTAL        {:>3} {:>3} {:>3} {:>3}".format(totals["BTC"], totals["ETH"], totals["SOL"], totals["XRP"]))

if btc_sol_dist:
    s = sorted(btc_sol_dist)
    print("\nBTC+SOL trades/day: min={}  median={}  max={}  avg={:.1f}".format(
        s[0], s[len(s) // 2], s[-1], sum(s) / len(s)
    ))

print("\nTop block reasons (14d):")
for t, n in sorted(blocks.items(), key=lambda x: -x[1])[:12]:
    print("  [{}]: {}".format(t, n))
