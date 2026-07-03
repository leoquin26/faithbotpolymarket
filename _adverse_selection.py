#!/usr/bin/env python3
"""Maker adverse-selection audit: our GTC rests 1c below ask. Do the orders that FILL
win less often than the ones that get CANCELED (unfilled) would have? If canceled
would-have-won >> filled WR, the book is picking us off. Read-only."""
import csv, re, time

RE_ENTER = re.compile(r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \| \[ENTER\] (\w+) (UP|DOWN) ')
RE_FILL = re.compile(r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \| \[FILLED\] (\w+) (UP|DOWN) ')
RE_CXL = re.compile(r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \| \[CANCEL\] unfilled (\w+) (UP|DOWN) ')

def ws_of(ts):
    t = time.mktime(time.strptime(ts, "%Y-%m-%d %H:%M:%S"))
    return str(int(t // 900) * 900)

winner = {}
for r in csv.DictReader(open('clean_bot_research.csv')):
    if r.get('winner'):
        winner[(r['coin'], r['window_start'])] = r['winner']

enters, fills, cxls = {}, set(), []
for ln in open('clean_bot.log', errors='ignore'):
    m = RE_ENTER.match(ln)
    if m:
        enters[(m.group(2), ws_of(m.group(1)), m.group(3))] = True
        continue
    m = RE_FILL.match(ln)
    if m:
        fills.add((m.group(2), ws_of(m.group(1)), m.group(3)))
        continue
    m = RE_CXL.match(ln)
    if m:
        cxls.append((m.group(2), ws_of(m.group(1)), m.group(3)))

def wr(keys, lab):
    w = l = 0
    for coin, ws, d in keys:
        win = winner.get((coin, ws))
        if win:
            w += (win == d); l += (win != d)
    n = w + l
    print(f'  {lab}: {w}W/{l}L = {100*w/n:.0f}% (n={n})' if n else f'  {lab}: no graded samples')

print('maker adverse-selection audit')
wr(list(fills), 'FILLED orders (we got in)        ')
wr([k for k in cxls if k not in fills], 'CANCELED unfilled (we missed)    ')
