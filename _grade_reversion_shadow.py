#!/usr/bin/env python3
"""
_grade_reversion_shadow.py — counterfactual grader for [REVERSION SHADOW] log lines.

For each shadow flag that landed on an actually-placed trade, compute what
would have happened if we'd acted on it:

  • action=CLEAN  → trade went as-is.  Win = good, Loss = miss.
  • action=DAMPEN → would have shrunk size by (1 − 0.5·risk).
                    On a Loss this saves money; on a Win it gives some up.
  • action=INVERT → would have flipped direction.
                    On a Loss this becomes a Win (the opposite side cleared);
                    on a Win this becomes a Loss.

The script reads the bot's per-day loguru file (default: today) and prints
a JSON-style summary plus a recommendation about promoting LIVE.

Usage:
    python3 _grade_reversion_shadow.py
    python3 _grade_reversion_shadow.py --since 2026-05-27
    python3 _grade_reversion_shadow.py --logs logs/bot_2026-05-27.log

The grader is intentionally pure (no I/O besides reading log files), so it can
run on EC2 inside cron or be diff-replayed locally with a downloaded log.
"""

import argparse
import datetime as _dt
import glob
import os
import re
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Tuple

# ── Log line regexes ──────────────────────────────────────────────────────────
# Loguru format: "HH:MM:SS | LEVEL    | [TAG] message"
_TS = r"(\d{2}):(\d{2}):(\d{2})"
RE_SHADOW = re.compile(
    rf"^{_TS}\s+\|\s+\w+\s+\|\s+\[REVERSION (SHADOW|LIVE)\]\s+"
    r"(BTC|ETH|SOL|XRP)\s+(UP|DOWN)\s+"
    r"risk=(?P<risk>[0-9.]+)\s+action=(?P<action>CLEAN|DAMPEN|INVERT)\s+"
    r"vel_adv=(?P<vel>[0-9.]+)cpm\s+spike=(?P<spike>none|mild|very)\s+"
    r"T=(?P<t>\d+)s"
)
RE_FILLED = re.compile(
    rf"^{_TS}\s+\|\s+\w+\s+\|\s+\[FILLED\]\s+(BTC|ETH|SOL|XRP)\s+(UP|DOWN)\s+\|\s+"
    r"(?P<shares>\d+)\s+shares\s+@\s+(?P<entry>\d+)c\s+=\s+\$(?P<cost>[0-9.]+)"
)
RE_RESOLVE = re.compile(
    rf"^{_TS}\s+\|\s+\w+\s+\|\s+\[RESOLVE POLY\]\s+(BTC|ETH|SOL|XRP)\s+(UP|DOWN):\s+"
    r"outcomePrice=(?P<price>[0-9.]+)"
)
RE_WINLOSS = re.compile(
    rf"^{_TS}\s+\|\s+\w+\s+\|\s+\[(WIN|LOSS)\s+(MORNING|PM|AFTERNOON)\]\s+"
    r"(BTC|ETH|SOL|XRP)\s+(UP|DOWN)\s+\|\s+([+\-]\$[0-9.]+)\s+\|\s+"
    r"Entry:\s+(\d+)c\s+x(\d+)"
)


def _parse_time(hh: str, mm: str, ss: str, day: _dt.date) -> _dt.datetime:
    return _dt.datetime(day.year, day.month, day.day, int(hh), int(mm), int(ss))


def _day_from_logpath(path: str) -> _dt.date:
    """`logs/bot_2026-05-27.log` → date(2026, 5, 27). Falls back to today."""
    m = re.search(r"bot_(\d{4})-(\d{2})-(\d{2})\.log", os.path.basename(path))
    if m:
        return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return _dt.date.today()


# ── Event extraction ──────────────────────────────────────────────────────────
class Event:
    __slots__ = ("ts", "kind", "coin", "direction", "data")

    def __init__(self, ts: _dt.datetime, kind: str, coin: str, direction: str, data: dict):
        self.ts = ts
        self.kind = kind  # SHADOW | FILLED | RESOLVE | WINLOSS
        self.coin = coin
        self.direction = direction
        self.data = data


