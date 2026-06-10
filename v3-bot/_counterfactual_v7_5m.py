"""V7 Counterfactual — 5m bot only.

Two goals:
  1. Validate the "Whipsaw Guard" filter: BLOCK trades entered after a
     violent ask price swing (>=12c range in 120s) followed by an 8c+
     drift from first-signal ask. This captures the reversal-trap losses
     observed 5/6 14:43, 5/11 10:32, 5/12 11:05.

  2. Find opportunities for a tier-based sizing strategy ("trade more,
     smaller, more constant"):
        - A-tier: edge >= 20%, trend >= 1.0  → full size
        - B-tier: edge >= 15%, trend >= 0.80 → current default
        - C-tier: edge >= 10%, trend >= 0.50 → half size

We log every SIGNAL (not just ORDERs) so we can size the C-tier opportunity.

Output:
  - per-day W/L/PnL under each filter
  - baseline vs filters PnL delta
  - whipsaw audit (top-20 swing trades with outcome)
  - near-miss SIGNAL counts that the current B-tier-only filter rejects
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

RE_SIGNAL = re.compile(
    r"^(?P<t>\d{2}:\d{2}:\d{2})\s.*?\[SIGNAL\]\s+(?P<coin>BTC|ETH|SOL|XRP)\s+"
    r"(?P<dir>UP|DOWN)\s+\|\s+Prob=(?P<prob>\d+)%\s+\|\s+Ask=(?P<ask>\d+)c\s+\|\s+"
    r"Edge=(?P<edge>[+-]?[\d.]+)%\s+\|\s+Trend=(?P<trend>[+-]?[\d.]+)\s+"
    r"Dist=(?P<dist>[+-]?[\d.]+)%\s+ROC60=(?P<roc>[+-]?[\d.]+)bps\s+"
    r"σ=(?P<sigma>[\d.eE+-]+)"
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
# 5m also logs CHEAP/EXPENSIVE/CLOB RANGE with the live ask — use them
# to reconstruct a full ask-price timeline per (coin, dir).
RE_ASK_TICK = re.compile(
    r"^(?P<t>\d{2}:\d{2}:\d{2})\s.*?\[(?:CHEAP|EXPENSIVE|CLOB RANGE)\]\s+"
    r"(?P<coin>BTC|ETH|SOL|XRP)\s+(?P<dir>UP|DOWN):\s+ask=(?P<ask>\d+)c"
)


def hms_to_secs(t: str) -> int:
    h, m, s = (int(x) for x in t.split(":"))
    return h * 3600 + m * 60 + s


@dataclass
class Signal:
    day: str
    t_sec: int
    coin: str
    dir: str
    ask: int
    edge: float
    trend: float
    roc: float
    sigma: float


@dataclass
class AskTick:
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
    cur_ask: int = 0
    cur_edge: float = 0.0
    cur_trend: float = 0.0
    cur_roc: float = 0.0
    cur_sigma: float = 0.0
    first_ask: int = 0
    first_edge: float = 0.0
    first_t_sec: int = 0
    # whipsaw features
    ask_max_120s: int = 0
    ask_min_120s: int = 0
    swing_120s: int = 0
    drift_first: int = 0
    result: Optional[str] = None
    pnl: float = 0.0


def parse_5m_log(path: Path) -> tuple[list[Signal], list[AskTick], list[Order]]:
    day = path.stem.split("_")[-1]
    sigs: list[Signal] = []
    ticks: list[AskTick] = []
    orders: list[Order] = []
    fills: list[dict] = []
    wins: list[dict] = []
    losses: list[dict] = []
    with path.open("r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            m = RE_SIGNAL.search(line)
            if m:
                sigs.append(Signal(
                    day=day, t_sec=hms_to_secs(m["t"]),
                    coin=m["coin"], dir=m["dir"],
                    ask=int(m["ask"]), edge=float(m["edge"]),
                    trend=float(m["trend"]), roc=float(m["roc"]),
                    sigma=float(m["sigma"]),
                ))
                # signal itself is also an ask tick
                ticks.append(AskTick(day, hms_to_secs(m["t"]),
                                     m["coin"], m["dir"], int(m["ask"])))
                continue
            m = RE_ASK_TICK.search(line)
            if m:
                ticks.append(AskTick(day, hms_to_secs(m["t"]),
                                     m["coin"], m["dir"], int(m["ask"])))
                continue
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
        # latest signal before order, same coin/dir, within 30s
        for s in reversed(sigs):
            if s.t_sec > o.t_sec:
                continue
            if s.coin == o.coin and s.dir == o.dir and (o.t_sec - s.t_sec) <= 30:
                o.cur_ask = s.ask
                o.cur_edge = s.edge
                o.cur_trend = s.trend
                o.cur_roc = s.roc
                o.cur_sigma = s.sigma
                break
        # first signal for same coin/dir within last 30 min
        for s in sigs:
            if s.coin != o.coin or s.dir != o.dir:
                continue
            if 0 <= (o.t_sec - s.t_sec) <= 30 * 60:
                if o.first_ask == 0:
                    o.first_ask = s.ask
                    o.first_edge = s.edge
                    o.first_t_sec = s.t_sec
                break
        # ask range in last 120s — across all directions for the coin
        # (whipsaw on UP side = collapse on DOWN side: same event).
        window_start = o.t_sec - 120
        window_ticks = [
            t.ask for t in ticks
            if t.coin == o.coin and t.dir == o.dir
            and window_start <= t.t_sec <= o.t_sec
        ]
        if window_ticks:
            o.ask_max_120s = max(window_ticks)
            o.ask_min_120s = min(window_ticks)
            o.swing_120s = o.ask_max_120s - o.ask_min_120s
        if o.first_ask:
            o.drift_first = o.ask - o.first_ask
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
    return sigs, ticks, orders


# ───────── Filters ─────────

def f_whipsaw(o: Order, swing_cents: int = 12, drift_cents: int = 8) -> bool:
    return (o.swing_120s >= swing_cents
            and o.first_ask > 0
            and abs(o.drift_first) >= drift_cents)


def f_anti_chase(o: Order, chase_cents: int = 3) -> bool:
    if not o.first_ask:
        return False
    return (o.ask - o.first_ask) >= chase_cents


def f_v7(o: Order) -> bool:
    return f_whipsaw(o) or f_anti_chase(o)


def main():
    all_sigs: list[Signal] = []
    all_orders: list[Order] = []
    files = []
    for d in DAYS:
        p = LOG_DIR / f"bot_5m_{d}.log"
        if p.exists() and p.stat().st_size > 1000:
            files.append(p)

    for p in files:
        sigs, ticks, orders = parse_5m_log(p)
        all_sigs.extend(sigs)
        all_orders.extend(orders)

    # Dedup orders (the 5/6-5/7 logs had duplicate INFO lines)
    seen = set()
    deduped: list[Order] = []
    for o in all_orders:
        k = (o.day, o.t_sec, o.coin, o.dir, o.ask, o.shares)
        if k in seen:
            continue
        seen.add(k)
        deduped.append(o)
    all_orders = deduped

    resolved = [o for o in all_orders if o.result in ("WIN", "LOSS")]
    print(f"\n=== 5m bot, {len(files)} day(s) ===")
    print(f"Total ORDERs: {len(all_orders)}  resolved: {len(resolved)}")

    actual_pnl = sum(o.pnl for o in resolved)
    actual_w = sum(1 for o in resolved if o.result == "WIN")
    actual_l = sum(1 for o in resolved if o.result == "LOSS")
    print(f"BASELINE: W={actual_w} L={actual_l}  "
          f"WR={(actual_w/max(1,len(resolved))*100):.1f}%  "
          f"PnL=${actual_pnl:+.2f}")

    def eval_filter(fn, name: str, mode: str = "BLOCK"):
        flagged = [o for o in resolved if fn(o)]
        flagged_w = sum(1 for o in flagged if o.result == "WIN")
        flagged_l = sum(1 for o in flagged if o.result == "LOSS")
        flagged_pnl = sum(o.pnl for o in flagged)
        new_pnl = actual_pnl - flagged_pnl if mode == "BLOCK" else actual_pnl
        wr_flag = (flagged_w / (flagged_w + flagged_l) * 100
                   if (flagged_w + flagged_l) else 0.0)
        print(f"\n--- {name} ({mode}) ---")
        print(f"  flagged: {len(flagged)} (W={flagged_w} L={flagged_l} "
              f"WR={wr_flag:.1f}%)")
        print(f"  flagged actual PnL: ${flagged_pnl:+.2f}")
        print(f"  PnL UNDER {name}: ${new_pnl:+.2f}  "
              f"(Δ vs actual: ${new_pnl - actual_pnl:+.2f})")
        return flagged

    print("\n========== V7 Whipsaw Guard ==========")
    eval_filter(lambda o: f_whipsaw(o, 12, 8), "Whipsaw(swing≥12c, drift≥8c)")
    eval_filter(lambda o: f_whipsaw(o, 10, 6), "Whipsaw(swing≥10c, drift≥6c) — tighter")
    eval_filter(lambda o: f_whipsaw(o, 15, 10), "Whipsaw(swing≥15c, drift≥10c) — looser")

    print("\n========== Anti-Chase Guard ==========")
    eval_filter(lambda o: f_anti_chase(o, 3), "AntiChase(≥3c)")
    eval_filter(lambda o: f_anti_chase(o, 2), "AntiChase(≥2c) — strict")

    print("\n========== V7 Combined (Whipsaw OR Anti-Chase ≥3c) ==========")
    flagged_v7 = eval_filter(f_v7, "V7 combined")

    # Audit every order
    print("\n=== Per-order audit (sorted by swing desc) ===")
    print(f"{'day':>10} {'t':>9} {'coin':>4} {'dir':>4} "
          f"{'ask':>3} {'fAsk':>4} {'min120':>7} {'max120':>7} "
          f"{'swing':>5} {'drift':>5} "
          f"{'edge':>5} {'trend':>6} {'res':>4} {'pnl':>6} "
          f"{'WHIP':>4} {'CHASE':>5}")
    for o in sorted(resolved, key=lambda x: -x.swing_120s)[:40]:
        hh, rem = divmod(o.t_sec, 3600)
        mm, ss = divmod(rem, 60)
        print(f"{o.day:>10} {f'{hh:02d}:{mm:02d}:{ss:02d}':>9} "
              f"{o.coin:>4} {o.dir:>4} "
              f"{o.ask:>3} {o.first_ask:>4} {o.ask_min_120s:>7} "
              f"{o.ask_max_120s:>7} {o.swing_120s:>5} {o.drift_first:>+5} "
              f"{o.cur_edge:>5.1f} {o.cur_trend:>+6.2f} "
              f"{o.result:>4} {o.pnl:>+6.2f} "
              f"{'Y' if f_whipsaw(o) else '-':>4} "
              f"{'Y' if f_anti_chase(o) else '-':>5}")

    # ──── Tier-sizing analysis ────
    # Signals already pass through the bot's 15% edge / 0.8 trend / etc.
    # so the SIGNAL log = "tier-B+" already.
    # We need to see: of the [SIGNAL] events that didn't become orders,
    # how many would qualify under a C-tier (10% edge, 0.5 trend) cut?
    # And on outcomes — we can use proximate SIGNALS for orders as proxy.
    print("\n========== Tier-sizing opportunity ==========")
    by_tier_orders = defaultdict(lambda: {"w": 0, "l": 0, "pnl": 0.0})
    for o in resolved:
        e, tr = o.cur_edge, abs(o.cur_trend)
        if e >= 20 and tr >= 1.0:
            tier = "A"
        elif e >= 15 and tr >= 0.8:
            tier = "B"
        elif e >= 10 and tr >= 0.5:
            tier = "C"
        else:
            tier = "X"
        bucket = by_tier_orders[tier]
        bucket["w"] += 1 if o.result == "WIN" else 0
        bucket["l"] += 1 if o.result == "LOSS" else 0
        bucket["pnl"] += o.pnl
    print("Within RESOLVED orders (already trade-approved):")
    print(f"  {'tier':>5} {'W':>3} {'L':>3} {'WR':>5} {'PnL':>8}")
    for t in ("A", "B", "C", "X"):
        b = by_tier_orders[t]
        total = b["w"] + b["l"]
        wr = b["w"] / total * 100 if total else 0
        print(f"  {t:>5} {b['w']:>3} {b['l']:>3} {wr:>4.1f}% ${b['pnl']:>+7.2f}")

    # How many SIGNAL events DIDN'T become orders but fall into A or B tier?
    # (these would be near-miss high-quality entries the bot rejected for
    # other reasons: dir-lock, weak trend further upstream, exhaust, etc.)
    sig_a, sig_b, sig_c = 0, 0, 0
    for s in all_sigs:
        e, tr = s.edge, abs(s.trend)
        if e >= 20 and tr >= 1.0:
            sig_a += 1
        elif e >= 15 and tr >= 0.8:
            sig_b += 1
        elif e >= 10 and tr >= 0.5:
            sig_c += 1
    print(f"\nAll SIGNAL events (5m) — opportunity sizing:")
    print(f"  A-tier (edge≥20%, trend≥1.0): {sig_a}")
    print(f"  B-tier (edge≥15%, trend≥0.8): {sig_b}")
    print(f"  C-tier (edge≥10%, trend≥0.5): {sig_c}  ← new tier candidates")

    # Whipsaw stats per result type (sanity check that whipsaw correlates w/ loss)
    print("\n=== Swing distribution by outcome ===")
    swings_win = sorted(o.swing_120s for o in resolved if o.result == "WIN")
    swings_loss = sorted(o.swing_120s for o in resolved if o.result == "LOSS")
    def pct(arr, p):
        if not arr:
            return 0
        idx = int(len(arr) * p / 100)
        return arr[min(idx, len(arr) - 1)]
    print(f"  WINS  n={len(swings_win)}  p50={pct(swings_win,50)}c "
          f"p75={pct(swings_win,75)}c p90={pct(swings_win,90)}c max={max(swings_win) if swings_win else 0}c")
    print(f"  LOSS  n={len(swings_loss)} p50={pct(swings_loss,50)}c "
          f"p75={pct(swings_loss,75)}c p90={pct(swings_loss,90)}c max={max(swings_loss) if swings_loss else 0}c")


if __name__ == "__main__":
    main()
