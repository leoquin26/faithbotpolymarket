"""
Analysis engine: loads trade_events.jsonl + backfill + CSVs and produces
five canonical reports:

  1. Calibration curve        — does prob=p really win p% of the time?
  2. Per-feature lift         — win rate by entry-price, trend, coin, hour bucket
  3. Counterfactual on blocks — what % of blocked signals would have won?
  4. R:R matrix               — avg win $ vs avg loss $ by entry-price bucket
  5. Regime slicing           — win rate by session range & Polymarket breadth

Output: pretty ASCII tables to stdout + summary JSON to
  /home/ubuntu/v3-bot/data/analytics_report.json

Usage:
  python3 -m analytics.analyze [--live events.jsonl] [--backfill backfill.jsonl]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from statistics import mean, median
from typing import Iterable, Optional

LIVE_DEFAULT = "/home/ubuntu/v3-bot/data/trade_events.jsonl"
BACKFILL_DEFAULT = "/home/ubuntu/v3-bot/data/trade_events_backfill.jsonl"
REPORT_OUT = "/home/ubuntu/v3-bot/data/analytics_report.json"


# ---------------------------------------------------------------- data loader

def load_events(*paths: str) -> list[dict]:
    rows = []
    for p in paths:
        if not p or not os.path.exists(p):
            continue
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    rows.sort(key=lambda r: r.get("ts_epoch") or 0)
    return rows


def group_by_trade(events: list[dict]) -> list[dict]:
    """
    Pair each FIRED with its RESOLVED and upstream SIGNAL/EXHAUST events
    into a 'trade' row with a unified feature vector.
    """
    by_id = defaultdict(list)
    for e in events:
        tid = e.get("trade_id")
        if tid:
            by_id[tid].append(e)

    # Fallback: pair FIRED/RESOLVED by (coin, window_start) when no trade_id.
    by_pair: dict[tuple, dict] = defaultdict(dict)
    for e in events:
        c, ws = e.get("coin"), e.get("window_start")
        ev = e.get("event")
        if ev not in ("FIRED", "RESOLVED", "SIGNAL", "EXHAUST") or not c or ws is None:
            continue
        try:
            ws = int(ws)
        except Exception:
            continue
        bucket = by_pair[(c, ws)]
        # keep the *latest* of each type
        prev = bucket.get(ev)
        if prev is None or (e.get("ts_epoch") or 0) >= (prev.get("ts_epoch") or 0):
            bucket[ev] = e

    trades = []

    # 1. trade_id-paired trades
    for tid, evs in by_id.items():
        by_type = {e["event"]: e for e in evs if "event" in e}
        fired = by_type.get("FIRED")
        resolved = by_type.get("RESOLVED")
        if not fired or not resolved:
            continue
        sig = by_type.get("SIGNAL") or {}
        ex = by_type.get("EXHAUST") or {}
        trades.append(_mk_trade(fired, resolved, sig, ex, source="id"))

    # 2. (coin, ws)-paired trades with no trade_id
    for (c, ws), bucket in by_pair.items():
        fired = bucket.get("FIRED")
        resolved = bucket.get("RESOLVED")
        if not fired or not resolved:
            continue
        if fired.get("trade_id"):  # already captured above
            continue
        sig = bucket.get("SIGNAL") or {}
        ex = bucket.get("EXHAUST") or {}
        trades.append(_mk_trade(fired, resolved, sig, ex, source="pair"))

    trades.sort(key=lambda t: t.get("ts_epoch") or 0)
    return trades


def _mk_trade(fired: dict, resolved: dict, sig: dict, ex: dict, source: str) -> dict:
    return {
        "ts_epoch": fired.get("ts_epoch"),
        "coin": fired.get("coin"),
        "side": fired.get("side"),
        "entry": fired.get("entry") or sig.get("entry"),
        "prob": sig.get("prob"),
        "edge": sig.get("edge"),
        "trend_score": sig.get("trend_score"),
        "exhaust_score": ex.get("score"),
        "exhaust_action": ex.get("action"),
        "session_range": ex.get("session_range"),
        "breadth": ex.get("breadth"),
        "shares": fired.get("shares"),
        "cost": fired.get("cost"),
        "kelly_tier": fired.get("kelly_tier"),
        "kelly_size": fired.get("kelly_size"),
        "phase": fired.get("phase") or resolved.get("phase"),
        "won": bool(resolved.get("won")),
        "pnl": resolved.get("pnl"),
        "window_start": fired.get("window_start"),
        "_source": source,
    }


# ------------------------------------------------------------ report helpers

def _fmt_pct(x: Optional[float]) -> str:
    if x is None:
        return "   -  "
    return f"{x*100:5.1f}%"


def _fmt_num(x: Optional[float], w: int = 6, d: int = 2) -> str:
    if x is None:
        return " " * (w - 1) + "-"
    return f"{x:{w}.{d}f}"


def _rate(wins: int, n: int) -> Optional[float]:
    return wins / n if n else None


def _ev(pnls: list[float]) -> Optional[float]:
    return mean(pnls) if pnls else None


def _bucket(x: Optional[float], edges: list[float]) -> Optional[str]:
    if x is None:
        return None
    for i, e in enumerate(edges):
        if x < e:
            if i == 0:
                return f"<{e:.2f}"
            return f"{edges[i-1]:.2f}-{e:.2f}"
    return f">={edges[-1]:.2f}"


# ---------------------------------------------------------------- 1. calibration

def calibration_curve(trades: list[dict]) -> dict:
    buckets = defaultdict(lambda: {"n": 0, "wins": 0})
    edges = [0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 1.01]
    for t in trades:
        p = t.get("prob")
        if p is None:
            continue
        b = _bucket(p, edges) or "n/a"
        buckets[b]["n"] += 1
        if t.get("won"):
            buckets[b]["wins"] += 1

    rows = []
    for k in sorted(buckets.keys()):
        v = buckets[k]
        rate = _rate(v["wins"], v["n"])
        rows.append({"bucket": k, "n": v["n"], "wins": v["wins"], "win_rate": rate})
    return {"rows": rows}


def print_calibration(report: dict) -> None:
    print("\n" + "=" * 60)
    print("  1. CALIBRATION  (do predicted probs match realized win rate?)")
    print("=" * 60)
    print(f"  {'prob bucket':<14} {'n':>4} {'wins':>5} {'actual':>8} {'ideal':>8} {'gap':>8}")
    for r in report["rows"]:
        lo_hi = r["bucket"].replace("<", "").replace(">=", "")
        try:
            if "-" in lo_hi:
                lo, hi = [float(x) for x in lo_hi.split("-")]
                ideal = (lo + hi) / 2
            else:
                ideal = float(lo_hi)
        except Exception:
            ideal = None
        actual = r["win_rate"]
        gap = (actual - ideal) if (actual is not None and ideal is not None) else None
        print(f"  {r['bucket']:<14} {r['n']:>4} {r['wins']:>5} "
              f"{_fmt_pct(actual):>8} {_fmt_pct(ideal):>8} "
              f"{('' if gap is None else f'{gap*100:+5.1f}pp'):>8}")


# ---------------------------------------------------------- 2. per-feature lift

def feature_lift(trades: list[dict]) -> dict:
    def slice_by(key: str, bucketize=None):
        b = defaultdict(lambda: {"n": 0, "wins": 0, "pnls": []})
        for t in trades:
            v = t.get(key)
            if bucketize:
                v = bucketize(v)
            if v is None:
                continue
            b[v]["n"] += 1
            if t.get("won"):
                b[v]["wins"] += 1
            pnl = t.get("pnl")
            if pnl is not None:
                b[v]["pnls"].append(pnl)
        return [
            {
                "bucket": k, "n": v["n"], "wins": v["wins"],
                "win_rate": _rate(v["wins"], v["n"]),
                "avg_pnl": _ev(v["pnls"]),
                "total_pnl": sum(v["pnls"]) if v["pnls"] else 0,
            }
            for k, v in sorted(b.items(), key=lambda kv: str(kv[0]))
        ]

    return {
        "by_coin": slice_by("coin"),
        "by_side": slice_by("side"),
        "by_phase": slice_by("phase"),
        "by_entry": slice_by("entry", lambda x: _bucket(x, [0.55, 0.60, 0.63, 0.66, 0.69])),
        "by_trend": slice_by("trend_score", lambda x: _bucket(abs(x) if x is not None else None, [0.5, 1.0, 1.5, 2.0])),
        "by_kelly_tier": slice_by("kelly_tier"),
    }


def print_feature_lift(report: dict) -> None:
    print("\n" + "=" * 60)
    print("  2. PER-FEATURE LIFT")
    print("=" * 60)
    for label, rows in report.items():
        print(f"\n  -- {label} --")
        print(f"  {'bucket':<16} {'n':>4} {'wins':>5} {'rate':>8} {'avg$':>8} {'tot$':>8}")
        for r in rows:
            print(f"  {str(r['bucket']):<16} {r['n']:>4} {r['wins']:>5} "
                  f"{_fmt_pct(r['win_rate']):>8} "
                  f"{_fmt_num(r['avg_pnl'], 8, 2):>8} "
                  f"{_fmt_num(r['total_pnl'], 8, 2):>8}")


# ---------------------------------------------- 3. counterfactual on blocks

def counterfactual_on_blocks(events: list[dict], trades: list[dict]) -> dict:
    """
    For every BLOCKED signal, try to find the ground-truth outcome for that
    (coin, window_start). If another trade for that window resolved, we use it.
    Otherwise we fall back to the neighboring 15m window's direction.

    This underestimates — we only count blocks we can confidently label.
    """
    # ground truth: (coin, window_start) -> won(UP) / won(DOWN)
    truth: dict[tuple, dict] = {}
    for t in trades:
        c, ws, side, won = t.get("coin"), t.get("window_start"), t.get("side"), t.get("won")
        if not (c and ws is not None and side):
            continue
        truth.setdefault((c, int(ws)), {})[side] = won

    # also use CSV_ROW events (from backfill) as truth
    for e in events:
        if e.get("event") != "CSV_ROW":
            continue
        c, ws, side = e.get("coin"), e.get("window_start"), e.get("side")
        price = e.get("price")
        if not (c and ws is not None and side and price is not None):
            continue
        # price ~ 0 means that side lost; ~1 means that side won
        if price >= 0.98:
            truth.setdefault((c, int(ws)), {})[side] = True
        elif price <= 0.02:
            truth.setdefault((c, int(ws)), {})[side] = False

    per_reason = defaultdict(lambda: {"n": 0, "would_win": 0, "unknown": 0})

    for e in events:
        if e.get("event") != "BLOCKED":
            continue
        reason = e.get("blocked_by") or "UNKNOWN"
        c, side, ws = e.get("coin"), e.get("side"), e.get("window_start")
        # blocks often don't carry window_start; skip those
        if not c or not side or ws is None:
            per_reason[reason]["unknown"] += 1
            continue
        key = (c, int(ws))
        if key in truth and side in truth[key]:
            per_reason[reason]["n"] += 1
            if truth[key][side]:
                per_reason[reason]["would_win"] += 1
        else:
            per_reason[reason]["unknown"] += 1

    rows = []
    for r, v in per_reason.items():
        rate = _rate(v["would_win"], v["n"])
        rows.append({
            "reason": r,
            "blocked_known": v["n"],
            "would_win": v["would_win"],
            "would_win_rate": rate,
            "blocked_unknown": v["unknown"],
        })
    rows.sort(key=lambda r: -(r["blocked_known"] or 0))
    return {"rows": rows}


def print_counterfactual(report: dict) -> None:
    print("\n" + "=" * 60)
    print("  3. COUNTERFACTUAL ON BLOCKS  (if we had fired, would we have won?)")
    print("=" * 60)
    print(f"  {'reason':<22} {'known':>6} {'wins':>5} {'rate':>8} {'unknown':>8}")
    for r in report["rows"]:
        print(f"  {r['reason']:<22} {r['blocked_known']:>6} {r['would_win']:>5} "
              f"{_fmt_pct(r['would_win_rate']):>8} {r['blocked_unknown']:>8}")
    print("\n  interpretation:")
    print("    rate < 50%  -> the filter is PROTECTING (would have been a loser).")
    print("    rate > 60%  -> the filter is OVERBLOCKING (killing winners).")


# --------------------------------------------------------- 4. R:R matrix

def risk_reward_matrix(trades: list[dict]) -> dict:
    buckets = defaultdict(lambda: {"wins": [], "losses": []})
    for t in trades:
        e = t.get("entry")
        pnl = t.get("pnl")
        if e is None or pnl is None:
            continue
        b = _bucket(e, [0.55, 0.60, 0.63, 0.66, 0.69]) or "n/a"
        (buckets[b]["wins"] if pnl > 0 else buckets[b]["losses"]).append(abs(pnl))

    rows = []
    for k in sorted(buckets.keys()):
        v = buckets[k]
        w_n, l_n = len(v["wins"]), len(v["losses"])
        aw = mean(v["wins"]) if v["wins"] else None
        al = mean(v["losses"]) if v["losses"] else None
        rr = (aw / al) if (aw is not None and al) else None
        net = sum(v["wins"]) - sum(v["losses"])
        rows.append({
            "entry_bucket": k,
            "wins": w_n, "losses": l_n,
            "avg_win": aw, "avg_loss": al,
            "rr": rr, "net": net,
        })
    return {"rows": rows}


def print_rr(report: dict) -> None:
    print("\n" + "=" * 60)
    print("  4. R:R MATRIX BY ENTRY PRICE BUCKET")
    print("=" * 60)
    print(f"  {'entry':<12} {'W':>4} {'L':>4} {'avg$W':>7} {'avg$L':>7} {'R:R':>6} {'net$':>8}")
    for r in report["rows"]:
        rr_s = "  -  " if r["rr"] is None else f"{r['rr']:5.2f}"
        print(f"  {r['entry_bucket']:<12} {r['wins']:>4} {r['losses']:>4} "
              f"{_fmt_num(r['avg_win'], 7, 2)} {_fmt_num(r['avg_loss'], 7, 2)} "
              f"{rr_s:>6} {_fmt_num(r['net'], 8, 2)}")


# --------------------------------------------------- 5. regime slicing

def regime_slices(trades: list[dict]) -> dict:
    def s(key: str, buckets):
        b = defaultdict(lambda: {"n": 0, "wins": 0, "pnls": []})
        for t in trades:
            v = t.get(key)
            bk = _bucket(v, buckets) if v is not None else None
            if bk is None:
                continue
            b[bk]["n"] += 1
            if t.get("won"):
                b[bk]["wins"] += 1
            if t.get("pnl") is not None:
                b[bk]["pnls"].append(t["pnl"])
        return [
            {"bucket": k, "n": v["n"], "wins": v["wins"],
             "win_rate": _rate(v["wins"], v["n"]),
             "total_pnl": sum(v["pnls"])}
            for k, v in sorted(b.items())
        ]
    return {
        "by_session_range": s("session_range", [0.3, 0.6, 0.8, 0.9, 0.95]),
        "by_breadth": s("breadth", [0.3, 0.5, 0.7, 0.9]),
        "by_exhaust_score": s("exhaust_score", [0.2, 0.4, 0.6, 0.8]),
    }


def print_regime(report: dict) -> None:
    print("\n" + "=" * 60)
    print("  5. REGIME SLICING  (market conditions that matter)")
    print("=" * 60)
    for label, rows in report.items():
        print(f"\n  -- {label} --")
        print(f"  {'bucket':<14} {'n':>4} {'wins':>5} {'rate':>8} {'net$':>8}")
        for r in rows:
            print(f"  {str(r['bucket']):<14} {r['n']:>4} {r['wins']:>5} "
                  f"{_fmt_pct(r['win_rate']):>8} "
                  f"{_fmt_num(r['total_pnl'], 8, 2)}")


# ------------------------------------------------------------ top-line summary

def top_line(trades: list[dict]) -> dict:
    n = len(trades)
    wins = sum(1 for t in trades if t.get("won"))
    pnls = [t["pnl"] for t in trades if t.get("pnl") is not None]
    pos = [p for p in pnls if p > 0]
    neg = [p for p in pnls if p < 0]
    return {
        "n_trades": n,
        "wins": wins,
        "win_rate": _rate(wins, n),
        "total_pnl": sum(pnls) if pnls else 0,
        "avg_pnl": mean(pnls) if pnls else None,
        "median_pnl": median(pnls) if pnls else None,
        "avg_win": mean(pos) if pos else None,
        "avg_loss": mean(neg) if neg else None,
        "expectancy": mean(pnls) if pnls else None,
    }


def print_topline(s: dict) -> None:
    print("\n" + "=" * 60)
    print("  TOP LINE")
    print("=" * 60)
    print(f"  trades       : {s['n_trades']}")
    print(f"  win rate     : {_fmt_pct(s['win_rate'])}  ({s['wins']}/{s['n_trades']})")
    print(f"  total PnL    : {_fmt_num(s['total_pnl'], 10, 2)}")
    print(f"  avg PnL      : {_fmt_num(s['avg_pnl'], 10, 2)}")
    print(f"  avg WIN      : {_fmt_num(s['avg_win'], 10, 2)}")
    print(f"  avg LOSS     : {_fmt_num(s['avg_loss'], 10, 2)}")


# --------------------------------------------------------------- CLI

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", default=LIVE_DEFAULT)
    ap.add_argument("--backfill", default=BACKFILL_DEFAULT)
    ap.add_argument("--out", default=REPORT_OUT)
    args = ap.parse_args()

    events = load_events(args.live, args.backfill)
    print(f"[analyze] loaded {len(events)} events total")
    trades = group_by_trade(events)
    print(f"[analyze] reconstructed {len(trades)} completed trades")
    if not trades:
        print("\n  NO COMPLETED TRADES YET. Re-run after the bot has closed a few windows,")
        print("  or run `python3 -m analytics.backfill --csv ...` to seed history.")
        return 0

    report = {
        "generated_ts": int(__import__("time").time()),
        "n_events": len(events),
        "n_trades": len(trades),
        "topline": top_line(trades),
        "calibration": calibration_curve(trades),
        "feature_lift": feature_lift(trades),
        "counterfactual": counterfactual_on_blocks(events, trades),
        "rr_matrix": risk_reward_matrix(trades),
        "regime": regime_slices(trades),
    }

    print_topline(report["topline"])
    print_calibration(report["calibration"])
    print_feature_lift(report["feature_lift"])
    print_counterfactual(report["counterfactual"])
    print_rr(report["rr_matrix"])
    print_regime(report["regime"])

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\n[analyze] wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
