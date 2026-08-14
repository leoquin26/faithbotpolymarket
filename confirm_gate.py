#!/usr/bin/env python3
"""CONFIRMATION GATE — decides the high-band micro-audition on UNSEEN data.

Pre-registered 2026-08-13 (~16:30 UTC), before the confirmation data existed:
  population seat = favourites, ask 0.75-0.85, spread <= 3c, entry buckets
  20-50min, maker px, hold — measured ONLY on windows recorded AFTER the
  seat_scan run (hs >= CUTOFF).
  GATE OPEN   : n >= 60 and ROI > 0   -> $20 micro-audition (3sh, stop -$10, n=40)
  MIRAGE      : n >= 60 and ROI <= 0  -> stand down, weekly sweep continues
Runs every 6h via cron; Telegram on status change (or daily heartbeat)."""
import csv, json, os, sys, time
from collections import defaultdict

V3 = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, V3)
CUTOFF = 1786734000          # 2026-08-14 19:00 UTC — FRESH window post cycle-6 kill (hysteresis law)
GATE = os.path.join(V3, "gate_state.json")
COINS = {"BTC", "ETH", "SOL", "XRP"}

winners, quotes = {}, defaultdict(list)
for r in csv.DictReader(open(os.path.join(V3, "hourly_research.csv"))):
    if r["coin"] not in COINS:
        continue
    hs = int(r["hour_start"])
    if hs < CUTOFF:
        continue
    if r["winner"]:
        winners[(hs, r["coin"])] = r["winner"]
        continue
    try:
        t = int(r["t_left"])
        if not (1200 <= t <= 3000):
            continue
        ua, ub = float(r["up_ask"]), float(r["up_bid"])
        da, db = float(r["down_ask"]), float(r["down_bid"])
    except ValueError:
        continue
    quotes[(hs, r["coin"])].append((t, ua, ub, da, db))

pairs = []
for (hs, coin), rows in quotes.items():
    wn = winners.get((hs, coin))
    if not wn:
        continue
    rows.sort(key=lambda x: -x[0])
    seen = set()
    for t, ua, ub, da, db in rows:
        tb = t // 600
        if tb in seen or ua <= 0 or da <= 0 or ub <= 0 or db <= 0:
            continue
        seen.add(tb)
        if ua == da:
            continue
        if ua > da:
            fav, ask, bid = "UP", ua, ub
        else:
            fav, ask, bid = "DOWN", da, db
        if not (0.75 <= ask <= 0.85) or (ask - bid) > 0.03:
            continue
        pairs.append((min(bid + 0.01, ask - 0.01), wn == fav))

n = len(pairs)
if n:
    risked = sum(p for p, _ in pairs)
    roi = sum((1 - p) if w else -p for p, w in pairs) / risked
    wr = sum(1 for _, w in pairs if w) / n
else:
    roi = wr = 0.0

# shadow ledger view
sh_line = ""
try:
    sh = json.load(open(os.path.join(V3, "shadow_state.json")))
    done = [x for x in sh["entries"] if "won" in x]
    if done:
        net = sum(x["pnl"] for x in done)
        sw = sum(1 for x in done if x["won"])
        sh_line = f"\n🕶 shadow bot: n={len(done)} {sw}W net {net:+.2f} (at 3sh)"
except Exception:
    pass

# HYSTERESIS (law 2026-08-14): re-entry needs roi >= +4% at n>=60,
# TWO consecutive reads -> ARMED (cycle 7 authorized). Exit stays <= 0.
status = ("OPEN" if n >= 60 and roi >= 0.04 else
          "MIRAGE" if n >= 60 and roi <= 0 else
          "WEAK" if n >= 60 else "FILLING")
prev = {}
try:
    prev = json.load(open(GATE))
except Exception:
    pass
greens = (prev.get("greens", 0) + 1) if status == "OPEN" else 0
armed = greens >= 2
head = {"OPEN":   ("🚀 <b>CYCLE 7 AUTHORIZED</b> — two consecutive fresh reads >= +4%. "
                   "Hysteresis bar met; relaunch is lawful." if armed else
                   "🟡 gate green (1/2) — one more >= +4% read arms cycle 7"),
        "MIRAGE": "🔴 MIRAGE — seat still dead on fresh data; nothing to launch.",
        "WEAK":   "⚪ seat alive but under the +4% bar — watch, spend nothing.",
        "FILLING": "⏳ fresh gate window filling"}[status]
msg = (f"{head}\nconfirmation sample (post-sweep only): n={n}/60 "
       f"ROI {roi*100:+.1f}% win {wr*100:.0f}%{sh_line}")
print(msg)

changed = prev.get("status") != status
stale = time.time() - prev.get("sent", 0) > 20 * 3600
if changed or stale:
    try:
        import telegram_notifier as tg
        tg._send(msg, dedup_key=f"gate-{status}-{int(time.time()//21600)}")
        json.dump({"status": status, "sent": time.time(), "n": n, "roi": roi, "greens": greens},
                  open(GATE, "w"))
    except Exception as e:
        print("tg send failed:", e)
else:
    json.dump({"status": status, "sent": prev.get("sent", 0), "n": n, "roi": roi, "greens": greens},
              open(GATE, "w"))
