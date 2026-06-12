"""Comprehensive blocker accuracy audit (v2).

Combines:
  1. trade_events.jsonl RESOLVED events to get the winning side per (coin, window).
  2. Daily log lines for every gate (FLIP GUARD, CONSENSUS, COUNTER-TREND,
     EXHAUST DAMPEN, EXHAUST OVERRIDE, TRAP BAND, MORNING P1/P2/P3,
     RECENT FLIP, EXPENSIVE, NO ASK).

For each gate, dedupe to unique (coin, side, window_start), then compare
the would-have-bet side to the actual winning side.

  KEEP    block_killed_winner < 35%   (gate is preventing losses)
  TUNE    35-50%                      (mixed; tighten/loosen)
  REVIEW  > 50%                       (gate is killing more winners than losers)
"""
import glob
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone

EVENTS_PATH = "data/trade_events.jsonl"

GATE_PATTERNS = {
    # gate_name : (regex, has_direction)
    "FLIP_GUARD": (
        re.compile(r"\[FLIP GUARD\] (\w+) (UP|DOWN):"),
        True,
    ),
    "CONSENSUS": (
        re.compile(r"\[CONSENSUS\] (\w+) (UP|DOWN):"),
        True,
    ),
    "COUNTER_TREND": (
        re.compile(r"\[COUNTER-TREND\] (\w+) (UP|DOWN):"),
        True,
    ),
    "EXHAUST_DAMPEN": (
        re.compile(r"\[EXHAUST DAMPEN\] (\w+) (UP|DOWN)"),
        True,
    ),
    "EXHAUST_OVERRIDE": (
        re.compile(r"\[EXHAUST OVERRIDE\] (\w+) (UP|DOWN):"),
        True,
    ),
    "EXHAUST_BLOCK": (
        re.compile(r"\[EXHAUST BLOCK\] (\w+) (UP|DOWN)"),
        True,
    ),
    "TRAP_BAND": (
        re.compile(r"\[TRAP BAND\] (\w+) (UP|DOWN):"),
        True,
    ),
    "MORNING_P1_BLOCK": (
        re.compile(r"\[MORNING P1\] (\w+) prob \d+% < \d+%"),
        False,  # log line doesn't include direction for blocks
    ),
    "MORNING_P3_BLOCK": (
        re.compile(r"\[MORNING P3\] (\w+) prob \d+% < \d+%"),
        False,
    ),
    "EXPENSIVE": (
        re.compile(r"\[EXPENSIVE\] (\w+) (UP|DOWN)"),
        True,
    ),
    "CHEAP": (
        re.compile(r"\[CHEAP\] (\w+) (UP|DOWN)"),
        True,
    ),
}

LOG_LINE_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2})")


def epoch_for_log_line(date_str, h, m, s):
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(
        hour=h, minute=m, second=s, tzinfo=timezone.utc
    )
    return int(dt.timestamp())


