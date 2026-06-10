#!/usr/bin/env python3
"""Trust engine+momentum+book: never flip UP when both scream DOWN (and vice versa)."""
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

    # ── 1. Per-coin engine conviction state ──
    old_init = (
        "        self._window_direction: Optional[str] = None\n"
        "        self._window_start_ts: int = 0\n"
        "        self._window_trends: Dict[str, str] = {}"
    )
    new_init = (
        "        self._window_direction: Optional[str] = None\n"
        "        self._window_directions: Dict[str, str] = {}  # per-coin dir lock\n"
        "        self._engine_conviction: Dict[str, str] = {}  # mom+book agreed direction\n"
        "        self._window_start_ts: int = 0\n"
        "        self._window_trends: Dict[str, str] = {}"
    )
    if "_engine_conviction" not in text:
        if old_init not in text:
            raise SystemExit("init block not found")
        text = text.replace(old_init, new_init, 1)

    # ── 2. Engine conviction override (after direction set, before strike conflict) ──
    anchor = "        # Spot vs strike: never buy DOWN above strike / UP below strike"
    engine_block = '''        # ── Engine conviction: mom + book agree → trust engine, no dist-bounce flip ──
        _conv_on = os.getenv("ENGINE_CONVICTION_ON", "on").lower() not in ("off", "0", "false")
        if _conv_on:
            _bd_gap = float(os.getenv("BOOK_DIRECTION_GAP", "0.04"))
            _roc60_min = float(os.getenv("MOM_LOCK_MIN_ROC60", "0.00003"))
            _roc300_min = float(os.getenv("MOM_LOCK_MIN_ROC300", "0.00003"))
            _mom_down = roc_60 < -_roc60_min and roc_300 < -_roc300_min
            _mom_up = roc_60 > _roc60_min and roc_300 > _roc300_min
            _ua, _da = float(up_ask or 0), float(down_ask or 0)
            _book_screams_down = (
                book_up <= (0.50 - _bd_gap)
                or (_ua > 0.05 and _da > 0.05 and _ua + _bd_gap <= _da)
            )
            _book_screams_up = (
                book_up >= (0.50 + _bd_gap)
                or (_ua > 0.05 and _da > 0.05 and _da + _bd_gap <= _ua)
            )
            _forced = False
            if _mom_down and _book_screams_down and direction == "UP":
                direction = "DOWN"
                is_up = False
                win_prob = max(0.01, min(0.99, 1.0 - combined_prob))
                ask = down_ask
                mid = down_mid
                depth = down_depth
                token_id = info.down_token_id
                self._engine_conviction[coin] = "DOWN"
                _forced = True
                self._diag_log(
                    f"engine-conv-{coin}",
                    f"[ENGINE CONVICTION] {coin}: mom+book DOWN "
                    f"(roc60={roc_60*10000:+.1f}bps roc300={roc_300*10000:+.1f}bps "
                    f"book={book_up:.2f} up_ask={_ua*100:.0f}c down_ask={_da*100:.0f}c) "
                    f"— blocked UP flip, betting DOWN",
                    12.0,
                )
            elif _mom_up and _book_screams_up and direction == "DOWN":
                direction = "UP"
                is_up = True
                win_prob = max(0.01, min(0.99, combined_prob))
                ask = up_ask
                mid = up_mid
                depth = up_depth
                token_id = info.up_token_id
                self._engine_conviction[coin] = "UP"
                _forced = True
                self._diag_log(
                    f"engine-conv-{coin}",
                    f"[ENGINE CONVICTION] {coin}: mom+book UP "
                    f"(roc60={roc_60*10000:+.1f}bps roc300={roc_300*10000:+.1f}bps "
                    f"book={book_up:.2f}) — blocked DOWN flip, betting UP",
                    12.0,
                )
            elif _mom_down and _book_screams_down:
                self._engine_conviction[coin] = "DOWN"
            elif _mom_up and _book_screams_up:
                self._engine_conviction[coin] = "UP"

            # Per-coin lock: once engine+book committed, never flip opposite
            _prior_conv = self._engine_conviction.get(coin)
            if not _forced and _prior_conv and direction != _prior_conv:
                self._diag_log(
                    f"engine-lock-{coin}",
                    f"[ENGINE LOCK] {coin} {direction}: engine+book committed {coin} "
                    f"to {_prior_conv} this window — no flip",
                    12.0,
                )
                return None

        '''
    if "ENGINE CONVICTION" not in text:
        if anchor not in text:
            raise SystemExit("strike anchor not found")
        text = text.replace(anchor, engine_block + anchor, 1)

    # ── 3. Book ask gate (block wrong-side when asks disagree) ──
    book_anchor = "        # Cross-asset direction consistency"
    book_gate = '''        # Book ask gate: UP token cheap = market expects DOWN (and vice versa)
        try:
            if os.getenv("BOOK_DIRECTION_ENFORCE", "on").lower() == "on":
                _bd_gap = float(os.getenv("BOOK_DIRECTION_GAP", "0.04"))
                _ua, _da = float(up_ask or 0), float(down_ask or 0)
                if direction == "UP" and _ua > 0.05 and _da > 0.05 and _ua + _bd_gap <= _da:
                    self._diag_log(
                        f"book-conflict-{coin}",
                        f"[BOOK CONFLICT] {coin} UP: UP ask={_ua*100:.0f}c cheaper than "
                        f"DOWN={_da*100:.0f}c — market says DOWN, skip UP",
                        12.0,
                    )
                    return None
                if direction == "DOWN" and _ua > 0.05 and _da > 0.05 and _da + _bd_gap <= _ua:
                    self._diag_log(
                        f"book-conflict-{coin}",
                        f"[BOOK CONFLICT] {coin} DOWN: DOWN ask={_da*100:.0f}c cheaper than "
                        f"UP={_ua*100:.0f}c — market says UP, skip DOWN",
                        12.0,
                    )
                    return None
        except Exception as _e_bc:
            logger.debug(f"[BOOK CONFLICT] check failed: {_e_bc}")

'''
    if "[BOOK CONFLICT]" not in text:
        if book_anchor not in text:
            raise SystemExit("cross-asset anchor not found")
        text = text.replace(book_anchor, book_gate + book_anchor, 1)

    # ── 4. Per-coin DIR LOCK (replace global lock) ──
    old_window_reset = (
        "        if window_start != self._window_start_ts:\n"
        "            self._window_direction = None\n"
        "            self._window_start_ts = window_start\n"
        "            self._window_trends.clear()"
    )
    new_window_reset = (
        "        if window_start != self._window_start_ts:\n"
        "            self._window_direction = None\n"
        "            self._window_directions.clear()\n"
        "            self._engine_conviction.clear()\n"
        "            self._window_start_ts = window_start\n"
        "            self._window_trends.clear()"
    )
    if "_engine_conviction.clear()" not in text:
        if old_window_reset not in text:
            raise SystemExit("window reset not found")
        text = text.replace(old_window_reset, new_window_reset, 1)

    old_dirlock = (
        "        # If we already committed to a direction, block contradictions\n"
        "        if self._window_direction is not None and direction != self._window_direction:\n"
        "            self._diag_log(\n"
        "                f\"dirlock-{coin}\",\n"
        "                f\"[DIR LOCK] {coin} {direction}: committed to {self._window_direction} — skipping\",\n"
        "                15.0,\n"
        "            )\n"
        "            return None"
    )
    new_dirlock = (
        "        # Per-coin DIR LOCK: block flip only for THIS coin\n"
        "        prior_dir = self._window_directions.get(coin)\n"
        "        if prior_dir is not None and direction != prior_dir:\n"
        "            self._diag_log(\n"
        "                f\"dirlock-{coin}\",\n"
        "                f\"[DIR LOCK] {coin} {direction}: this coin committed to {prior_dir} — skipping\",\n"
        "                15.0,\n"
        "            )\n"
        "            return None"
    )
    if "prior_dir = self._window_directions.get(coin)" not in text:
        if old_dirlock not in text:
            raise SystemExit("dir lock block not found")
        text = text.replace(old_dirlock, new_dirlock, 1)

    old_commit = (
        "        self._window_direction = direction\n"
        "        # ChopDetector records actual market outcome in run_bot.py, NOT bot's trade direction"
    )
    new_commit = (
        "        self._window_direction = direction  # legacy global\n"
        "        self._window_directions[coin] = direction\n"
        "        # ChopDetector records actual market outcome in run_bot.py, NOT bot's trade direction"
    )
    if "self._window_directions[coin] = direction" not in text:
        if old_commit not in text:
            raise SystemExit("commit block not found")
        text = text.replace(old_commit, new_commit, 1)

    p.write_text(text, encoding="utf-8")
    print("patched predictor.py")


def patch_env():
    p = ROOT / ".env"
    backup(p)
    updates = {
        "ENGINE_CONVICTION_ON": "on",
        "BOOK_DIRECTION_ENFORCE": "on",
        "BOOK_DIRECTION_GAP": "0.04",
        "MOM_LOCK_MIN_ROC60": "0.00003",
        "MOM_LOCK_MIN_ROC300": "0.00003",
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
    r = subprocess.run(
        [sys.executable, "-m", "py_compile", str(ROOT / "predictor.py")],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        print(r.stderr)
        raise SystemExit("syntax check failed")
    print("syntax OK")


if __name__ == "__main__":
    main()
