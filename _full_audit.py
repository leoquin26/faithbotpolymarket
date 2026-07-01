#!/usr/bin/env python3
"""FULL AUDIT: every signal we log, tested with the OOS discipline, plus the sizing math.
Read-only. Goal: find VERIFIED accuracy/growth levers, reject noise."""
import csv, math

rows = [r for r in csv.DictReader(open('clean_bot_research.csv')) if r.get('drift_correct') in ('0', '1')]
def fl(r, k):
    try: return float(r[k])
    except Exception: return None

split = int(len(rows) * 0.7)
TRAIN, TEST = rows[:split], rows[split:]

def measure(rs, pred, label='', be_override=None):
    sel = [r for r in rs if pred(r) and fl(r, 'fav_ask')]
    n = len(sel)
    if n == 0: return None
    w = sum(int(r['drift_correct']) for r in sel)
    wr = w / n
    be = be_override or sum(fl(r, 'fav_ask') / 100 for r in sel) / n
    se = math.sqrt(be * (1 - be) / n)
    z = (wr - be) / se if se > 0 else 0
    ev = sum(((1 - fl(r,'fav_ask')/100) / (fl(r,'fav_ask')/100)) if int(r['drift_correct']) else -1.0 for r in sel) / n
    return dict(n=n, wr=wr, be=be, z=z, ev=ev)

def show(label, pred):
    tr, te = measure(TRAIN, pred), measure(TEST, pred)
    f = lambda m: f"n={m['n']:<4} WR={m['wr']*100:4.1f}% be={m['be']*100:4.1f}% z={m['z']:+4.2f} EV={m['ev']:+.3f}" if m else "(none)"
    print(f"  {label:<44} IS: {f(tr)}")
    print(f"  {'':<44} OOS:{f(te)}")

band = lambda r: fl(r,'fav_ask') is not None and 55 <= fl(r,'fav_ask') <= 74
d5   = lambda r: fl(r,'drift_pct') is not None and abs(fl(r,'drift_pct')*100) >= 5
core = lambda r: band(r) and d5(r)

print(f"rows={len(rows)} train={len(TRAIN)} test={len(TEST)}\n")

print("=== A. UP vs DOWN asymmetry (the overnight killer) — within CORE ===")
show("CORE, UP bets",   lambda r: core(r) and r['dir']=='UP')
show("CORE, DOWN bets", lambda r: core(r) and r['dir']=='DOWN')

print("\n=== B. trend-alignment proxy: bet dir == sign(btc_drift) [macro proxy] ===")
btc_al = lambda r: fl(r,'btc_drift_pct') is not None and (fl(r,'btc_drift_pct')>0)==(r['dir']=='UP')
show("CORE + BTC-aligned",  lambda r: core(r) and btc_al(r))
show("CORE + BTC-opposed",  lambda r: core(r) and not btc_al(r))

print("\n=== C. ER regime (288 rows now) — ALL rows with er, within CORE ===")
er_t = lambda r: fl(r,'er') not in (None,) and r.get('er') not in ('',) and fl(r,'er') >= 0.32
er_c = lambda r: r.get('er') not in (None,'') and fl(r,'er') < 0.32
show("CORE + ER>=0.32 (trend)", lambda r: core(r) and er_t(r))
show("CORE + ER<0.32 (chop)",   lambda r: core(r) and er_c(r))

print("\n=== D. flow60 (138 rows) — does volume direction predict? (all rows w/ flow) ===")
fa = lambda r: r.get('flow60') not in (None,'') and (fl(r,'flow60')>0)==(r['dir']=='UP')
fo = lambda r: r.get('flow60') not in (None,'') and (fl(r,'flow60')>0)!=(r['dir']=='UP')
show("flow AGREES with dir",  lambda r: band(r) and fa(r))
show("flow OPPOSES dir",      lambda r: band(r) and fo(r))

print("\n=== E. BTC / XRP shadow coins (tradeable?) ===")
for c in ('BTC','XRP','ETH','SOL'):
    show(f"{c} in-band any drift>=5", lambda r, c=c: r['coin']==c and core(r))

print("\n=== F. hour blocks (UTC, within CORE) — regime by session ===")
def hr(r):
    ws = fl(r,'window_start')
    return int(ws//3600)%24 if ws else None
for lo,hi,name in [(0,8,'Asia 00-08'),(8,14,'EU 08-14'),(14,20,'US 14-20'),(20,24,'late 20-24')]:
    show(f"{name} UTC", lambda r, lo=lo, hi=hi: core(r) and hr(r) is not None and lo<=hr(r)<hi)

print("\n=== G. SIZING MATH (Kelly, floor-bound reality) ===")
te = measure(TEST, core)
if te:
    p, price = te['wr'], te['be']
    b = (1-price)/price
    kelly = (b*p - (1-p))/b
    print(f"  OOS: p={p:.3f} at avg {price*100:.0f}c -> payout b={b:.3f}")
    print(f"  full Kelly f*={kelly*100:.1f}%  half-Kelly={kelly*50:.1f}%  (bot uses 6%, cap 12%)")
    print(f"  5-share floor at ~64c = $3.20/bet = {3.20/25.12*100:.1f}% of $25.12 bankroll")
    print(f"  -> sizing is FLOOR-BOUND: exchange minimum already >= half-Kelly. Kelly is academic until ~$45+.")
    g = p*math.log(1+ .128*b) + (1-p)*math.log(1-.128)
    print(f"  log-growth/trade at floor size (12.8%): {g*100:+.2f}% -> x{math.exp(g*12):.2f} per 12-trade day if edge holds")
