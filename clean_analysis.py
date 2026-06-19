#!/usr/bin/env python3
"""Analyze CleanBot's full record from clean_bot.log — what found the edge.
Breaks WIN/LOSS down by coin, direction, entry, day, and hour. Read-only."""
import re, os, collections

LOG = os.path.expanduser("~/v3-bot/clean_bot.log")
RES = re.compile(r"^(\d{4}-\d\d-\d\d) (\d\d):\d\d:\d\d \| \[(WIN|LOSS)\] (BTC|ETH|SOL|XRP) "
                 r"(UP|DOWN) @ (\d+)c -> (UP|DOWN) \| ([-+][\d.]+) \| day net ([-+][\d.]+)")
FILL = re.compile(r"\[FILLED\] (BTC|ETH|SOL|XRP) (UP|DOWN) @ (\d+)c x(\d+)")

rows = []
fills = 0
for ln in open(LOG, encoding="utf-8", errors="ignore"):
    if FILL.search(ln):
        fills += 1
    m = RES.search(ln)
    if m:
        rows.append(dict(date=m.group(1), hour=int(m.group(2)), res=m.group(3),
                         coin=m.group(4), dir=m.group(5), entry=int(m.group(6)),
                         winner=m.group(7), pnl=float(m.group(8))))

n = len(rows)
if not n:
    print("no resolved trades parsed"); raise SystemExit
w = sum(1 for r in rows if r["res"] == "WIN")
net = sum(r["pnl"] for r in rows)
print(f"=== CleanBot ALL resolved trades ===")
print(f"fills={fills}  resolved={n}  W/L={w}/{n-w}  WR={100*w/n:.0f}%  net=${net:+.2f}\n")

def grp(key, label):
    d = collections.defaultdict(lambda: [0, 0, 0.0])
    for r in rows:
        k = key(r); d[k][0] += 1; d[k][1] += (r["res"] == "WIN"); d[k][2] += r["pnl"]
    print(f"--- by {label} ---")
    for k in sorted(d, key=lambda x: str(x)):
        nn, ww, pp = d[k]
        print(f"  {str(k):<12} n={nn:<3} WR={100*ww/nn:4.0f}%  net=${pp:+.2f}")
    print()

grp(lambda r: r["coin"], "coin")
grp(lambda r: r["dir"], "predicted direction")
grp(lambda r: "<=58c" if r["entry"] <= 58 else "59-62c" if r["entry"] <= 62 else "63-66c", "entry price")
grp(lambda r: r["date"], "day")
grp(lambda r: f"{r['hour']:02d}h", "hour (UTC)")

print(f"first: {rows[0]['date']} {rows[0]['hour']:02d}h UTC  | last: {rows[-1]['date']} {rows[-1]['hour']:02d}h UTC")
print("sequence: " + "".join("W" if r["res"] == "WIN" else "L" for r in rows))
