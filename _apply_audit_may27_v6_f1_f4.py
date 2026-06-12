"""
_apply_audit_may27_v6_f1_f4.py — re-apply F1 (MIN_DISTANCE_PCT) and
F4 (MIN_ACCURACY) gates after the original v6 script crashed on F2.
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


PREDICTOR_EDITS: List[Tuple[str, str, str]] = [
    (
        "F1+F4: MIN_DISTANCE_PCT and MIN_ACCURACY gates",
        '''        # Distance from strike as percentage
        dist_pct = (current_price - strike) / strike if strike > 0 else 0.0

        # Trend score: combines short-term momentum with position relative to strike
        # Positive = price moving UP / above strike, Negative = DOWN / below strike
        trend_score = 0.0''',
        '''        # Distance from strike as percentage
        dist_pct = (current_price - strike) / strike if strike > 0 else 0.0

        # [AUDIT MAY27 F1] enforce MIN_DISTANCE_PCT
        # Today's losses (BTC#2 dist=0.058%, BTC#3 dist=0.013%, ETH#1 dist=0.042%)
        # all happened with the price right at the strike. The config defined
        # MIN_DISTANCE_PCT=0.0008 (0.08%) but no code enforced it. Now it does.
        try:
            if os.getenv("MIN_DISTANCE_ENFORCE", "on").lower() == "on":
                _min_dist_pct = float(getattr(config, "MIN_DISTANCE_PCT", 0.0008))
                if abs(dist_pct) < _min_dist_pct:
                    self._diag_log(
                        f"near-strike-{coin}",
                        f"[NEAR STRIKE] {coin}: dist={dist_pct*100:.3f}% "
                        f"< {_min_dist_pct*100:.2f}% - abstaining (price too close to strike)",
                        15.0,
                    )
                    return None
        except Exception as _e_md:
            logger.debug(f"[NEAR STRIKE] check failed: {_e_md}")

        # [AUDIT MAY27 F4] MIN_ACCURACY gate (re-enabled)
        # The comment said "afternoon has proven 80%+ WR" - that was historical.
        # Today's afternoon is 33% WR. Slow the bot down on bad days.
        try:
            if os.getenv("ACCURACY_GATE_ON", "on").lower() == "on":
                _acc = self._recent_accuracy()
                _min_acc = float(getattr(self, "MIN_ACCURACY", 0.45))
                if len(self._outcomes) >= max(4, self.ACCURACY_WINDOW // 2) and _acc < _min_acc:
                    self._diag_log(
                        f"acc-{coin}",
                        f"[ACCURACY GATE] {coin}: recent={_acc*100:.0f}% "
                        f"< {_min_acc*100:.0f}% over {len(self._outcomes)} trades - abstaining",
                        30.0,
                    )
                    return None
        except Exception as _e_ag:
            logger.debug(f"[ACCURACY GATE] check failed: {_e_ag}")

        # Trend score: combines short-term momentum with position relative to strike
        # Positive = price moving UP / above strike, Negative = DOWN / below strike
        trend_score = 0.0''',
    ),
]


def main() -> int:
    print("=" * 64)
    print("  Applying AUDIT_MAY27 v6 F1+F4 (re-application)")
    print("=" * 64)
    print()
    print("→ predictor.py")
    p_path = os.path.join(REPO, "predictor.py")
    n = patch_file(p_path, PREDICTOR_EDITS)
    print(f"  applied {n}/{len(PREDICTOR_EDITS)} edits")
    print()
    print("→ Verifying syntax")
    import py_compile
    try:
        py_compile.compile(p_path, doraise=True)
        print(f"  [OK] predictor.py")
    except py_compile.PyCompileError as e:
        print(f"  [FAIL] {e}")
        return 2
    print()
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
