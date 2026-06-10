"""Backtest: how would a late-window cap (>62c, <9min remaining) affect history?

Reads completed trade events from the analytics backfill + live event log,
joins FIRED with RESOLVED, and computes net PnL impact of two cap variants:
  - Cap A: block ask > 62c when time_remaining < 9 min
  - Cap B: same as A + also block when trend_score < 1.0 with <9 min left

Result printed: trades blocked, breakdown of wins-blocked vs losses-blocked,
net PnL impact (positive = filter helps).
"""
import json, os, sys
from collections import defaultdict

PATHS = [
    "data/trade_events_backfill.jsonl",
    "data/trade_events.jsonl",
]

def load():
    fired_by_id = {}
    resolved_by_id = {}
    for p in PATHS:
        if not os.path.exists(p): continue
        with open(p, errors="ignore") as f:
            for line in f:
                try: e = json.loads(line)
                except Exception: continue
                evt = e.get("event")
                # Try multiple key paths for the trade id
                tid = e.get("trade_id") or (e.get("coin"), e.get("window_start"))
                if evt == "FIRED":
                    fired_by_id[tid] = e
                elif evt == "RESOLVED":
                    resolved_by_id[tid] = e
    return fired_by_id, resolved_by_id

def get_feat(e, key, default=None):
    v = e.get(key)
    if v is None and "features" in e:
        v = e["features"].get(key)
    if v is None:
        return default
    if isinstance(v, str):
        try:
            return float(v.rstrip("%c"))
        except Exception:
            return default
    return v

def main():
    fired, resolved = load()
    print(f"Loaded: {len(fired)} FIRED, {len(resolved)} RESOLVED")

    matched = []
    for tid, f in fired.items():
        r = resolved.get(tid)
        if not r: continue
        ask = get_feat(f, "entry_price") or get_feat(f, "ask") or get_feat(f, "poly_price")
        ts = f.get("ts_epoch") or f.get("ts")
        if not ts: continue
        if isinstance(ts, str):
            try:
                from datetime import datetime
                ts = int(datetime.fromisoformat(ts.replace("Z","+00:00")).timestamp())
            except Exception:
                continue
        ws = f.get("window_start") or get_feat(f, "window_start")
        if not ws: continue
        time_left = (ws + 900) - ts  # seconds remaining
        outcome = r.get("outcome")
        pnl = r.get("pnl") or r.get("net_pnl") or 0
        # If pnl missing, infer rough pnl from cost & outcome
        cost = get_feat(f, "cost") or get_feat(f, "size") or 0
        shares = get_feat(f, "shares") or 0
        if pnl == 0 and ask and shares:
            payout = float(shares) * 1.0 if outcome == "WIN" else 0.0
            pnl = payout - float(cost or shares*ask)
        trend = get_feat(f, "trend_score") or get_feat(f, "trend")
        matched.append({
            "tid": tid, "coin": f.get("coin"), "side": f.get("side") or f.get("direction"),
            "ask": ask, "time_left_sec": time_left, "outcome": outcome,
            "pnl": float(pnl) if pnl else 0.0,
            "trend": trend,
        })

    print(f"Matched FIRED+RESOLVED: {len(matched)}")
    if not matched:
        print("No data — backfill events may lack required fields.")
        return

    # Cap A: block when ask > 0.62 and time_left < 540
    def caught_A(t):
        return t["ask"] is not None and t["ask"] > 0.62 and t["time_left_sec"] < 540

    # Cap B: A + block when ask > 0.55 with weak trend (<1.0) in <9min
    def caught_B(t):
        if caught_A(t): return True
        if t["time_left_sec"] < 540 and t["trend"] is not None and abs(t["trend"]) < 1.0 and t["ask"] and t["ask"] > 0.55:
            return True
        return False

    for name, fn in [("Cap A (>62c, <9min)", caught_A), ("Cap B (A + weak trend)", caught_B)]:
        wins_blocked = sum(1 for t in matched if fn(t) and t["outcome"]=="WIN")
        losses_blocked = sum(1 for t in matched if fn(t) and t["outcome"]=="LOSS")
        pnl_blocked = sum(t["pnl"] for t in matched if fn(t))
        net_impact = -pnl_blocked  # blocking removes those PnL events
        total_blocked = wins_blocked + losses_blocked
        wr_blocked = (100*wins_blocked/total_blocked) if total_blocked else 0
        print()
        print(f"=== {name} ===")
        print(f"  Trades blocked: {total_blocked} ({wins_blocked} wins, {losses_blocked} losses, {wr_blocked:.0f}% WR of blocked)")
        print(f"  PnL of blocked trades: ${pnl_blocked:+.2f}")
        print(f"  Net effect of filter: ${net_impact:+.2f} (positive = filter helps)")

    # Bucket by ask in late window for diagnostics
    print()
    print("=== Diagnostic: late-window (<9min) by ask bucket ===")
    buckets = defaultdict(lambda: [0,0,0.0])
    for t in matched:
        if t["time_left_sec"] >= 540: continue
        if t["ask"] is None: continue
        b = int(t["ask"]*100/5)*5
        buckets[b][0] += 1
        if t["outcome"]=="WIN": buckets[b][1] += 1
        buckets[b][2] += t["pnl"]
    for b in sorted(buckets):
        n,w,p = buckets[b]
        wr = 100*w/n if n else 0
        print(f"  {b}-{b+4}c: n={n} wins={w} WR={wr:.0f}% pnl=${p:+.2f}")

if __name__ == "__main__":
    main()
