#!/usr/bin/env python3
"""THE CLOCK scorer (CYCLE_LAW amendment 2026-09-01).
Filtered sample: BTC only, maker px in [0.20, 0.85], fair in (0.10, 0.90),
settled entries from late_shadow_state.json. Verdict bar: EV/$ >= +0.03."""
import json, time

S = json.load(open("/home/ubuntu/v3-bot/late_shadow_state.json"))
entries = S.get("entries", S if isinstance(S, list) else [])
sel, rej = [], 0
for e in entries:
    if "won" not in e:
        continue
    coin = e.get("coin", "?")
    px = float(e.get("px", 0))
    fair = float(e.get("fair", -1))
    if coin != "BTC" or not (0.20 <= px <= 0.85) or not (0.10 < fair < 0.90):
        rej += 1
        continue
    sel.append(e)

n = len(sel)
w = sum(1 for e in sel if e["won"])
pnl = sum(float(e.get("pnl", 0)) for e in sel)
risked = sum(float(e["px"]) * 3 for e in sel)   # 3sh nominal ledger
ev = pnl / risked if risked else 0.0
print(f"CLOCK SCORE @ {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}")
print(f"filtered BTC 20-85c fair 10-90c: n={n} {w}W/{n-w}L pnl {pnl:+.2f} "
      f"(3sh fake) EV/$ {ev:+.3f}   [rejected rows: {rej}]")
for e in sel:
    print(f"  {time.strftime('%m-%d %H:%M', time.gmtime(e.get('hs', e.get('ts', 0))))} "
          f"{e.get('dir','?')} @{round(float(e['px'])*100)}c fair {round(float(e['fair'])*100)}c "
          f"{'W' if e['won'] else 'L'} {float(e.get('pnl',0)):+.2f}")
print()
if n and ev >= 0.03:
    print("VERDICT: >= +0.03 -> T3 LIVE AMENDMENT (5sh, stop -$12, one/hour, "
          "last 10m, BTC only). Auto-launch stays revoked; the clock is the go.")
else:
    print("VERDICT: below +0.03 -> KILL late_shadow, archive state, "
          "no new 1H favourite seat. (Per the no-third-wait law.)")
