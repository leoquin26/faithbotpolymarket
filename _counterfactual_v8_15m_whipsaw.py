"""V8 Late-Window Whipsaw counterfactual — 15m bot only.

Hypothesis: 15m bot loses when entering LATE in a window (T_remaining < 720s)
right at the TOP of a recent ask whipsaw (range >= 15c in last 90s, entry
in top 30% of that range). This catches the ETH 12:33 60c-entry loss
(ask 56→38→60 in 90s, entered at 60c = top of bounce).

We test several thresholds and tabulate:
  - flagged orders (would-be blocks)
  - W/L killed vs L saved
  - PnL delta vs baseline
  - per-trade audit so we can see which wins would die

Run on EC2 with full bot_2026-05-*.log set.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from collections import defaultdict
from typing import Optional


LOG_DIR = Path("/home/ubuntu/v3-bot/logs")
DAYS = ["2026-05-04", "2026-05-05", "2026-05-06",
        "2026-05-07", "2026-05-08", "2026-05-09",
        "2026-05-10", "2026-05-11", "2026-05-12"]

# 15m logs do NOT have a [5M] prefix — we exclude any line containing [5M].
# This rules out the duplicate stream that bled into bot_*.log historically.

RE_SIGNAL = re.compile(
    r"^(?P<t>\d{2}:\d{2}:\d{2})\s.*?\[SIGNAL\]\s+(?P<coin>BTC|ETH|SOL|XRP)\s+"
    r"(?P<dir>UP|DOWN)\s+\|\s+Prob=(?P<prob>\d+)%\s+\|\s+Ask=(?P<ask>\d+)c\s+\|\s+"
    r"Edge=(?P<edge>[+-]?[\d.]+)%\s+\|\s+Trend=(?P<trend>[+-]?[\d.]+)\s+"
    r"Dist=(?P<dist>[+-]?[\d.]+)%\s+ROC60=(?P<roc>[+-]?[\d.]+)bps\s+"
    r"σ=(?P<sigma>[\d.eE+-]+)\s+T=(?P<remain>\d+)s"
)
RE_ORDER = re.compile(
    r"^(?P<t>\d{2}:\d{2}:\d{2})\s.*?\[ORDER\]\s+(?P<coin>BTC|ETH|SOL|XRP)\s+"
    r"(?P<dir>UP|DOWN)\s+\|\s+FOK\s+@\s+(?P<ask>\d+)c\s+\|\s+(?P<shares>\d+)\s+shares\s+"
    r"\(cost=\$(?P<cost>[\d.]+),\s+sized=\$(?P<sized>[\d.]+)\)"
)
RE_FILLED = re.compile(
    r"^(?P<t>\d{2}:\d{2}:\d{2})\s.*?\[FILLED\]\s+(?P<coin>BTC|ETH|SOL|XRP)\s+"
    r"(?P<dir>UP|DOWN)\s+\|\s+(?P<shares>\d+)\s+shares\s+@\s+(?P<ask>\d+)c"
)
RE_WIN = re.compile(
    r"^(?P<t>\d{2}:\d{2}:\d{2})\s.*?\[WIN\s+(?P<sess>\w+)\]\s+"
    r"(?P<coin>BTC|ETH|SOL|XRP)\s+(?P<dir>UP|DOWN)\s+\|\s+\+\$(?P<amt>[\d.]+)\s+\|\s+"
    r"Entry:\s+(?P<entry>\d+)c\s+x(?P<shares>\d+)"
)
RE_LOSS = re.compile(
    r"^(?P<t>\d{2}:\d{2}:\d{2})\s.*?\[LOSS\s+(?P<sess>\w+)\]\s+"
    r"(?P<coin>BTC|ETH|SOL|XRP)\s+(?P<dir>UP|DOWN)\s+\|\s+-\$(?P<amt>[\d.]+)\s+\|\s+"
    r"Entry:\s+(?P<entry>\d+)c\s+x(?P<shares>\d+)"
)
# 15m bot logs ask via [CHEAP] / [EXPENSIVE] / [CLOB RANGE] / [PRICE]
RE_ASK_TICK = re.compile(
    r"^(?P<t>\d{2}:\d{2}:\d{2})\s.*?\[(?:CHEAP|EXPENSIVE|CLOB RANGE)\]\s+"
    r"(?P<coin>BTC|ETH|SOL|XRP)\s+(?P<dir>UP|DOWN):\s+ask=(?P<ask>\d+)c"
)
RE_PRICE = re.compile(
    r"^(?P<t>\d{2}:\d{2}:\d{2})\s.*?\[PRICE\]\s+(?P<coin>BTC|ETH|SOL|XRP):\s+"
    r"poly=(?P<poly>\d+)c\s+ask=(?P<ask>\d+)c\s+limit=(?P<lim>\d+)c"
)


def hms_to_secs(t: str) -> int:
    h, m, s = (int(x) for x in t.split(":"))
    return h * 3600 + m * 60 + s


@dataclass
class Tick:
    day: str
    t_sec: int
    coin: str
    dir: str
    ask: int


@dataclass
class Order:
    day: str
    t_sec: int
    coin: str
    dir: str
    ask: int
    shares: int
    sized: float
    cost: float
    fill_ask: Optional[int] = None
    sig_ask: int = 0
    sig_edge: float = 0.0
    sig_trend: float = 0.0
    sig_roc: float = 0.0
    sig_remain: int = 0
    sig_t_sec: int = 0
    # whipsaw features over last 90s, same dir, before order
    ask_min_90s: int = 0
    ask_max_90s: int = 0
    swing_90s: int = 0
    pos_in_range: float = 0.0  # 0..1, where 1 = at the top of the range
    result: Optional[str] = None
    pnl: float = 0.0


def parse_15m_log(path: Path) -> tuple[list[Tick], list[Order]]:
    day = path.stem.split("_")[-1]
    ticks: list[Tick] = []
    orders: list[Order] = []
    sigs: list[dict] = []
    fills: list[dict] = []
    wins: list[dict] = []
    losses: list[dict] = []
    with path.open("r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            # 5m bot writes into the same log with [5M] tag — skip it.
            if "[5M]" in line:
                continue
            m = RE_SIGNAL.search(line)
            if m:
                sigs.append({
                    "t_sec": hms_to_secs(m["t"]),
                    "coin": m["coin"], "dir": m["dir"],
                    "ask": int(m["ask"]), "edge": float(m["edge"]),
                    "trend": float(m["trend"]), "roc": float(m["roc"]),
                    "remain": int(m["remain"]),
                })
                ticks.append(Tick(day, hms_to_secs(m["t"]),
                                  m["coin"], m["dir"], int(m["ask"])))
                continue
            m = RE_ASK_TICK.search(line)
            if m:
                ticks.append(Tick(day, hms_to_secs(m["t"]),
                                  m["coin"], m["dir"], int(m["ask"])))
                continue
            m = RE_PRICE.search(line)
            if m:
                # [PRICE] doesn't specify dir; use both dirs implicitly via ask
                # only — skip for swing calc (ask listed already per dir).
                pass
            m = RE_ORDER.search(line)
            if m:
                orders.append(Order(
                    day=day, t_sec=hms_to_secs(m["t"]),
                    coin=m["coin"], dir=m["dir"],
                    ask=int(m["ask"]), shares=int(m["shares"]),
                    sized=float(m["sized"]), cost=float(m["cost"]),
                ))
                continue
            m = RE_FILLED.search(line)
            if m:
                fills.append({
                    "t_sec": hms_to_secs(m["t"]), "coin": m["coin"],
                    "dir": m["dir"], "shares": int(m["shares"]),
                    "fill_ask": int(m["ask"]),
                })
                continue
            m = RE_WIN.search(line)
            if m:
                wins.append({
                    "t_sec": hms_to_secs(m["t"]), "coin": m["coin"],
                    "dir": m["dir"], "entry": int(m["entry"]),
                    "shares": int(m["shares"]), "amt": float(m["amt"]),
                })
                continue
            m = RE_LOSS.search(line)
            if m:
                losses.append({
                    "t_sec": hms_to_secs(m["t"]), "coin": m["coin"],
                    "dir": m["dir"], "entry": int(m["entry"]),
                    "shares": int(m["shares"]), "amt": float(m["amt"]),
                })

    for o in orders:
        # fill
        for f in fills:
            if (f["coin"] == o.coin and f["dir"] == o.dir
                    and f["shares"] == o.shares
                    and 0 <= (f["t_sec"] - o.t_sec) <= 60):
                o.fill_ask = f["fill_ask"]
                break
        # latest signal before order, within 30s
        for s in reversed(sigs):
            if s["t_sec"] > o.t_sec:
                continue
            if s["coin"] == o.coin and s["dir"] == o.dir and (o.t_sec - s["t_sec"]) <= 30:
                o.sig_ask = s["ask"]
                o.sig_edge = s["edge"]
                o.sig_trend = s["trend"]
                o.sig_roc = s["roc"]
                o.sig_remain = s["remain"]
                o.sig_t_sec = s["t_sec"]
                break
        # whipsaw: ask range over last 90s, same coin & dir
        window_start = o.t_sec - 90
        wticks = [t.ask for t in ticks
                  if t.coin == o.coin and t.dir == o.dir
                  and window_start <= t.t_sec <= o.t_sec]
        if wticks:
            o.ask_min_90s = min(wticks)
            o.ask_max_90s = max(wticks)
            o.swing_90s = o.ask_max_90s - o.ask_min_90s
            if o.swing_90s > 0:
                o.pos_in_range = (o.ask - o.ask_min_90s) / o.swing_90s
            else:
                o.pos_in_range = 0.5
        # resolution
        entry_match = o.fill_ask if o.fill_ask is not None else o.ask
        for w in wins:
            if (w["coin"] == o.coin and w["dir"] == o.dir
                    and w["shares"] == o.shares
                    and w["entry"] == entry_match
                    and w["t_sec"] >= o.t_sec):
                o.result = "WIN"
                o.pnl = w["amt"]
                break
        if o.result is None:
            for l in losses:
                if (l["coin"] == o.coin and l["dir"] == o.dir
                        and l["shares"] == o.shares
                        and l["entry"] == entry_match
                        and l["t_sec"] >= o.t_sec):
                    o.result = "LOSS"
                    o.pnl = -l["amt"]
                    break
        if o.result is None:
            o.result = "UNRESOLVED"
    return ticks, orders


def main():
    all_orders: list[Order] = []
    for d in DAYS:
        p = LOG_DIR / f"bot_{d}.log"
        if not p.exists() or p.stat().st_size < 1000:
            continue
        _, orders = parse_15m_log(p)
        all_orders.extend(orders)

    # Dedup orders by (day, t_sec, coin, dir, ask, shares)
    seen = set()
    deduped = []
    for o in all_orders:
        k = (o.day, o.t_sec, o.coin, o.dir, o.ask, o.shares)
        if k in seen:
            continue
        seen.add(k)
        deduped.append(o)
    all_orders = deduped

    resolved = [o for o in all_orders if o.result in ("WIN", "LOSS")]
    print(f"\n=== 15m bot — full log set ===")
    print(f"orders={len(all_orders)} resolved={len(resolved)}")

    actual_pnl = sum(o.pnl for o in resolved)
    actual_w = sum(1 for o in resolved if o.result == "WIN")
    actual_l = sum(1 for o in resolved if o.result == "LOSS")
    print(f"BASELINE: W={actual_w} L={actual_l}  "
          f"WR={(actual_w/max(1,len(resolved))*100):.1f}%  "
          f"PnL=${actual_pnl:+.2f}")

    # ── Filter family ──
    def make_filter(swing_min: int, pos_min: float, t_max: int,
                    edge_max: float = 100.0):
        """Order is flagged if:
           swing_90s >= swing_min AND pos_in_range >= pos_min
           AND sig_remain < t_max AND sig_edge < edge_max."""
        def fn(o: Order) -> bool:
            return (o.swing_90s >= swing_min
                    and o.pos_in_range >= pos_min
                    and o.sig_remain > 0
                    and o.sig_remain < t_max
                    and o.sig_edge < edge_max)
        return fn

    def eval_block(fn, name: str):
        flagged = [o for o in resolved if fn(o)]
        w = sum(1 for o in flagged if o.result == "WIN")
        l = sum(1 for o in flagged if o.result == "LOSS")
        flag_pnl = sum(o.pnl for o in flagged)
        new_pnl = actual_pnl - flag_pnl
        wr = (w / (w + l) * 100) if (w + l) else 0
        print(f"\n--- {name} ---")
        print(f"  flagged: {len(flagged)} (W={w} L={l} WR={wr:.1f}%)")
        print(f"  flagged actual PnL: ${flag_pnl:+.2f}")
        print(f"  PnL UNDER BLOCK: ${new_pnl:+.2f}  "
              f"(Δ vs actual: ${new_pnl - actual_pnl:+.2f})")
        return flagged

    print("\n========== V8 Late-Window Whipsaw — variant sweep ==========")

    # Strict (high confidence, fewer flags)
    eval_block(make_filter(15, 0.70, 720), "V8a swing≥15 pos≥0.70 T<720")
    # User's exact spec
    eval_block(make_filter(15, 0.70, 720, 25), "V8b same + edge<25%")
    # Wider window
    eval_block(make_filter(15, 0.70, 900), "V8c swing≥15 pos≥0.70 T<900 (all 15m)")
    # Looser pos
    eval_block(make_filter(12, 0.60, 720), "V8d swing≥12 pos≥0.60 T<720")
    # Stricter swing
    eval_block(make_filter(20, 0.75, 720), "V8e swing≥20 pos≥0.75 T<720")
    # Very strict — only catch deep whipsaws
    eval_block(make_filter(20, 0.80, 600), "V8f swing≥20 pos≥0.80 T<600 (super late)")

    # Audit V8d (most impactful)
    print("\n=== Audit V8d (swing≥12 pos≥0.60 T<720) ===")
    print(f"{'day':>10} {'t':>9} {'coin':>4} {'dir':>4} "
          f"{'ask':>3} {'sig':>3} {'fill':>4} {'edge':>5} {'tr':>5} "
          f"{'min':>4} {'max':>4} {'sw':>3} {'pos':>4} {'T':>4} "
          f"{'res':>4} {'pnl':>7}")
    fn_d = make_filter(12, 0.60, 720)
    for o in sorted(resolved, key=lambda x: (x.day, x.t_sec)):
        if fn_d(o):
            hh, rem = divmod(o.t_sec, 3600)
            mm, ss = divmod(rem, 60)
            print(f"{o.day:>10} {f'{hh:02d}:{mm:02d}:{ss:02d}':>9} "
                  f"{o.coin:>4} {o.dir:>4} "
                  f"{o.ask:>3} {o.sig_ask:>3} "
                  f"{(o.fill_ask if o.fill_ask else 0):>4} "
                  f"{o.sig_edge:>5.1f} {o.sig_trend:>+5.2f} "
                  f"{o.ask_min_90s:>4} {o.ask_max_90s:>4} "
                  f"{o.swing_90s:>3} {o.pos_in_range:>.2f} "
                  f"{o.sig_remain:>4} "
                  f"{o.result:>4} {o.pnl:>+7.2f}")

    # Audit V8b
    print("\n=== Audit V8b (swing≥15 pos≥0.70 T<720 edge<25%) ===")
    print(f"{'day':>10} {'t':>9} {'coin':>4} {'dir':>4} "
          f"{'ask':>3} {'sig':>3} {'fill':>4} {'edge':>5} {'tr':>5} "
          f"{'min':>4} {'max':>4} {'sw':>3} {'pos':>4} {'T':>4} "
          f"{'res':>4} {'pnl':>7}")
    fn = make_filter(15, 0.70, 720, 25)
    for o in sorted(resolved, key=lambda x: (x.day, x.t_sec)):
        if fn(o):
            hh, rem = divmod(o.t_sec, 3600)
            mm, ss = divmod(rem, 60)
            print(f"{o.day:>10} {f'{hh:02d}:{mm:02d}:{ss:02d}':>9} "
                  f"{o.coin:>4} {o.dir:>4} "
                  f"{o.ask:>3} {o.sig_ask:>3} "
                  f"{(o.fill_ask if o.fill_ask else 0):>4} "
                  f"{o.sig_edge:>5.1f} {o.sig_trend:>+5.2f} "
                  f"{o.ask_min_90s:>4} {o.ask_max_90s:>4} "
                  f"{o.swing_90s:>3} {o.pos_in_range:>.2f} "
                  f"{o.sig_remain:>4} "
                  f"{o.result:>4} {o.pnl:>+7.2f}")

    # Sanity check: per-WR profile by swing bucket (to see if whipsaw IS bad on 15m)
    print("\n=== WR profile by swing_90s bucket (15m, all resolved) ===")
    buckets = [(0, 5), (5, 10), (10, 15), (15, 20), (20, 30), (30, 100)]
    for lo, hi in buckets:
        b = [o for o in resolved if lo <= o.swing_90s < hi]
        if not b:
            continue
        w = sum(1 for o in b if o.result == "WIN")
        l = sum(1 for o in b if o.result == "LOSS")
        wr = (w / (w + l) * 100) if (w + l) else 0
        pnl = sum(o.pnl for o in b)
        print(f"  swing {lo:>2}-{hi:>2}c: n={len(b):>3}  W={w:>2}  L={l:>2}  "
              f"WR={wr:>5.1f}%  PnL=${pnl:>+7.2f}")

    # Same by pos_in_range
    print("\n=== WR by pos_in_range bucket (15m, swing>=10c only) ===")
    big = [o for o in resolved if o.swing_90s >= 10]
    for lo, hi in [(0.0, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 0.9), (0.9, 1.01)]:
        b = [o for o in big if lo <= o.pos_in_range < hi]
        if not b:
            continue
        w = sum(1 for o in b if o.result == "WIN")
        l = sum(1 for o in b if o.result == "LOSS")
        wr = (w / (w + l) * 100) if (w + l) else 0
        pnl = sum(o.pnl for o in b)
        print(f"  pos {lo:.1f}-{hi:.2f}: n={len(b):>3}  W={w:>2}  L={l:>2}  "
              f"WR={wr:>5.1f}%  PnL=${pnl:>+7.2f}")

    # By T_remaining (late vs early)
    print("\n=== WR by T_remain bucket (15m, all resolved) ===")
    for lo, hi in [(0, 300), (300, 600), (600, 800), (800, 1000)]:
        b = [o for o in resolved if lo <= o.sig_remain < hi]
        if not b:
            continue
        w = sum(1 for o in b if o.result == "WIN")
        l = sum(1 for o in b if o.result == "LOSS")
        wr = (w / (w + l) * 100) if (w + l) else 0
        pnl = sum(o.pnl for o in b)
        print(f"  T={lo:>4}-{hi:>4}: n={len(b):>3}  W={w:>2}  L={l:>2}  "
              f"WR={wr:>5.1f}%  PnL=${pnl:>+7.2f}")


if __name__ == "__main__":
    main()
