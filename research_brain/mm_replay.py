#!/usr/bin/env python3
"""TWO-SIDED MAKER REPLAY on late_book.jsonl (1Hz top-2 book, 15m windows).
The only 15m idea not yet killed on our tape (AGENT_STATUS §4).

Strategy per (window, coin): at the first snapshot with tr in [Q_HI, Q_LO],
post a bid on UP at UP best-bid and a bid on DOWN at DOWN best-bid (join the
queue). Fill proxy (conservative on price, optimistic on queue): a leg fills
when a later snapshot shows that outcome's best ASK <= our bid price
(market traded through our level). Hold every fill to settlement.
PnL per leg: win -> (1 - px), lose -> -px.  Maker fee $0.
Rebate estimate per filled share = REBATE * fee(px), fee(px)=RATE*px*(1-px)
(crypto taker curve: 7c*p(1-p) => 1.75c at 50c; makers get 20% of the pool,
proportional to their share of executed maker volume — i.e. ~20% of the fee
paid on OUR fills, fee-curve weighted). Reported separately so it can be
zeroed.  Winners from clean_bot_research.csv (window_start, coin, winner)."""
import csv, json, sys, time
from collections import defaultdict

Q_HI, Q_LO = int(sys.argv[1]) if len(sys.argv) > 1 else 780, int(sys.argv[2]) if len(sys.argv) > 2 else 600
RATE, REBATE = 0.07, 0.20
V3 = "/home/ubuntu/v3-bot"

winners = {}
for r in csv.DictReader(open(V3 + "/clean_bot_research.csv")):
    w = (r.get("winner") or "").upper()
    if w in ("UP", "DOWN"):
        try:
            winners[(int(float(r["window_start"])), r["coin"])] = w
        except Exception:
            pass
print("winner keys:", len(winners), "| quoting at tr in [%d,%d]s" % (Q_HI, Q_LO), flush=True)

# state per (ws, coin): {"q": {"UP": px, "DOWN": px} or None, "fill": {"UP": bool, ...}, "t0": tr}
state = {}
def key(r): return (r["ws"], r["c"])

t_start = time.time()
n_lines = 0
with open(V3 + "/late_book.jsonl") as f:
    for ln in f:
        n_lines += 1
        try:
            r = json.loads(ln)
        except Exception:
            continue
        k = key(r)
        if k not in winners:
            continue
        st = state.setdefault(k, {"q": {}, "fill": {}, "tr0": None})
        o = r["o"]
        b = r["b"][0][0] if r["b"] else None
        a = r["a"][0][0] if r["a"] else None
        tr = r["tr"]
        # 1) quote: first snapshot inside the quote band, per outcome
        if o not in st["q"] and Q_LO <= tr <= Q_HI and b and 0.02 <= b <= 0.98:
            st["q"][o] = b
            st["fill"][o] = False
            st["tr0"] = tr
            continue
        # 2) fill proxy: later snapshot with best ask at/below our bid
        if o in st["q"] and not st["fill"][o] and tr < st["tr0"] and a and a <= st["q"][o]:
            st["fill"][o] = True

print("scanned %d lines in %.0fs" % (n_lines, time.time() - t_start), flush=True)

# ---- settle ----
rows = []
for (ws, c), st in state.items():
    if len(st["q"]) < 2:
        continue
    wn = winners[(ws, c)]
    legs = {}
    for o in ("UP", "DOWN"):
        if st["fill"].get(o):
            px = st["q"][o]
            pnl = (1 - px) if wn == o else -px
            legs[o] = (px, pnl)
    rows.append((c, st["q"]["UP"], st["q"]["DOWN"], legs, wn))

def fee(px): return RATE * px * (1 - px)

def report(label, sel):
    if not sel:
        print(f"{label}: n=0"); return
    n = len(sel)
    both = [x for x in sel if len(x[3]) == 2]
    one = [x for x in sel if len(x[3]) == 1]
    none = n - len(both) - len(one)
    pnl_both = sum(v[1] for x in both for v in x[3].values())
    pnl_one = sum(v[1] for x in one for v in x[3].values())
    risk = sum(v[0] for x in sel for v in x[3].values())
    reb = sum(REBATE * fee(v[0]) for x in sel for v in x[3].values())
    filled_shares = sum(len(x[3]) for x in sel)
    paircost = sum(x[1] + x[2] for x in sel) / n
    print(f"{label}: windows={n} | double-fill {len(both)/n*100:.0f}% single {len(one)/n*100:.0f}% none {none/n*100:.0f}% "
          f"| pair cost@bid {paircost:.3f}")
    print(f"   PnL/share: double-fill legs {pnl_both/(2*len(both)) if both else 0:+.4f}  single-fill legs {pnl_one/len(one) if one else 0:+.4f}  "
          f"| gross EV/$ {((pnl_both+pnl_one)/risk if risk else 0):+.4f}  rebate/$ {(reb/risk if risk else 0):+.4f}  "
          f"=> net EV/$ {((pnl_both+pnl_one+reb)/risk if risk else 0):+.4f}  (filled legs {filled_shares})")

print()
report("ALL coins", rows)
for c in ("BTC", "ETH", "SOL"):
    report(c, [x for x in rows if x[0] == c])
# single-fill anatomy: which side fills alone and does it lose?
one = [x for x in rows if len(x[3]) == 1]
fav_alone = [x for x in one if list(x[3].keys())[0] == ("UP" if x[1] > x[2] else "DOWN")]
dog_alone = [x for x in one if list(x[3].keys())[0] != ("UP" if x[1] > x[2] else "DOWN")]
def legs_ev(sel):
    p = sum(v[1] for x in sel for v in x[3].values()); r = sum(v[0] for x in sel for v in x[3].values())
    return (p / r if r else 0, len(sel))
print("\nsingle-fill anatomy: favourite leg alone EV/$ %+.3f (n=%d) | longshot leg alone EV/$ %+.3f (n=%d)"
      % (*legs_ev(fav_alone), *legs_ev(dog_alone)))
