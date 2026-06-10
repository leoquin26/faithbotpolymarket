#!/usr/bin/env python3
"""
Deep analysis: direction-accuracy audit across all available logs.

For each FILLED trade, compute: was the bet in the direction that won?
- Match [SIGNAL] -> [FILLED] -> [WIN|LOSS]
- Group by day, by coin, by hour bucket
- Compare to spot price moves to validate predictor not flipped
"""
import re
from pathlib import Path
from datetime import date, timedelta
from collections import defaultdict

LOG_DIR = Path("logs")
SIGNAL_RE = re.compile(
    r"^(?P<ts>\d\d:\d\d:\d\d).*\[SIGNAL\] (?P<coin>\w+) (?P<dir>UP|DOWN) "
    r"\| Prob=(?P<prob>\d+)% \| Ask=(?P<ask>\d+)c \| Edge=(?P<edge>[\-+\d\.]+)% "
    r"\| Trend=(?P<trend>[\-+\d\.]+) Dist=(?P<dist>[\-+\d\.]+)% "
    r"ROC60=(?P<roc60>[\-+\d\.]+)bps"
)
FILLED_RE = re.compile(
    r"^(?P<ts>\d\d:\d\d:\d\d).*\[FILLED\] (?P<coin>\w+) (?P<dir>UP|DOWN)"
)
OUT_RE = re.compile(
    r"^(?P<ts>\d\d:\d\d:\d\d).*\[(?P<kind>WIN|LOSS)( MORNING| PM| 5M)?\] "
    r"(?P<coin>\w+) (?P<dir>UP|DOWN)"
)


