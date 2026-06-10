"""
_apply_audit_may27_v2.py — second wave of AUDIT_MAY27 patches.

Adds:
  C1) confidence_calibrator wired into predictor.py — shadow only
  L1) ledger sqlite mirror hooked into analytics.event_logger.log()

Idempotent and anchor-verified, like the v1 patch script.

Run on EC2:
    cd /home/ubuntu/v3-bot && python3 _apply_audit_may27_v2.py
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


# ── predictor.py: wire confidence_calibrator after reversion_risk block ──────
PREDICTOR_EDITS: List[Tuple[str, str, str]] = [
    (
        "C1: confidence calibrator (shadow) after reversion-risk block",
        # Anchor on the closing of the reversion_risk try-block, just before
        # the Prediction is built.
        '''        except Exception as _e_rr:
            logger.warning(f"[REVERSION] compute failed: {_e_rr}")

        _ra_pred = Prediction(''',
        '''        except Exception as _e_rr:
            logger.warning(f"[REVERSION] compute failed: {_e_rr}")

        # ── [AUDIT MAY27 C1] confidence calibrator (SHADOW) ──
        # Calibrates raw `win_prob` using regime + bucket WR + microstructure
        # + reversion-risk + late-window factor. Logs only; no behavior change
        # unless CALIBRATION_LIVE=on (after offline grading).
        try:
            import os as _os_cal
            if _os_cal.getenv("CALIBRATION_SHADOW", "on").lower() == "on":
                from regime_aware.confidence_calibrator import (
                    calibrate as _cal_compute,
                    format_log_line as _cal_log,
                )
                _cal_live = _os_cal.getenv("CALIBRATION_LIVE", "off").lower() == "on"
                _cal_features = None
                try:
                    _cal_features = _ms_feats  # set in reversion-risk block above
                except NameError:
                    _cal_features = None
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
                )
                logger.info(_cal_log(coin, direction, _cal_res, mode=("LIVE" if _cal_live else "SHADOW")))
                if _cal_live:
                    win_prob = float(_cal_res["calibrated_prob"])
                    edge = win_prob - ask
        except Exception as _e_cal:
            logger.warning(f"[CALIBRATION] compute failed: {_e_cal}")

        _ra_pred = Prediction(''',
    ),
]


# ── analytics/event_logger.py: hook sqlite ledger fan-out ────────────────────
EVENT_LOGGER_EDITS: List[Tuple[str, str, str]] = [
    (
        "L1: mirror every JSONL event into the sqlite ledger if enabled",
        # Anchor on the function-end of log() — find the existing _safe_dumps
        # and write block.
        '''def log(event: str, **fields: Any) -> None:
    """
    Append one event to the events JSONL. Swallows all errors.

    Always adds `ts` (ISO8601 UTC) and `ts_epoch` (int seconds).
    """
    if not _ENABLED:
        return

    row: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ts_epoch": int(time.time()),
        "event": event,
    }''',
        '''def log(event: str, **fields: Any) -> None:
    """
    Append one event to the events JSONL. Swallows all errors.

    Always adds `ts` (ISO8601 UTC) and `ts_epoch` (int seconds).
    """
    if not _ENABLED:
        # Even if JSONL is disabled, the sqlite ledger may be enabled.
        try:
            from . import ledger as _ledger  # type: ignore
            if _ledger.is_enabled():
                _ledger.log_event_dict({
                    "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "ts_epoch": int(time.time()),
                    "event": event,
                    **fields,
                })
        except Exception:
            pass
        return

    row: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ts_epoch": int(time.time()),
        "event": event,
    }''',
    ),
    (
        "L1.b: also fan out from log() after JSONL write succeeds",
        # Anchor on the actual file write (we'll add ledger after it).
        # Find the line "f.write(line + \"\\n\")" inside log().
        # Add fan-out right after the write loop completes successfully.
        '''            with open(EVENTS_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\\n")
    except Exception as e:
        _warn_once(f"[ANALYTICS] write failed: {e}")''',
        '''            with open(EVENTS_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\\n")
        # [AUDIT MAY27 L1] mirror to sqlite ledger if enabled
        try:
            from . import ledger as _ledger  # type: ignore
            if _ledger.is_enabled():
                _ledger.log_event_dict(row)
        except Exception:
            pass
    except Exception as e:
        _warn_once(f"[ANALYTICS] write failed: {e}")''',
    ),
]


def main() -> int:
    paths_to_compile: list = []
    print("=" * 64)
    print("  Applying AUDIT_MAY27 v2 patches to /home/ubuntu/v3-bot")
    print("=" * 64)

    print()
    print("→ predictor.py")
    p_path = os.path.join(REPO, "predictor.py")
    n = patch_file(p_path, PREDICTOR_EDITS)
    print(f"  applied {n}/{len(PREDICTOR_EDITS)} edits")
    paths_to_compile.append(p_path)

    print()
    print("→ analytics/event_logger.py")
    e_path = os.path.join(REPO, "analytics/event_logger.py")
    n = patch_file(e_path, EVENT_LOGGER_EDITS)
    print(f"  applied {n}/{len(EVENT_LOGGER_EDITS)} edits")
    paths_to_compile.append(e_path)

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
