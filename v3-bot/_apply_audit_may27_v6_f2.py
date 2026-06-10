"""
_apply_audit_may27_v6_f2.py — F2 + F3 only (the F1+F4 already applied
successfully in v6 main, but the F2 anchor failed due to mojibake'd
box-drawing chars in the source).

This patch anchors only on lines that don't contain the mojibake'd
characters, avoiding the encoding mismatch.
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
                f"({src.count(anchor)}) — anchor too generic"
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


# Anchor on the unique code lines without the mojibake'd box-drawing comments.
PREDICTOR_EDITS: List[Tuple[str, str, str]] = [
    (
        "F2+F3: env-knob steepness + 50/50 blend + dead-zone-on-bs",
        '''        base_up_prob = _bs_binary_prob(current_price, strike, sigma, time_remaining)
        raw_prob = _sigmoid(trend_score * 3.0)

        # Blend: 70% trend-based, 30% BS mathematical
        combined_prob = 0.70 * raw_prob + 0.30 * base_up_prob
        combined_prob = max(0.01, min(0.99, combined_prob))''',
        '''        base_up_prob = _bs_binary_prob(current_price, strike, sigma, time_remaining)
        # [AUDIT MAY27 F2] env-knob steepness (was hardcoded 3.0)
        _trend_steepness = float(os.getenv("TREND_SIGMOID_STEEPNESS", "1.5"))
        _trend_weight = float(os.getenv("TREND_BS_BLEND", "0.50"))
        _trend_weight = max(0.0, min(1.0, _trend_weight))
        raw_prob = _sigmoid(trend_score * _trend_steepness)

        # [AUDIT MAY27 F3] dead-zone abstention on bs_prob (the honest one).
        # By the time the trend overlay pushes 50.7% BS to 79% combined,
        # DEAD_ZONE is bypassed. Run it on the raw BS prob instead.
        try:
            if os.getenv("DEAD_ZONE_ON_BS", "on").lower() == "on":
                if abs(base_up_prob - 0.5) < self.DEAD_ZONE:
                    self._diag_log(
                        f"dead-bs-{coin}",
                        f"[DEAD ZONE BS] {coin}: BS prob={base_up_prob*100:.1f}% "
                        f"(within +-{self.DEAD_ZONE*100:.0f}pp of 50%) - coinflip; abstaining",
                        15.0,
                    )
                    return None
        except Exception as _e_dz:
            logger.debug(f"[DEAD ZONE BS] check failed: {_e_dz}")

        # Blend: env-driven (default 50/50 trend/BS)
        combined_prob = _trend_weight * raw_prob + (1.0 - _trend_weight) * base_up_prob
        combined_prob = max(0.01, min(0.99, combined_prob))''',
    ),
]


def main() -> int:
    print("=" * 64)
    print("  Applying AUDIT_MAY27 v6 F2+F3 (re-anchored)")
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
