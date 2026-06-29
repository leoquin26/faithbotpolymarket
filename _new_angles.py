#!/usr/bin/env python3
"""Hunt for NEW ways to bet beyond drift-following: market mispricing (value), best coin,
best hours, volatility regime, and a high-consistency subset. Read-only.
Outcome = drift_correct (did the bet/drift direction win)."""
import csv, collections

rows = [r for r in csv.DictReader(open('clean_bot_research.csv')) if r.get('drift_correct') in ('0','1')]
def fl(r,k):
    try: return float(r[k])
    except Exception: return None
def wr(rs):
    return sum(int(r['drift_correct']) for r in rs)/len(rs) if rs else 0
def line(lab, rs, implied=None):
    if len(rs) < 12:
        print(f'  {lab:<26} n={len(rs):<4} (thin)'); return
    w = wr(rs)
    extra = ''
    if implied is not None:                      # +EV check vs the price you'd pay
        edge = w - implied
        extra = f'  implied~{implied*100:.0f}%  edge={edge*100:+.0f}pts'
    print(f'  {lab:<26} WR={w*100:4.0f}%  n={len(rs):<4}{extra}')

print(f'TOTAL: {len(rows)} resolved windows\n')

# A. MARKET CALIBRATION / VALUE: realized WR vs the price (implied prob) you pay.
# edge>0 = the favorite is UNDERPRICED at that ask = +EV value bet.
print('=== A. value: realized WR vs favorite ask (implied prob) ===')
for lo, hi in [(52,58),(58,62),(62,66),(66,70),(70,74),(74,80),(80,90)]:
    g = [r for r in rows if fl(r,'fav_ask') is not None and lo <= fl(r,'fav_ask') < hi]
    line(f'{lo}-{hi}c', g, implied=(lo+hi)/200.0)

# B. per coin
print('\n=== B. by coin ===')
for c in ('ETH','SOL','BTC'):
    line(c, [r for r in rows if r['coin']==c])

# C. by hour of day (UTC) from window_start epoch
print('\n=== C. by hour-of-day (UTC) — find the trending hours ===')
byh = collections.defaultdict(list)
for r in rows:
    ws = fl(r,'window_start')
    if ws: byh[int(ws//3600)%24].append(r)
for h in range(0,24,1):
    g = byh.get(h,[])
    if len(g) >= 20:
        print(f'  {h:02d}:00 UTC  WR={wr(g)*100:4.0f}%  n={len(g)}')

# D. volatility (sigma) quartiles
print('\n=== D. by volatility (sigma) ===')
ss = sorted(fl(r,'sigma') for r in rows if fl(r,'sigma') is not None)
if ss:
    q1,q2,q3 = ss[len(ss)//4], ss[len(ss)//2], ss[3*len(ss)//4]
    line(f'low vol (<{q1:.5f})',  [r for r in rows if fl(r,'sigma') is not None and fl(r,'sigma')<q1])
    line('mid-low',  [r for r in rows if fl(r,'sigma') is not None and q1<=fl(r,'sigma')<q2])
    line('mid-high', [r for r in rows if fl(r,'sigma') is not None and q2<=fl(r,'sigma')<q3])
    line(f'high vol (>={q3:.5f})', [r for r in rows if fl(r,'sigma') is not None and fl(r,'sigma')>=q3])

# E. entry timing (t_left)
print('\n=== E. by time-left at decision (t_left s) ===')
for lo,hi in [(0,300),(300,600),(600,750),(750,900)]:
    g = [r for r in rows if fl(r,'t_left') is not None and lo<=fl(r,'t_left')<hi]
    line(f'{lo}-{hi}s left', g)

# F. high-consistency subset hunt (want WR>=75% with usable n)
print('\n=== F. consistency subsets (aligned pairs only, |drift| tiers) ===')
import collections as _c
byws=_c.defaultdict(dict)
for r in rows: byws[r['window_start']][r['coin']]=r
aligned=set()
for ws,d in byws.items():
    if 'ETH' in d and 'SOL' in d and d['ETH']['dir']==d['SOL']['dir']:
        aligned.add(ws)
al = [r for r in rows if r['window_start'] in aligned]
print(f'  (aligned-pair universe: {len(al)} legs)')
for lo,hi in [(8,12),(12,16),(16,25),(25,999)]:
    g=[r for r in al if lo<=abs(fl(r,'drift_pct')*100)<hi and fl(r,'fav_ask') is not None and 58<=fl(r,'fav_ask')<=70]
    line(f'aligned+{lo}-{hi}bps+band', g)
