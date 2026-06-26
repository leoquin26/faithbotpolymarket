#!/usr/bin/env python3
"""Deep quantitative analysis of CleanBot directional accuracy (drift_correct) vs every
logged feature, to find a higher-accuracy filter. Read-only."""
import csv

rows = [r for r in csv.DictReader(open('clean_bot_research.csv'))
        if r.get('winner') and r.get('drift_correct') in ('0', '1')]

def fl(r, k):
    try:
        return float(r[k])
    except Exception:
        return None

def acc(rs):
    if not rs:
        return '   —'
    c = sum(int(r['drift_correct']) for r in rs)
    return f'{100*c/len(rs):4.0f}%  (n={len(rs)})'

print(f'TOTAL: {acc(rows)}   [need ~67% at 67c, ~72% at 72c to profit]')

print('\n=== by |drift| magnitude (bps) ===')
for lo, hi in [(3, 7), (7, 10), (10, 13), (13, 18), (18, 30), (30, 999)]:
    g = [r for r in rows if lo <= abs(fl(r, 'drift_pct') * 100) < hi]
    print(f'  {lo:>2}-{hi:<3}bps: {acc(g)}')

print('\n=== CROSS-COIN agreement (bet dir vs BTC & SOL drift) ===')
def agree(r):
    d = r['dir'] == 'UP'
    bd, sd = fl(r, 'btc_drift_pct'), fl(r, 'sol_drift_pct')
    if bd is None or sd is None:
        return None
    return int((bd > 0) == d) + int((sd > 0) == d)
for n in [0, 1, 2]:
    g = [r for r in rows if agree(r) == n]
    print(f'  {n} of 2 coins agree: {acc(g)}')

print('\n=== momentum roc300 / roc60 agrees with bet direction? ===')
for k in ('roc300_bps', 'roc60_bps'):
    g1 = [r for r in rows if fl(r, k) is not None and (fl(r, k) > 0) == (r['dir'] == 'UP')]
    g0 = [r for r in rows if fl(r, k) is not None and (fl(r, k) > 0) != (r['dir'] == 'UP')]
    print(f'  {k} SAME dir: {acc(g1)}   OPP dir: {acc(g0)}')

print('\n=== by sigma (volatility), median split ===')
sg = sorted(fl(r, 'sigma') for r in rows if fl(r, 'sigma') is not None)
if sg:
    med = sg[len(sg) // 2]
    print(f'  low vol  (<{med:.5f}): {acc([r for r in rows if fl(r,"sigma") is not None and fl(r,"sigma")<med])}')
    print(f'  high vol (>={med:.5f}): {acc([r for r in rows if fl(r,"sigma") is not None and fl(r,"sigma")>=med])}')

print('\n=== STACKED FILTERS (hunting a high-accuracy zone) ===')
def sub(cond, lab):
    g = [r for r in rows if cond(r)]
    print(f'  {lab:<50} {acc(g)}')
d10 = lambda r: abs(fl(r, 'drift_pct') * 100) >= 10
r3same = lambda r: fl(r, 'roc300_bps') is not None and (fl(r, 'roc300_bps') > 0) == (r['dir'] == 'UP')
sub(d10, '|drift|>=10bps')
sub(lambda r: d10(r) and agree(r) == 2, '|drift|>=10 + BOTH coins agree')
sub(lambda r: d10(r) and agree(r) == 2 and r3same(r), '|drift|>=10 + both agree + roc300 same')
sub(lambda r: d10(r) and r3same(r), '|drift|>=10 + roc300 same dir')
sub(lambda r: d10(r) and agree(r) == 0, '|drift|>=10 but BOTH coins DISAGREE (reversal)')
sub(lambda r: d10(r) and not r3same(r), '|drift|>=10 but roc300 OPPOSITE (fading)')