def parse_log(path: str) -> List[Event]:
    day = _day_from_logpath(path)
    out: List[Event] = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            m = RE_SHADOW.match(line)
            if m:
                ts = _parse_time(m.group(1), m.group(2), m.group(3), day)
                out.append(Event(ts, "SHADOW", m.group(5), m.group(6), {
                    "mode": m.group(4),
                    "risk": float(m.group("risk")),
                    "action": m.group("action"),
                    "vel": float(m.group("vel")),
                    "spike": m.group("spike"),
                    "T": int(m.group("t")),
                }))
                continue
            m = RE_FILLED.match(line)
            if m:
                ts = _parse_time(m.group(1), m.group(2), m.group(3), day)
                out.append(Event(ts, "FILLED", m.group(4), m.group(5), {
                    "shares": int(m.group("shares")),
                    "entry": int(m.group("entry")),  # in cents
                    "cost": float(m.group("cost")),
                }))
                continue
            m = RE_WINLOSS.match(line)
            if m:
                ts = _parse_time(m.group(1), m.group(2), m.group(3), day)
                out.append(Event(ts, "WINLOSS", m.group(6), m.group(7), {
                    "result": m.group(4),  # WIN | LOSS
                    "phase": m.group(5),
                    "pnl_str": m.group(8),
                    "entry": int(m.group(9)),
                    "shares": int(m.group(10)),
                }))
                continue
            m = RE_RESOLVE.match(line)
            if m:
                ts = _parse_time(m.group(1), m.group(2), m.group(3), day)
                out.append(Event(ts, "RESOLVE", m.group(4), m.group(5), {
                    "price": float(m.group("price")),
                }))
    return out


