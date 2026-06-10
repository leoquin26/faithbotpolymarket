#!/usr/bin/env python3
"""
W/L feature audit for v3-bot.

Parses logs/bot_YYYY-MM-DD.log for the last N days and joins each FILLED
trade with its preceding [SIGNAL] line and its eventual [WIN ...] / [LOSS ...]
outcome. Then bucket-summarizes win-rate and net-PnL across:
  - prob bucket
  - edge bucket
  - |trend| bucket
  - ROC60 vs trend sign agreement
  - coin
  - direction
  - hour-of-day bucket
  - phase (P1 morning / P3 midday / afternoon)
  - exhaust override flag

Goal: find subsets that are profitable (keep) vs subsets that lose (filter).
Run on EC2: python3 _wl_feature_audit.py
"""
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

LOG_DIR = Path("logs")
DAYS = int(os.getenv("AUDIT_DAYS", "10"))

SIGNAL_RE = re.compile(
    r"^(?P<ts>\d\d:\d\d:\d\d).*\[SIGNAL\] (?P<coin>\w+) (?P<dir>UP|DOWN) "
    r"\| Prob=(?P<prob>\d+)% \| Ask=(?P<ask>\d+)c \| Edge=(?P<edge>[\-+\d\.]+)% "
    r"\| Trend=(?P<trend>[\-+\d\.]+) Dist=(?P<dist>[\-+\d\.]+)% "
    r"ROC60=(?P<roc60>[\-+\d\.]+)bps"
)
FILLED_RE = re.compile(
    r"^(?P<ts>\d\d:\d\d:\d\d).*\[FILLED\] (?P<coin>\w+) (?P<dir>UP|DOWN) "
    r"\| (?P<shares>\d+) shares @ (?P<price>\d+)c = \$(?P<cost>[\d\.]+)"
)
OUTCOME_RE = re.compile(
    r"^(?P<ts>\d\d:\d\d:\d\d).*\[(?P<kind>WIN|LOSS)( MORNING| PM)?\] "
    r"(?P<coin>\w+) (?P<dir>UP|DOWN) \| (?P<pnl>[\-+]\$[\d\.]+)"
)
EX_OVR_RE = re.compile(r"\[EXHAUST OVERRIDE\] (?P<coin>\w+) (?P<dir>UP|DOWN)")
EX_FLIP_RE = re.compile(r"\[EXHAUST FLIP\] (?P<coin>\w+) (?P<dir>UP|DOWN)->")


def parse_day(path: Path):
    """Return list of trade dicts: signal+fill+outcome joined by (coin, dir, time order)."""
    open_trades = []
    finished = []
    last_signal = {}
    last_overridden = {}
    seen_lines = set()

    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.rstrip("\n")
            if line in seen_lines:
                continue
            seen_lines.add(line)

            m = EX_OVR_RE.search(line)
            if m:
                last_overridden[(m["coin"], m["dir"])] = True
                continue

            m = EX_FLIP_RE.search(line)
            if m:
                last_overridden[(m["coin"], m["dir"])] = "FLIP"
                continue

            m = SIGNAL_RE.match(line)
            if m:
                last_signal[(m["coin"], m["dir"])] = m.groupdict()
                continue

            m = FILLED_RE.match(line)
            if m:
                key = (m["coin"], m["dir"])
                sig = last_signal.get(key)
                trade = {
                    "fill_ts": m["ts"],
                    "coin": m["coin"],
                    "dir": m["dir"],
                    "shares": int(m["shares"]),
                    "fill_price": int(m["price"]) / 100.0,
                    "cost": float(m["cost"]),
                    "ovr": last_overridden.pop(key, False),
                }
                if sig:
                    trade.update(
                        prob=int(sig["prob"]) / 100.0,
                        ask=int(sig["ask"]) / 100.0,
                        edge=float(sig["edge"]) / 100.0,
                        trend=float(sig["trend"]),
                        dist=float(sig["dist"]) / 100.0,
                        roc60=float(sig["roc60"]),
                    )
                open_trades.append(trade)
                continue

            m = OUTCOME_RE.match(line)
            if m:
                key = (m["coin"], m["dir"])
                # Match against earliest open trade for this coin+dir
                for i, t in enumerate(open_trades):
                    if (t["coin"], t["dir"]) == key:
                        t["outcome"] = m["kind"]
                        t["pnl"] = float(m["pnl"].replace("$", ""))
                        finished.append(t)
                        del open_trades[i]
                        break
                continue
    return finished


