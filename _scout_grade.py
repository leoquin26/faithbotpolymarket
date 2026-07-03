#!/usr/bin/env python3
"""Grade the daily-scout's 17 days of settled model-vs-market divergences.
Decisive test for the daily-threshold pivot: does following the MODEL against the
market price make money, overall and at larger divergence sizes? Read-only."""
import re

pat = re.compile(r'\[RESULT\] (\w+) >\$([\d,]+) -> spot \$([\d,.]+) (above|below) '
                 r'\| model said (\d+)% mkt (\d+)%')
res = []
for ln in open('logs/daily_scout.log', errors='ignore'):
    m = pat.search(ln)
    if m:
        coin, strike, spot, ab, mp, kp = m.groups()
        res.append(dict(coin=coin, outcome=1 if ab == 'above' else 0,
                        model=int(mp) / 100, mkt=int(kp) / 100))
print('settled results parsed:', len(res))

def sim(min_edge):
    evs = []
    for r in res:
        e = r['model'] - r['mkt']
        if abs(e) < min_edge:
            continue
        if e > 0:                       # model says market underprices YES -> buy YES
            win = r['outcome'] == 1; cost = r['mkt']
        else:                           # model says overpriced -> buy NO
            win = r['outcome'] == 0; cost = 1 - r['mkt']
        if cost <= 0.02 or cost >= 0.98:
            continue
        evs.append(((1 - cost) / cost) if win else -1.0)
    n = len(evs)
    return (n, sum(evs) / n if n else 0, 100 * sum(1 for e in evs if e > 0) / n if n else 0)

for me in (0.05, 0.08, 0.12, 0.20):
    n, ev, wr = sim(me)
    print(f'|edge|>={int(me*100):>2}%: n={n:<3} trades, WR {wr:3.0f}%, EV/$ {ev:+.3f}')

# per-coin split at the base threshold
for c in ('BITCOIN', 'ETHEREUM', 'SOLANA'):
    sub = [r for r in res if r['coin'] == c]
    evs = []
    for r in sub:
        e = r['model'] - r['mkt']
        if abs(e) < 0.05:
            continue
        if e > 0:
            win = r['outcome'] == 1; cost = r['mkt']
        else:
            win = r['outcome'] == 0; cost = 1 - r['mkt']
        if 0.02 < cost < 0.98:
            evs.append(((1 - cost) / cost) if win else -1.0)
    if evs:
        print(f'  {c:<9} n={len(evs):<3} EV/$ {sum(evs)/len(evs):+.3f}')
