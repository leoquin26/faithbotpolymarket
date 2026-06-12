#!/usr/bin/env python3
"""Balance gates: keep quality, allow real trades (DOWN expensive OK, softer locks)."""
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path("/home/ubuntu/v3-bot")
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")


def backup(p: Path):
    shutil.copy2(p, p.with_suffix(p.suffix + f".bak_{STAMP}"))


def patch_predictor():
    p = ROOT / "predictor.py"
    backup(p)
    text = p.read_text(encoding="utf-8")

    # Direction-aware entry max: DOWN can be 72c when market dumps; UP capped lower
    old_entry = '''        # Entry price filters (early window allows cheaper asks)
        entry_max = getattr(config, "ENTRY_MAX", 0.75)
        if early_window:
            entry_min = float(os.getenv("EARLY_ENTRY_MIN", "0.35"))
        else:
            entry_min = getattr(config, "ENTRY_MIN", 0.10)'''
    new_entry = '''        # Entry price filters — direction-aware (DOWN 90c is normal in dumps)
        if direction == "DOWN":
            entry_max = float(os.getenv("ENTRY_MAX_DOWN", os.getenv("ENTRY_MAX", "0.72")))
        else:
            entry_max = float(os.getenv("ENTRY_MAX_UP", "0.62"))
        if early_window:
            entry_min = float(os.getenv("EARLY_ENTRY_MIN", "0.35"))
        else:
            entry_min = getattr(config, "ENTRY_MIN", 0.10)'''
    if "ENTRY_MAX_DOWN" not in text:
        text = text.replace(old_entry, new_entry, 1)

    # Only DIR-commit when trend is real (stops micro-bounce locking wrong side)
    old_dir_check = '''        # Per-coin DIR LOCK: block flip only for THIS coin
        prior_dir = self._window_directions.get(coin)
        if prior_dir is not None and direction != prior_dir:
            self._diag_log(
                f"dirlock-{coin}",
                f"[DIR LOCK] {coin} {direction}: this coin committed to {prior_dir} — skipping",
                15.0,
            )
            return None'''
    new_dir_check = '''        # Per-coin DIR LOCK: only if prior commit was strong trend
        _commit_min = float(os.getenv("DIR_COMMIT_MIN_TREND", "0.55"))
        prior_dir = self._window_directions.get(coin)
        if prior_dir is not None and direction != prior_dir:
            _prior_strong = self._window_directions.get(f"{coin}_strength", 0) >= _commit_min
            if _prior_strong and abs(trend_score) < float(os.getenv("DIR_FLIP_MIN_TREND", "1.0")):
                self._diag_log(
                    f"dirlock-{coin}",
                    f"[DIR LOCK] {coin} {direction}: committed to {prior_dir} "
                    f"(|trend|={abs(trend_score):.2f} < flip min) — skipping",
                    15.0,
                )
                return None'''
    if "DIR_COMMIT_MIN_TREND" not in text:
        text = text.replace(old_dir_check, new_dir_check, 1)

    old_commit_dirs = '''        self._window_direction = direction  # legacy global
        self._window_directions[coin] = direction
        self._chop_detector.record_direction(direction)'''
    new_commit_dirs = '''        self._window_direction = direction  # legacy global
        if abs(trend_score) >= float(os.getenv("DIR_COMMIT_MIN_TREND", "0.55")):
            self._window_directions[coin] = direction
            self._window_directions[f"{coin}_strength"] = abs(trend_score)
        self._chop_detector.record_direction(direction)'''
    if "_strength" not in text:
        text = text.replace(old_commit_dirs, new_commit_dirs, 1)

    # ENGINE LOCK: only block flip, don't freeze whole window on early noise
    old_eng_lock = '''            # Per-coin lock: once engine+book committed, never flip opposite
            _prior_conv = self._engine_conviction.get(coin)
            if not _forced and _prior_conv and direction != _prior_conv:
                self._diag_log(
                    f"engine-lock-{coin}",
                    f"[ENGINE LOCK] {coin} {direction}: engine+book committed {coin} "
                    f"to {_prior_conv} this window — no flip",
                    12.0,
                )
                return None'''
    new_eng_lock = '''            # Engine lock: only block weak flips (strong trend can override)
            _prior_conv = self._engine_conviction.get(coin)
            _eng_lock_on = os.getenv("ENGINE_LOCK_ON", "off").lower() == "on"
            if (_eng_lock_on and not _forced and _prior_conv and direction != _prior_conv
                    and abs(trend_score) < float(os.getenv("ENGINE_FLIP_MIN_TREND", "1.2"))):
                self._diag_log(
                    f"engine-lock-{coin}",
                    f"[ENGINE LOCK] {coin} {direction}: committed {_prior_conv} "
                    f"trend={trend_score:+.2f} too weak to flip — skip",
                    12.0,
                )
                return None'''
    if "ENGINE_LOCK_ON" not in text:
        text = text.replace(old_eng_lock, new_eng_lock, 1)

    # MOM CONFLICT: only hard-block UP on negative momentum; DOWN allows roc60 bounce
    old_mom = '''        if _mom_align:
            _mm = float(os.getenv("MOM_ALIGN_MIN_ROC", "0.00003"))
            if direction == "UP" and roc_60 < -_mm and roc_300 < -_mm:
                self._diag_log(
                    f"mom-conflict-{coin}",
                    f"[MOM CONFLICT] {coin} UP: roc60={roc_60*10000:+.1f}bps "
                    f"roc300={roc_300*10000:+.1f}bps both negative — skip",
                    12.0,
                )
                return None
            if direction == "DOWN" and roc_60 > _mm and roc_300 > _mm:
                self._diag_log(
                    f"mom-conflict-{coin}",
                    f"[MOM CONFLICT] {coin} DOWN: roc60={roc_60*10000:+.1f}bps "
                    f"roc300={roc_300*10000:+.1f}bps both positive — skip",
                    12.0,
                )
                return None'''
    new_mom = '''        if _mom_align:
            _mm = float(os.getenv("MOM_ALIGN_MIN_ROC", "0.00003"))
            # UP: both ROC must be negative to block (keep — prevents dead-cat UP)
            if direction == "UP" and roc_60 < -_mm and roc_300 < -_mm:
                self._diag_log(
                    f"mom-conflict-{coin}",
                    f"[MOM CONFLICT] {coin} UP: roc60={roc_60*10000:+.1f}bps "
                    f"roc300={roc_300*10000:+.1f}bps both negative — skip",
                    12.0,
                )
                return None
            # DOWN: only block if roc_300 positive AND trend weak (allow dump bounces)
            if (direction == "DOWN" and roc_300 > _mm
                    and abs(trend_score) < float(os.getenv("MOM_DOWN_MIN_TREND", "0.80"))):
                self._diag_log(
                    f"mom-conflict-{coin}",
                    f"[MOM CONFLICT] {coin} DOWN: roc300={roc_300*10000:+.1f}bps "
                    f"positive + weak trend={trend_score:+.2f} — skip",
                    12.0,
                )
                return None'''
    if "MOM_DOWN_MIN_TREND" not in text:
        text = text.replace(old_mom, new_mom, 1)

    # FLIP GUARD: softer threshold
    text = text.replace(
        'FLIP_TREND_MIN = float(getattr(config, "FLIP_TREND_MIN_15M", 1.5))',
        'FLIP_TREND_MIN = float(os.getenv("FLIP_TREND_MIN_15M", "1.0"))',
    )

    # Clear strength keys on new window
    old_reset = "            self._window_directions.clear()"
    new_reset = "            self._window_directions.clear()  # includes _strength keys"
    # no change needed - clear() handles all keys

    p.write_text(text, encoding="utf-8")
    print("patched predictor.py")


