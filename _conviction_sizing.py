#!/usr/bin/env python3
"""Does CONVICTION-WEIGHTED sizing (bet more on the strongest setups, less on marginal)
beat flat sizing? Simulate on the real traded sequence. Read-only.
The point: break the win/loss SIZE symmetry so confident wins > marginal losses."""
import csv

rows = list(csv.DictReader(open('clean_bot_research.csv')))
def fl(r, k):
    try: return float(r[k])
    except Exception: return None

# the set the bot would actually trade: real favorite, base drift bar, momentum not opposing
trades = []
for r in rows:
    p = fl(r, 'fav_ask'); dc = r.get('drift_correct')
    if p is None or not (58 <= p <= 70) or dc not in ('0', '1'):
        continue
    drift = abs(fl(r, 'drift_pct') * 100) if fl(r, 'drift_pct') is not None else 0
    if drift < 10:
        continue
    roc = fl(r, 'roc300_bps') or 0
    is_up = r['dir'] == 'UP'
    if (roc if is_up else -roc) < -2:          # momentum strongly opposing -> bot skips
        continue
    confirmed = r.get('confirmed') == '1'
    # conviction tier (data: |drift|>=13 + cross-coin agree + momentum agree = the 84% tier)
    mom_ok = (roc > 0) == is_up
    high = drift >= 13 and confirmed and mom_ok
    trades.append({'p': p/100.0, 'win': int(dc), 'high': high})

n = len(trades); hi = sum(t['high'] for t in trades)
print(f'traded windows: {n}  | HIGH-conviction: {hi} ({100*hi/max(n,1):.0f}%)  | MID: {n-hi}')
wr = sum(t['win'] for t in trades)/max(n,1)
hwr = sum(t['win'] for t in trades if t['high'])/max(hi,1)
mwr = sum(t['win'] for t in trades if not t['high'])/max(n-hi,1)
print(f'WR overall {wr*100:.0f}%  | HIGH-tier WR {hwr*100:.0f}%  | MID-tier WR {mwr*100:.0f}%')

def sim(frac_hi, frac_mid, start=44.0, floor_sh=5, cap_pct=0.16):
    bk = start; peak = start; dd = 0
    for t in trades:
        frac = frac_hi if t['high'] else frac_mid
        stake = min(bk*frac, bk*cap_pct)
        sh = max(floor_sh, round(stake/t['p']))   # 5-share exchange floor
        stake = sh * t['p']
        if stake > bk: stake = bk
        bk += stake*(1-t['p'])/t['p'] if t['win'] else -stake
        peak = max(peak, bk); dd = max(dd, (peak-bk)/peak)
    return bk, dd*100

print()
print(f"{'strategy':<34}{'end $':>9}{'x':>7}{'maxDD':>8}")
for lab, fh, fm in [('flat 6%', 0.06, 0.06),
                    ('flat 8%', 0.08, 0.08),
                    ('conviction 12% / 5%', 0.12, 0.05),
                    ('conviction 14% / 4%', 0.14, 0.04),
                    ('conviction 16% / 3%(floored)', 0.16, 0.03)]:
    end, dd = sim(fh, fm)
    print(f'{lab:<34}{end:>9.2f}{end/44:>7.2f}{dd:>7.0f}%')
