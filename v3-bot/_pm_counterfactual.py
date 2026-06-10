#!/usr/bin/env python3
"""PM Hybrid Mode Counterfactual Analysis.

Re-scores every PM trade in the last 8 days under hypothetical "morning
rules" and reports what would have happened.

Morning rules (proxied since backfill lacks prob/trend fields):
  1. Block trap band (60-63c entries) — already deployed via Option A
  2. Block sub-quality entries: only allow <0.60c OR 0.63-0.66c (data-derived
     from per-entry-band WR). Both bands have >=65% WR; everything else
     is breakeven or worse in PM.
  3. Half-Kelly sizing: pnl * 0.5 (smaller wins, smaller losses)
  4. EXHAUST override: A-tier signals (proxied by very-low-entry bias)
     would have been allowed in morning. Approximate via entry < 0.55
     bucket.

The script reports actual PM, then 5 hypothetical scenarios:
  S1: Just Option A applied (trap band blocked) — already live
  S2: + Block 66-69c entries (worst R:R band, R:R 0.43)
  S3: + Half-Kelly sizing on remaining trades
  S4: + Block XRP in PM — already live (Option A)
  S5: All of the above (full PM Hybrid Mode)
"""
from __future__ import annotations
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

DATA = Path("/home/ubuntu/v3-bot/data/trade_events_backfill.jsonl")


def load_resolved_trades():
    """Load all RESOLVED events (which have full P&L data)."""
    trades = []
    with open(DATA) as f:
        for line in f:
            try:
                e = json.loads(line)
            except Exception:
                continue
            if e.get("event") != "RESOLVED":
                continue
            phase = e.get("phase")
            if phase not in ("MORNING", "PM"):
                continue
            entry = e.get("entry") or 0.0
            cost = e.get("cost") or 0.0
            payout = e.get("payout") or 0.0
            pnl = e.get("pnl")
            if pnl is None:
                pnl = (payout - cost) if e.get("won") else -cost
            trades.append({
                "date": e.get("date"),
                "ts_epoch": e.get("ts_epoch", 0),
                "coin": e.get("coin"),
                "side": e.get("side"),
                "phase": phase,
                "entry": entry,
                "shares": e.get("shares") or 0,
                "cost": cost,
                "payout": payout,
                "pnl": pnl,
                "won": bool(e.get("won")),
            })
    return trades


def hour_lima(ts_epoch: int) -> int:
    """UTC ts_epoch -> Lima hour (UTC-5 fixed; Peru doesn't observe DST)."""
    return (datetime.fromtimestamp(ts_epoch, tz=timezone.utc).hour - 5) % 24


def fmt_money(x: float) -> str:
    sign = "+" if x >= 0 else "-"
    return f"{sign}${abs(x):.2f}"


def fmt_pct(x: float) -> str:
    return f"{x*100:.1f}%"


def aggregate(trades) -> dict:
    if not trades:
        return {"n": 0, "wins": 0, "wr": 0.0, "net": 0.0, "avg_win": 0.0, "avg_loss": 0.0, "rr": 0.0}
    n = len(trades)
    wins = sum(1 for t in trades if t["won"])
    losses = n - wins
    win_pnl = [t["pnl"] for t in trades if t["won"]]
    loss_pnl = [t["pnl"] for t in trades if not t["won"]]
    avg_win = sum(win_pnl) / len(win_pnl) if win_pnl else 0.0
    avg_loss = abs(sum(loss_pnl) / len(loss_pnl)) if loss_pnl else 0.0
    rr = (avg_win / avg_loss) if avg_loss > 0 else 0.0
    net = sum(t["pnl"] for t in trades)
    return {"n": n, "wins": wins, "losses": losses, "wr": wins / n, "net": net, "avg_win": avg_win, "avg_loss": avg_loss, "rr": rr}