# ── Counterfactual grading ────────────────────────────────────────────────────
def grade(events: List[Event]) -> dict:
    """Pair each FILLED with the most-recent SHADOW within 60s for same coin+dir,
    then look ahead for WINLOSS to decide outcome."""
    shadows = [e for e in events if e.kind == "SHADOW"]
    fills = [e for e in events if e.kind == "FILLED"]
    winlosses = [e for e in events if e.kind == "WINLOSS"]

    matched: List[dict] = []
    for fill in fills:
        # Find shadow most-recently issued for same (coin, direction), within 60s before fill.
        candidates = [
            s for s in shadows
            if s.coin == fill.coin and s.direction == fill.direction
            and 0 <= (fill.ts - s.ts).total_seconds() <= 60
        ]
        if not candidates:
            continue
        shadow = max(candidates, key=lambda s: s.ts)

        # Find the WINLOSS event that closed this fill (same coin, dir, within 25 min).
        outcome = None
        for wl in winlosses:
            if (wl.coin == fill.coin and wl.direction == fill.direction
                    and 0 < (wl.ts - fill.ts).total_seconds() <= 25 * 60):
                outcome = wl
                break
        if outcome is None:
            continue

        won = outcome.data["result"] == "WIN"
        cost = fill.data["cost"]
        # Win pnl ≈ (1 − entry) * shares; loss pnl = −cost (already in WINLOSS line)
        if won:
            # exact pnl: shares * (1 − entry/100)
            pnl_actual = fill.data["shares"] * (1 - fill.data["entry"] / 100.0)
        else:
            pnl_actual = -cost

        # Counterfactual P&L if we had ACTED on the shadow:
        action = shadow.data["action"]
        risk = shadow.data["risk"]
        if action == "CLEAN":
            pnl_cf = pnl_actual                          # no change
        elif action == "DAMPEN":
            mult = max(0.40, 1.0 - 0.5 * risk)
            pnl_cf = pnl_actual * mult                   # shrink P&L proportionally
        else:  # INVERT
            # Flipping direction inverts win→loss and vice versa, with new entry.
            # Approximation: opposite-side payout ≈ 1 - own_payout. We treat the
            # cost as similar (~0.5c spread); this is a coarse counterfactual.
            inv_size_factor = min(1.0, 0.7 + 0.5 * risk)
            if won:
                # We won UP; if we'd inverted to DOWN we would have lost the
                # inverted-side cost. Approximate as -cost * inv_size_factor.
                pnl_cf = -cost * inv_size_factor
            else:
                # We lost UP; inverted DOWN side would have won. Approximate
                # win = shares * (1 - opposite_entry) where opposite_entry ≈ 1 - entry.
                # i.e. cheap opposite side, big win.
                opposite_entry = max(0.05, 1 - fill.data["entry"] / 100.0)
                inv_shares = max(1, int(cost / opposite_entry))
                pnl_cf = inv_shares * (1 - opposite_entry) * inv_size_factor

        matched.append({
            "ts": fill.ts.isoformat(timespec="seconds"),
            "coin": fill.coin,
            "direction": fill.direction,
            "result": "WIN" if won else "LOSS",
            "shadow_action": action,
            "shadow_risk": round(risk, 3),
            "vel_cpm": round(shadow.data["vel"], 1),
            "spike": shadow.data["spike"],
            "T_at_signal": shadow.data["T"],
            "entry_cents": fill.data["entry"],
            "shares": fill.data["shares"],
            "cost": round(cost, 2),
            "pnl_actual": round(pnl_actual, 3),
            "pnl_counterfactual": round(pnl_cf, 3),
            "pnl_delta": round(pnl_cf - pnl_actual, 3),
        })

    # Aggregate
    by_action: Dict[str, List[dict]] = defaultdict(list)
    for m in matched:
        by_action[m["shadow_action"]].append(m)

    summary = {
        "n_total_shadow_flags": len(shadows),
        "n_total_fills": len(fills),
        "n_matched_pairs": len(matched),
        "by_action": {},
        "net_pnl_actual": round(sum(m["pnl_actual"] for m in matched), 2),
        "net_pnl_counterfactual": round(sum(m["pnl_counterfactual"] for m in matched), 2),
        "net_delta_if_live": round(
            sum(m["pnl_counterfactual"] for m in matched)
            - sum(m["pnl_actual"] for m in matched), 2
        ),
        "details": matched,
    }

    for action, rows in by_action.items():
        wins = [r for r in rows if r["result"] == "WIN"]
        losses = [r for r in rows if r["result"] == "LOSS"]
        delta = round(sum(r["pnl_delta"] for r in rows), 2)
        summary["by_action"][action] = {
            "n": len(rows),
            "n_actual_wins": len(wins),
            "n_actual_losses": len(losses),
            "actual_wr_pct": round(100 * len(wins) / max(1, len(rows)), 1),
            "net_pnl_actual": round(sum(r["pnl_actual"] for r in rows), 2),
            "net_pnl_cf": round(sum(r["pnl_counterfactual"] for r in rows), 2),
            "net_delta_if_live": delta,
        }

    return summary


