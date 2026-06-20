#!/usr/bin/env python3
"""Find an indicator that guides ETH direction. Tests the cross-coin-confirmation
hypothesis (ETH follows the market: trust ETH only when SOL agrees), plus ETH by
direction / drift / market-agreement. Read-only on clean_bot.log."""
import re, os, collections, datetime

LOG = os.path.expanduser("~/v3-bot/clean_bot.log")
ENT = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d).*\[ENTER\] (ETH|SOL) (UP|DOWN) "
                 r"drift=([-+][\d.]+)% ask=(\d+)c -> maker (\d+)c x(\d+)")
RES = re.compile(r"\[(WIN|LOSS)\] (ETH|SOL) (UP|DOWN) @ (\d+)c -> (UP|DOWN)")

enters, outs = [], []
for ln in open(LOG, encoding="utf-8", errors="ignore"):
    e = ENT.search(ln)
    if e:
        ep = int(datetime.datetime.strptime(e.group(1), "%Y-%m-%d %H:%M:%S").timestamp())
        enters.append(dict(coin=e.group(2), dir=e.group(3), drift=abs(float(e.group(4))),
                           ask=int(e.group(5)), maker=int(e.group(6)), win=ep // 900))
    r = RES.search(ln)
    if r:
        outs.append(dict(res=r.group(1), coin=r.group(2), dir=r.group(3), entry=int(r.group(4))))

# index ENTERs by window for cross-coin lookup
by_win = collections.defaultdict(list)
for e in enters:
    by_win[e["win"]].append(e)

# join outcomes to ENTERs
el = list(enters); rows = []
for o in outs:
    for i, e in enumerate(el):
        if e and e["coin"] == o["coin"] and e["dir"] == o["dir"] and e["maker"] == o["entry"]:
            o.update(drift=e["drift"], ask=e["ask"], win=e["win"]); el[i] = None; break
    if "win" in o:
        rows.append(o)

eth = [r for r in rows if r["coin"] == "ETH"]
def wr(rs): return (100 * sum(x["res"] == "WIN" for x in rs) / len(rs)) if rs else float("nan")
print(f"=== ETH trades: n={len(eth)} WR={wr(eth):.0f}% ===\n")

# cross-coin confirmation: did SOL signal the SAME direction in the same window?
conf, solo = [], []
for r in eth:
    sol_same = any(e["coin"] == "SOL" and e["dir"] == r["dir"] for e in by_win.get(r["win"], []))
    (conf if sol_same else solo).append(r)
print("--- CROSS-COIN: ETH when SOL agrees (same window, same dir) vs ETH alone ---")
print(f"  SOL CONFIRMS ETH   n={len(conf):<3} WR={wr(conf):4.0f}%")
print(f"  ETH solo (no SOL)  n={len(solo):<3} WR={wr(solo):4.0f}%")

# also: SOL signalled the OPPOSITE direction in the window (divergence)?
div = []
for r in eth:
    sol_opp = any(e["coin"] == "SOL" and e["dir"] != r["dir"] for e in by_win.get(r["win"], []))
    if sol_opp:
        div.append(r)
print(f"  SOL DIVERGES (opp dir) n={len(div):<3} WR={wr(div):4.0f}%")

print("\n--- ETH by direction ---")
for d in ("UP", "DOWN"):
    g = [r for r in eth if r["dir"] == d]
    print(f"  ETH {d:<4} n={len(g):<3} WR={wr(g):4.0f}%")
print("\n--- ETH by drift magnitude ---")
for lo, hi, lab in [(0, .10, "7-10bps"), (.10, .15, "10-15bps"), (.15, 9, "15bps+")]:
    g = [r for r in eth if lo <= r["drift"] < hi]
    print(f"  drift {lab:<9} n={len(g):<3} WR={wr(g):4.0f}%")
print("\n--- ETH by market agreement (ask) ---")
for lo, hi, lab in [(0, 56, "<56c"), (56, 63, "56-62c"), (63, 99, ">=63c")]:
    g = [r for r in eth if lo <= r["ask"] < hi]
    print(f"  ask {lab:<7} n={len(g):<3} WR={wr(g):4.0f}%")

print("\n--- best ETH cell: SOL confirms AND drift>=10 ---")
g = [r for r in conf if r["drift"] >= .10]
print(f"  n={len(g)} WR={wr(g):.0f}%  (vs all ETH {wr(eth):.0f}%)")
