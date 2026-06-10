"""Per-day audit of the last 30 days. Find when the bot was actually
crushing it ($50 -> $250 run) and figure out what was different.

For each day, compute:
  - Trades placed / resolved
  - Win rate
  - PnL
  - Avg trade size
  - Bankroll trajectory (from KELLY lines)
  - Trades per coin
  - Time-of-day distribution
"""
import re
from pathlib import Path
from collections import defaultdict

LOG_DIR = Path("/home/ubuntu/v3-bot/logs")
import os
DAYS = sorted([p.stem.replace("bot_", "") for p in LOG_DIR.glob("bot_2026-*.log")
               if p.stat().st_size > 1000])

RE_ORDER = re.compile(
    r"\[ORDER\]\s+(\w+)\s+(UP|DOWN).*?FOK\s+@\s+(\d+)c.*?(\d+)\s+shares.*?cost=\$([\d.]+)"
)
RE_FILLED = re.compile(
    r"\[FILLED\]\s+(\w+)\s+(UP|DOWN).*?(\d+)\s+shares\s+@\s+(\d+)c"
)
RE_WIN = re.compile(
    r"\[WIN\s+\w+\]\s+(\w+)\s+(UP|DOWN)\s+\|\s+\+\$([\d.]+).*?Entry:\s+(\d+)c\s+x(\d+)"
)
RE_LOSS = re.compile(
    r"\[LOSS\s+\w+\]\s+(\w+)\s+(UP|DOWN)\s+\|\s+-\$([\d.]+).*?Entry:\s+(\d+)c\s+x(\d+)"
)
RE_KELLY = re.compile(r"\[KELLY\]\s+(\w+):.*?bankroll=\$([\d.]+)")
RE_TIME = re.compile(r"^(\d{2}):(\d{2}):(\d{2})")


def t_sec(line):
    m = RE_TIME.match(line)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) if m else 0