def parse_day(fp):
    sig_by_coin_dir = {}
    open_fills = []
    finished = []
    seen = set()
    with fp.open(encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.rstrip()
            if line in seen:
                continue
            seen.add(line)

            m = SIGNAL_RE.match(line)
            if m:
                key = (m["coin"], m["dir"])
                sig_by_coin_dir[key] = {
                    "ts": m["ts"],
                    "prob": int(m["prob"]) / 100,
                    "edge": float(m["edge"]) / 100,
                    "trend": float(m["trend"]),
                    "dist": float(m["dist"]) / 100,
                    "roc60": float(m["roc60"]),
                }
                continue
            m = FILLED_RE.match(line)
            if m:
                key = (m["coin"], m["dir"])
                sig = sig_by_coin_dir.get(key, {})
                open_fills.append({
                    "fill_ts": m["ts"],
                    "coin": m["coin"],
                    "dir": m["dir"],
                    **sig,
                })
                continue
            m = OUT_RE.match(line)
            if m:
                key = (m["coin"], m["dir"])
                for i, t in enumerate(open_fills):
                    if (t["coin"], t["dir"]) == key:
                        t["outcome"] = m["kind"]
                        finished.append(t)
                        del open_fills[i]
                        break
    return finished


def hour_bucket(ts):
    h = int(ts[:2])
    if 9 <= h < 11:
        return "09-11 morning"
    if 11 <= h < 12:
        return "11-12 chop"
    if 12 <= h < 14:
        return "12-14 midday"
    if 14 <= h < 17:
        return "14-17 afternoon"
    return f"other {h:02d}"


def main():
    today = date.today()
    files = []
    for off in range(20):
        d = today - timedelta(days=off)
        fp = LOG_DIR / f"bot_{d.isoformat()}.log"
        if fp.exists():
            files.append((d, fp))

    by_day = defaultdict(list)
    by_coin = defaultdict(list)
    by_hour = defaultdict(list)
    by_dir = defaultdict(list)
    all_trades = []

    for d, fp in files:
        trades = parse_day(fp)
        for t in trades:
            t["day"] = d.isoformat()
            all_trades.append(t)
            by_day[d.isoformat()].append(t)
            by_coin[t["coin"]].append(t)
            by_hour[hour_bucket(t["fill_ts"])].append(t)
            by_dir[t["dir"]].append(t)

    print(f"{'DEEP DIRECTION-ACCURACY ANALYSIS':^60}")
    print("=" * 60)
    print(f"Total fills resolved: {len(all_trades)} across {len(by_day)} days")
    print()

    def show(label, groups, sort_by="day"):
        print(f"\n=== {label} ===")
        print(f"{'bucket':<22} {'N':>4} {'W':>3} {'L':>3} {'WR':>7}  {'mean_prob':>9} {'mean_edge':>9}")
        rows = []
        for k, ts in groups.items():
            n = len(ts)
            w = sum(1 for t in ts if t.get("outcome") == "WIN")
            wr = 100.0 * w / max(n, 1)
            mean_prob = sum(t.get("prob", 0) for t in ts) / max(n, 1)
            mean_edge = sum(t.get("edge", 0) for t in ts) / max(n, 1)
            rows.append((k, n, w, n - w, wr, mean_prob, mean_edge))
        if sort_by == "day":
            rows.sort(key=lambda r: r[0])
        else:
            rows.sort(key=lambda r: -r[4])
        for k, n, w, l, wr, mp, me in rows:
            print(
                f"{str(k):<22} {n:>4} {w:>3} {l:>3} {wr:>6.1f}%  "
                f"{mp*100:>8.1f}% {me*100:>+8.1f}%"
            )

    show("BY DAY (chronological)", by_day, sort_by="day")
    show("BY COIN", by_coin, sort_by="wr")
    show("BY DIRECTION", by_dir, sort_by="wr")
    show("BY HOUR BUCKET", by_hour, sort_by="wr")

    # Direction self-consistency: did predictor.dir agree with sign(trend)?
    # If trend was negative and dir was UP → that would be a real bug.
    print(f"\n=== DIRECTION SELF-CONSISTENCY (predictor sanity) ===")
    consistent = inconsistent = 0
    inconsistent_examples = []
    for t in all_trades:
        tr = t.get("trend")
        d = t["dir"]
        if tr is None:
            continue
        # Bot direction should agree with sign(trend) IF |trend| meaningful
        if abs(tr) < 0.1:
            continue
        is_up_dir = d == "UP"
        is_up_trend = tr > 0
        if is_up_dir == is_up_trend:
            consistent += 1
        else:
            inconsistent += 1
            inconsistent_examples.append(t)
    total_checked = consistent + inconsistent
    pct = 100.0 * consistent / max(total_checked, 1)
    print(f"Direction matches trend sign: {consistent}/{total_checked} ({pct:.1f}%)")
    if inconsistent_examples:
        print(f"\nInconsistent examples (BUG indicators if any):")
        for t in inconsistent_examples[:10]:
            print(
                f"  {t['day']} {t['fill_ts']} {t['coin']:<4} {t['dir']:<4} "
                f"trend={t.get('trend'):+.2f} dist={t.get('dist',0)*100:+.3f}% "
                f"roc60={t.get('roc60'):+.1f}bps "
                f"-> {t.get('outcome', '?')}"
            )

    # Per-day mean-reversion rate: how often did winning direction *flip* mid-window?
    # Approximated by: mean signal-trend strength per day, lower = more whipsaw
    print(f"\n=== MARKET REGIME (mean |trend| per day) ===")
    print(f"{'day':<12} {'N':>4} {'mean_|trend|':>12} {'WR':>7}  {'verdict':<25}")
    for day in sorted(by_day.keys()):
        ts = by_day[day]
        if not ts:
            continue
        n = len(ts)
        mean_abs_trend = sum(abs(t.get("trend", 0)) for t in ts) / max(n, 1)
        w = sum(1 for t in ts if t.get("outcome") == "WIN")
        wr = 100.0 * w / max(n, 1)
        verdict = (
            "strong trend (good)" if mean_abs_trend >= 1.2
            else "moderate (ok)" if mean_abs_trend >= 0.8
            else "weak/whipsaw (hard)"
        )
        print(f"{day:<12} {n:>4} {mean_abs_trend:>12.2f} {wr:>6.1f}%  {verdict:<25}")


if __name__ == "__main__":
    main()
