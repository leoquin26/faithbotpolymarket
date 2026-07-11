#!/usr/bin/env python3
"""Frequency-preserving edge scan on clean_bot_research.csv (late/mid)."""
import csv
from collections import defaultdict
from datetime import datetime

rows = list(csv.DictReader(open("clean_bot_research.csv", encoding="utf-8", errors="ignore")))


def slice_stats(rs, label):
    if not rs:
        print(f"{label:42s} n=0")
        return
    n = len(rs)
    w = sum(int(r["drift_correct"]) for r in rs)
    asks = [float(r["fav_ask"]) / 100 for r in rs]
    be = sum(asks) / n
    wr = w / n
    edge = (wr - be) * 100
    # EV per $1 staked at the ask (hold to $1)
    ev = sum(((1 - a) if int(r["drift_correct"]) else -a) for r, a in zip(rs, asks)) / n
    print(f"{label:42s} n={n:4d} WR={wr*100:5.1f}% BE={be*100:4.1f}% edge={edge:+5.1f}pts EV/$={ev:+.3f}")


late = []
for r in rows:
    if r.get("phase") != "late":
        continue
    if r.get("drift_correct") not in ("0", "1"):
        continue
    try:
        ask = float(r["fav_ask"])
    except Exception:
        continue
    if not (55 <= ask <= 70):
        continue
    late.append(r)

print("LATE in-band 55-70 settled", len(late))
slice_stats(late, "ALL late 55-70")
late_s = sorted(late, key=lambda r: r.get("ts", ""))
cut = int(len(late_s) * 0.7)
slice_stats(late_s[:cut], "IS 70%")
slice_stats(late_s[cut:], "OOS 30%")

for c in ["SOL", "ETH", "BTC", "XRP"]:
    slice_stats([r for r in late if r.get("coin") == c], f"coin {c}")

for lo, hi, name in [(55, 60, "ask 55-60"), (60, 65, "ask 60-65"), (65, 71, "ask 65-70")]:
    slice_stats([r for r in late if lo <= float(r["fav_ask"]) < hi], name)

# drift magnitude buckets (bps)
for lo, hi, name in [(0, 3, "drift <3bps"), (3, 5, "drift 3-5"), (5, 10, "drift 5-10"),
                     (10, 20, "drift 10-20"), (20, 999, "drift 20+")]:
    def bps(r):
        return abs(float(r["drift_pct"])) * 100  # drift_pct is percent points; *100 = bps? 
        # research: drift_pct = dist*100 so 0.10 = 0.10% = 10 bps. abs*100 = bps? 0.10*100=10 yes bps.
    sel = []
    for r in late:
        try:
            d = abs(float(r["drift_pct"])) * 100
        except Exception:
            continue
        if lo <= d < hi:
            sel.append(r)
    slice_stats(sel, name)

by_h = defaultdict(list)
for r in late:
    try:
        ts = r["ts"].replace("T", " ")
        h = datetime.fromisoformat(ts).hour  # stored UTC-ish
        lima = (h - 5) % 24
        by_h[lima].append(r)
    except Exception:
        pass
print("\nBy Lima hour (n>=20):")
for h in range(24):
    rs = by_h.get(h, [])
    if len(rs) < 20:
        continue
    n = len(rs)
    wr = sum(int(r["drift_correct"]) for r in rs) / n
    be = sum(float(r["fav_ask"]) for r in rs) / n / 100
    print(f"  {h:02d} n={n:3d} WR={wr*100:4.1f}% BE={be*100:4.1f}% edge={(wr-be)*100:+5.1f}")

mid = []
for r in rows:
    if r.get("phase") != "mid":
        continue
    if r.get("drift_correct") not in ("0", "1"):
        continue
    try:
        ask = float(r["fav_ask"])
        if 55 <= ask <= 70:
            mid.append(r)
    except Exception:
        continue
print()
slice_stats(mid, "MID phase 55-70 shadow")
if len(mid) >= 40:
    mid_s = sorted(mid, key=lambda r: r.get("ts", ""))
    slice_stats(mid_s[int(len(mid_s) * 0.7) :], "MID last 30%")

print("\nGeometry: fixed WR 65%, cheaper entry compounds more")
for p in (0.58, 0.62, 0.66, 0.70):
    wr = 0.65
    ev = wr * (1 - p) / p - (1 - wr)
    print(f"  entry {p*100:.0f}c  EV/$={ev:+.3f}  wins_to_cover_1_loss={p/(1-p):.2f}")

# book_imb if present
def fnum(r, k):
    try:
        return float(r.get(k) or "nan")
    except Exception:
        return float("nan")

for name, pred in [
    ("book_imb same-side >0.1", lambda r: (fnum(r, "book_imb") > 0.1 and r["dir"] == "UP") or (fnum(r, "book_imb") < -0.1 and r["dir"] == "DOWN")),
    ("book_imb oppose", lambda r: (fnum(r, "book_imb") < -0.1 and r["dir"] == "UP") or (fnum(r, "book_imb") > 0.1 and r["dir"] == "DOWN")),
    ("flow60 same-side", lambda r: (fnum(r, "flow60") > 0.2 and r["dir"] == "UP") or (fnum(r, "flow60") < -0.2 and r["dir"] == "DOWN")),
    ("flow60 oppose", lambda r: (fnum(r, "flow60") < -0.2 and r["dir"] == "UP") or (fnum(r, "flow60") > 0.2 and r["dir"] == "DOWN")),
]:
    sel = [r for r in late if pred(r)]
    slice_stats(sel, name)
