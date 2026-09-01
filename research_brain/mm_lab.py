#!/usr/bin/env python3
"""MM LAB — the DEFENSIVE MAKER experiment on late_book.jsonl (1Hz, last ~5.5 min).

Something new, built from our own finding: a single-filled bid in the last
minutes loses 97-99% of the time, i.e. THE FILL IS THE RESOLUTION SHOWING UP IN
THE BOOK. So: quote both sides for rewards/spread, but PULL a side the moment
the book shows the sweep forming against it. Pull triggers tested (all use
only what a 1Hz book feed gives us live):

  T_ASKDROP(k, w) : pull bid on X when X's best ASK dropped >= k ticks within
                    the last w seconds (X is being sold down = X is dying)
  T_DEPTH(r, w)   : pull bid on X when X's top-2 bid depth shrank to <= r of
                    its level w seconds ago (queue ahead evaporating)
  T_OTHER(k, w)   : pull bid on X when the OTHER outcome's best BID rose
                    >= k ticks within w seconds (the winner is being bought)
Conservative timing: if the fill event (ask <= our bid) happens in the SAME
second as the trigger, we count it as FILLED (we were too late).
Reported per rule: single-fill rate, EV/$ (spread + rebate), and REWARD-SECONDS
= seconds both quotes were alive within 1.5c of the size-adjusted mid
(the liquidity-rewards qualifying condition) as % of baseline.
"""
import csv, json, sys, time
from collections import defaultdict, deque

import os
V3 = os.environ.get("TAPE", "/home/ubuntu/v3-bot")   # local: TAPE=<scratchpad>/tape
RATE, REBATE, BAND = 0.07, 0.20, 0.015
Q_HI = 330          # tape starts ~330s out; quote from the first snapshot
T_CANCEL = 60       # hard cancel of any resting quote at T-60s (settlement chaos)
COIN = sys.argv[1] if len(sys.argv) > 1 else None

RULES = {
    "baseline (no pull)":       None,
    "askdrop 1t/3s":            ("askdrop", 1, 3),
    "askdrop 2t/5s":            ("askdrop", 2, 5),
    "other-bid +1t/3s":         ("other", 1, 3),
    "other-bid +2t/5s":         ("other", 2, 5),
    "depth <=50%/5s":           ("depth", 0.5, 5),
    "askdrop1/3s OR other1/3s": ("combo", 1, 3),
}

winners = {}
for r in csv.DictReader(open(V3 + "/clean_bot_research.csv")):
    w = (r.get("winner") or "").upper()
    if w in ("UP", "DOWN"):
        try:
            winners[(int(float(r["window_start"])), r["coin"])] = w
        except Exception:
            pass

# per window: chronological list of (tr, o, bids, asks)
win = defaultdict(list)
n = 0
t0 = time.time()
with open(V3 + "/late_book.jsonl") as f:
    for ln in f:
        n += 1
        try:
            r = json.loads(ln)
        except Exception:
            continue
        k = (r["ws"], r["c"])
        if k not in winners:
            continue
        if COIN and r["c"] != COIN:
            continue
        bids, asks = r["b"], r["a"]
        win[k].append((r["tr"], r["o"],
                       bids[0][0] if bids else None,
                       asks[0][0] if asks else None,
                       sum(sz for _, sz in bids[:2]) if bids else 0))
print(f"loaded {len(win)} windows from {n} lines in {time.time()-t0:.0f}s", flush=True)

def fee(px): return RATE * px * (1 - px)

