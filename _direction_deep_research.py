#!/usr/bin/env python3
"""Deep research: is late/early direction selection signal or noise?
Run on EC2: python3 _direction_deep_research.py
"""
from __future__ import annotations
import csv
import math
from collections import defaultdict
from datetime import datetime

CSV = "clean_bot_research.csv"


def f(r, k, default=None):
    try:
        v = r.get(k)
        if v is None or v == "":
            return default
        return float(v)
    except Exception:
        return default


def one_prop_z(wr, be, n):
    if n <= 0 or be <= 0 or be >= 1:
        return 0.0
    return (wr - be) / math.sqrt(be * (1 - be) / n)


def stats(rows, label, price_key="fav_ask"):
    rows = [r for r in rows if r.get("drift_correct") in ("0", "1")]
    if not rows:
        print(f"{label:48s} n=0")
        return None
    n = len(rows)
    wins = sum(int(r["drift_correct"]) for r in rows)
    wr = wins / n
    prices = []
    for r in rows:
        p = f(r, price_key)
        if p is None:
            continue
        # fav_ask stored as cents (55-70) usually
        if p > 1.5:
            p = p / 100.0
        prices.append(p)
    be = sum(prices) / len(prices) if prices else 0.5
    edge = (wr - be) * 100
    # EV per $1 at ask
    ev = 0.0
    for r in rows:
        p = f(r, price_key)
        if p is None:
            continue
        if p > 1.5:
            p = p / 100.0
        ev += (1 - p) if int(r["drift_correct"]) else -p
    ev /= max(1, len(prices))
    z = one_prop_z(wr, be, n)
    print(f"{label:48s} n={n:4d} WR={wr*100:5.1f}% BE={be*100:4.1f}% "
          f"edge={edge:+5.1f}pts EV/$={ev:+.3f} z={z:+.2f}")
    return dict(n=n, wr=wr, be=be, edge=edge, ev=ev, z=z)


def chrono_oos(rows, label, frac=0.3):
    rows = sorted(rows, key=lambda r: r.get("ts", ""))
    if len(rows) < 30:
        stats(rows, label + " (all, small)")
        return
    cut = int(len(rows) * (1 - frac))
    stats(rows[:cut], label + " IS70")
    stats(rows[cut:], label + " OOS30")


