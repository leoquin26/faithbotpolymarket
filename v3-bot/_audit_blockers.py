"""Counter-factual blocker accuracy audit.

For every BLOCKED signal in trade_events.jsonl, look up the RESOLVED record
for the same (coin, window_start). Compare the blocked SIDE to the actual
winning side:

  - blocked_side == winning_side -> block KILLED A WINNER (false positive)
  - blocked_side != winning_side -> block SAVED A LOSER (correct)

For SIGNAL events that became FIRED (not blocked), pair with same window's
RESOLVED to compute true win rate of each gate's *complement*.

Also breaks down by:
  - prob/edge band (A-tier 80%+ vs B-tier 60-70%)
  - coin
  - day
"""
import json
from collections import defaultdict, Counter
from datetime import datetime, timezone

EVENTS_PATH = "data/trade_events.jsonl"


def load_events():
    out = []
    with open(EVENTS_PATH) as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def main():
    events = load_events()
    # Index resolutions by (coin, window_start) -> winning side
    resolutions = {}
    for e in events:
        if e.get("event") != "RESOLVED":
            continue
        if not e.get("won"):
            continue
        key = (e.get("coin"), e.get("window_start"))
        resolutions[key] = e.get("side")  # the side that WON

    # For SIGNAL events, attach prob/edge for tier classification
    signals_by_id = {}
    for e in events:
        if e.get("event") == "SIGNAL":
            signals_by_id[e.get("trade_id")] = e

    # For each BLOCKED event, look up resolution
    blocked_stats = defaultdict(
        lambda: {
            "total": 0,
            "with_resolution": 0,
            "block_correct": 0,
            "block_killed_winner": 0,
            "by_coin": Counter(),
            "by_tier": Counter(),
            "wrong_examples": [],
        }
    )

    # Filter to last 14 days for relevance (April 24 onward)
    cutoff_epoch = int(datetime(2026, 4, 24, tzinfo=timezone.utc).timestamp())

    for e in events:
        if e.get("event") != "BLOCKED":
            continue
        if e.get("ts_epoch", 0) < cutoff_epoch:
            continue
        gate = e.get("blocked_by", "UNKNOWN")
        st = blocked_stats[gate]
        st["total"] += 1
        coin = e.get("coin")
        side = e.get("side")
        ws = e.get("window_start")
        st["by_coin"][coin] += 1

        sig = signals_by_id.get(e.get("trade_id"), {})
        prob = sig.get("prob")
        if prob is None:
            tier = "?"
        elif prob >= 0.80:
            tier = "A(80+)"
        elif prob >= 0.70:
            tier = "B(70-80)"
        elif prob >= 0.60:
            tier = "C(60-70)"
        else:
            tier = "D(<60)"
        st["by_tier"][tier] += 1

        winning_side = resolutions.get((coin, ws))
        if winning_side is None:
            continue
        st["with_resolution"] += 1
        if winning_side == side:
            st["block_killed_winner"] += 1
            if len(st["wrong_examples"]) < 5:
                st["wrong_examples"].append(
                    {
                        "coin": coin,
                        "side": side,
                        "ts": e.get("ts"),
                        "prob": prob,
                        "edge": sig.get("edge"),
                        "trend": sig.get("trend_score"),
                    }
                )
        else:
            st["block_correct"] += 1

    # Output
    print("=" * 90)
    print("BLOCKER ACCURACY (last 14 days, where same-window resolution exists)")
    print("=" * 90)
    print(
        "{:<22} {:>7} {:>9} {:>9} {:>10} {:>9} {:>10}".format(
            "gate", "total", "resolved", "saved", "killed", "kill_pct", "verdict"
        )
    )
    print("-" * 90)
    rows = sorted(
        blocked_stats.items(),
        key=lambda x: -x[1]["total"],
    )
    for gate, st in rows:
        n = st["with_resolution"]
        if n == 0:
            print(
                "{:<22} {:>7} {:>9} {:>9} {:>10} {:>10} {:>10}".format(
                    gate, st["total"], 0, 0, 0, "n/a", "no resolution data"
                )
            )
            continue
        killed_pct = st["block_killed_winner"] / n * 100
        if killed_pct < 35:
            verdict = "KEEP (good)"
        elif killed_pct < 50:
            verdict = "TUNE"
        else:
            verdict = "REVIEW (bad)"
        print(
            "{:<22} {:>7} {:>9} {:>9} {:>10} {:>9.1f}% {:>10}".format(
                gate,
                st["total"],
                n,
                st["block_correct"],
                st["block_killed_winner"],
                killed_pct,
                verdict,
            )
        )

    print()
    print("=" * 90)
    print("PER-GATE TIER BREAKDOWN (where killed_winner happened)")
    print("=" * 90)
    for gate, st in rows:
        if st["with_resolution"] == 0:
            continue
        if st["block_killed_winner"] == 0:
            continue
        print(f"\n[{gate}] tier mix of all blocks (n={st['total']}):")
        for tier, c in st["by_tier"].most_common():
            print(f"    {tier:<10} {c}")
        print(
            f"  Coins killed-winner: "
            f"{[ex['coin'] for ex in st['wrong_examples']]}"
        )
        if st["wrong_examples"]:
            print("  Sample killed-winners:")
            for ex in st["wrong_examples"]:
                tr = ex["trend"] if ex["trend"] is not None else 0
                pr = ex["prob"] if ex["prob"] is not None else 0
                ed = ex["edge"] if ex["edge"] is not None else 0
                print(
                    f"    {ex['ts']}  {ex['coin']:<3} {ex['side']:<4}  "
                    f"prob={pr:.2f}  edge={ed:+.2f}  trend={tr:+.2f}"
                )

    # Compare: signals that FIRED vs signals BLOCKED, on same coin+window
    print()
    print("=" * 90)
    print("FIRED-SIGNAL WIN RATE BY GATE-PATH (sanity benchmark)")
    print("=" * 90)
    fired_by_id = {}
    for e in events:
        if e.get("event") == "FIRED":
            fired_by_id[e.get("trade_id")] = e
    fired_won = fired_lost = fired_unresolved = 0
    for tid, fe in fired_by_id.items():
        winning_side = resolutions.get((fe.get("coin"), fe.get("window_start")))
        if winning_side is None:
            fired_unresolved += 1
            continue
        if winning_side == fe.get("side"):
            fired_won += 1
        else:
            fired_lost += 1
    print(
        f"FIRED total={len(fired_by_id)}  resolved={fired_won + fired_lost}  "
        f"won={fired_won}  lost={fired_lost}  "
        f"WR={fired_won / (fired_won + fired_lost) * 100:.1f}%"
        if (fired_won + fired_lost)
        else ""
    )


if __name__ == "__main__":
    main()
