"""V7B — Tier-sizing opportunity sizing for 5m bot.

We need to know: of the trade windows the 5m bot processed, how many had
a signal that meets C-tier criteria (edge>=10%, |trend|>=0.5) but DIDN'T
become an order? That's the addressable opportunity for "trade more,
smaller, more constant" via tier-based sizing.

A "window" = a single market resolution period (5m), keyed by
(day, coin, window_start_minute). We collapse to one (coin, dir) outcome.

We classify each window into the BEST tier of any signal observed:
  A: edge>=20% AND |trend|>=1.0
  B: edge>=15% AND |trend|>=0.8
  C: edge>=10% AND |trend|>=0.5
  (lower tiers are subsumed; classification = best-tier reached)

For each window we record:
  - tier
  - whether an ORDER was placed
  - actual W/L outcome (if any order in window resolved)

Output:
  - tier × ordered-or-not matrix
  - PnL by tier among orders that fired
  - count of C-tier "missed" windows = the new-trades opportunity
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from collections import defaultdict
from typing import Optional


LOG_DIR = Path("/home/ubuntu/v3-bot/logs")
DAYS = ["2026-05-04", "2026-05-06", "2026-05-07", "2026-05-08",
        "2026-05-11", "2026-05-12"]

# Match BOTH "[5M] [SIGNAL]" and bare "[SIGNAL]" (older logs had double-log).
# We'll dedup by (day, t_sec, coin, dir, ask, edge).
RE_SIGNAL = re.compile(
    r"^(?P<t>\d{2}:\d{2}:\d{2}).*?\[SIGNAL\]\s+(?P<coin>BTC|ETH|SOL|XRP)\s+"
    r"(?P<dir>UP|DOWN)\s+\|\s+Prob=(?P<prob>\d+)%\s+\|\s+Ask=(?P<ask>\d+)c\s+\|\s+"
    r"Edge=(?P<edge>[+-]?[\d.]+)%\s+\|\s+Trend=(?P<trend>[+-]?[\d.]+)\s+"
    r"Dist=(?P<dist>[+-]?[\d.]+)%\s+ROC60=(?P<roc>[+-]?[\d.]+)bps\s+"
    r"σ=(?P<sigma>[\d.eE+-]+)\s+T=(?P<remain>\d+)s"
)
RE_ORDER = re.compile(
    r"^(?P<t>\d{2}:\d{2}:\d{2}).*?\[ORDER\]\s+(?P<coin>BTC|ETH|SOL|XRP)\s+"
    r"(?P<dir>UP|DOWN)\s+\|\s+FOK\s+@\s+(?P<ask>\d+)c\s+\|\s+(?P<shares>\d+)\s+shares"
)
RE_WIN = re.compile(
    r"^(?P<t>\d{2}:\d{2}:\d{2}).*?\[WIN\s+5M\]\s+"
    r"(?P<coin>BTC|ETH|SOL|XRP)\s+(?P<dir>UP|DOWN)\s+\|\s+\+\$(?P<amt>[\d.]+)\s+\|\s+"
    r"Entry:\s+(?P<entry>\d+)c\s+x(?P<shares>\d+)"
)
RE_LOSS = re.compile(
    r"^(?P<t>\d{2}:\d{2}:\d{2}).*?\[LOSS\s+5M\]\s+"
    r"(?P<coin>BTC|ETH|SOL|XRP)\s+(?P<dir>UP|DOWN)\s+\|\s+-\$(?P<amt>[\d.]+)\s+\|\s+"
    r"Entry:\s+(?P<entry>\d+)c\s+x(?P<shares>\d+)"
)


def hms_to_secs(t: str) -> int:
    h, m, s = (int(x) for x in t.split(":"))
    return h * 3600 + m * 60 + s


def tier_of(edge: float, trend: float) -> str:
    e, tr = edge, abs(trend)
    if e >= 20.0 and tr >= 1.0:
        return "A"
    if e >= 15.0 and tr >= 0.8:
        return "B"
    if e >= 10.0 and tr >= 0.5:
        return "C"
    return "X"


@dataclass
class WindowRec:
    day: str
    coin: str
    dir: str
    window_start_sec: int   # signal_t + remain - 300
    best_tier: str = "X"
    n_signals: int = 0
    ordered: bool = False
    result: Optional[str] = None
    pnl: float = 0.0
    best_edge: float = 0.0
    best_trend: float = 0.0


def main():
    # Collect per-day data
    windows: dict[tuple, WindowRec] = {}
    orders_seen: list[dict] = []
    results: list[dict] = []   # WIN / LOSS

    for d in DAYS:
        p = LOG_DIR / f"bot_5m_{d}.log"
        if not p.exists():
            continue
        sig_dedup = set()
        with p.open("r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                m = RE_SIGNAL.search(line)
                if m:
                    t_sec = hms_to_secs(m["t"])
                    coin, dir_ = m["coin"], m["dir"]
                    edge = float(m["edge"])
                    trend = float(m["trend"])
                    ask = int(m["ask"])
                    remain = int(m["remain"])
                    # window_start = current time - (300 - remain)
                    wstart = t_sec - (300 - remain)
                    # round to nearest 5s to keep dedup robust
                    wstart_bucket = (wstart // 30) * 30
                    sig_key = (d, t_sec, coin, dir_, ask, int(edge * 10))
                    if sig_key in sig_dedup:
                        continue
                    sig_dedup.add(sig_key)
                    wkey = (d, coin, dir_, wstart_bucket)
                    if wkey not in windows:
                        windows[wkey] = WindowRec(
                            day=d, coin=coin, dir=dir_,
                            window_start_sec=wstart_bucket,
                        )
                    w = windows[wkey]
                    w.n_signals += 1
                    t = tier_of(edge, trend)
                    rank = {"A": 4, "B": 3, "C": 2, "X": 1}
                    if rank[t] > rank[w.best_tier]:
                        w.best_tier = t
                        w.best_edge = edge
                        w.best_trend = trend
                    continue
                m = RE_ORDER.search(line)
                if m:
                    orders_seen.append({
                        "day": d, "t_sec": hms_to_secs(m["t"]),
                        "coin": m["coin"], "dir": m["dir"],
                        "ask": int(m["ask"]), "shares": int(m["shares"]),
                    })
                    continue
                m = RE_WIN.search(line)
                if m:
                    results.append({
                        "day": d, "t_sec": hms_to_secs(m["t"]),
                        "coin": m["coin"], "dir": m["dir"],
                        "shares": int(m["shares"]), "entry": int(m["entry"]),
                        "kind": "WIN", "amt": float(m["amt"]),
                    })
                    continue
                m = RE_LOSS.search(line)
                if m:
                    results.append({
                        "day": d, "t_sec": hms_to_secs(m["t"]),
                        "coin": m["coin"], "dir": m["dir"],
                        "shares": int(m["shares"]), "entry": int(m["entry"]),
                        "kind": "LOSS", "amt": -float(m["amt"]),
                    })

    # Dedup orders (some files have double-log)
    seen_ord = set()
    orders: list[dict] = []
    for o in orders_seen:
        k = (o["day"], o["t_sec"], o["coin"], o["dir"], o["ask"], o["shares"])
        if k in seen_ord:
            continue
        seen_ord.add(k)
        orders.append(o)

    # Mark windows that had orders
    for o in orders:
        for wkey, w in windows.items():
            if w.day != o["day"] or w.coin != o["coin"] or w.dir != o["dir"]:
                continue
            if abs(o["t_sec"] - w.window_start_sec) > 300:
                continue
            # match window where order happened within window
            if w.window_start_sec <= o["t_sec"] <= w.window_start_sec + 300:
                w.ordered = True
                # match result
                for r in results:
                    if (r["day"] == o["day"] and r["coin"] == o["coin"]
                            and r["dir"] == o["dir"]
                            and r["shares"] == o["shares"]
                            and r["t_sec"] >= o["t_sec"]
                            and r["t_sec"] - o["t_sec"] <= 600):
                        w.result = r["kind"]
                        w.pnl = r["amt"]
                        break
                break

    # ── Report ──
    by_tier_total = defaultdict(int)
    by_tier_ordered = defaultdict(int)
    by_tier_pnl = defaultdict(float)
    by_tier_w = defaultdict(int)
    by_tier_l = defaultdict(int)
    for w in windows.values():
        by_tier_total[w.best_tier] += 1
        if w.ordered:
            by_tier_ordered[w.best_tier] += 1
            by_tier_pnl[w.best_tier] += w.pnl
            if w.result == "WIN":
                by_tier_w[w.best_tier] += 1
            elif w.result == "LOSS":
                by_tier_l[w.best_tier] += 1

    print(f"\n=== 5m windows by BEST tier reached ({len(windows)} unique windows) ===")
    print(f"{'tier':>5} {'windows':>8} {'ordered':>8} {'fire%':>6} "
          f"{'W':>3} {'L':>3} {'WR':>5} {'PnL':>8}")
    for t in ("A", "B", "C", "X"):
        n = by_tier_total[t]
        nord = by_tier_ordered[t]
        pct = nord / n * 100 if n else 0
        w = by_tier_w[t]
        l = by_tier_l[t]
        wr = w / (w + l) * 100 if (w + l) else 0
        print(f"  {t:>3} {n:>8} {nord:>8} {pct:>5.1f}% "
              f"{w:>3} {l:>3} {wr:>4.1f}% ${by_tier_pnl[t]:>+7.2f}")

    # By coin
    print(f"\n=== Windows by coin × tier ===")
    by_coin_tier = defaultdict(lambda: defaultdict(int))
    for w in windows.values():
        by_coin_tier[w.coin][w.best_tier] += 1
    for coin in sorted(by_coin_tier):
        print(f"  {coin}: " + "  ".join(
            f"{t}={by_coin_tier[coin][t]}" for t in ("A", "B", "C", "X")
        ))

    # Per-day window counts that reached at least C-tier
    print(f"\n=== Per-day NEW C-tier opportunity (windows that reached C but not ordered) ===")
    per_day_new = defaultdict(int)
    per_day_ordered = defaultdict(int)
    for w in windows.values():
        if w.best_tier == "C" and not w.ordered:
            per_day_new[w.day] += 1
        if w.ordered:
            per_day_ordered[w.day] += 1
    for d in DAYS:
        new = per_day_new.get(d, 0)
        ord_ = per_day_ordered.get(d, 0)
        print(f"  {d}: ordered={ord_}  new-C-tier-opportunities={new}")


if __name__ == "__main__":
    main()