def bucket(value, edges, labels):
    for i, e in enumerate(edges):
        if value < e:
            return labels[i]
    return labels[-1]


def hour_bucket(ts: str) -> str:
    h = int(ts.split(":")[0])
    if 9 <= h < 11:
        return "morning_p1 (09-11)"
    if 11 <= h < 12:
        return "p2_chop (11-12)"
    if 12 <= h < 14:
        return "p3_midday (12-14)"
    if 14 <= h < 17:
        return "afternoon (14-17)"
    return f"other ({h:02d})"


def summarize(trades, key_fn, label):
    groups = defaultdict(list)
    for t in trades:
        if "outcome" not in t:
            continue
        try:
            k = key_fn(t)
        except Exception:
            continue
        if k is None:
            continue
        groups[k].append(t)
    rows = []
    for k, ts in groups.items():
        n = len(ts)
        wins = sum(1 for t in ts if t["outcome"] == "WIN")
        wr = wins / n if n else 0.0
        pnl = sum(t["pnl"] for t in ts)
        rows.append((k, n, wins, n - wins, wr, pnl))
    rows.sort(key=lambda r: -r[5])  # by net PnL desc
    print(f"\n=== {label} ===")
    print(f"{'bucket':<28} {'N':>4} {'W':>3} {'L':>3} {'WR':>6}  {'PnL':>8}")
    for k, n, w, l, wr, pnl in rows:
        print(f"{str(k):<28} {n:>4} {w:>3} {l:>3} {wr*100:>5.1f}%  ${pnl:>+7.2f}")


def main():
    today = datetime.now().date()
    files = []
    for off in range(DAYS):
        d = today - timedelta(days=off)
        f = LOG_DIR / f"bot_{d.isoformat()}.log"
        if f.exists():
            files.append(f)
    if not files:
        print("No log files found")
        sys.exit(1)

    all_trades = []
    for f in sorted(files):
        ts = parse_day(f)
        all_trades.extend([dict(t, day=f.stem.replace("bot_", "")) for t in ts])

    finished = [t for t in all_trades if "outcome" in t]
    print(f"Loaded {len(all_trades)} fills across {len(files)} days; "
          f"{len(finished)} resolved")

    if not finished:
        return

    total_pnl = sum(t["pnl"] for t in finished)
    wins = sum(1 for t in finished if t["outcome"] == "WIN")
    print(f"Total: {wins}W/{len(finished)-wins}L = "
          f"{wins/len(finished)*100:.1f}% WR | net ${total_pnl:+.2f}")

    summarize(finished, lambda t: t["day"], "by day")
    summarize(finished, lambda t: t["coin"], "by coin")
    summarize(finished, lambda t: t["dir"], "by direction")
    summarize(finished, lambda t: f"{t['coin']} {t['dir']}", "by coin+dir")
    summarize(finished, lambda t: hour_bucket(t["fill_ts"]), "by hour bucket")

    summarize(
        finished,
        lambda t: bucket(t.get("prob", 0), [0.78, 0.80, 0.82, 0.85, 0.90],
                         ["<78", "78-79", "80-81", "82-84", "85-89", "90+"]),
        "by prob bucket",
    )
    summarize(
        finished,
        lambda t: bucket(t.get("edge", 0), [0.10, 0.15, 0.18, 0.22, 0.27],
                         ["<10%", "10-15%", "15-18%", "18-22%", "22-27%", "27%+"]),
        "by edge bucket",
    )
    summarize(
        finished,
        lambda t: bucket(abs(t.get("trend", 0)), [0.5, 0.8, 1.2, 1.6, 2.0],
                         ["<0.5", "0.5-0.8", "0.8-1.2", "1.2-1.6", "1.6-2.0", "2.0+"]),
        "by |trend| bucket",
    )
    summarize(
        finished,
        lambda t: bucket(t.get("fill_price", 0), [0.55, 0.60, 0.65],
                         ["<55c", "55-59c", "60-64c", "65c+"]),
        "by fill price",
    )

    def trend_roc_agree(t):
        tr = t.get("trend")
        roc = t.get("roc60")
        if tr is None or roc is None:
            return None
        if tr == 0 or roc == 0:
            return "zero"
        return "agree" if (tr > 0) == (roc > 0) else "disagree"

    summarize(finished, trend_roc_agree, "by trend/roc60 sign agreement")

    summarize(
        finished,
        lambda t: "OVERRIDE" if t.get("ovr") is True else
                  ("FLIP" if t.get("ovr") == "FLIP" else "normal"),
        "by exhaust override flag",
    )


if __name__ == "__main__":
    main()
