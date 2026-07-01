#!/usr/bin/env python3
"""THE CHECKER (maker-checker verifier). Independent, deterministic gate a signal/filter must
PASS on OUT-OF-SAMPLE data before it goes live — no agent opinions, just statistics.

Gates (all must hold on the OOS test set):
  1. n >= MIN_N                      — enough samples to trust
  2. z >= Z_MIN vs break-even        — WR is REALLY above break-even, not luck (one-proportion z)
  3. EV per $1 staked > 0            — economically +EV after the favorite payout asymmetry
Break-even WR = the average entry price (implied prob). Overfit flag: passes in-sample, fails OOS.

Usage: define a filter predicate, run it. A change deploys ONLY if OOS verdict = PASS.
"""
import csv, math, sys

MIN_N  = 80      # minimum out-of-sample trades
Z_MIN  = 1.64    # 95% one-sided — reject "WR == break-even (luck)"
OOS    = 0.30    # last 30% of the timeline = held-out test set

rows = [r for r in csv.DictReader(open('clean_bot_research.csv')) if r.get('drift_correct') in ('0', '1')]
split = int(len(rows) * (1 - OOS))
TRAIN, TEST = rows[:split], rows[split:]

def fl(r, k):
    try: return float(r[k])
    except Exception: return None

def measure(rs, pred):
    sel = [r for r in rs if pred(r) and fl(r, 'fav_ask')]
    n = len(sel)
    if n == 0:
        return None
    wins = sum(int(r['drift_correct']) for r in sel)
    wr = wins / n
    be = sum(fl(r, 'fav_ask') / 100 for r in sel) / n          # break-even WR = avg entry price
    se = math.sqrt(be * (1 - be) / n)
    z = (wr - be) / se if se > 0 else 0.0
    ev = sum(((1 - fl(r, 'fav_ask') / 100) / (fl(r, 'fav_ask') / 100)) if int(r['drift_correct'])
             else -1.0 for r in sel) / n                         # EV per $1 staked
    return dict(n=n, wr=wr, be=be, z=z, ev=ev)

def verify(label, pred):
    tr, te = measure(TRAIN, pred), measure(TEST, pred)
    print(f"\n=== {label} ===")
    for tag, m in (("in-sample", tr), ("OUT-OF-SAMPLE", te)):
        if not m:
            print(f"  {tag:<13} (no trades)"); continue
        print(f"  {tag:<13} n={m['n']:<4} WR={m['wr']*100:4.1f}%  break-even={m['be']*100:4.1f}%  "
              f"z={m['z']:+4.2f}  EV/$={m['ev']:+.3f}")
    if not te:
        print("  VERDICT: ❌ FAIL (no OOS data)"); return False
    passed = te['n'] >= MIN_N and te['z'] >= Z_MIN and te['ev'] > 0
    overfit = tr and tr['z'] >= Z_MIN and not passed
    verdict = "✅ PASS" if passed else ("❌ FAIL — OVERFIT (in-sample only)" if overfit else "❌ FAIL")
    reasons = []
    if te['n'] < MIN_N: reasons.append(f"n<{MIN_N}")
    if te['z'] < Z_MIN: reasons.append(f"z<{Z_MIN} (not signif. > break-even)")
    if te['ev'] <= 0:   reasons.append("EV<=0")
    print(f"  VERDICT: {verdict}" + (f"  [{', '.join(reasons)}]" if reasons else ""))
    return passed

print(f"dataset: {len(rows)} resolved windows | train={len(TRAIN)} | OOS test={len(TEST)}")
print(f"gates: OOS n>={MIN_N}, z>={Z_MIN} vs break-even, EV/$>0")

band  = lambda r: fl(r, 'fav_ask') is not None and 58 <= fl(r, 'fav_ask') <= 66
d7    = lambda r: abs(fl(r, 'drift_pct') * 100) >= 7 if fl(r, 'drift_pct') is not None else False
mom   = lambda r: fl(r, 'roc300_bps') is not None and (fl(r, 'roc300_bps') > 0) == (r['dir'] == 'UP')
conf  = lambda r: r.get('confirmed') == '1'
trend = lambda r: fl(r, 'er') not in (None, '') and fl(r, 'er') >= 0.32
flowok = lambda r: fl(r, 'flow60') not in (None, '') and (fl(r, 'flow60') > 0) == (r['dir'] == 'UP')

verify("CORE: band 58-66c + drift>=7bps (LIVE)", lambda r: band(r) and d7(r))
verify("CORE + momentum agrees",                 lambda r: band(r) and d7(r) and mom(r))
verify("CORE + trending regime (ER>=0.32)",      lambda r: band(r) and d7(r) and trend(r))

# FREQUENCY EXPLORATION: which looser config MAXIMIZES OOS n while keeping EV>0 (and z as high
# as possible)? More trades × same edge = faster compounding. Compare n (frequency) vs z/EV.
print("\n\n########## FREQUENCY vs EDGE (maximize n while EV>0) ##########")
def band2(lo, hi):
    return lambda r: fl(r, 'fav_ask') is not None and lo <= fl(r, 'fav_ask') <= hi
def dN(bps):
    return lambda r: fl(r, 'drift_pct') is not None and abs(fl(r, 'drift_pct') * 100) >= bps
wide = lambda r: band2(55,74)(r) and dN(5)(r)
tl = lambda r: fl(r,'t_left')
for lab, pr in [
    ("wide 55-74c / d>=5 (VERIFIED, all timing)", wide),
    ("  + entry delay t_left<=750 (LIVE v1.21)",  lambda r: wide(r) and tl(r) is not None and tl(r) <= 750),
    ("  + early only t_left>750",                 lambda r: wide(r) and tl(r) is not None and tl(r) > 750),
]:
    verify(lab, pr)
