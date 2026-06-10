"""
_apply_audit_may27_v5.py — fix the math bug in regime-inverted predictions.

The bug:
  When regime engine says TRADE_INVERTED, predictor.py does:
      win_prob = 1.0 - win_prob       # 80%  →  20%
      edge    = win_prob - ask        # 0.20 - 0.33 = -0.13  ← NEGATIVE
  Then run_bot's `p.edge >= MIN_EDGE` filter throws it out.

  The "1 - prob" flip is wrong for empirical inversions. The trap-band INVERT
  exists because 60-70c entries have ~47% empirical WR — the opposite side
  wins ~53% of the time. We should use that empirical 0.53, not (1 - 0.80).

The fix:
  • Default empirical inversion prob = 0.55 (conservative)
  • If detector.get_bucket_wr returns ≥5 samples, use clamp(1 - bucket_wr, 0.50, 0.65)
  • Logged so we can audit each inversion's prob source

Affected log: REGIME-INVERT trades will now actually fire (and pay) instead of
silently dying at the MIN_EDGE filter.
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


PREDICTOR_EDITS: List[Tuple[str, str, str]] = [
    (
        "V5: empirical inversion prob (regime engine)",
        '''                    if _ra_action.kind == "TRADE_INVERTED":
                        _ra_new_dir = _ra_action.direction
                        _ra_new_token = info.down_token_id if direction == "UP" else info.up_token_id
                        _ra_new_ask = down_ask if direction == "UP" else up_ask
                        if _ra_new_token and 0.05 < _ra_new_ask < 0.95:
                            direction = _ra_new_dir
                            token_id = _ra_new_token
                            ask = _ra_new_ask
                            win_prob = max(0.05, min(0.95, 1.0 - win_prob))
                            edge = win_prob - ask
                            confidence = "REGIME-INVERT"
                    _ra_size_factor = float(_ra_action.size_factor)''',
        '''                    if _ra_action.kind == "TRADE_INVERTED":
                        _ra_new_dir = _ra_action.direction
                        _ra_new_token = info.down_token_id if direction == "UP" else info.up_token_id
                        _ra_new_ask = down_ask if direction == "UP" else up_ask
                        if _ra_new_token and 0.05 < _ra_new_ask < 0.95:
                            direction = _ra_new_dir
                            token_id = _ra_new_token
                            ask = _ra_new_ask
                            # ── [AUDIT MAY27 v5] empirical inversion prob ──
                            # The simple `1 - win_prob` flip produced edge = +0.20 - 0.33 = -0.13
                            # for an 80% UP signal at 66c — bot couldn't fire any inverted
                            # trade. Real trap-band data has ~47% WR, so opposite ~53% WR.
                            # Use bucket WR when we have empirical data, else a conservative
                            # default of 0.55. Clamp to [0.50, 0.65] so we never get cocky.
                            _ra_inv_prob = 0.55
                            _ra_inv_src = "default-0.55"
                            try:
                                if _ra_bucket and _ra_bucket.get("n", 0) >= 5:
                                    _ra_bw = float(_ra_bucket.get("wr", 0.5))
                                    _ra_inv_prob = max(0.50, min(0.65, 1.0 - _ra_bw))
                                    _ra_inv_src = f"bucket-wr({_ra_bw*100:.0f}%/n={_ra_bucket['n']})"
                            except Exception:
                                pass
                            win_prob = _ra_inv_prob
                            edge = win_prob - ask
                            confidence = "REGIME-INVERT"
                            logger.info(
                                f"[REGIME INVERT-PROB] {coin} {_ra_new_dir}@{ask*100:.0f}c "
                                f"prob={win_prob:.2f} edge={edge*100:+.1f}% src={_ra_inv_src}"
                            )
                    _ra_size_factor = float(_ra_action.size_factor)''',
    ),
]


def main() -> int:
    print("=" * 64)
    print("  Applying AUDIT_MAY27 v5: empirical inversion prob")
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
    print("Done. Restart the bot.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