def patch_env():
    p = ROOT / ".env"
    backup(p)
    updates = {
        "ENTRY_MAX": "0.70",
        "ENTRY_MAX_UP": "0.62",
        "ENTRY_MAX_DOWN": "0.72",
        "EXPENSIVE_UP_MAX_ASK": "0.62",
        "MIN_WIN_PROB": "0.65",
        "HARD_WARMUP_15M": "60",
        "WARMUP_SEC": "60",
        "ACCURACY_CONFIRM_SCANS": "1",
        "FLIP_TREND_MIN_15M": "1.0",
        "DIR_COMMIT_MIN_TREND": "0.55",
        "DIR_FLIP_MIN_TREND": "1.0",
        "ENGINE_LOCK_ON": "off",
        "MOM_DOWN_MIN_TREND": "0.80",
        "HIGH_ASK_EDGE_MIN_ASK": "0.62",
        "HIGH_ASK_EDGE_MIN_EDGE": "0.15",
    }
    lines = p.read_text(encoding="utf-8").splitlines()
    seen = set()
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.strip().startswith("#") else None
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    for k, v in updates.items():
        if k not in seen:
            out.append(f"{k}={v}")
    p.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("patched .env")


def main():
    patch_predictor()
    patch_env()
    for rel in ("predictor.py",):
        r = subprocess.run(
            [sys.executable, "-m", "py_compile", str(ROOT / rel)],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            raise SystemExit(r.stderr)
    print("OK")


if __name__ == "__main__":
    main()
