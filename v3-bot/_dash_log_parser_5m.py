"""Per-day ledger from bot logs: pair every ORDER with its WIN/LOSS line.

Captures: time, coin, side, ask, shares, size, edge, then the resolution
line later in the day. Outputs a per-day rollup: trades, WR, net pnl
estimate, big vs small bucket WR.
"""
import re
import sys
import glob
from collections import defaultdict

ORDER_15 = re.compile(
    r"^(\d{2}:\d{2}:\d{2})\s.*\[ORDER\] (BTC|ETH|SOL|XRP) (UP|DOWN) \| FOK @ (\d+)c \| (\d+) shares \(cost=\$([\d.]+), sized=\$([\d.]+)\) \| Edge ([\d.]+)%"
)
FILLED_15 = re.compile(
    r"^(\d{2}:\d{2}:\d{2})\s.*\[FILLED\] (BTC|ETH|SOL|XRP) (UP|DOWN) \| (\d+) shares @ (\d+)c = \$([\d.]+)"
)
WIN_15 = re.compile(
    r"^(\d{2}:\d{2}:\d{2})\s.*\[WIN (PM|MORNING|5M)\] (BTC|ETH|SOL|XRP) (UP|DOWN) \| \+\$([\d.]+)"
)
LOSS_15 = re.compile(
    r"^(\d{2}:\d{2}:\d{2})\s.*\[LOSS (PM|MORNING|5M)\] (BTC|ETH|SOL|XRP) (UP|DOWN) \| -\$([\d.]+)"
)


def parse_log(path, is_5m=False):
    fills = []
    pending = []
    try:
        with open(path, errors="ignore") as f:
            for line in f:
                m = FILLED_15.search(line)
                if m:
                    pending.append({
                        "time": m.group(1),
                        "coin": m.group(2),
                        "side": m.group(3),
                        "shares": int(m.group(4)),
                        "fill_c": int(m.group(5)),
                        "cost": float(m.group(6)),
                        "outcome": None,
                        "pnl": None,
                    })
                    continue
                w = WIN_15.search(line)
                if w:
                    coin, side = w.group(3), w.group(4)
                    pnl = float(w.group(5))
                    for p in pending:
                        if p["outcome"] is None and p["coin"] == coin and p["side"] == side:
                            p["outcome"] = "WIN"
                            p["pnl"] = pnl
                            fills.append(p)
                            break
                    continue
                l = LOSS_15.search(line)
                if l:
                    coin, side = l.group(3), l.group(4)
                    cost = float(l.group(5))
                    for p in pending:
                        if p["outcome"] is None and p["coin"] == coin and p["side"] == side:
                            p["outcome"] = "LOSS"
                            p["pnl"] = -cost
                            fills.append(p)
                            break
    except FileNotFoundError:
        return []
    for p in pending:
        if p["outcome"] is None:
            fills.append(p)
    return fills


def report(date, rows15, rows5):
    rows = rows15 + rows5
    if not rows:
        print(f"  {date}: (no trades)")
        return
    n = len(rows)
    wins = sum(1 for r in rows if r["outcome"] == "WIN")
    losses = sum(1 for r in rows if r["outcome"] == "LOSS")
    pending = n - wins - losses
    pnl = sum(r["pnl"] or 0 for r in rows if r["outcome"] in ("WIN", "LOSS"))
    big = [r for r in rows if r["cost"] >= 5]
    small = [r for r in rows if r["cost"] < 5]
    bw = sum(1 for r in big if r["outcome"] == "WIN")
    sw = sum(1 for r in small if r["outcome"] == "WIN")
    by_coin = defaultdict(lambda: {"n": 0, "w": 0, "pnl": 0.0})
    for r in rows:
        if r["outcome"] is None:
            continue
        by_coin[r["coin"]]["n"] += 1
        by_coin[r["coin"]]["pnl"] += r["pnl"] or 0
        if r["outcome"] == "WIN":
            by_coin[r["coin"]]["w"] += 1
    coin_str = " ".join(
        f"{c}:{d['w']}/{d['n']}({d['pnl']:+.1f})"
        for c, d in sorted(by_coin.items())
    )
    print(
        f"  {date}: n={n:>2}  W={wins} L={losses} P={pending}  "
        f"WR={wins/(wins+losses)*100 if (wins+losses) else 0:5.1f}%  "
        f"PnL=${pnl:+6.2f}  big {bw}/{len(big)}  small {sw}/{len(small)}  | {coin_str}"
    )


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else "logs"
    days = sorted({
        m.group(1)
        for f in glob.glob(f"{base}/bot_2026-05-*.log") + glob.glob(f"{base}/bot_5m_2026-05-*.log")
        for m in [re.search(r"(\d{4}-\d{2}-\d{2})", f)]
        if m
    })
    print(f"Per-day rollup ({base}):")
    for d in days:
        r15 = parse_log(f"{base}/bot_{d}.log")
        r5 = parse_log(f"{base}/bot_5m_{d}.log", is_5m=True)
        report(d, r15, r5)


if __name__ == "__main__":
    main()
