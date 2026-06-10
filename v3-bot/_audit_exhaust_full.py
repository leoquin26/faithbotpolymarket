"""
FULL EXHAUST AUDIT (apr28)

Resolves EVERY unique (coin, side, window) ABSTAIN, DAMPEN, FLIP from the
last N days and measures:

1. Actual hit rate of blocked signals (already done — recap)
2. Hit rate of DAMPENED signals (do we hit our half-size dampened bets?)
3. Hit rate of FLIPPED signals (does flip work?)
4. Net EXHAUST contribution: PnL diff between current state and "no EXHAUST"
5. Option A simulation: what if we deploy entry>=63c override?
6. Compare to actually-fired trades during same window
"""
import json, time, sys, ast
from collections import defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/home/ubuntu/v3-bot")
import force_tor
import requests

PATH = "/home/ubuntu/v3-bot/data/trade_events.jsonl"
DAYS = 7

cutoff = (datetime.now(timezone.utc) - timedelta(days=DAYS)).timestamp()
trades = {}
with open(PATH) as f:
    for line in f:
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("ts_epoch", 0) < cutoff:
            continue
        tid = e.get("trade_id")
        if not tid: continue
        ev = e.get("event", "?")
        rec = trades.setdefault(tid, {})
        if ev == "SIGNAL":
            rec.update({
                "coin": e.get("coin"), "side": e.get("side"),
                "entry": e.get("entry"), "prob": e.get("prob"),
                "edge": e.get("edge"), "trend": e.get("trend_score"),
                "ws": e.get("window_start"), "tok": e.get("token_id"),
                "conf": e.get("confidence"), "ts": e.get("ts_epoch"),
            })
        elif ev == "EXHAUST":
            rec["action"] = e.get("action")
            rec["score"] = e.get("score")
        elif ev == "FIRED":
            rec["fired"] = True
            rec["fired_size"] = e.get("size") or e.get("size_usd") or e.get("cost")
            rec["fired_shares"] = e.get("shares")
        elif ev == "RESOLVED":
            rec["actual_outcome"] = e.get("outcome")
            rec["actual_pnl"] = e.get("pnl")

# Sessions for EXHAUST
recs_by_action = defaultdict(list)
for r in trades.values():
    a = r.get("action")
    if a in ("ABSTAIN", "DAMPEN", "FLIP", "CLEAN") and r.get("ws") and r.get("coin"):
        recs_by_action[a].append(r)

print("=== Action distribution (full sample) ===")
for a, lst in recs_by_action.items():
    print(f"  {a:8s}  n={len(lst)}")

# Dedupe per (coin, side, window) — keep earliest by ts
def dedupe(records):
    out = {}
    for r in records:
        if not r.get("ts"): continue
        k = (r["coin"], r["side"], r["ws"])
        if k not in out or r["ts"] < out[k]["ts"]:
            out[k] = r
    return list(out.values())

now_ts = datetime.now(timezone.utc).timestamp()
def filter_resolved(lst):
    return [r for r in lst if r["ws"] + 900 + 60 < now_ts]

abstain_u = filter_resolved(dedupe(recs_by_action.get("ABSTAIN", [])))
dampen_u  = filter_resolved(dedupe(recs_by_action.get("DAMPEN",  [])))
flip_u    = filter_resolved(dedupe(recs_by_action.get("FLIP",    [])))
clean_u   = filter_resolved(dedupe(recs_by_action.get("CLEAN",   [])))

print("")
print("=== Unique (coin, side, window) records with resolved windows ===")
print(f"  ABSTAIN: {len(abstain_u)}")
print(f"  DAMPEN:  {len(dampen_u)}")
print(f"  FLIP:    {len(flip_u)}")
print(f"  CLEAN:   {len(clean_u)}")

# HTTP via Tor
COIN_TO_SLUG = {"BTC": "btc", "ETH": "eth", "SOL": "sol", "XRP": "xrp"}
sess = requests.Session()