def recommendation(summary: dict) -> str:
    """Heuristic promotion gate."""
    by = summary["by_action"]
    n_invert = by.get("INVERT", {}).get("n", 0)
    n_dampen = by.get("DAMPEN", {}).get("n", 0)
    delta = summary["net_delta_if_live"]
    n_pairs = summary["n_matched_pairs"]

    if n_pairs < 8:
        return f"NOT YET — only {n_pairs} matched pairs (need ≥8 for a stable read)."

    invert_precision = 0.0
    invert_block = by.get("INVERT", {})
    if invert_block.get("n", 0) > 0:
        # INVERT is "right" when actual outcome was a LOSS (so flipping wins).
        invert_precision = invert_block["n_actual_losses"] / max(1, invert_block["n"])

    if delta > 1.0 and (n_invert == 0 or invert_precision >= 0.55):
        return (
            f"PROMOTE — would have netted +${delta:.2f} over {n_pairs} matched trades "
            f"(INVERT precision {invert_precision*100:.0f}% over {n_invert} flags)."
        )
    if delta >= 0:
        return (
            f"WAIT — neutral-to-positive (+${delta:.2f}) but evidence is thin. "
            f"Soak another 24–48h."
        )
    return (
        f"DO NOT PROMOTE — would have cost ${delta:.2f} over {n_pairs} matched trades. "
        f"Investigate which INVERT flags were wrong before adjusting thresholds."
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--logs", default=None,
                    help="Path to a single log file (default: today's loguru file).")
    ap.add_argument("--since", default=None,
                    help="YYYY-MM-DD; aggregate every bot_*.log from this date forward.")
    ap.add_argument("--logdir", default="/home/ubuntu/v3-bot/logs",
                    help="Directory holding loguru log files.")
    ap.add_argument("--no-detail", action="store_true",
                    help="Suppress per-trade detail rows.")
    args = ap.parse_args()

    paths: List[str] = []
    if args.logs:
        paths = [args.logs]
    elif args.since:
        since_dt = _dt.date.fromisoformat(args.since)
        for p in sorted(glob.glob(os.path.join(args.logdir, "bot_*.log"))):
            if "5m" in os.path.basename(p):
                continue
            d = _day_from_logpath(p)
            if d >= since_dt:
                paths.append(p)
    else:
        today = _dt.date.today().isoformat()
        cand = os.path.join(args.logdir, f"bot_{today}.log")
        if os.path.exists(cand):
            paths = [cand]

    if not paths:
        print("[grader] no log files matched filters")
        return 1

    all_events: List[Event] = []
    for p in paths:
        ev = parse_log(p)
        print(f"[grader] {p} → {len(ev)} events")
        all_events.extend(ev)
    all_events.sort(key=lambda e: e.ts)

    summary = grade(all_events)

    print()
    print("=" * 64)
    print("  REVERSION-RISK SHADOW GRADING")
    print("=" * 64)
    print(f"  log files:           {len(paths)}")
    print(f"  total shadow flags:  {summary['n_total_shadow_flags']}")
    print(f"  total fills:         {summary['n_total_fills']}")
    print(f"  matched pairs:       {summary['n_matched_pairs']}")
    print()
    print(f"  net P&L actual:           ${summary['net_pnl_actual']:+.2f}")
    print(f"  net P&L if live:          ${summary['net_pnl_counterfactual']:+.2f}")
    print(f"  delta if we'd been live:  ${summary['net_delta_if_live']:+.2f}")
    print()
    print("  by shadow action:")
    for action in ("CLEAN", "DAMPEN", "INVERT"):
        if action not in summary["by_action"]:
            continue
        b = summary["by_action"][action]
        print(
            f"    {action:6s}  n={b['n']:<3d}  W/L={b['n_actual_wins']}/"
            f"{b['n_actual_losses']}  WR={b['actual_wr_pct']:.0f}%  "
            f"delta=${b['net_delta_if_live']:+.2f}"
        )

    if not args.no_detail and summary["details"]:
        print()
        print("  detail:")
        print(f"    {'time':<19s}  {'sym':<8s}  {'res':<4s}  "
              f"{'action':<6s}  {'risk':>4s}  {'pnl_act':>8s}  {'pnl_cf':>8s}")
        for d in summary["details"]:
            print(
                f"    {d['ts']:<19s}  {d['coin']:<3s} {d['direction']:<4s}  "
                f"{d['result']:<4s}  {d['shadow_action']:<6s}  "
                f"{d['shadow_risk']:>4.2f}  ${d['pnl_actual']:+7.2f}  "
                f"${d['pnl_counterfactual']:+7.2f}"
            )

    print()
    print(f"  → {recommendation(summary)}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
