"""
_apply_audit_may27_v4.py — wire CrossAssetState's latest snapshot into the
confidence calibrator at predict() time.

Why: the two 13:02–13:03 LIVE losses both fired at breadth=0 (no
cross-asset consensus). XASSET was logged but ignored. Now the calibrator
gets it as a 6th factor: shrink to 0.85 when |breadth|<0.4, shrink to 0.75
when breadth contradicts our direction, lift to 1.05 when breadth strongly
confirms our direction.

Patches:
  P1) Predictor.__init__ — track an optional xas_state reference
  P2) Predictor.set_xas_state — setter the bot calls at startup
  P3) Predictor.predict — pull latest xasset snapshot and pass into calibrator
  R1) run_bot.py — after creating predictor, predictor.set_xas_state(_xas_state)
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
        "P1+P2: xas_state attribute + setter (added after _poly_book_latest init)",
        '''        self._poly_book_latest: Dict[str, dict] = {}''',
        '''        self._poly_book_latest: Dict[str, dict] = {}
        # ── [AUDIT MAY27 v4] cross-asset state hook ──
        # run_bot.py calls set_xas_state() at startup; predict() reads the
        # latest snapshot at signal time to feed the calibrator.
        self._xas_state = None

    def set_xas_state(self, xas_state) -> None:
        """Wire the CrossAssetState singleton so calibrate() can read breadth."""
        self._xas_state = xas_state''',
    ),
    (
        "P3: pass xasset_features + direction into calibrate()",
        '''                _cal_res = _cal_compute(
                    raw_prob=win_prob,
                    regime=(self._regime_detector.get_regime(coin=coin)
                            if self._regime_detector is not None else "WARMUP"),
                    trend_abs=abs(trend_score),
                    bucket_stats=(self._regime_detector.get_bucket_wr(coin, ask, trend_score)
                                  if self._regime_detector is not None else None),
                    microstructure_features=_cal_features,
                    reversion_risk=float(_rr_res.get("risk", 0.0)) if "_rr_res" in dir() else 0.0,
                    T_sec=time_remaining,
                )''',
        '''                # [AUDIT MAY27 v4] pull latest cross-asset snapshot for calibrator
                _cal_xasset = None
                try:
                    if self._xas_state is not None:
                        _cal_xasset = self._xas_state.get_latest_snapshot() or None
                except Exception:
                    _cal_xasset = None
                _cal_res = _cal_compute(
                    raw_prob=win_prob,
                    regime=(self._regime_detector.get_regime(coin=coin)
                            if self._regime_detector is not None else "WARMUP"),
                    trend_abs=abs(trend_score),
                    bucket_stats=(self._regime_detector.get_bucket_wr(coin, ask, trend_score)
                                  if self._regime_detector is not None else None),
                    microstructure_features=_cal_features,
                    reversion_risk=float(_rr_res.get("risk", 0.0)) if "_rr_res" in dir() else 0.0,
                    T_sec=time_remaining,
                    xasset_features=_cal_xasset,
                    direction=direction,
                )''',
    ),
]


RUN_BOT_EDITS: List[Tuple[str, str, str]] = [
    (
        "R1: call predictor.set_xas_state(_xas_state) right after both are created",
        '''        _xas_state = _XASState()
    except Exception as _e_xas_init:
        logger.warning(f"[XASSET] init failed: {_e_xas_init}")
        _xas_state = None
        _xas_log = None''',
        '''        _xas_state = _XASState()
    except Exception as _e_xas_init:
        logger.warning(f"[XASSET] init failed: {_e_xas_init}")
        _xas_state = None
        _xas_log = None

    # [AUDIT MAY27 v4] wire xas_state into the predictor so the calibrator
    # can read cross-asset breadth at signal-time.
    try:
        if _xas_state is not None and hasattr(predictor, "set_xas_state"):
            predictor.set_xas_state(_xas_state)
            logger.info("[XASSET] wired into predictor calibrator")
    except Exception as _e_xas_wire:
        logger.warning(f"[XASSET] wire failed: {_e_xas_wire}")''',
    ),
]


def main() -> int:
    paths_to_compile: list = []
    print("=" * 64)
    print("  Applying AUDIT_MAY27 v4: xasset → calibrator wiring")
    print("=" * 64)
    print()
    print("→ predictor.py")
    p_path = os.path.join(REPO, "predictor.py")
    n = patch_file(p_path, PREDICTOR_EDITS)
    print(f"  applied {n}/{len(PREDICTOR_EDITS)} edits")
    paths_to_compile.append(p_path)

    print()
    print("→ run_bot.py")
    r_path = os.path.join(REPO, "run_bot.py")
    n = patch_file(r_path, RUN_BOT_EDITS)
    print(f"  applied {n}/{len(RUN_BOT_EDITS)} edits")
    paths_to_compile.append(r_path)

    print()
    print("→ Verifying syntax")
    import py_compile
    for path in paths_to_compile:
        try:
            py_compile.compile(path, doraise=True)
            print(f"  [OK] {os.path.basename(path)}")
        except py_compile.PyCompileError as e:
            print(f"  [FAIL] {os.path.basename(path)}: {e}")
            return 2
    print()
    print("Done. Restart the bot.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
