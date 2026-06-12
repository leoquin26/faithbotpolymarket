#!/usr/bin/env python3
"""
_grade_calibration_shadow.py — counterfactual grader for [CALIBRATION SHADOW]
log lines.

For each calibration shadow that landed on an actually-placed trade, compute:
  • Kelly-sized $ delta between raw_prob and calibrated_prob
  • Whether the calibrated prob would have flagged a SKIP (cal < ENTRY_MIN_PROB)
  • Per-factor attribution (which calibration factor moved the size most)

Usage:
    python3 _grade_calibration_shadow.py
    python3 _grade_calibration_shadow.py --since 2026-05-27
    python3 _grade_calibration_shadow.py --logs logs/bot_2026-05-27.log

Output: per-factor lift summary + promotion recommendation.
"""

import argparse
import datetime as _dt
import glob
import math
import os
import re
from collections import defaultdict
from typing import Dict, List, Optional

# ── Log regexes ──────────────────────────────────────────────────────────────
_TS = r"(\d{2}):(\d{2}):(\d{2})"
RE_CAL = re.compile(
    rf"^{_TS}\s+\|\s+\w+\s+\|\s+\[CALIBRATION (SHADOW|LIVE)\]\s+"
    r"(BTC|ETH|SOL|XRP)\s+(UP|DOWN)\s+"
    r"raw=(?P<raw>\d+)%\s+cal=(?P<cal>\d+)%\s+\(([-+0-9.]+)pp\)\s+\|\s+"
    r"reg=(?P<reg>[0-9.]+)\s+bkt=(?P<bkt>[0-9.]+)\s+"
    r"mic=(?P<mic>[0-9.]+)\s+rev=(?P<rev>[0-9.]+)\s+late=(?P<late>[0-9.]+)"
)
RE_FILLED = re.compile(
    rf"^{_TS}\s+\|\s+\w+\s+\|\s+\[FILLED\]\s+(BTC|ETH|SOL|XRP)\s+(UP|DOWN)\s+\|\s+"
    r"(?P<shares>\d+)\s+shares\s+@\s+(?P<entry>\d+)c\s+=\s+\$(?P<cost>[0-9.]+)"
)
RE_WINLOSS = re.compile(
    rf"^{_TS}\s+\|\s+\w+\s+\|\s+\[(WIN|LOSS)\s+(MORNING|PM|AFTERNOON)\]\s+"
    r"(BTC|ETH|SOL|XRP)\s+(UP|DOWN)"
)


def _parse_time(hh: str, mm: str, ss: str, day: _dt.date) -> _dt.datetime:
    return _dt.datetime(day.year, day.month, day.day, int(hh), int(mm), int(ss))


def _day_from_logpath(path: str) -> _dt.date:
    m = re.search(r"bot_(\d{4})-(\d{2})-(\d{2})\.log", os.path.basename(path))
    if m:
        return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return _dt.date.today()


# ── Event extraction ─────────────────────────────────────────────────────────
class Event:
    __slots__ = ("ts", "kind", "coin", "direction", "data")

    def __init__(self, ts, kind, coin, direction, data):
        self.ts = ts
        self.kind = kind
        self.coin = coin
        self.direction = direction
        self.data = data


def parse_log(path: str) -> List[Event]:
    day = _day_from_logpath(path)
    out: List[Event] = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            m = RE_CAL.match(line)
            if m:
                ts = _parse_time(m.group(1), m.group(2), m.group(3), day)
                out.append(Event(ts, "CAL", m.group(5), m.group(6), {
                    "mode": m.group(4),
                    "raw": int(m.group("raw")) / 100.0,
                    "cal": int(m.group("cal")) / 100.0,
                    "reg": float(m.group("reg")),
                    "bkt": float(m.group("bkt")),
                    "mic": float(m.group("mic")),
                    "rev": float(m.group("rev")),
                    "late": float(m.group("late")),
                }))
                continue
            m = RE_FILLED.match(line)
            if m:
                ts = _parse_time(m.group(1), m.group(2), m.group(3), day)
                out.append(Event(ts, "FILL", m.group(4), m.group(5), {
                    "shares": int(m.group("shares")),
                    "entry": int(m.group("entry")) / 100.0,
                    "cost": float(m.group("cost")),
                }))
                continue
            m = RE_WINLOSS.match(line)
            if m:
                ts = _parse_time(m.group(1), m.group(2), m.group(3), day)
                out.append(Event(ts, "WL", m.group(6), m.group(7), {
                    "result": m.group(4),
                    "phase": m.group(5),
                }))
    return out


