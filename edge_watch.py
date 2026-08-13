#!/usr/bin/env python3
"""EDGE WATCH — weekly zero-cost monitor of the 1H maker-favourite seat.
Runs on cron; simulates the retired engine's exact seat on the fresh
population data and reports whether the edge has returned.

PRE-REGISTERED RE-ARM RULE (CYCLE_LAW.md): population ROI >= +4% for two
consecutive weeks -> owner may consider a $20 micro-audition. Until then:
watch only, spend nothing."""
import csv, sys, time
from collections import defaultdict

sys.path.insert(0, "/home/ubuntu/v3-bot")

COINS = {"BTC", "ETH", "SOL"}
PATH = "/home/ubuntu/v3-bot/hourly_research.csv"
WEEK = 7 * 86400

winners, quotes = {}, defaultdict(list)
for r in csv.DictReader(open(PATH)):
    if r["coin"] not in COINS:
        continue
    hs = int(r["hour_start"])
    if r["winner"]:
        winners[(hs, r["coin"])] = r["winner"]
        continue
    try:
        t = int(r["t_left"])
        if not (1800 <= t <= 3300):
            continue
        ua, ub = float(r["up_ask"]), float(r["up_bid"])
        da, db = float(r["down_ask"]), float(r["down_bid"])
    except ValueError:
        continue
    quotes[(hs, r["coin"])].append((t, ua, ub, da, db))

now = time.time()
weekly = defaultdict(list)  # weeks_ago -> [(px, won)]
for (hs, coin), rows in quotes.items():
    if (hs, coin) not in winners:
        continue
    rows.sort(key=lambda x: -x[0])
    for t, ua, ub, da, db in rows:
        if ua <= 0 or da <= 0:
            continue
        if ua > da:
            fav, ask, bid = "UP", ua, ub
        elif da > ua:
            fav, ask, bid = "DOWN", da, db
        else:
            continue
        if not (0.55 <= ask <= 0.85) or (bid + ask) / 2 < 0.55:
            continue
        weekly[int((now - hs) // WEEK)].append(
            (min(bid + 0.01, ask - 0.01), winners[(hs, coin)] == fav))
        break

lines = []
for wk in sorted(weekly):
    sel = weekly[wk]
    risked = sum(p for p, _ in sel)
    roi = sum((1 - p) if w else -p for p, w in sel) / risked if risked else 0
    tag = "week now-%d" % wk if wk else "THIS WEEK"
    lines.append("%s: n=%d ROI %+.1f%%" % (tag, len(sel), roi * 100))

this_wk = weekly.get(0, [])
risked = sum(p for p, _ in this_wk)
roi0 = sum((1 - p) if w else -p for p, w in this_wk) / risked if risked else 0
last_wk = weekly.get(1, [])
risked1 = sum(p for p, _ in last_wk)
roi1 = sum((1 - p) if w else -p for p, w in last_wk) / risked1 if risked1 else 0

armed = roi0 >= 0.04 and roi1 >= 0.04
verdict = ("🟢 RE-ARM CRITERIA MET (two weeks >= +4%) — a $20 micro-audition "
           "is now on the table per CYCLE_LAW." if armed else
           "⚪ edge not back — keep watching, spend nothing.")
msg = ("🔭 <b>EDGE WATCH</b> (1H maker seat, population sim)\n"
       + "\n".join(lines[:4]) + "\n" + verdict)
print(msg)
try:
    import telegram_notifier as tg
    tg._send(msg, dedup_key="edgewatch-%d" % int(now // WEEK))
except Exception as e:
    print("tg send failed:", e)
