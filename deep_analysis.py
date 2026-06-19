#!/usr/bin/env python3
"""Deep analysis of CleanBot: join ENTER(drift) -> FILLED(shares) -> WIN/LOSS to
find improvement levers (coin x dir, drift magnitude, entry) and the sizing/EV
basis for compounding. Read-only on clean_bot.log."""
import re, os, collections

LOG = os.path.expanduser("~/v3-bot/clean_bot.log")
ENTER = re.compile(r"\[ENTER\] (BTC|ETH|SOL|XRP) (UP|DOWN) drift=([-+][\d.]+)% ask=(\d+)c "
                   r"-> maker (\d+)c x(\d+) T=(\d+)s")
RES = re.compile(r"\[(WIN|LOSS)\] (BTC|ETH|SOL|XRP) (UP|DOWN) @ (\d+)c -> (UP|DOWN) \| ([-+][\d.]+)")

enters = collections.deque()   # FIFO of (coin,dir,maker,drift,ask,T)
outs = []
for ln in open(LOG, encoding="utf-8", errors="ignore"):
    e = ENTER.search(ln)
    if e:
        enters.append(dict(coin=e.group(1), dir=e.group(2), drift=float(e.group(3)),
                           ask=int(e.group(4)), maker=int(e.group(5)), T=int(e.group(7))))
    r = RES.search(ln)
    if r:
        outs.append(dict(res=r.group(1), coin=r.group(2), dir=r.group(3),
                         entry=int(r.group(4)), winner=r.group(5), pnl=float(r.group(6))))

# join each outcome to the earliest matching ENTER (coin,dir,maker==entry)
rows = []
el = list(enters)
for o in outs:
    for i, e in enumerate(el):
        if e and e["coin"] == o["coin"] and e["dir"] == o["dir"] and e["maker"] == o["entry"]:
            o["drift"] = abs(e["drift"]); o["ask"] = e["ask"]; o["T"] = e["T"]; el[i] = None
            break
    rows.append(o)

n = len(rows); w = sum(1 for r in rows if r["res"] == "WIN"); net = sum(r["pnl"] for r in rows)
wins = [r["pnl"] for r in rows if r["res"] == "WIN"]
losses = [r["pnl"] for r in rows if r["res"] == "LOSS"]
print(f"=== {n} trades | {w}W/{n-w}L = {100*w/n:.0f}% | net ${net:+.2f} ===")
print(f"avg win +${sum(wins)/len(wins):.2f} | avg loss ${sum(losses)/len(losses):.2f} | "
      f"EV/trade ${net/n:+.3f}\n")

def grp(key, label, rs=None):
    rs = rs if rs is not None else rows
    d = collections.defaultdict(lambda: [0, 0, 0.0])
    for r in rs:
        k = key(r)
        if k is None:
            continue
        d[k][0] += 1; d[k][1] += (r["res"] == "WIN"); d[k][2] += r["pnl"]
    print(f"--- {label} ---")
    for k in sorted(d, key=lambda x: str(x)):
        nn, ww, pp = d[k]
        print(f"  {str(k):<14} n={nn:<3} WR={100*ww/nn:4.0f}%  net=${pp:+.2f}  EV=${pp/nn:+.2f}")
    print()

grp(lambda r: f"{r['coin']} {r['dir']}", "coin x direction")
grp(lambda r: ("7-10bps" if r.get("drift", 0) < .10 else "10-15bps" if r["drift"] < .15
               else "15-25bps" if r["drift"] < .25 else "25bps+") if "drift" in r else None,
    "drift magnitude (early move)")
grp(lambda r: "<=58c" if r["entry"] <= 58 else "59-62c" if r["entry"] <= 62 else "63-66c", "entry")

# what if we DROP the weak cells?
def sim(name, keep):
    rs = [r for r in rows if keep(r)]
    if not rs:
        print(f"  {name}: none"); return
    ww = sum(1 for r in rs if r["res"] == "WIN"); pp = sum(r["pnl"] for r in rs)
    print(f"  {name:<34} n={len(rs):<3} WR={100*ww/len(rs):4.0f}%  net=${pp:+.2f}")

print("--- counterfactual filters (vs actual net above) ---")
sim("actual (all)", lambda r: True)
sim("SOL only", lambda r: r["coin"] == "SOL")
sim("drop ETH-DOWN", lambda r: not (r["coin"] == "ETH" and r["dir"] == "DOWN"))
sim("entry <=62c only", lambda r: r["entry"] <= 62)
sim("SOL + entry<=62c", lambda r: r["coin"] == "SOL" and r["entry"] <= 62)
sim("drop ETH-DOWN + entry<=64c", lambda r: not (r["coin"]=="ETH" and r["dir"]=="DOWN") and r["entry"] <= 64)
