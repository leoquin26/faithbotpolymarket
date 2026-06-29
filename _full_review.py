#!/usr/bin/env python3
"""Full data review: find what actually separates winners from losers, and whether
opposite-direction correlated pairs are a real leak. Read-only. Uses drift_correct
(did the bet/drift direction win) as the outcome."""
import csv, collections

rows = [r for r in csv.DictReader(open('clean_bot_research.csv')) if r.get('drift_correct') in ('0', '1')]
def fl(r, k):
    try: return float(r[k])
    except Exception: return None
def acc(rs):
    if not rs: return '  —'
    c = sum(int(r['drift_correct']) for r in rs)
    return f'{100*c/len(rs):4.0f}% (n={len(rs)})'

print(f'TOTAL resolved windows: {len(rows)}\n')

# break-even WR by price (the asymmetry)
print('=== break-even WR needed by entry price (favorite ask) ===')
for c in (58, 62, 66, 70, 74):
    print(f'  {c}c: need {c}% WR just to break even (win pays {(100-c)/c:.2f}/$1)')

print('\n=== WR by |drift| magnitude (bps) ===')
for lo, hi in [(5,8),(8,10),(10,13),(13,16),(16,20),(20,30),(30,999)]:
    g = [r for r in rows if lo <= abs(fl(r,'drift_pct')*100) < hi]
    print(f'  {lo:>2}-{hi:<3}: {acc(g)}')

print('\n=== momentum (roc300) agree vs oppose the bet ===')
ag = [r for r in rows if fl(r,'roc300_bps') is not None and (fl(r,'roc300_bps')>0)==(r['dir']=='UP')]
op = [r for r in rows if fl(r,'roc300_bps') is not None and (fl(r,'roc300_bps')>0)!=(r['dir']=='UP')]
print(f'  roc300 AGREES: {acc(ag)}   OPPOSES: {acc(op)}')

print('\n=== cross-coin: bet dir vs BTC & SOL drift agreement ===')
def agree2(r):
    d = r['dir']=='UP'; bd, sd = fl(r,'btc_drift_pct'), fl(r,'sol_drift_pct')
    if bd is None or sd is None: return None
    return int((bd>0)==d) + int((sd>0)==d)
for n in (0,1,2):
    print(f'  {n}/2 coins agree: {acc([r for r in rows if agree2(r)==n])}')

# OPPOSITE-DIRECTION CORRELATED PAIRS: group by window, find ETH vs SOL opposite
print('\n=== OPPOSITE-direction pairs in the SAME window (ETH vs SOL) ===')
byws = collections.defaultdict(dict)
for r in rows:
    byws[r['window_start']][r['coin']] = r
aligned_legs, opp_legs = [], []
for ws, d in byws.items():
    if 'ETH' in d and 'SOL' in d:
        if d['ETH']['dir'] == d['SOL']['dir']:
            aligned_legs += [d['ETH'], d['SOL']]
        else:
            opp_legs += [d['ETH'], d['SOL']]
print(f'  ALIGNED-pair legs (ETH & SOL same dir): {acc(aligned_legs)}')
print(f'  OPPOSITE-pair legs (ETH & SOL diverge): {acc(opp_legs)}')

print('\n=== STACKED: hunt the highest-WR robust subset ===')
def sub(cond, lab):
    g = [r for r in rows if cond(r)]
    print(f'  {lab:<48} {acc(g)}')
d13 = lambda r: abs(fl(r,'drift_pct')*100) >= 13
mom = lambda r: fl(r,'roc300_bps') is not None and (fl(r,'roc300_bps')>0)==(r['dir']=='UP')
a2  = lambda r: agree2(r)==2
inband = lambda r: fl(r,'fav_ask') is not None and 58 <= fl(r,'fav_ask') <= 70
sub(inband, 'ask 58-70c')
sub(lambda r: inband(r) and d13(r), '+ |drift|>=13')
sub(lambda r: inband(r) and d13(r) and mom(r), '+ momentum agrees')
sub(lambda r: inband(r) and d13(r) and mom(r) and a2(r), '+ both coins agree (full stack)')
sub(lambda r: inband(r) and mom(r) and a2(r), 'ask-band + mom + both agree (no drift req)')
sub(lambda r: inband(r) and not mom(r), 'ask-band + momentum OPPOSES (the fakeouts)')