def parse_bot(path, is_5m_file=False):
    """Returns list of trades + bankroll snapshots."""
    orders = []
    fills = []
    wins = []
    losses = []
    bankrolls = []
    if not path.exists():
        return orders, fills, wins, losses, bankrolls
    with path.open(encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            is_5m_line = "[5M]" in line
            if is_5m_file:
                pass  # all lines are 5m in this file
            else:
                if is_5m_line:
                    continue
            ts = t_sec(line)
            m = RE_ORDER.search(line)
            if m:
                orders.append({
                    "ts": ts, "coin": m.group(1), "dir": m.group(2),
                    "limit": int(m.group(3)), "shares": int(m.group(4)),
                    "cost": float(m.group(5)),
                })
                continue
            m = RE_FILLED.search(line)
            if m:
                fills.append({
                    "ts": ts, "coin": m.group(1), "dir": m.group(2),
                    "shares": int(m.group(3)), "fill": int(m.group(4)),
                })
                continue
            m = RE_WIN.search(line)
            if m:
                wins.append({
                    "ts": ts, "coin": m.group(1), "dir": m.group(2),
                    "amt": float(m.group(3)),
                    "entry": int(m.group(4)), "shares": int(m.group(5)),
                })
                continue
            m = RE_LOSS.search(line)
            if m:
                losses.append({
                    "ts": ts, "coin": m.group(1), "dir": m.group(2),
                    "amt": float(m.group(3)),
                    "entry": int(m.group(4)), "shares": int(m.group(5)),
                })
                continue
            m = RE_KELLY.search(line)
            if m:
                bankrolls.append({"ts": ts, "coin": m.group(1), "br": float(m.group(2))})
    return orders, fills, wins, losses, bankrolls


def resolve_orders(orders, fills, wins, losses):
    for o in orders:
        o["fill"] = None
        for f in fills:
            if (f["coin"] == o["coin"] and f["dir"] == o["dir"]
                    and f["shares"] == o["shares"]
                    and 0 <= (f["ts"] - o["ts"]) <= 60):
                o["fill"] = f["fill"]
                break
        entry = o["fill"] if o["fill"] is not None else o["limit"]
        o["result"] = None
        o["pnl"] = 0
        for w in wins:
            if (w["coin"] == o["coin"] and w["dir"] == o["dir"]
                    and w["shares"] == o["shares"]
                    and w["entry"] == entry
                    and w["ts"] >= o["ts"]):
                o["result"] = "WIN"
                o["pnl"] = w["amt"]
                break
        if o["result"] is None:
            for l in losses:
                if (l["coin"] == o["coin"] and l["dir"] == o["dir"]
                        and l["shares"] == o["shares"]
                        and l["entry"] == entry
                        and l["ts"] >= o["ts"]):
                    o["result"] = "LOSS"
                    o["pnl"] = -l["amt"]
                    break
    return orders


def main():
    print(f"========= 30-DAY GROWTH AUDIT =========\n")
    print(f"Days found: {len(DAYS)}\n")

    print(f"{'Date':>11}  {'15m':>20}  {'5m':>20}  "
          f"{'Total':>8}  {'Bankroll':>9}")
    print(f"{'':>11}  {'n  W  L   WR  PnL':>20}  "
          f"{'n  W  L   WR  PnL':>20}  {'':>8}  {'':>9}")
    print("-" * 90)

    grand_15m = []
    grand_5m = []

    for d in DAYS:
        p15 = LOG_DIR / f"bot_{d}.log"
        orders15, fills15, wins15, losses15, br15 = parse_bot(p15, is_5m_file=False)
        orders15 = resolve_orders(orders15, fills15, wins15, losses15)

        # 5m from same file (5M-tagged) + separate 5m file
        all_5m_orders, all_5m_fills, all_5m_wins, all_5m_losses = [], [], [], []
        # 5M lines from main log
        if p15.exists():
            with p15.open(encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    if "[5M]" not in line:
                        continue
                    ts = t_sec(line)
                    m = RE_ORDER.search(line)
                    if m:
                        all_5m_orders.append({
                            "ts": ts, "coin": m.group(1), "dir": m.group(2),
                            "limit": int(m.group(3)), "shares": int(m.group(4)),
                            "cost": float(m.group(5)),
                        })
                        continue
                    m = RE_FILLED.search(line)
                    if m:
                        all_5m_fills.append({
                            "ts": ts, "coin": m.group(1), "dir": m.group(2),
                            "shares": int(m.group(3)), "fill": int(m.group(4)),
                        })
                        continue
                    m = RE_WIN.search(line)
                    if m:
                        all_5m_wins.append({
                            "ts": ts, "coin": m.group(1), "dir": m.group(2),
                            "amt": float(m.group(3)),
                            "entry": int(m.group(4)), "shares": int(m.group(5)),
                        })
                        continue
                    m = RE_LOSS.search(line)
                    if m:
                        all_5m_losses.append({
                            "ts": ts, "coin": m.group(1), "dir": m.group(2),
                            "amt": float(m.group(3)),
                            "entry": int(m.group(4)), "shares": int(m.group(5)),
                        })

        p5m = LOG_DIR / f"bot_5m_{d}.log"
        if p5m.exists():
            o, fi, w, l, _ = parse_bot(p5m, is_5m_file=True)
            all_5m_orders.extend(o)
            all_5m_fills.extend(fi)
            all_5m_wins.extend(w)
            all_5m_losses.extend(l)

        orders5m = resolve_orders(all_5m_orders, all_5m_fills,
                                  all_5m_wins, all_5m_losses)

        # Dedup 5m
        seen = set()
        dedup = []
        for o in orders5m:
            k = (o["ts"], o["coin"], o["dir"], o["shares"], o["limit"])
            if k in seen:
                continue
            seen.add(k)
            dedup.append(o)
        orders5m = dedup

        # End-of-day bankroll
        eod_br = br15[-1]["br"] if br15 else 0

        for o in orders15:
            o["day"] = d
        for o in orders5m:
            o["day"] = d
        grand_15m.extend(orders15)
        grand_5m.extend(orders5m)

        def fmt(orders):
            res = [o for o in orders if o["result"] in ("WIN", "LOSS")]
            n = len(res)
            w = sum(1 for o in res if o["result"] == "WIN")
            l = sum(1 for o in res if o["result"] == "LOSS")
            wr = (w / (w + l) * 100) if (w + l) else 0
            pnl = sum(o["pnl"] for o in res)
            return f"{n:>2} {w:>2} {l:>2}  {wr:>4.1f} ${pnl:>+6.2f}"

        total_pnl = (sum(o["pnl"] for o in orders15 if o["result"] in ("WIN", "LOSS"))
                     + sum(o["pnl"] for o in orders5m if o["result"] in ("WIN", "LOSS")))

        print(f"{d:>11}  {fmt(orders15):>20}  {fmt(orders5m):>20}  "
              f"${total_pnl:>+6.2f}  ${eod_br:>8.2f}")

    # Summary stats
    print()
    print("=" * 90)
    res_15m = [o for o in grand_15m if o["result"] in ("WIN", "LOSS")]
    res_5m = [o for o in grand_5m if o["result"] in ("WIN", "LOSS")]

    def summary(arr, name):
        n = len(arr)
        w = sum(1 for o in arr if o["result"] == "WIN")
        l = sum(1 for o in arr if o["result"] == "LOSS")
        wr = (w / (w + l) * 100) if (w + l) else 0
        pnl = sum(o["pnl"] for o in arr)
        avg = pnl / n if n else 0
        print(f"{name}: n={n}  W={w} L={l}  WR={wr:.1f}%  PnL=${pnl:+.2f}  avg/trade=${avg:+.2f}")

    summary(res_15m, "15m TOTAL")
    summary(res_5m, "5m TOTAL")
    print()

    # Best days
    days_pnl = defaultdict(float)
    days_n = defaultdict(int)
    days_w = defaultdict(int)
    days_l = defaultdict(int)
    for o in res_15m + res_5m:
        days_pnl[o["day"]] += o["pnl"]
        days_n[o["day"]] += 1
        if o["result"] == "WIN":
            days_w[o["day"]] += 1
        else:
            days_l[o["day"]] += 1
    print("=== TOP 8 DAYS BY PnL ===")
    for d in sorted(days_pnl.keys(), key=lambda x: days_pnl[x], reverse=True)[:8]:
        print(f"  {d}: n={days_n[d]:>3} W={days_w[d]:>2} L={days_l[d]:>2} "
              f"WR={(days_w[d]/(days_w[d]+days_l[d])*100 if days_w[d]+days_l[d] else 0):.1f}% "
              f"PnL=${days_pnl[d]:+.2f}")
    print()
    print("=== WORST 5 DAYS ===")
    for d in sorted(days_pnl.keys(), key=lambda x: days_pnl[x])[:5]:
        print(f"  {d}: n={days_n[d]:>3} W={days_w[d]:>2} L={days_l[d]:>2} "
              f"WR={(days_w[d]/(days_w[d]+days_l[d])*100 if days_w[d]+days_l[d] else 0):.1f}% "
              f"PnL=${days_pnl[d]:+.2f}")

    # By coin
    print()
    print("=== PnL by coin (combined 15m + 5m, all days) ===")
    coin_pnl = defaultdict(lambda: {"n": 0, "w": 0, "l": 0, "pnl": 0})
    for o in res_15m + res_5m:
        c = coin_pnl[o["coin"]]
        c["n"] += 1
        c["pnl"] += o["pnl"]
        if o["result"] == "WIN":
            c["w"] += 1
        else:
            c["l"] += 1
    for coin in sorted(coin_pnl.keys(), key=lambda x: coin_pnl[x]["pnl"], reverse=True):
        c = coin_pnl[coin]
        wr = (c["w"] / (c["w"] + c["l"]) * 100) if (c["w"] + c["l"]) else 0
        print(f"  {coin}: n={c['n']:>3} W={c['w']:>2} L={c['l']:>2} "
              f"WR={wr:>5.1f}% PnL=${c['pnl']:>+7.2f}")

    # Per-coin × per-bot
    print()
    print("=== 5m bot per-coin breakdown ===")
    coin_pnl_5m = defaultdict(lambda: {"n": 0, "w": 0, "l": 0, "pnl": 0})
    for o in res_5m:
        c = coin_pnl_5m[o["coin"]]
        c["n"] += 1
        c["pnl"] += o["pnl"]
        if o["result"] == "WIN":
            c["w"] += 1
        else:
            c["l"] += 1
    for coin in sorted(coin_pnl_5m.keys(), key=lambda x: coin_pnl_5m[x]["pnl"], reverse=True):
        c = coin_pnl_5m[coin]
        wr = (c["w"] / (c["w"] + c["l"]) * 100) if (c["w"] + c["l"]) else 0
        print(f"  {coin}: n={c['n']:>3} W={c['w']:>2} L={c['l']:>2} "
              f"WR={wr:>5.1f}% PnL=${c['pnl']:>+7.2f}")


if __name__ == "__main__":
    main()