def main():
    # 1) Build resolution map: (coin, window_start) -> winning_side
    resolutions = {}
    with open(EVENTS_PATH) as f:
        for line in f:
            try:
                e = json.loads(line)
            except Exception:
                continue
            if e.get("event") != "RESOLVED":
                continue
            side = e.get("side")
            won = e.get("won")
            if not side or won is None:
                continue
            key = (e.get("coin"), e.get("window_start"))
            if won:
                resolutions[key] = side
            else:
                # Losing side; flip it for winning side
                opposite = "DOWN" if side == "UP" else "UP"
                resolutions.setdefault(key, opposite)

    # 2) Gather signals from trade_events to provide tier/prob context
    signals_by_window = defaultdict(list)  # (coin, ws) -> [signal events]
    for line in open(EVENTS_PATH):
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("event") != "SIGNAL":
            continue
        signals_by_window[(e.get("coin"), e.get("window_start"))].append(e)

    # 3) Parse all daily logs (15m + 5m), May 1-7
    files = sorted(
        glob.glob("logs/bot_2026-05-*.log")
        + glob.glob("logs/bot_5m_2026-05-*.log")
    )
    files = [f for f in files if "stderr" not in f]

    # gate -> {(coin, side, ws): {first_ts, count, bot}}
    blocks = defaultdict(dict)
    print(f"Scanning {len(files)} log files...")
    for f in files:
        bn = f.split("/")[-1]
        m = re.search(r"bot_(?:5m_)?(\d{4}-\d{2}-\d{2})\.log", bn)
        if not m:
            continue
        date_str = m.group(1)
        is_5m = "5m" in bn
        bucket_sec = 300 if is_5m else 900
        with open(f, errors="ignore") as fh:
            for line in fh:
                tm = LOG_LINE_RE.match(line)
                if not tm:
                    continue
                h, mi, s = int(tm.group(1)), int(tm.group(2)), int(tm.group(3))
                ts = epoch_for_log_line(date_str, h, mi, s)
                ws = ts // bucket_sec * bucket_sec

                for gate, (pat, has_dir) in GATE_PATTERNS.items():
                    g = pat.search(line)
                    if not g:
                        continue
                    coin = g.group(1)
                    side = g.group(2) if has_dir else "?"
                    key = (coin, side, ws)
                    if key not in blocks[gate]:
                        blocks[gate][key] = {
                            "first_ts": ts,
                            "count": 1,
                            "bot": "5m" if is_5m else "15m",
                        }
                    else:
                        blocks[gate][key]["count"] += 1
                    break

    # 4) Score each gate
    print()
    print("=" * 100)
    print("GATE ACCURACY (May 2026, where same-window resolution exists)")
    print("=" * 100)
    print(
        "{:<22} {:>10} {:>10} {:>10} {:>10} {:>10} {:>10}".format(
            "gate",
            "uniq_blks",
            "raw_lines",
            "resolved",
            "saved_L",
            "killed_W",
            "kill_pct",
        )
    )
    print("-" * 100)
    rows = []
    for gate, items in sorted(blocks.items(), key=lambda x: -len(x[1])):
        uniq = len(items)
        raw = sum(v["count"] for v in items.values())
        resolved = saved = killed = 0
        wrong_examples = []
        for (coin, side, ws), info in items.items():
            winning_side = resolutions.get((coin, ws))
            if winning_side is None:
                continue
            resolved += 1
            if side == "?":
                # gate doesn't specify direction; can't score
                continue
            if side == winning_side:
                killed += 1
                if len(wrong_examples) < 5:
                    sigs = signals_by_window.get((coin, ws), [])
                    sig = next(
                        (s for s in sigs if s.get("side") == side), {}
                    )
                    wrong_examples.append(
                        {
                            "coin": coin,
                            "side": side,
                            "ts": info["first_ts"],
                            "prob": sig.get("prob"),
                            "edge": sig.get("edge"),
                            "trend": sig.get("trend_score"),
                        }
                    )
            else:
                saved += 1
        kp = killed / (killed + saved) * 100 if (killed + saved) else 0
        if (killed + saved) == 0:
            verdict = "no scored"
        elif kp < 35:
            verdict = "KEEP"
        elif kp < 50:
            verdict = "TUNE"
        else:
            verdict = "REVIEW"
        rows.append((gate, uniq, raw, resolved, saved, killed, kp, verdict, wrong_examples))
        print(
            "{:<22} {:>10} {:>10} {:>10} {:>10} {:>10} {:>9.1f}% {:>10}".format(
                gate, uniq, raw, resolved, saved, killed, kp, verdict
            )
        )

    print()
    print("=" * 100)
    print("DETAIL: gates that killed winners (sample of mistaken blocks)")
    print("=" * 100)
    for gate, uniq, raw, resolved, saved, killed, kp, verdict, examples in rows:
        if killed == 0:
            continue
        print(f"\n[{gate}] killed {killed} winners ({kp:.1f}%):")
        for ex in examples:
            ts = datetime.fromtimestamp(ex["ts"], tz=timezone.utc).strftime(
                "%m-%d %H:%M"
            )
            pr = f"{ex['prob']:.2f}" if ex.get("prob") is not None else "?"
            ed = f"{ex['edge']:+.2f}" if ex.get("edge") is not None else "?"
            tr = f"{ex['trend']:+.2f}" if ex.get("trend") is not None else "?"
            print(
                f"  {ts}  {ex['coin']:<3} {ex['side']:<4}  "
                f"prob={pr}  edge={ed}  trend={tr}"
            )

    # 5) Sanity baseline: FIRED win rate using same resolution map
    print()
    print("=" * 100)
    print("BASELINE: FIRED-trade WR using same resolution map")
    print("=" * 100)
    fired = []
    for line in open(EVENTS_PATH):
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("event") == "FIRED":
            fired.append(e)
    won = lost = unres = 0
    for fe in fired:
        winning_side = resolutions.get((fe.get("coin"), fe.get("window_start")))
        if winning_side is None:
            unres += 1
        elif winning_side == fe.get("side"):
            won += 1
        else:
            lost += 1
    total_resolved = won + lost
    wr = won / total_resolved * 100 if total_resolved else 0
    print(f"FIRED total={len(fired)}  unresolved={unres}  W={won}  L={lost}  WR={wr:.1f}%")

    # 6) Save full per-gate detail to JSON for further drilling
    out = {
        gate: {
            "uniq_blocks": uniq,
            "raw_log_lines": raw,
            "resolved": resolved,
            "saved_losers": saved,
            "killed_winners": killed,
            "kill_pct": round(kp, 1),
            "verdict": verdict,
        }
        for gate, uniq, raw, resolved, saved, killed, kp, verdict, _ in rows
    }
    with open("_blocker_audit_results.json", "w") as f:
        json.dump(out, f, indent=2)
    print("\n[SAVED] _blocker_audit_results.json")


if __name__ == "__main__":
    main()
