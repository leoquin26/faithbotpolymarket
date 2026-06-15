#!/usr/bin/env python3
"""Truthful daily scorecard — re-checks every bet against the REAL chain outcome.

Why: the bot's own [WIN]/[LOSS] log can be wrong (phantom-win resolution bug,
Jun 15). This re-resolves each trade via Polymarket gamma (ground truth),
flags any bot-vs-chain mismatch, and reports TRUE win-rate / P&L — broken down
by edge bucket (does the new 5% edge gate actually help?) and by coin.

Usage: python3 _scorecard.py [YYYY-MM-DD]   (default: today)
"""
import re, sys, datetime
import poly_resolution as pr

DAY = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().isoformat()
LOG = f"logs/bot_{DAY}.log"

ORDER = re.compile(r'\[ORDER\] (\w+) (UP|DOWN) \| \w+ @ (\d+)c \| (\d+) shares.*Edge ([-+\d.]+)%')
FILLED = re.compile(r'\[FILLED\] (\w+) (UP|DOWN) \| (\d+) shares @ (\d+)c')
RES = re.compile(r'\[RESOLVE\] (\w+) (UP|DOWN) \|.*?(\w+-updown-\w+-(\d+))?.*?(WIN|LOSS)')
RESLINE = re.compile(r'\[RESOLVE\] (\w+) (UP|DOWN) \|.*winner=(\w+).*\| (WIN|LOSS)')
SLUG = re.compile(r'(\w+)-updown-15m-(\d+)')
RECWL = re.compile(r'\[(WIN|LOSS)\] (\w+) (UP|DOWN) \|.*Entry: (\d+)c x(\d+)')


def main():
    try:
        lines = open(LOG, encoding="utf-8", errors="ignore").readlines()
    except FileNotFoundError:
        print(f"no log for {DAY}"); return

    # fills with edge (match [ORDER] edge to following [FILLED]); recorded W/L
    fills = []          # (coin, side, entry, shares, edge)
    recorded = []       # (result, coin, side, entry, shares)
    ws_by_coin = {}     # coin -> list of window_starts seen (chronological)
    last_edge = {}
    for ln in lines:
        mo = ORDER.search(ln)
        if mo:
            last_edge[(mo.group(1), mo.group(2))] = float(mo.group(5))
            continue
        mf = FILLED.search(ln)
        if mf:
            c, s, sh, e = mf.group(1), mf.group(2), int(mf.group(3)), int(mf.group(4)) / 100
            fills.append([c, s, e, sh, last_edge.get((c, s))])
            continue
        mw = RECWL.search(ln)
        if mw:
            recorded.append((mw.group(1), mw.group(2), mw.group(3), int(mw.group(4)) / 100, int(mw.group(5))))
        # only [RESOLVE]/[RESOLVE PENDING] lines carry slugs for ACTUAL positions
        if "[RESOLVE" in ln:
            for sm in SLUG.finditer(ln):
                _c = sm.group(1).upper()
                ws_by_coin.setdefault(_c, [])
                if int(sm.group(2)) not in ws_by_coin[_c]:
                    ws_by_coin[_c].append(int(sm.group(2)))

    # match each recorded trade to its window_start (FIFO per coin) and re-resolve
    idx = {c: 0 for c in ws_by_coin}
    print(f"=== SCORECARD {DAY} ===")
    print(f"{'coin':<4}{'side':<5}{'edge':>6} {'entry':>6} {'bot':>5} {'CHAIN':>6}  {'$pnl':>7}  flag")
    true_pnl = 0.0
    n = wins = mism = 0
    by_edge = {"hi(>=5%)": [0, 0, 0.0], "lo(<5%)": [0, 0, 0.0]}   # n, wins, pnl
    by_coin = {}
    # pair recorded W/L with fills (same order) for edge
    for i, (rec_res, c, s, entry, sh) in enumerate(recorded):
        edge = fills[i][4] if i < len(fills) and fills[i][0] == c and fills[i][1] == s else None
        # find window_start
        wslist = ws_by_coin.get(c, [])
        ws = wslist[idx[c]] if idx[c] < len(wslist) else (wslist[-1] if wslist else 0)
        idx[c] = min(idx[c] + 1, len(wslist))
        true = None
        try:
            r = pr.resolve_position(c, s, ws, "15m")
            if r and r.get("winner"):
                true = "WIN" if r["winner"] == s else "LOSS"
        except Exception:
            pass
        true = true or rec_res            # fall back to recorded if gamma unsure
        won = (true == "WIN")
        pnl = sh * (1 - entry) if won else -sh * entry
        true_pnl += pnl; n += 1; wins += 1 if won else 0
        flag = "" if true == rec_res else f"<<< MISMATCH (bot said {rec_res})"
        if true != rec_res:
            mism += 1
        eb = "hi(>=5%)" if (edge is not None and edge >= 5.0) else "lo(<5%)"
        by_edge[eb][0] += 1; by_edge[eb][1] += 1 if won else 0; by_edge[eb][2] += pnl
        bc = by_coin.setdefault(c, [0, 0, 0.0]); bc[0] += 1; bc[1] += 1 if won else 0; bc[2] += pnl
        es = f"{edge:>5.1f}%" if edge is not None else "   ?  "
        print(f"{c:<4}{s:<5}{es} {entry*100:>5.0f}c {rec_res:>5} {true:>6}  {pnl:>+6.2f}  {flag}")

    print("-" * 60)
    print(f"TRUE: {n} trades, {wins} wins ({100*wins/max(1,n):.0f}%), net ${true_pnl:+.2f}"
          + (f"   ⚠ {mism} BOT-vs-CHAIN MISMATCH(es)" if mism else "   (no mismatches)"))
    print("by edge bucket (does the 5% gate help?):")
    for k, (nn, w, p) in by_edge.items():
        if nn:
            print(f"   {k:<9} n={nn} WR={100*w/nn:.0f}% net=${p:+.2f}")
    print("by coin:")
    for c, (nn, w, p) in sorted(by_coin.items()):
        print(f"   {c:<4} n={nn} WR={100*w/nn:.0f}% net=${p:+.2f}")


if __name__ == "__main__":
    main()
