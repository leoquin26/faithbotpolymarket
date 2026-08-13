#!/usr/bin/env python3
"""NIGHTLY ANALYST — daily cron (05:10 UTC). Reads what the collectors wrote,
re-measures the retired seat on fresh data, and sends a Telegram digest.
PROPOSES ONLY: nothing here deploys, changes config, or places orders.
Re-arm rule lives in CYCLE_LAW.md (population >= +4% two consecutive weeks)."""
import csv, json, os, sys, time
from collections import defaultdict

V3 = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, V3)

COINS = {"BTC", "ETH", "SOL"}
DAY = 86400

def seat_roi(days_back_lo, days_back_hi):
    """population seat ROI in [now-hi, now-lo) days."""
    now = time.time()
    winners, quotes = {}, defaultdict(list)
    lo, hi = now - days_back_hi * DAY, now - days_back_lo * DAY
    for r in csv.DictReader(open(os.path.join(V3, "hourly_research.csv"))):
        if r["coin"] not in COINS:
            continue
        hs = int(r["hour_start"])
        if not (lo <= hs < hi):
            continue
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
    pairs = []
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
            pairs.append((min(bid + 0.01, ask - 0.01), winners[(hs, coin)] == fav))
            break
    if not pairs:
        return None
    risked = sum(p for p, _ in pairs)
    pnl = sum((1 - p) if w else -p for p, w in pairs)
    return {"n": len(pairs), "roi": pnl / risked}


def growth():
    """rows added since yesterday's snapshot (data_control keeps the history)."""
    try:
        hist = json.load(open(os.path.join(V3, "data_control_stats.json")))
    except Exception:
        return ""
    lines = []
    for name, snap in sorted(hist.items()):
        if snap.get("rows"):
            lines.append(f"{name.split('.')[0]}: {snap['rows']:,} rows")
    return " · ".join(lines)


d1 = seat_roi(0, 1)
d7 = seat_roi(0, 7)
p1 = f"24h: n={d1['n']} ROI {d1['roi']*100:+.1f}%" if d1 else "24h: no data"
p7 = f"7d: n={d7['n']} ROI {d7['roi']*100:+.1f}%" if d7 else "7d: no data"
status = ("🟢 seat warming — watch the Sunday edge_watch"
          if d7 and d7["roi"] >= 0.04 else "⚪ seat still cold — spend nothing")
msg = (f"🌙 <b>NIGHTLY ANALYST</b>\n{p1}\n{p7}\n{status}\n"
       f"<i>{growth()}</i>\n(proposals only — nothing self-deploys)")
print(msg)
try:
    import telegram_notifier as tg
    tg._send(msg, dedup_key=f"nightly-{int(time.time()//DAY)}")
except Exception as e:
    print("tg send failed:", e)
