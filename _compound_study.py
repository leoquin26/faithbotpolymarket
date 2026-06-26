#!/usr/bin/env python3
"""Why one loss wipes ~N wins, and what price band / sizing actually COMPOUNDS fastest.
Read-only. Re-derives the traded set with consistent filters, then measures GEOMETRIC
growth (Kelly log-growth), not just win rate."""
import csv, math

rows = list(csv.DictReader(open('clean_bot_research.csv')))

def fl(r, k):
    try: return float(r[k])
    except Exception: return None

# keep only resolved windows with a real favorite ask + known outcome
data = []
for r in rows:
    p = fl(r, 'fav_ask')
    dc = r.get('drift_correct')
    if p is None or p <= 0 or p >= 100 or dc not in ('0', '1'):
        continue
    data.append({
        'p': p / 100.0,                       # price we'd pay for the favored side
        'win': int(dc),                       # 1 if the bet (drift) side won
        'drift': abs(fl(r, 'drift_pct') * 100) if fl(r, 'drift_pct') is not None else 0,
        'roc300': fl(r, 'roc300_bps') or 0,
        'roc60': fl(r, 'roc60_bps') or 0,
        'dir': r['dir'],
        'confirmed': r.get('confirmed') == '1',
        'coin': r['coin'],
    })

def kelly_loggrowth(trades):
    """Numerically find Kelly fraction f* that maximizes mean log(1+f*ret), and the growth."""
    if not trades:
        return 0.0, 0.0, 0.0
    rets = [((1 - t['p']) / t['p']) if t['win'] else -1.0 for t in trades]
    best_f, best_g = 0.0, 0.0
    f = 0.01
    while f <= 0.60:                          # cap at 60% bankroll (sane ceiling)
        g = 0.0; ok = True
        for x in rets:
            v = 1 + f * x
            if v <= 0: ok = False; break
            g += math.log(v)
        if ok:
            g /= len(rets)
            if g > best_g:
                best_g, best_f = g, f
        f += 0.01
    return best_f, best_g, math.expm1(best_g)  # f*, log-growth/trade, %growth/trade

def summarize(label, trades):
    n = len(trades)
    if n < 8:
        print(f'  {label:<34} n={n:<4} (too few)'); return
    wins = sum(t['win'] for t in trades)
    wr = wins / n
    avg_p = sum(t['p'] for t in trades) / n
    # per-$1-staked outcomes
    win_per = sum((1 - t['p']) / t['p'] for t in trades if t['win']) / max(wins, 1)
    ev = sum(((1 - t['p']) / t['p']) if t['win'] else -1.0 for t in trades) / n
    wipe = avg_p / (1 - avg_p)                # how many avg wins one avg loss erases
    f, lg, gpt = kelly_loggrowth(trades)
    print(f'  {label:<34} n={n:<4} WR={wr*100:4.1f}%  ask~{avg_p*100:4.1f}c  '
          f'win/$={win_per:.2f}  EV/$={ev:+.3f}  loss=~{wipe:.1f}W  '
          f'Kelly f*={f*100:4.1f}%  growth/trade={gpt*100:+5.2f}%')

print(f'TOTAL resolved windows with a favorite: {len(data)}\n')

print('=== THE ASYMMETRY (why one loss hurts): by entry-price band ===')
print('  win/$ = profit per $1 staked on a WIN ; loss=~NW = one loss erases ~N average wins')
for lo, hi in [(50,58),(58,62),(62,66),(66,70),(70,74),(74,80),(80,100)]:
    g = [t for t in data if lo <= t['p']*100 < hi]
    summarize(f'{lo}-{hi}c', g)

print('\n=== current live filter (drift>=10bps, ask 61-74, momentum not opposing) ===')
base = [t for t in data if t['drift'] >= 10 and 61 <= t['p']*100 <= 74
        and ((t['roc300'] if t['dir']=='UP' else -t['roc300']) >= -2)]
summarize('LIVE filter', base)
print('   within that, split by cross-coin confirmed:')
summarize('  + confirmed (84% tier)', [t for t in base if t['confirmed']])
summarize('  + NOT confirmed',        [t for t in base if not t['confirmed']])

print('\n=== hunting the FASTEST-COMPOUNDING price band (within drift>=10 + momentum ok) ===')
strong = [t for t in data if t['drift'] >= 10 and ((t['roc300'] if t['dir']=='UP' else -t['roc300']) >= -2)]
for lo, hi in [(55,62),(58,66),(60,68),(61,70),(62,72),(61,74),(64,74),(66,74),(55,74)]:
    summarize(f'ask {lo}-{hi}c', [t for t in strong if lo <= t['p']*100 <= hi])

print('\n=== same, but ONLY cross-coin confirmed (the high-accuracy tier) ===')
strongc = [t for t in strong if t['confirmed']]
for lo, hi in [(55,62),(58,66),(60,68),(61,74),(62,72),(64,74),(55,74)]:
    summarize(f'ask {lo}-{hi}c +conf', [t for t in strongc if lo <= t['p']*100 <= hi])

print('\n=== CONCRETE: sequential bankroll sim, start $46, flat 8% per bet, in time order ===')
def sim(trades, f=0.08, start=46.0):
    bk = start; peak = start; worst_dd = 0
    for t in trades:                          # already in chronological (file) order
        stake = bk * f
        if t['win']: bk += stake * (1 - t['p']) / t['p']
        else:        bk -= stake
        peak = max(peak, bk); worst_dd = max(worst_dd, (peak - bk) / peak)
    return bk, worst_dd
for lab, lo, hi in [('current 61-74c', 61, 74), ('proposed 58-70c', 58, 70),
                    ('proposed 58-68c', 58, 68), ('cheap 58-66c', 58, 66)]:
    g = [t for t in strong if lo <= t['p']*100 <= hi]
    end, dd = sim(g)
    print(f'  {lab:<18} n={len(g):<4} $46 -> ${end:7.2f}  ({end/46:5.1f}x)  maxDD={dd*100:4.1f}%')
