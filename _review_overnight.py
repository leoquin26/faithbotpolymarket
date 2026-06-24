#!/usr/bin/env python3
"""Review CleanBot trades: equity peak, win rate by hour, drift/coin/entry breakdown,
overnight vs morning vs last-hour. Read-only analysis of clean_bot.log."""
import re
from collections import defaultdict

RE = re.compile(r'(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \| \[(WIN|LOSS)\] (\w+) (UP|DOWN) @ (\d+)c '
                r'-> (UP|DOWN) \| ([+-][\d.]+) \| bankroll \$([\d.]+)')
REE = re.compile(r'\[ENTER\] (\w+) (UP|DOWN) drift=([+-][\d.]+)% ask=(\d+)c')

lines = open('clean_bot.log', errors='ignore').read().splitlines()
enters = {}
for ln in lines:
    e = REE.search(ln)
    if e:
        enters[(e.group(1), e.group(2), e.group(4))] = float(e.group(3)) * 100  # bps

trades = []
for ln in lines:
    m = RE.search(ln)
    if m:
        ts, res, coin, d, entry, outcome, pnl, bk = m.groups()
        trades.append(dict(ts=ts, res=res, coin=coin, dir=d, entry=int(entry),
                           pnl=float(pnl), bk=float(bk),
                           drift=enters.get((coin, d, entry))))

# only since yesterday 18:00 (last night onward)
trades = [t for t in trades if t['ts'] >= '2026-06-23 18:00']
print(f"trades since 2026-06-23 18:00: {len(trades)}")
if not trades:
    raise SystemExit

peak = max(trades, key=lambda t: t['bk'])
print(f"PEAK bankroll ${peak['bk']} at {peak['ts']}  |  current ${trades[-1]['bk']}")
w = sum(t['res'] == 'WIN' for t in trades); l = len(trades) - w
print(f"overall: {w}W {l}L = {100*w//len(trades)}% | net ${sum(t['pnl'] for t in trades):+.2f}\n")

def tbl(rows, key):
    g = defaultdict(lambda: [0, 0, 0.0])
    for t in rows:
        k = key(t)
        g[k][0] += t['res'] == 'WIN'; g[k][1] += t['res'] == 'LOSS'; g[k][2] += t['pnl']
    for k in sorted(g):
        wn, ln_, p = g[k]; n = wn + ln_
        print(f"  {str(k):>10} | {wn:2}W {ln_:2}L | {100*wn//n if n else 0:3}% | ${p:+6.2f}")

print("=== BY HOUR (Lima time) ===")
tbl(trades, lambda t: t['ts'][11:13] + ":00")
print("\n=== BY DRIFT BUCKET ===")
def db(t):
    d = abs(t['drift']) if t['drift'] is not None else -1
    return '<10' if d < 0 else ('7-10' if d < 10 else ('10-15' if d < 15 else ('15-25' if d < 25 else '25+')))
tbl(trades, db)
print("\n=== BY COIN+DIR ===")
tbl(trades, lambda t: f"{t['coin']} {t['dir']}")
print("\n=== BY ENTRY PRICE ===")
tbl(trades, lambda t: '<=60c' if t['entry'] <= 60 else ('61-68c' if t['entry'] <= 68 else '69c+'))

print("\n=== LAST 8 TRADES (the recent losses) ===")
for t in trades[-8:]:
    dr = f"{t['drift']:+.0f}bps" if t['drift'] is not None else "?"
    print(f"  {t['ts'][11:16]} {t['coin']:3} {t['dir']:4} @{t['entry']}c drift={dr:>8} -> {t['res']:4} {t['pnl']:+.2f}")