# Cache to avoid duplicate queries
_cache = {}
def query_won(coin, side, ws):
    key = (coin, side, ws)
    if key in _cache: return _cache[key]
    slug_coin = COIN_TO_SLUG.get(coin.upper(), coin.lower())
    slug = f"{slug_coin}-updown-15m-{ws}"
    try:
        r = sess.get(f"https://gamma-api.polymarket.com/events?slug={slug}", timeout=8)
        if r.status_code != 200:
            _cache[key] = None
            return None
        d = r.json() or []
        if not (d and isinstance(d, list) and d[0].get("markets")):
            _cache[key] = None
            return None
        m = d[0]["markets"][0]
        op = m.get("outcomePrices", [])
        if isinstance(op, str): op = ast.literal_eval(op)
        outs = m.get("outcomes", [])
        if isinstance(outs, str): outs = ast.literal_eval(outs)
        target = "Up" if str(side).upper() == "UP" else "Down"
        if target in outs and len(op) == 2:
            idx = outs.index(target)
            price = float(op[idx])
            won = True if price >= 0.98 else (False if price <= 0.02 else None)
            _cache[key] = won
            return won
        _cache[key] = None
        return None
    except Exception:
        _cache[key] = None
        return None

def resolve_all(records, label):
    out = []
    for i, r in enumerate(records):
        if i and i % 30 == 0:
            print(f"    {label}: {i}/{len(records)} resolved={len(out)}")
        won = query_won(r["coin"], r["side"], r["ws"])
        if won is None: continue
        out.append({**r, "won": won})
        time.sleep(0.04)
    return out

print("")
print("=== Resolving via Polymarket Gamma (via Tor) ===")
abstain_r = resolve_all(abstain_u, "ABSTAIN")
print(f"  ABSTAIN resolved: {len(abstain_r)}/{len(abstain_u)}")
dampen_r = resolve_all(dampen_u, "DAMPEN")
print(f"  DAMPEN resolved:  {len(dampen_r)}/{len(dampen_u)}")
flip_r = resolve_all(flip_u, "FLIP")
print(f"  FLIP resolved:    {len(flip_r)}/{len(flip_u)}")

def report(label, records):
    if not records:
        print(f"  {label}: no resolutions")
        return
    n = len(records)
    w = sum(1 for r in records if r["won"])
    wr = w/n
    pnl = 0.0
    for r in records:
        size = 8.0
        e = r.get("entry") or 0.6
        if r["won"]: pnl += size * (1.0/e - 1.0)
        else: pnl -= size
    print(f"  {label:8s}  n={n:4d}  W={w:4d}  WR={wr*100:5.1f}%  hypoth.PnL=${pnl:+8.2f}  avg=${pnl/n:+.2f}/trade")
    return n, w, wr, pnl

print("")
print("=" * 75)
print("RAW HIT RATES + HYPOTHETICAL PnL ($8/trade if all had fired)")
print("=" * 75)
report("ABSTAIN", abstain_r)
report("DAMPEN",  dampen_r)
report("FLIP",    flip_r)

# DAMPEN — for these, we ACTUALLY fire but at half size with -15% prob.
# Did the dampens reduce profitable trades?
print("")
print("=== DAMPEN by entry band ===")
b = defaultdict(lambda: [0, 0])
for r in dampen_r:
    e = r.get("entry") or 0.6
    if e < 0.55: bk = "<55c"
    elif e < 0.60: bk = "55-59c"
    elif e < 0.63: bk = "60-62c"
    elif e < 0.70: bk = "63-69c"
    else: bk = ">=70c"
    b[bk][0] += 1
    if r["won"]: b[bk][1] += 1
for k in ["<55c","55-59c","60-62c","63-69c",">=70c"]:
    n, w = b[k]
    if n: print(f"  {k:7s}  n={n:3d}  WR={w/n*100:.1f}%")

# FLIP — for these, we reverse direction. Did the flip work?
print("")
print("=== FLIP analysis ===")
print("  (won=True means flipped direction won, i.e., FLIP was correct)")
for r in flip_r:
    print(f"    {r['coin']} {r['side']} -> flipped, original_won={not r['won']}, score={r.get('score',0):.2f}, entry={r.get('entry',0)*100:.0f}c")