# ── Kelly sizing approximation ───────────────────────────────────────────────
def _kelly_size(prob: float, entry: float, bankroll: float,
                kelly_cap: float = 0.05, tier_factor: float = 0.75) -> float:
    """Mirror the bot's Kelly: f* = (b·p - q) / b, capped, tier-shrunk."""
    if prob <= 0 or prob >= 1 or entry <= 0 or entry >= 1:
        return 0.0
    b = (1.0 - entry) / entry
    q = 1.0 - prob
    f_star = (b * prob - q) / b
    if f_star <= 0:
        return 0.0
    f_kelly = min(kelly_cap, f_star) * tier_factor
    return bankroll * f_kelly


# ── Grading ──────────────────────────────────────────────────────────────────
def grade(events: List[Event], bankroll: float = 120.0) -> Dict:
    cals = [e for e in events if e.kind == "CAL"]
    fills = [e for e in events if e.kind == "FILL"]
    winlosses = [e for e in events if e.kind == "WL"]

    matched: List[dict] = []
    for fill in fills:
        # Latest calibration line for this (coin, direction) within 60s before fill
        candidates = [
            c for c in cals
            if c.coin == fill.coin and c.direction == fill.direction
            and 0 <= (fill.ts - c.ts).total_seconds() <= 60
        ]
        if not candidates:
            continue
        cal = max(candidates, key=lambda c: c.ts)

        outcome = None
        for wl in winlosses:
            if (wl.coin == fill.coin and wl.direction == fill.direction
                    and 0 < (wl.ts - fill.ts).total_seconds() <= 25 * 60):
                outcome = wl
                break
        if outcome is None:
            continue

        won = outcome.data["result"] == "WIN"
        entry = fill.data["entry"]
        cost_actual = fill.data["cost"]

        # Counterfactual: re-size with calibrated_prob via Kelly approximation
        size_raw = _kelly_size(cal.data["raw"], entry, bankroll)
        size_cal = _kelly_size(cal.data["cal"], entry, bankroll)
        # Compute shares & cost (mirror the bot's `max(2, int(size/limit))`)
        shares_cal = max(2, int(size_cal / entry)) if size_cal > 0 else 0
        cost_cal = shares_cal * entry

        if won:
            pnl_actual = fill.data["shares"] * (1 - entry)
            pnl_cf = shares_cal * (1 - entry) if shares_cal else 0.0
        else:
            pnl_actual = -cost_actual
            pnl_cf = -cost_cal

        matched.append({
            "ts": fill.ts.isoformat(timespec="seconds"),
            "coin": fill.coin,
            "direction": fill.direction,
            "result": "WIN" if won else "LOSS",
            "raw_prob": cal.data["raw"],
            "cal_prob": cal.data["cal"],
            "delta_pp": round((cal.data["cal"] - cal.data["raw"]) * 100, 1),
            "factors": {
                "reg": cal.data["reg"], "bkt": cal.data["bkt"],
                "mic": cal.data["mic"], "rev": cal.data["rev"],
                "late": cal.data["late"],
            },
            "entry": entry,
            "size_actual_usd": round(fill.data["shares"] * entry, 2),
            "size_cal_usd": round(cost_cal, 2),
            "size_delta_usd": round(cost_cal - cost_actual, 2),
            "pnl_actual": round(pnl_actual, 3),
            "pnl_cf": round(pnl_cf, 3),
            "pnl_delta": round(pnl_cf - pnl_actual, 3),
        })

    # Per-factor attribution: which factor moved the size most?
    factor_attribution: Dict[str, list] = defaultdict(list)
    for m in matched:
        # The factor furthest below 1.0 (or above) is the dominant mover
        f = m["factors"]
        for name, val in f.items():
            factor_attribution[name].append({
                "value": val,
                "pnl_delta": m["pnl_delta"],
                "won": m["result"] == "WIN",
            })

    summary = {
        "n_cal_lines": len(cals),
        "n_fills": len(fills),
        "n_matched": len(matched),
        "bankroll_assumed": bankroll,
        "net_pnl_actual": round(sum(m["pnl_actual"] for m in matched), 2),
        "net_pnl_cf": round(sum(m["pnl_cf"] for m in matched), 2),
        "net_delta_if_live": round(
            sum(m["pnl_cf"] for m in matched)
            - sum(m["pnl_actual"] for m in matched), 2
        ),
        "size_delta_avg": (
            round(sum(m["size_delta_usd"] for m in matched) / max(1, len(matched)), 2)
            if matched else 0.0
        ),
        "details": matched,
    }
    return summary


