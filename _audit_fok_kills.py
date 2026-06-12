"""Distinguish unique FOK kills (by orderID) from duplicate retries and net errors."""
import re
import glob
from collections import defaultdict

ORDER_RE = re.compile(r"\[ORDER\] (\w+) (UP|DOWN) \| FOK @ (\d+)c \| (\d+) shares")
FILL_RE = re.compile(r"\[FILLED\] (\w+) (UP|DOWN) \| (\d+) shares @ (\d+)c")
FOK_KILL_RE = re.compile(
    r"\[ERROR\] Order failed for (\w+):.*?FOK orders are fully filled or killed.*?'orderID': '([^']+)'"
)
NET_ERR_RE = re.compile(
    r"\[ERROR\] Order failed for (\w+):.*?(Request exception|Connection|Timeout|read|aborted)",
    re.IGNORECASE,
)


def audit(path):
    n_order = n_fill = 0
    fok_unique_oids = set()
    fok_dup_lines = 0  # raw error log lines mentioning FOK kill
    net_errors = 0
    coin_kill_count = defaultdict(int)
    try:
        with open(path, errors="ignore") as fh:
            for line in fh:
                if ORDER_RE.search(line):
                    n_order += 1
                    continue
                if FILL_RE.search(line):
                    n_fill += 1
                    continue
                m = FOK_KILL_RE.search(line)
                if m:
                    fok_dup_lines += 1
                    oid = m.group(2)
                    if oid not in fok_unique_oids:
                        fok_unique_oids.add(oid)
                        coin_kill_count[m.group(1)] += 1
                    continue
                if NET_ERR_RE.search(line):
                    net_errors += 1
    except Exception:
        return None
    return {
        "orders": n_order,
        "fills": n_fill,
        "fok_unique": len(fok_unique_oids),
        "fok_log_lines": fok_dup_lines,
        "net_err": net_errors,
        "by_coin": dict(coin_kill_count),
    }


def main():
    files = sorted(
        set(
            glob.glob("logs/bot_2026-05-*.log")
            + glob.glob("logs/bot_5m_2026-05-*.log")
        )
    )
    files = [f for f in files if "stderr" not in f]
    print(
        "{:<28} {:>6} {:>6} {:>10} {:>11} {:>8} {:>11}".format(
            "file", "orders", "fills", "fok_uniq", "fok_lines", "net_err", "true_fill%"
        )
    )
    print("-" * 90)
    for f in files:
        r = audit(f)
        if not r or (r["orders"] == 0 and r["fok_unique"] == 0 and r["net_err"] == 0):
            continue
        # "true denominator" = unique attempts (orders + unique FOK kills + net errors not retried)
        # Actually: orders is what made it past _calc_size and got submitted ONCE.
        # fok_unique are unique orderIDs that the CLOB rejected.
        # The retry layer re-submits the SAME orderID, inflating fok_log_lines but not fok_unique.
        denom = r["orders"]  # original distinct submissions
        true_fill = r["fills"] / denom * 100 if denom else 0
        print(
            "{:<28} {:>6} {:>6} {:>10} {:>11} {:>8} {:>10.1f}%   {}".format(
                f.split("/")[-1],
                r["orders"],
                r["fills"],
                r["fok_unique"],
                r["fok_log_lines"],
                r["net_err"],
                true_fill,
                r["by_coin"],
            )
        )


if __name__ == "__main__":
    main()
