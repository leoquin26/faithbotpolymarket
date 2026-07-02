#!/usr/bin/env python3
"""Post-mortem hypotheses from the 70c ETH UP loss (2026-07-02 13:49), tested on ALL live
trades, not the anecdote. Joins log ENTER/result lifecycles with research first-scan rows.

H1: LATE + EXPENSIVE entries (move already old, price near top of band) underperform.
H2: entries AGAINST the window's first-scan drift direction (whipsaw chases) underperform.
Read-only."""
import csv, re

# ── live trades from the log: ENTER (with ask + T) joined to WIN/LOSS by (coin, dir) FIFO ──
RE_ENTER = re.compile(r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \| \[ENTER\] (\w+) (UP|DOWN) '
                      r'drift=([+-][\d.]+)% ask=(\d+)c.*T=(\d+)s')
RE_RES = re.compile(r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \| \[(WIN|LOSS)\] (\w+) (UP|DOWN) @ (\d+)c')

def ts2min(ts):
    h, m, s = ts[11:].split(':')
    return int(ts[8:10]) * 1440 + int(h) * 60 + int(m)

enters, results = [], []
for ln in open('clean_bot.log', errors='ignore'):
    m = RE_ENTER.match(ln)
    if m:
        enters.append({'ts': m.group(1), 'coin': m.group(2), 'dir': m.group(3),
                       'drift': float(m.group(4)), 'ask': int(m.group(5)), 'T': int(m.group(6)),
                       'result': None})
        continue
    m = RE_RES.match(ln)
    if m:
        results.append({'ts': m.group(1), 'res': m.group(2), 'coin': m.group(3),
                        'dir': m.group(4), 'entry': int(m.group(5))})

# join: a result matches the most recent unmatched ENTER of same coin+dir within 20 min
for r in results:
    for e in reversed(enters):
        if (e['result'] is None and e['coin'] == r['coin'] and e['dir'] == r['dir']
                and 0 <= ts2min(r['ts']) - ts2min(e['ts']) <= 20):
            e['result'] = r['res']
            break
trades = [e for e in enters if e['result']]
print(f"joined live trades (ENTER→result, with ask+T): {len(trades)}")

def wr(g):
    if not g: return "  —"
    w = sum(1 for t in g if t['result'] == 'WIN')
    be = sum(t['ask'] for t in g) / len(g)
    return f"WR {100*w/len(g):4.0f}%  n={len(g):<3} avg_ask {be:.0f}c (break-even ~{be:.0f}%)"

# window age at entry = 900 - T
print("\n=== H1a: by entry AGE (900-T seconds into the window) ===")
for lo, hi in [(0, 90), (90, 180), (180, 240), (240, 320)]:
    print(f"  age {lo:>3}-{hi:<3}s: {wr([t for t in trades if lo <= 900-t['T'] < hi])}")

print("\n=== H1b: by ask price ===")
for lo, hi in [(54, 60), (60, 64), (64, 68), (68, 75)]:
    print(f"  ask {lo}-{hi}c: {wr([t for t in trades if lo <= t['ask'] < hi])}")

print("\n=== H1c: the toxic combo — LATE (age>=180s) x EXPENSIVE (ask>=68c) ===")
print(f"  late+expensive : {wr([t for t in trades if 900-t['T'] >= 180 and t['ask'] >= 68])}")
print(f"  late+cheap     : {wr([t for t in trades if 900-t['T'] >= 180 and t['ask'] < 68])}")
print(f"  early+expensive: {wr([t for t in trades if 900-t['T'] < 180 and t['ask'] >= 68])}")
print(f"  early+cheap    : {wr([t for t in trades if 900-t['T'] < 180 and t['ask'] < 68])}")

# ── H2: bet direction vs the window's FIRST-scan drift direction (research CSV) ──
first_dir = {}
for r in csv.DictReader(open('clean_bot_research.csv')):
    key = (r['coin'], r['window_start'])
    if key not in first_dir:                      # first logged scan of that window
        first_dir[key] = r['dir']
import time
def ws_of(e):
    t = time.mktime(time.strptime(e['ts'], "%Y-%m-%d %H:%M:%S"))
    return str(int(t // 900) * 900)

al, opp = [], []
for t in trades:
    fd = first_dir.get((t['coin'], ws_of(t)))
    if fd is None:
        continue
    (al if fd == t['dir'] else opp).append(t)
print("\n=== H2: bet vs the window's FIRST-scan drift direction ===")
print(f"  ALIGNED with first drift : {wr(al)}")
print(f"  AGAINST first drift (whipsaw chase): {wr(opp)}")