def simulate(rule):
    stats = {"win": 0, "double": 0, "single": 0, "none": 0, "pnl": 0.0, "risk": 0.0,
             "reb": 0.0, "rsec": 0, "single_pnl": 0.0, "single_n": 0}
    for (ws, c), rows in win.items():
        wn = winners[(ws, c)]
        rows.sort(key=lambda x: -x[0])          # descending tr = chronological
        # per outcome state
        q = {}          # o -> our bid px (None when pulled/none)
        filled = {}
        hist = {"UP": deque(), "DOWN": deque()}   # (tr, best_bid, best_ask, bid_depth)
        last = {}       # o -> (bb, ba, depth)
        for tr, o, bb, ba, dep in rows:
            last[o] = (bb, ba, dep)
            h = hist[o]; h.append((tr, bb, ba, dep))
            while h and h[0][0] - tr > 10: h.popleft()
            # 1) initial quote at first usable snapshot
            if o not in q and tr <= Q_HI and bb and 0.02 <= bb <= 0.98:
                q[o] = bb; filled[o] = False
                continue
            if o not in q or filled[o] or q[o] is None:
                continue
            # 2) hard cancel near settlement
            if tr < T_CANCEL:
                q[o] = None; continue
            # 3) pull trigger?
            pulled = False
            if rule:
                kind, k, w = rule
                past = [x for x in h if x[0] - tr >= w and x[0] - tr <= w + 2]
                if past:
                    p_bb, p_ba, p_dep = past[-1][1], past[-1][2], past[-1][3]
                    if kind in ("askdrop", "combo") and ba and p_ba and (p_ba - ba) >= k * 0.01 - 1e-9:
                        pulled = True
                    if kind == "depth" and p_dep and dep <= k * p_dep:
                        pulled = True
                if kind in ("other", "combo") and not pulled:
                    oo = "DOWN" if o == "UP" else "UP"
                    oh = hist[oo]
                    opast = [x for x in oh if x[0] - tr >= w and x[0] - tr <= w + 2]
                    if opast and oo in last and last[oo][0] and opast[-1][1]:
                        if (last[oo][0] - opast[-1][1]) >= k * 0.01 - 1e-9:
                            pulled = True
            # 4) fill check FIRST if same-second (too late to pull)
            if ba and ba <= q[o]:
                filled[o] = True
                continue
            if pulled:
                q[o] = None
                continue
            # 5) reward-seconds: both sides alive within BAND of mid
            if o == "DOWN" and q.get("UP") is not None and not filled.get("UP") \
                    and "UP" in last and last["UP"][0] and last["UP"][1]:
                mid = (last["UP"][0] + last["UP"][1]) / 2
                if abs(mid - q["UP"]) <= BAND and abs((1 - mid) - q["DOWN"]) <= BAND:
                    stats["rsec"] += 1
        if len(filled) < 2:
            continue
        stats["win"] += 1
        legs = [(o, q_) for o, q_ in q.items() if filled.get(o)]
        # px for filled legs must be the quote price at fill time: q[o] stays the
        # posted price until pulled; filled legs never get pulled, so q[o] is it.
        legs = [(o, px) for o, px in legs if px is not None]
        nf = len(legs)
        stats["double" if nf == 2 else "single" if nf == 1 else "none"] += 1
        for o, px in legs:
            pnl = (1 - px) if wn == o else -px
            stats["pnl"] += pnl; stats["risk"] += px; stats["reb"] += REBATE * fee(px)
            if nf == 1:
                stats["single_pnl"] += pnl; stats["single_n"] += 1
    return stats

base_rsec = None
print(f"\n{'rule':28s} {'win':>5s} {'dbl%':>5s} {'sgl%':>5s} {'none%':>5s} {'EV/$ gross':>10s} {'net w/rebate':>12s} {'single EV/$':>11s} {'reward-sec':>10s}")
for name, rule in RULES.items():
    s = simulate(rule)
    n_ = s["win"] or 1
    ev = s["pnl"] / s["risk"] if s["risk"] else 0
    net = (s["pnl"] + s["reb"]) / s["risk"] if s["risk"] else 0
    sev = s["single_pnl"] / s["single_n"] if s["single_n"] else 0
    if base_rsec is None: base_rsec = s["rsec"] or 1
    print(f"{name:28s} {s['win']:5d} {s['double']/n_*100:5.0f} {s['single']/n_*100:5.0f} {s['none']/n_*100:5.0f} "
          f"{ev:+10.4f} {net:+12.4f} {sev:+11.3f} {s['rsec']/base_rsec*100:9.0f}%", flush=True)
