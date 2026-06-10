"""
_apply_audit_may27_v3.py — third wave of AUDIT_MAY27 patches.

Adds:
  X1) Cross-asset features module wired into run_bot's main loop
      (logs [XASSET] once per scan after all coin books are fetched).

Idempotent and anchor-verified.
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


RUN_BOT_EDITS: List[Tuple[str, str, str]] = [
    (
        "X1.init: import CrossAssetState + instantiate before main loop",
        # Anchor right after the predictor & morning_pred init lines.
        '''    predictor = Predictor()
    morning_pred = MorningPredictor(predictor)
    bootstrap_traded_set()''',
        '''    predictor = Predictor()
    morning_pred = MorningPredictor(predictor)
    bootstrap_traded_set()
    # ── [AUDIT MAY27 X1] cross-asset feature aggregator (shadow log only) ──
    try:
        from core.cross_asset_features import (
            CrossAssetState as _XASState,
            format_log_line as _xas_log,
        )
        _xas_state = _XASState()
    except Exception as _e_xas_init:
        logger.warning(f"[XASSET] init failed: {_e_xas_init}")
        _xas_state = None
        _xas_log = None''',
    ),
    (
        "X1.tick: log [XASSET] once per scan after all books are fetched",
        # Anchor at the end of the futures-as_completed loop, right before
        # the EXHAUST shadow block. We use the "── Exhaustion detector" comment.
        '''            # ÔöÇÔöÇ Exhaustion detector (SHADOW MODE) ÔöÇÔöÇ
            # Evaluates every prediction for exhaustion signals. Logs only;
            # does NOT alter trade decisions while SHADOW_MODE=True.''',
        '''            # ── [AUDIT MAY27 X1] cross-asset features (shadow log only) ──
            # Runs once per scan cycle using _raw_coin_info, which now holds
            # one (up_ask, down_ask) entry per coin. Logged only — does not
            # affect any trade decision in this version.
            if _xas_state is not None and _raw_coin_info:
                try:
                    _xas_snap = _xas_state.update(_raw_coin_info)
                    logger.info(_xas_log(_xas_snap))
                except Exception as _e_xas:
                    logger.debug(f"[XASSET] tick failed: {_e_xas}")

            # ── Exhaustion detector (SHADOW MODE) ──
            # Evaluates every prediction for exhaustion signals. Logs only;
            # does NOT alter trade decisions while SHADOW_MODE=True.''',
    ),
]


def main() -> int:
    paths_to_compile: list = []
    print("=" * 64)
    print("  Applying AUDIT_MAY27 v3 patches to /home/ubuntu/v3-bot")
    print("=" * 64)
    print()
    print("→ run_bot.py")
    r_path = os.path.join(REPO, "run_bot.py")
    n = patch_file(r_path, RUN_BOT_EDITS)
    print(f"  applied {n}/{len(RUN_BOT_EDITS)} edits")
    paths_to_compile.append(r_path)
    print()
    print("→ Verifying syntax (py_compile)")
    import py_compile
    for path in paths_to_compile:
        try:
            py_compile.compile(path, doraise=True)
            print(f"  [OK] {os.path.basename(path)}")
        except py_compile.PyCompileError as e:
            print(f"  [FAIL] {os.path.basename(path)}: {e}")
            return 2
    print()
    print("Done. Restart the bot to load the new code.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
