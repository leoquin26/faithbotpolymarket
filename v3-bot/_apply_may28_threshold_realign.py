"""
_apply_may28_threshold_realign.py — make morning P3 thresholds env-driven
to match the v6 honest-probability calibration.

The v6 predictor formula (steepness 1.5 + 50/50 blend) produces ~10pp
lower probabilities on near-strike setups vs the old (3.0 + 70/30) formula.
The morning thresholds were calibrated for the OLD distribution:
  P1_MIN_PROB = 0.78  (env-driven, can lower in .env)
  P3_MIN_PROB = 0.78  (HARDCODED — must patch source)
  MORNING_OVERRIDE_PROB = 0.88  (env-driven)

Today's strongest signal (ETH UP, trend=+4.42) only hit Prob=75% under v6.
Without realignment, no morning signals can fire.

This patch makes ALL three P3 thresholds env-driven (and renames the
internal vars for clarity), so the .env change can take effect.
"""
from __future__ import annotations

import os
import sys
from typing import List, Tuple

REPO = "/home/ubuntu/v3-bot"


def patch_file(path: str, edits: List[Tuple[str, str, str]]) -> int:
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    applied = 0
    for label, anchor, replacement in edits:
        if replacement in src:
            print(f"  [skip] {label}: replacement already present")
            continue
        if anchor not in src:
            raise RuntimeError(
                f"{path}: anchor for {label!r} not found and replacement "
                "not present — manual intervention needed"
            )
        if src.count(anchor) > 1:
            raise RuntimeError(
                f"{path}: anchor for {label!r} matches multiple times "
                f"({src.count(anchor)})"
            )
        src = src.replace(anchor, replacement, 1)
        applied += 1
        print(f"  [done] {label}")
    if applied:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(src)
        os.replace(tmp, path)
    return applied


MORNING_EDITS: List[Tuple[str, str, str]] = [
    (
        "P3_MIN_PROB / EDGE / TREND now env-driven (was hardcoded for old prob distribution)",
        '''P3_ALLOWED = {"BTC", "ETH", "SOL", "XRP"}
P3_MIN_PROB = 0.78
P3_MIN_EDGE = 0.08
P3_MIN_TREND = 0.50''',
        '''P3_ALLOWED = {"BTC", "ETH", "SOL", "XRP"}
# [MAY 28] env-driven so v6 honest-probability calibration can lower these
P3_MIN_PROB = float(_os_getenv("P3_MIN_PROB", "0.78"))
P3_MIN_EDGE = float(_os_getenv("P3_MIN_EDGE", "0.08"))
P3_MIN_TREND = float(_os_getenv("P3_MIN_TREND", "0.50"))''',
    ),
]


def main() -> int:
    print("=" * 64)
    print("  May 28: morning threshold env-realignment")
    print("=" * 64)
    print()
    print("→ morning_strategy.py")
    p_path = os.path.join(REPO, "morning_strategy.py")
    n = patch_file(p_path, MORNING_EDITS)
    print(f"  applied {n}/{len(MORNING_EDITS)} edits")
    print()
    print("→ Verifying syntax")
    import py_compile
    try:
        py_compile.compile(p_path, doraise=True)
        print(f"  [OK] morning_strategy.py")
    except py_compile.PyCompileError as e:
        print(f"  [FAIL] {e}")
        return 2
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