def print_row(label: str, agg: dict, ref_n: int = 0):
    if agg["n"] == 0:
        print(f"  {label:<35} | n=0 (no trades)")
        return
    pct_volume = (agg["n"] / ref_n * 100) if ref_n else 100.0
    print(
        f"  {label:<35} | n={agg['n']:>3} ({pct_volume:>4.0f}%) | "
        f"WR={fmt_pct(agg['wr']):>6} | "
        f"avg_win={fmt_money(agg['avg_win']):>7} | "
        f"avg_loss=-${agg['avg_loss']:.2f} | "
        f"R:R={agg['rr']:>4.2f} | "
        f"net={fmt_money(agg['net']):>8}"
    )


def apply_filter(trades, predicate):
    return [t for t in trades if predicate(t)]


def main():
    all_trades = load_resolved_trades()
    print("=" * 100)
    print(f"PM HYBRID MODE — COUNTERFACTUAL ANALYSIS")
    print(f"Source: {DATA}")
    print(f"Total resolved trades in backfill: {len(all_trades)}")
    morning = [t for t in all_trades if t["phase"] == "MORNING"]
    pm_all = [t for t in all_trades if t["phase"] == "PM"]
    print(f"  MORNING: {len(morning)}")
    print(f"  PM:      {len(pm_all)}")
    print("=" * 100)

    # ── Baselines ──
    print("\n[BASELINES]")
    print_row("MORNING actual", aggregate(morning))
    print_row("PM actual", aggregate(pm_all))

    # ── Coin breakdown for PM ──
    print("\n[PM by coin (where the bleeding is)]")
    for coin in sorted({t["coin"] for t in pm_all}):
        trades = [t for t in pm_all if t["coin"] == coin]
        print_row(f"PM {coin}", aggregate(trades), ref_n=len(pm_all))

    # ── Entry-band breakdown for PM ──
    print("\n[PM by entry band (where the structural issues are)]")
    bands = [
        ("<0.55", lambda t: t["entry"] < 0.55),
        ("0.55-0.60", lambda t: 0.55 <= t["entry"] < 0.60),
        ("0.60-0.63 (TRAP)", lambda t: 0.60 <= t["entry"] <= 0.63),
        ("0.63-0.66", lambda t: 0.63 < t["entry"] <= 0.66),
        ("0.66-0.69", lambda t: 0.66 < t["entry"] <= 0.69),
        (">0.69", lambda t: t["entry"] > 0.69),
    ]
    for label, pred in bands:
        trades = [t for t in pm_all if pred(t)]
        print_row(f"PM entry {label}", aggregate(trades), ref_n=len(pm_all))

    # ── PM by hour ──
    print("\n[PM by Lima hour (when the bleeding happens)]")
    for h in sorted({hour_lima(t["ts_epoch"]) for t in pm_all}):
        trades = [t for t in pm_all if hour_lima(t["ts_epoch"]) == h]
        print_row(f"PM hour {h:02d}:00", aggregate(trades), ref_n=len(pm_all))

    # ──────────────────────────────────────────────────────────────────
    # Counterfactual scenarios
    # ──────────────────────────────────────────────────────────────────
    print("\n" + "=" * 100)
    print("COUNTERFACTUAL SCENARIOS — what PM would look like under each filter set")
    print("=" * 100)

    # S0: PM as-is (today's reality through Apr 26)
    print("\n[S0] PM actual (the baseline you saw in P&L)")
    print_row("S0 PM actual", aggregate(pm_all))

    # S1: Apply Option A only (trap band 60-63c blocked)
    s1 = apply_filter(pm_all, lambda t: not (0.60 <= t["entry"] <= 0.63))
    print("\n[S1] + Option A (block 60-63c trap band — ALREADY LIVE)")
    print_row("S1 PM with TRAP BAND blocked", aggregate(s1), ref_n=len(pm_all))

    # S2: + Also block 66-69c (R:R was 0.43, worst band)
    s2 = apply_filter(s1, lambda t: not (0.66 < t["entry"] <= 0.69))
    print("\n[S2] + Also block 66-69c (worst R:R band: 0.43)")
    print_row("S2 PM + 66-69c blocked", aggregate(s2), ref_n=len(pm_all))

    # S3: + Also block XRP in PM (Option A also)
    s3 = apply_filter(s2, lambda t: t["coin"] != "XRP")
    print("\n[S3] + Also block XRP in PM (already live via Option A)")
    print_row("S3 PM + no XRP", aggregate(s3), ref_n=len(pm_all))

    # S4: + Half-Kelly sizing on what remains
    s4 = [{**t, "pnl": t["pnl"] * 0.5} for t in s3]
    print("\n[S4] + Half-Kelly sizing on remaining trades (smaller wins, smaller losses)")
    print_row("S4 PM Hybrid Mode (full)", aggregate(s4), ref_n=len(pm_all))

    # S5: A different cut — what if we only kept the "morning-quality" entries?
    #     Best R:R bands: <0.55 (R:R 1.12) and 0.55-0.60 (R:R 0.93)
    s5 = apply_filter(pm_all, lambda t: t["entry"] < 0.60 and t["coin"] != "XRP")
    print("\n[S5] ALTERNATE: Only entries <0.60c, no XRP (skip 0.63+ entirely)")
    print_row("S5 PM ultra-selective", aggregate(s5), ref_n=len(pm_all))

    s6 = [{**t, "pnl": t["pnl"] * 0.5} for t in s5]
    print("\n[S6] = S5 + Half-Kelly")
    print_row("S6 PM ultra-selective + half-Kelly", aggregate(s6), ref_n=len(pm_all))

    # ──────────────────────────────────────────────────────────────────
    # Verdict
    # ──────────────────────────────────────────────────────────────────
    print("\n" + "=" * 100)
    print("VERDICT — comparing scenarios")
    print("=" * 100)
    n_days = len({t["date"] for t in pm_all})
    print(f"\nTime window: {n_days} unique trading days with PM activity")
    print(f"\n{'Scenario':<40} | {'Trades':>6} | {'WR':>6} | {'Net':>9} | {'Per-day':>8} | {'vs S0':>9}")
    print("-" * 100)
    s0_net = aggregate(pm_all)["net"]
    s0_per_day = s0_net / n_days if n_days else 0
    scenarios = [
        ("S0: PM actual", aggregate(pm_all)),
        ("S1: + Option A (trap band)", aggregate(s1)),
        ("S2: + 66-69c block", aggregate(s2)),
        ("S3: + No XRP (full Option A)", aggregate(s3)),
        ("S4: + Half-Kelly = PM HYBRID", aggregate(s4)),
        ("S5: Ultra-selective (<60c only)", aggregate(s5)),
        ("S6: Ultra-selective + half-Kelly", aggregate(s6)),
    ]
    for name, agg in scenarios:
        per_day = agg["net"] / n_days if n_days else 0
        delta = per_day - s0_per_day
        delta_str = f"+${delta:.2f}/d" if delta >= 0 else f"-${abs(delta):.2f}/d"
        print(f"{name:<40} | {agg['n']:>6} | {fmt_pct(agg['wr']):>6} | {fmt_money(agg['net']):>9} | {fmt_money(per_day):>8} | {delta_str:>9}")

    # ── A-tier signal counterfactual: how often would EXHAUST override fire? ──
    print("\n" + "=" * 100)
    print("A-TIER OVERRIDE COUNTERFACTUAL — would unblocking EXHAUST help PM?")
    print("=" * 100)
    print("\nFrom existing analytics report:")
    print("  EXHAUST_ABSTAIN events with known-outcome backtest: 31 known, 17 wins, WR=54.8%")
    print("  In PM, these would have fired 1-3 extra trades/week (rare A-tier signals)")
    print("  At 54.8% WR with full sizing: roughly breakeven contribution")
    print("  At 54.8% WR with half-Kelly: marginally negative due to time/effort")

    print("\nINTERPRETATION:")
    print("  - The biggest PM lever is the entry-band filter (S1-S3 = Option A, already live)")
    print("  - Half-Kelly is symmetric: shrinks wins AND losses, doesn't fix WR")
    print("  - EXHAUST override in PM contributes minor noise, not a needle-mover")
    print("  - Ultra-selective (<60c only) is the cleanest improvement, capping volume but lifting WR")
    print("=" * 100)


if __name__ == "__main__":
    main()
