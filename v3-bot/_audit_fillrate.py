"""Audit order→fill rate, miss rate, and average slippage in cents."""
import re
import glob

ORDER = re.compile(
    r"\[ORDER\] (\w+) (UP|DOWN) \| FOK @ (\d+)c \| (\d+) shares \(cost=\$([\d.]+)"
)
FILL = re.compile(
    r"\[FILLED\] (\w+) (UP|DOWN) \| (\d+) shares @ (\d+)c = \$([\d.]+)"
)
MISS = re.compile(r"\[MISS\] (\w+) (UP|DOWN)")


def audit_file(path):
    orders, fills, miss = [], [], 0
    try:
        with open(path, errors="ignore") as fh:
            for line in fh:
                m = ORDER.search(line)
                if m:
                    orders.append(
                        {
                            "coin": m.group(1),
                            "side": m.group(2),
                            "limit": int(m.group(3)),
                            "shares": int(m.group(4)),
                        }
                    )
                    continue
                m = FILL.search(line)
                if m:
                    fills.append(
                        {
                            "coin": m.group(1),
                            "side": m.group(2),
                            "shares": int(m.group(3)),
                            "avg": int(m.group(4)),
                        }
                    )
                    continue
                if MISS.search(line):
                    miss += 1
    except Exception:
        return None
    return orders, fills, miss


def main():
    files = sorted(
        set(glob.glob("logs/bot_2026-05-*.log") + glob.glob("logs/bot_5m_2026-05-*.log"))
    )
    files = [f for f in files if "stderr" not in f]
    print(
        "{:<32}{:>7}{:>7}{:>6}{:>9}{:>10}".format(
            "file", "orders", "fills", "miss", "fill_pct", "avg_slip"
        )
    )
    print("-" * 80)
    total_o = total_f = total_m = 0
    slips = []
    for f in files:
        result = audit_file(f)
        if not result:
            continue
        orders, fills, miss = result
        if not orders:
            continue
        used = set()
        local_slips = []
        for fl in fills:
            for i, o in enumerate(orders):
                if i in used:
                    continue
                if o["coin"] == fl["coin"] and o["side"] == fl["side"]:
                    local_slips.append(fl["avg"] - o["limit"])
                    used.add(i)
                    break
        avg = sum(local_slips) / len(local_slips) if local_slips else 0
        n_o, n_f = len(orders), len(fills)
        fr = n_f / n_o * 100 if n_o else 0
        bn = f.split("/")[-1]
        print(
            "{:<32}{:>7}{:>7}{:>6}{:>8.1f}%  {:>+6.1f}c".format(
                bn, n_o, n_f, miss, fr, avg
            )
        )
        total_o += n_o
        total_f += n_f
        total_m += miss
        slips.extend(local_slips)
    print("-" * 80)
    if total_o:
        print(
            "{:<32}{:>7}{:>7}{:>6}{:>8.1f}%  {:>+6.1f}c".format(
                "TOTAL",
                total_o,
                total_f,
                total_m,
                total_f / total_o * 100,
                sum(slips) / len(slips) if slips else 0,
            )
        )
    print()
    print("Slippage distribution (limit - fill, cents):")
    if slips:
        from collections import Counter

        c = Counter(slips)
        for k in sorted(c):
            bar = "#" * c[k]
            print(f"  {k:+3d}c  {c[k]:>3}  {bar}")
        print(
            f"\n  exact_fill (slip=0): {c.get(0, 0)}  "
            f"better_than_limit (slip<0): {sum(v for k, v in c.items() if k < 0)}  "
            f"worse_than_limit (slip>0): {sum(v for k, v in c.items() if k > 0)}"
        )


if __name__ == "__main__":
    main()
