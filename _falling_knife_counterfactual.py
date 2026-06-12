"""Counterfactual: would a FALLING KNIFE filter (block trades where the
order's FOK fill slipped >= 2c from the limit price, i.e. ask was
collapsing in real-time) have helped over 9 days?

For each [ORDER] line, parse the FOK limit price and the [FILLED]
price. If slip >= 2c against direction (UP filled below limit,
DOWN filled above limit), that order was a falling knife.

Then aggregate WR + PnL for blocked-vs-kept set.
"""
import re
from pathlib import Path
from collections import defaultdict

LOG_DIR = Path("/home/ubuntu/v3-bot/logs")
DAYS = ["2026-05-04", "2026-05-05", "2026-05-06", "2026-05-07",
        "2026-05-08", "2026-05-09", "2026-05-10", "2026-05-11",
        "2026-05-12", "2026-05-13"]

RE_TIME = re.compile(r"^(\d{2}):(\d{2}):(\d{2})")
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


def t_sec(line):
    m = RE_TIME.match(line)
    if not m:
        return 0
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))


def parse_day(path, only_5m=False):
    if not path.exists() or path.stat().st_size < 100:
        return []
    orders = []
    fills = []
    wins = []
    losses = []
    with path.open(encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            is_5m = "[5M]" in line
            if only_5m and not is_5m:
                continue
            if not only_5m and is_5m:
                continue
            ts = t_sec(line)
            m = RE_ORDER.search(line)
            if m:
                orders.append({
                    "day": path.stem.split("_")[-1],
                    "ts": ts, "coin": m.group(1), "dir": m.group(2),
                    "limit": int(m.group(3)), "shares": int(m.group(4)),
                    "cost": float(m.group(5)),
                    "is_5m": is_5m,
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

    for o in orders:
        o["fill"] = None
        o["slip"] = 0
        for f in fills:
            if (f["coin"] == o["coin"] and f["dir"] == o["dir"]
                    and f["shares"] == o["shares"]
                    and 0 <= (f["ts"] - o["ts"]) <= 60):
                o["fill"] = f["fill"]
                o["slip"] = o["limit"] - f["fill"]
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


def report(orders, label):
    n = len(orders)
    if n == 0:
        print(f"  {label:>40}: (empty)")
        return
    w = sum(1 for o in orders if o["result"] == "WIN")
    l = sum(1 for o in orders if o["result"] == "LOSS")
    pnl = sum(o["pnl"] for o in orders if o["result"] in ("WIN", "LOSS"))
    wr = (w / (w + l) * 100) if (w + l) else 0
    print(f"  {label:>40}: n={n:>3} W={w:>2} L={l:>2} "
          f"WR={wr:>5.1f}% PnL=${pnl:>+7.2f}")


def main():
    # 15m
    all_15m = []
    all_5m = []
    for d in DAYS:
        p = LOG_DIR / f"bot_{d}.log"
        all_15m.extend(parse_day(p))
        all_5m.extend(parse_day(p, only_5m=True))
        p5m = LOG_DIR / f"bot_5m_{d}.log"
        all_5m.extend(parse_day(p5m, only_5m=False))

    # Dedup
    def dedup(orders):
        seen = set()
        out = []
        for o in orders:
            k = (o["day"], o["ts"], o["coin"], o["dir"], o["shares"], o["limit"], o.get("is_5m"))
            if k in seen:
                continue
            seen.add(k)
            out.append(o)
        return out

    all_15m = dedup(all_15m)
    all_5m = dedup(all_5m)
    resolved_15m = [o for o in all_15m if o["result"] in ("WIN", "LOSS")]
    resolved_5m = [o for o in all_5m if o["result"] in ("WIN", "LOSS")]

    for bot_name, resolved in (("15m", resolved_15m), ("5m", resolved_5m)):
        print(f"\n========= {bot_name} BOT FALLING KNIFE COUNTERFACTUAL =========")
        print(f"Total resolved: {len(resolved)}")
        report(resolved, "ALL trades (baseline)")
        print()
        print("=== Slip distribution ===")
        for s in (0, 1, 2, 3, 4, 5):
            arr = [o for o in resolved if o["slip"] == s]
            report(arr, f"slip = {s}c")
        for s in (6, 7, 99):
            arr = [o for o in resolved if o["slip"] >= s]
            report(arr, f"slip >= {s}c")
        print()
        print("=== Filter: BLOCK if slip >= 2c (proxy for falling knife) ===")
        keep = [o for o in resolved if o["slip"] < 2]
        block = [o for o in resolved if o["slip"] >= 2]
        report(keep, "KEEP (slip < 2c)")
        report(block, "BLOCK (slip >= 2c)")
        if block:
            print(f"  Δ = block PnL × -1 = ${-sum(o['pnl'] for o in block):+.2f}")
        print()
        print("=== Filter: BLOCK if slip >= 3c (stricter) ===")
        keep = [o for o in resolved if o["slip"] < 3]
        block = [o for o in resolved if o["slip"] >= 3]
        report(keep, "KEEP (slip < 3c)")
        report(block, "BLOCK (slip >= 3c)")
        if block:
            print(f"  Δ = ${-sum(o['pnl'] for o in block):+.2f}")
        print()
        print("=== Detail: every BLOCKED order (slip >= 2c) ===")
        for o in sorted([o for o in resolved if o["slip"] >= 2],
                        key=lambda x: (x["day"], x["ts"])):
            hh, rem = divmod(o["ts"], 3600)
            mm, ss = divmod(rem, 60)
            print(f"  {o['day']:>10} {f'{hh:02d}:{mm:02d}:{ss:02d}':>9} "
                  f"{o['coin']:>4} {o['dir']:>5} "
                  f"limit={o['limit']:>3}c fill={(o['fill'] or 0):>3}c "
                  f"slip={o['slip']:>2}c "
                  f"{o['result']:>4} pnl=${o['pnl']:>+6.2f}")


if __name__ == "__main__":
    main()