def main():
    rows = list(csv.DictReader(open(CSV, encoding="utf-8", errors="ignore")))
    print(f"Loaded {len(rows)} research rows\n")

    by_phase = defaultdict(list)
    for r in rows:
        if r.get("drift_correct") not in ("0", "1"):
            continue
        by_phase[r.get("phase") or "early"].append(r)

    print("=== 1. BASELINE: sign(drift) vs settlement (all settled) ===")
    for ph in ("early", "mid", "late"):
        stats(by_phase[ph], f"phase={ph} ALL")
        # in-band 55-70
        ib = []
        for r in by_phase[ph]:
            a = f(r, "fav_ask")
            if a is None:
                continue
            if a > 1.5:
                a = a / 100
            if 0.55 <= a <= 0.70:
                ib.append(r)
        stats(ib, f"phase={ph} ask55-70")
        chrono_oos(ib, f"phase={ph} ask55-70")
        print()

    print("=== 2. IS DIRECTION JUST MARKET PRICE? (late 55-70) ===")
    # If market ask already prices direction, our 'skill' is agreeing with market
    late = []
    for r in by_phase["late"]:
        a = f(r, "fav_ask")
        if a is None:
            continue
        if a > 1.5:
            a = a / 100
        if 0.55 <= a <= 0.70:
            late.append(r)
    # Always-bet-favorite (same as our dir since fav_ask is favorite side)
    stats(late, "late: follow drift=fav (current bot)")
    # Counterfactual: fade drift (bet underdog)
    fade = []
    for r in late:
        rr = dict(r)
        rr["drift_correct"] = "1" if r["drift_correct"] == "0" else "0"
        # underdog price approx 1 - fav
        a = f(r, "fav_ask")
        if a > 1.5:
            a = a / 100
        # store underdog as fav_ask for BE calc — rough
        rr["fav_ask"] = (1 - a) * 100
        fade.append(rr)
    stats(fade, "late: FADE drift (counterfactual)")
    print()

    print("=== 3. LEAD STRENGTH (late 55-70): is bigger better? ===")
    # drift_pct is percent points; *100 = bps
    buckets = [(0, 3), (3, 5), (5, 8), (8, 12), (12, 20), (20, 999)]
    for lo, hi in buckets:
        sel = [r for r in late if lo <= abs(f(r, "drift_pct", 0) or 0) * 100 < hi]
        stats(sel, f"|drift| {lo}-{hi} bps")
    print()

    print("=== 4. TIME LEFT at late snapshot ===")
    for lo, hi, name in [(60, 100, "T 60-100s"), (100, 150, "T 100-150"),
                          (150, 180, "T 150-180"), (180, 210, "T 180-210")]:
        sel = [r for r in late if lo <= (f(r, "t_left", 0) or 0) < hi]
        stats(sel, name)
    print()

    print("=== 5. MOMENTUM ALIGNMENT (roc vs dir) ===")
    def aligned(r, key, thr=2):
        roc = f(r, key, 0) or 0
        if r.get("dir") == "UP":
            return roc >= thr
        return roc <= -thr

    def opposed(r, key, thr=2):
        roc = f(r, key, 0) or 0
        if r.get("dir") == "UP":
            return roc <= -thr
        return roc >= thr

    stats([r for r in late if aligned(r, "roc60_bps")], "roc60 ALIGNED ≥2bps")
    stats([r for r in late if opposed(r, "roc60_bps")], "roc60 OPPOSES ≥2bps")
    stats([r for r in late if not opposed(r, "roc60_bps")], "roc60 not opposing")
    stats([r for r in late if aligned(r, "roc300_bps")], "roc300 ALIGNED")
    stats([r for r in late if opposed(r, "roc300_bps")], "roc300 OPPOSES")
    # missing roc (0)
    z0 = [r for r in late if abs(f(r, "roc60_bps", 0) or 0) < 0.5]
    stats(z0, "roc60 ~0 (missing/flat)")
    print()

    print("=== 6. EARLY→LATE TRAJECTORY (join quality) ===")
    early = {(r["coin"], r["window_start"]): r for r in by_phase["early"]}
    mid = {(r["coin"], r["window_start"]): r for r in by_phase["mid"]}
    grow, fade_s, flip, no_e = [], [], [], []
    for r in late:
        e = early.get((r["coin"], r["window_start"]))
        if not e:
            no_e.append(r)
            continue
        try:
            ed = float(e["drift_pct"])
            ld = float(r["drift_pct"])
            same = (ed > 0) == (ld > 0)
            if not same:
                flip.append(r)
            elif abs(ld) >= abs(ed):
                grow.append(r)
            else:
                fade_s.append(r)
        except Exception:
            continue
    stats(grow, "GROWING lead early→late")
    stats(fade_s, "FADING lead early→late")
    stats(flip, "FLIPPED dir early→late")
    stats(no_e, "NO early snapshot")
    # mid agreement
    mid_agree, mid_disagree, no_m = [], [], []
    for r in late:
        m = mid.get((r["coin"], r["window_start"]))
        if not m or m.get("dir") not in ("UP", "DOWN"):
            no_m.append(r)
            continue
        if m["dir"] == r["dir"]:
            mid_agree.append(r)
        else:
            mid_disagree.append(r)
    stats(mid_agree, "MID agrees with late dir")
    stats(mid_disagree, "MID disagrees late dir")
    stats(no_m, "NO mid snapshot")
    print()

    print("=== 7. COIN / SESSION (late 55-70) ===")
    for c in ("SOL", "ETH", "BTC", "XRP"):
        stats([r for r in late if r.get("coin") == c], f"coin {c}")
    by_h = defaultdict(list)
    for r in late:
        try:
            ts = r["ts"].replace("T", " ")
            h = datetime.fromisoformat(ts).hour  # often UTC in file
            lima = (h - 5) % 24
            by_h[lima].append(r)
        except Exception:
            pass
    print("Lima hour blocks:")
    for name, hours in [("night 0-7", range(0, 7)), ("morning 7-12", range(7, 12)),
                        ("afternoon 12-18", range(12, 18)), ("evening 18-24", range(18, 24))]:
        sel = []
        for h in hours:
            sel.extend(by_h.get(h, []))
        stats(sel, name)
    print()

    print("=== 8. BOOK / FLOW microstructure ===")
    def same_side_imb(r, thr=0.1):
        bi = f(r, "book_imb")
        if bi is None:
            return None
        if r.get("dir") == "UP":
            return bi >= thr
        return bi <= -thr

    ss = [r for r in late if same_side_imb(r) is True]
    opp = [r for r in late if same_side_imb(r) is False and f(r, "book_imb") is not None
           and abs(f(r, "book_imb") or 0) >= 0.1]
    stats(ss, "book_imb same-side ≥0.1")
    stats(opp, "book_imb oppose ≥0.1")
    fl_s = [r for r in late if aligned(r, "flow60", 0.2)]
    fl_o = [r for r in late if opposed(r, "flow60", 0.2)]
    stats(fl_s, "flow60 same-side")
    stats(fl_o, "flow60 oppose")
    print()

    print("=== 9. COMBINED JOIN RULES (frequency + quality) ===")
    # A: growing + not roc60 oppose (v1.52-ish + fade)
    a = [r for r in late if r in grow or (
        early.get((r["coin"], r["window_start"])) and r in flip)]
    # rebuild properly
    rule_a = []
    for r in late:
        e = early.get((r["coin"], r["window_start"]))
        if e:
            try:
                ed = float(e["drift_pct"]); ld = float(r["drift_pct"])
                same = (ed > 0) == (ld > 0)
                if same and abs(ld) < abs(ed):
                    continue  # fade skip
            except Exception:
                pass
        if opposed(r, "roc60_bps", 2):
            continue
        rule_a.append(r)
    stats(late, "BASELINE late 55-70")
    chrono_oos(late, "BASELINE")
    stats(rule_a, "RULE A: not-fade + not roc60-oppose")
    chrono_oos(rule_a, "RULE A")

    rule_b = [r for r in rule_a if r.get("coin") in ("SOL", "ETH")]
    stats(rule_b, "RULE B: A + SOL/ETH only")
    chrono_oos(rule_b, "RULE B")

    rule_c = []
    for r in rule_a:
        # require mid agree if mid exists; else keep
        m = mid.get((r["coin"], r["window_start"]))
        if m and m.get("dir") in ("UP", "DOWN") and m["dir"] != r["dir"]:
            continue
        rule_c.append(r)
    stats(rule_c, "RULE C: A + mid agree-if-present")
    chrono_oos(rule_c, "RULE C")

    # D: only when market strongly prices the lead (ask>=60) AND growing
    rule_d = []
    for r in rule_a:
        a = f(r, "fav_ask")
        if a > 1.5:
            a /= 100
        if a < 0.60:
            continue
        rule_d.append(r)
    stats(rule_d, "RULE D: A + ask≥60c")
    chrono_oos(rule_d, "RULE D")
    print()

    print("=== 10. CALIBRATION: does WR track fav_ask? (late) ===")
    for lo, hi in [(55, 60), (60, 65), (65, 70), (70, 80), (80, 95)]:
        sel = []
        for r in by_phase["late"]:
            a = f(r, "fav_ask")
            if a is None:
                continue
            if a > 1.5:
                a = a / 100
            if lo / 100 <= a < hi / 100:
                sel.append(r)
        stats(sel, f"fav_ask {lo}-{hi}c")
    print()

    print("=== 11. RANDOMNESS CHECK ===")
    # If direction were random 50%, WR would be 50%. Actual:
    for ph, rs in by_phase.items():
        if len(rs) < 50:
            continue
        wr = sum(int(r["drift_correct"]) for r in rs) / len(rs)
        # vs coin flip
        z50 = (wr - 0.5) / math.sqrt(0.5 * 0.5 / len(rs))
        print(f"phase={ph}: WR={wr*100:.1f}% vs 50% coin-flip z={z50:+.2f} n={len(rs)}")
    # Autocorr of correctness (regime persistence)
    late_s = sorted(late, key=lambda r: r.get("ts", ""))
    if len(late_s) > 20:
        corr = [int(r["drift_correct"]) for r in late_s]
        pairs = list(zip(corr[:-1], corr[1:]))
        both1 = sum(1 for a, b in pairs if a == 1 and b == 1)
        both0 = sum(1 for a, b in pairs if a == 0 and b == 0)
        print(f"late sequential: P(W|prevW)={sum(1 for a,b in pairs if a==1 and b==1)/max(1,sum(1 for a,b in pairs if a==1)):.3f} "
              f"P(L|prevL)={sum(1 for a,b in pairs if a==0 and b==0)/max(1,sum(1 for a,b in pairs if a==0)):.3f}")
    print()

    print("=== 12. COMPOUNDING GEOMETRY (same WR, different price) ===")
    wr = 0.75  # approx late shadow
    for p in (0.55, 0.58, 0.62, 0.66, 0.70):
        ev = wr * (1 - p) / p - (1 - wr)
        # growth per trade if bet f=8% of bank: approx
        print(f"  entry {p*100:.0f}c WR75% EV/$={ev:+.3f}  wins_to_cover_loss={p/(1-p):.2f}")

    print("\n=== DONE ===")


if __name__ == "__main__":
    main()