# Option A simulation: ABSTAIN events where entry >= 63c AND score < 0.65
# would now go through as DAMPEN (half size).
print("")
print("=" * 75)
print("OPTION A SIMULATION")
print("=" * 75)
print("Rule: if ABSTAIN AND entry >= 63c AND score < 0.65, downgrade to DAMPEN")
print("")

opt_a_let_through = [r for r in abstain_r
                     if (r.get("entry") or 0) >= 0.63
                     and (r.get("score") or 0) < 0.65]
opt_a_kept_blocked = [r for r in abstain_r
                      if (r.get("entry") or 0) < 0.63
                      or (r.get("score") or 0) >= 0.65]

n1 = len(opt_a_let_through); w1 = sum(1 for r in opt_a_let_through if r["won"])
n2 = len(opt_a_kept_blocked); w2 = sum(1 for r in opt_a_kept_blocked if r["won"])

print(f"  Let through (DAMPEN @ half size): n={n1}  W={w1}  WR={w1/n1*100 if n1 else 0:.1f}%")
print(f"  Still blocked (ABSTAIN):          n={n2}  W={w2}  WR={w2/n2*100 if n2 else 0:.1f}%")

# Hypothetical PnL of Option A let-through trades, AT HALF SIZE ($4/trade)
pnl_let_through_half = 0.0
pnl_let_through_full = 0.0
for r in opt_a_let_through:
    e = r.get("entry") or 0.6
    half = 4.0; full = 8.0
    if r["won"]:
        pnl_let_through_half += half * (1.0/e - 1.0)
        pnl_let_through_full += full * (1.0/e - 1.0)
    else:
        pnl_let_through_half -= half
        pnl_let_through_full -= full
print(f"  Option A net PnL (half size, DAMPEN): ${pnl_let_through_half:+.2f}")
print(f"  Option A net PnL (full size, no DAMPEN): ${pnl_let_through_full:+.2f}")

# Net effect on the bot over 7 days (extrapolating to ~Kelly fractional sizing)
# Bot uses ~1/4 Kelly so size depends on edge. Let's also compute at $10 to be conservative.
pnl_at_5 = 0
for r in opt_a_let_through:
    e = r.get("entry") or 0.6
    if r["won"]: pnl_at_5 += 5.0 * (1.0/e - 1.0)
    else: pnl_at_5 -= 5.0
print(f"  Option A net PnL (@$5/trade): ${pnl_at_5:+.2f}")

# How many of these would have actually fired given other bot constraints?
# Most would (they passed all other filters before EXHAUST).
print("")
print("=== Option A blocked-signals breakdown by score band ===")
b = defaultdict(lambda: [0, 0])
for r in opt_a_let_through:
    s = r.get("score") or 0
    if s < 0.55: bk = "0.50-0.55"
    elif s < 0.60: bk = "0.55-0.60"
    elif s < 0.65: bk = "0.60-0.65"
    else: bk = ">=0.65"
    b[bk][0] += 1
    if r["won"]: b[bk][1] += 1
for k in sorted(b.keys()):
    n, w = b[k]
    print(f"  {k:12s}  n={n:3d}  WR={w/n*100:.1f}%")

print("")
print("=" * 75)
print("FINAL VERDICT")
print("=" * 75)
print(f"  EXHAUST current state: {len(abstain_r)} blocks @ {sum(1 for r in abstain_r if r['won'])/len(abstain_r)*100:.1f}% WR (-${(sum(8 if not r['won'] else -8*(1.0/(r.get('entry') or 0.6)-1.0) for r in abstain_r)):+.2f} hypothetical)")
print(f"  Option A: lets through {n1} trades @ {w1/n1*100 if n1 else 0:.1f}% WR")
print(f"            recovered EV @ half size: ${pnl_let_through_half:+.2f} / 7 days")
print(f"            recovered EV @ full size: ${pnl_let_through_full:+.2f} / 7 days")

# Also check: 5m bot — if EXHAUST overblocking applies there too, the impact is bigger
# (5m uses fixed $3 sizing during test week)
opt_a_5m = pnl_let_through_full * (3.0/8.0)  # scale by sizing
print(f"  Same logic applied to 5m bot @$3/trade: ${opt_a_5m:+.2f} / 7 days")
