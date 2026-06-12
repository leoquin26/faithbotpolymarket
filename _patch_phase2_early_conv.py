"""Phase-2 Early-Window High-Conviction Gate (F-COMBO1) — 15m bot only.

Adds a gate immediately AFTER V8 block, BEFORE the commit
(`self._window_direction = direction`).

Filter (only applies when timeframe is 15m AND T_remain >= 600s):
  ALLOW if:
    - |trend_score| >= 1.5 AND edge >= 0.20   (high conviction), OR
    - ask >= 0.65                              (high-entry zone, V6/V8 catch tails)
  Else return None with [P2 EARLY-CONV BLOCK] log.

Kill switch: env var PHASE2_EARLY_CONV=off disables the filter (default on).

Counterfactual May 4-12 (32 early-window trades):
  KEEP: 13 (10W/3L, 77% WR, +$13.56)
  DROP: 19 (8W/11L, 42% WR, -$30.88)
  → Net delta: +$30.88 over 9 days

Idempotent.
"""
import sys
import pathlib

PATH = pathlib.Path("/home/ubuntu/v3-bot/predictor.py")
src = pathlib.Path(PATH).read_text(encoding="utf-8")

ANCHOR = """                        return None

        self._window_direction = direction
"""

INSERT = """                        return None

        # ── Phase-2: Early-Window High-Conviction Gate (added 2026-05-12) ──
        # 15m only. Block low-conviction entries with T_remain >= 600s
        # to avoid the "mid-trend trap" (1.0-1.5 |trend| zone, 47% WR
        # historical, -$32.76 on 15 trades May 4-12).
        # Kill switch: env PHASE2_EARLY_CONV=off to disable.
        if _tf == "15m" and time_remaining >= 600:
            _p2_enabled = os.getenv("PHASE2_EARLY_CONV", "on").lower() != "off"
            if _p2_enabled:
                _p2_keep = (
                    (abs(trend_score) >= 1.5 and edge >= 0.20)
                    or (ask >= 0.65)
                )
                if not _p2_keep:
                    logger.info(
                        f"[P2 EARLY-CONV BLOCK] {coin} {direction}"
                        f"@{ask*100:.0f}c | trend={trend_score:+.2f} "
                        f"edge={edge*100:.1f}% T={time_remaining:.0f}s "
                        f"prob={win_prob*100:.0f}% — abstaining "
                        f"(mid-trend early-window trap)"
                    )
                    return None

        self._window_direction = direction
"""

if "P2 EARLY-CONV BLOCK" in src:
    print("Already patched — no-op")
    sys.exit(0)

if ANCHOR not in src:
    print("ANCHOR not found — V8 must be deployed first", file=sys.stderr)
    sys.exit(1)

# anchor appears twice in the file (FLIP GUARD also has same shape)
# we need the V8 one — match with V8 context
WIDER_ANCHOR_START = "                        return None\n\n        self._window_direction = direction"
count = src.count(WIDER_ANCHOR_START)
print(f"Anchor occurrences: {count}")

# Find the one right after V8 block specifically (it's preceded by V8 text)
v8_idx = src.find("[V8 WHIPSAW BLOCK]")
if v8_idx == -1:
    print("V8 marker not found — V8 must be deployed first", file=sys.stderr)
    sys.exit(1)

anchor_idx = src.find(WIDER_ANCHOR_START, v8_idx)
if anchor_idx == -1:
    print("Anchor after V8 not found", file=sys.stderr)
    sys.exit(1)

src2 = (
    src[:anchor_idx]
    + INSERT.rstrip("\n")  # already has trailing newlines in INSERT
    + src[anchor_idx + len(WIDER_ANCHOR_START):]
)

PATH.write_text(src2, encoding="utf-8")
print("Phase-2 F-COMBO1 patch applied")
print("Wrote", PATH)
