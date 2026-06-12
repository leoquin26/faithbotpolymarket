"""Audit: is there a real correlation between bet size and outcome?

Hypothesis the user is testing:
  "When the bot bets high size (Kelly $5+), it tends to lose;
   when it bets low size ($3 fixed), it tends to win."

Two competing explanations:
  A) Bug: predictor is biased / over-confident on big bets
  B) Structural: 15m bot uses bigger sizing AND has 3x longer
     reversal window than 5m bot, so loss probability is higher
     by horizon, not by sizing decision.

Approach: pair every FILLED line with the next matching WIN/LOSS
line for the same coin+side, separately for 15m and 5m bots.
"""
import re, glob
from collections import defaultdict

ORDER = re.compile(
    r"\[(?:5M\] \[)?ORDER\] (BTC|ETH|SOL|XRP) (UP|DOWN) \| FOK @ (\d+)c \| (\d+) shares \(cost=\$([\d.]+), sized=\$([\d.]+)\)"
)
WIN = re.compile(r"\[(WIN|LOSS) (?:5M|MORNING|PM)\] (BTC|ETH|SOL|XRP) (UP|DOWN) \| (?:\+|\-)\$([\d.]+)")


def parse_bot(file_glob: str, bot: str):
    rows = []
    pending = []
    for f in sorted(glob.glob(file_glob)):
        date = re.search(r"(\d{4}-\d{2}-\d{2})", f).group(1)
        try:
            with open(f, errors="ignore") as fh:
                for line in fh:
                    m = ORDER.search(line)
                    if m:
                        pending.append({
                            "date": date,
                            "bot": bot,
                            "coin": m.group(1),
                            "side": m.group(2),
                            "ask_c": int(m.group(3)),
                            "shares": int(m.group(4)),
                            "size_usd": float(m.group(6)),
                        })
                        continue
                    w = WIN.search(line)
                    if w:
                        outcome = w.group(1)
                        coin = w.group(2)
                        side = w.group(3)
                        for i, p in enumerate(pending):
                            if p["coin"] == coin and p["side"] == side:
                                p["outcome"] = outcome
                                rows.append(p)
                                pending.pop(i)
                                break
        except Exception:
            pass
    return rows


def bucket(rows, bot):
    print(f"\n=== {bot} bot — size vs outcome ===")
    if not rows:
        print("  (no resolved trades)")
        return
    buckets = [
        (0, 3.0, "0–3"),
        (3.0, 5.0, "3–5"),
        (5.0, 7.0, "5–7"),
        (7.0, 99, "7+"),
    ]
    for lo, hi, label in buckets:
        sub = [r for r in rows if lo <= r["size_usd"] < hi]
        if not sub:
            continue
        wins = sum(1 for r in sub if r.get("outcome") == "WIN")
        n = len(sub)
        wr = wins / n * 100 if n else 0
        avg = sum(r["size_usd"] for r in sub) / n
        print(f"  ${label:>5}  n={n:>3}  WR={wr:5.1f}%  avg=${avg:5.2f}")
    n = len(rows)
    wins = sum(1 for r in rows if r.get("outcome") == "WIN")
    print(f"  TOTAL  n={n:>3}  WR={wins/n*100:5.1f}%")


def main():
    rows15 = parse_bot("logs/bot_2026-04-2*.log", "15m") \
        + parse_bot("logs/bot_2026-04-3*.log", "15m") \
        + parse_bot("logs/bot_2026-05-0*.log", "15m")
    rows15 = [r for r in rows15 if "_5m_" not in (r.get("file") or "")]
    rows5 = parse_bot("logs/bot_5m_2026-04-2*.log", "5m") \
        + parse_bot("logs/bot_5m_2026-04-3*.log", "5m") \
        + parse_bot("logs/bot_5m_2026-05-0*.log", "5m")

    bucket(rows15, "15m")
    bucket(rows5, "5m")

    print("\n=== combined: WR by size bucket regardless of bot ===")
    bucket(rows15 + rows5, "all")


if __name__ == "__main__":
    main()