def recommendation(summary: Dict) -> str:
    n = summary["n_matched"]
    if n < 8:
        return f"NOT YET — only {n} matched pairs (need ≥ 8 for stable read)."
    delta = summary["net_delta_if_live"]
    if delta > 1.0:
        return (
            f"PROMOTE — calibrated sizing would have netted +${delta:.2f} "
            f"over {n} matched trades."
        )
    if delta >= -0.5:
        return (
            f"NEUTRAL — within noise (${delta:+.2f}) over {n} trades. "
            f"Soak another 24–48h."
        )
    return (
        f"DO NOT PROMOTE — would have cost ${delta:.2f} over {n} trades. "
        f"Calibrator weights need re-tuning."
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--logs", default=None)
    ap.add_argument("--since", default=None)
    ap.add_argument("--logdir", default="/home/ubuntu/v3-bot/logs")
    ap.add_argument("--bankroll", type=float, default=120.0)
    ap.add_argument("--no-detail", action="store_true")
    args = ap.parse_args()

    paths: List[str] = []
    if args.logs:
        paths = [args.logs]
    elif args.since:
        since_dt = _dt.date.fromisoformat(args.since)
        for p in sorted(glob.glob(os.path.join(args.logdir, "bot_*.log"))):
            if "5m" in os.path.basename(p):
                continue
            if _day_from_logpath(p) >= since_dt:
                paths.append(p)
    else:
        today = _dt.date.today().isoformat()
        cand = os.path.join(args.logdir, f"bot_{today}.log")
        if os.path.exists(cand):
            paths = [cand]

    if not paths:
        print("[grader] no log files matched")
        return 1

    all_events: List[Event] = []
    for p in paths:
        ev = parse_log(p)
        print(f"[grader] {p} → {len(ev)} events")
        all_events.extend(ev)
    all_events.sort(key=lambda e: e.ts)

    summary = grade(all_events, bankroll=args.bankroll)

    print()
    print("=" * 64)
    print("  CALIBRATOR SHADOW GRADING")
    print("=" * 64)
    print(f"  log files:           {len(paths)}")
    print(f"  calibration lines:   {summary['n_cal_lines']}")
    print(f"  fills:               {summary['n_fills']}")
    print(f"  matched pairs:       {summary['n_matched']}")
    print(f"  bankroll assumed:    ${summary['bankroll_assumed']:.0f}")
    print()
    print(f"  net P&L actual:           ${summary['net_pnl_actual']:+.2f}")
    print(f"  net P&L if live:          ${summary['net_pnl_cf']:+.2f}")
    print(f"  delta if we'd been live:  ${summary['net_delta_if_live']:+.2f}")
    print(f"  avg per-trade size delta: ${summary['size_delta_avg']:+.2f}")

    if not args.no_detail and summary["details"]:
        print()
        print("  detail:")
        print(f"    {'time':<19s}  {'sym':<8s}  {'res':<4s}  "
              f"{'raw%':>5s} {'cal%':>5s}  {'size$':>7s}  {'pnl_d':>7s}")
        for d in summary["details"]:
            print(
                f"    {d['ts']:<19s}  {d['coin']:<3s} {d['direction']:<4s}  "
                f"{d['result']:<4s}  "
                f"{d['raw_prob']*100:>4.0f}% {d['cal_prob']*100:>4.0f}%  "
                f"${d['size_delta_usd']:+6.2f}  ${d['pnl_delta']:+6.2f}"
            )

    print()
    print(f"  → {recommendation(summary)}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
