#!/usr/bin/env python3
"""Disable FLIP/INVERT; trending-with-spot only; book-direction gate."""
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path("/home/ubuntu/v3-bot")
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")


def backup(p: Path):
    shutil.copy2(p, p.with_suffix(p.suffix + f".bak_{STAMP}"))


def patch_regime_strategy():
    p = ROOT / "regime_aware" / "regime_strategy.py"
    backup(p)
    text = p.read_text(encoding="utf-8")
    old = """        self._prune_trap_memory(window_start)
        sticky_key = (coin, window_start)

        # ── Per-bucket adaptive override"""
    new = """        self._prune_trap_memory(window_start)
        sticky_key = (coin, window_start)
        import os as _os_inv_g
        _invert_glob = _os_inv_g.getenv("REGIME_INVERT_ENABLED", "off").lower() == "on"

        # ── Per-bucket adaptive override"""
    if old not in text:
        raise SystemExit("regime decide start not found")
    text = text.replace(old, new, 1)

    text = text.replace(
        '                _invert_on = _os_inv.getenv("REGIME_STRONG_TREND_INVERT", "on").lower() == "on"',
        '                _invert_on = (_invert_glob and _os_inv.getenv("REGIME_STRONG_TREND_INVERT", "off").lower() == "on")',
        1,
    )
    p.write_text(text, encoding="utf-8")
    print("patched regime_strategy.py")


def patch_predictor():
    p = ROOT / "predictor.py"
    backup(p)
    text = p.read_text(encoding="utf-8")

    # CHOPPY -> skip when trending-only
    old_chop = '        regime = "CHOPPY" if self._chop_detector.is_choppy() else "TRENDING"'
    new_chop = (
        '        regime = "CHOPPY" if self._chop_detector.is_choppy() else "TRENDING"\n'
        '        if (os.getenv("TRENDING_ONLY_MARKET", "on").lower() == "on"\n'
        '                and regime == "CHOPPY"):\n'
        '            self._diag_log(\n'
        '                f"choppy-{coin}",\n'
        '                f"[CHOPPY SKIP] {coin}: trending-only mode — no trade in chop",\n'
        '                15.0,\n'
        '            )\n'
        '            return None'
    )
    if old_chop not in text:
        raise SystemExit("choppy regime line not found")
    text = text.replace(old_chop, new_chop, 1)

    # Book vs model direction (after direction set, after strike conflict)
    anchor = """        # Cross-asset / per-coin direction consistency (may01: per-coin)"""
    book_gate = """
        # Jun-3: book must agree with direction (no UP when DOWN ask dominates)
        try:
            if os.getenv("BOOK_DIRECTION_ENFORCE", "on").lower() == "on":
                _bd_min = float(os.getenv("BOOK_DIRECTION_GAP", "0.06"))
                _bu, _bd = float(up_ask or 0), float(down_ask or 0)
                if direction == "UP" and _bd >= _bu + _bd_min and _bd > 0.05:
                    self._diag_log(
                        f"book-conflict-{coin}",
                        f"[BOOK CONFLICT] {coin} UP: book DOWN={_bd*100:.0f}c > UP={_bu*100:.0f}c — skip",
                        15.0,
                    )
                    return None
                if direction == "DOWN" and _bu >= _bd + _bd_min and _bu > 0.05:
                    self._diag_log(
                        f"book-conflict-{coin}",
                        f"[BOOK CONFLICT] {coin} DOWN: book UP={_bu*100:.0f}c > DOWN={_bd*100:.0f}c — skip",
                        15.0,
                    )
                    return None
        except Exception as _e_bc:
            logger.debug(f"[BOOK CONFLICT] check failed: {_e_bc}")

"""
    if anchor not in text:
        raise SystemExit("book gate anchor not found")
    text = text.replace(anchor, book_gate + anchor, 1)

    # Disable regime invert application in predictor
    old_inv = "                    if _ra_action.kind == \"TRADE_INVERTED\" and not _trap_off_keep:"
    new_inv = (
        "                    if (False and _ra_action.kind == \"TRADE_INVERTED\""
        " and not _trap_off_keep):  # FLIP disabled Jun-3"
    )
    if old_inv not in text:
        raise SystemExit("regime invert apply not found")
    text = text.replace(old_inv, new_inv, 1)

    # Reversion INVERT disabled
    old_rr = '                if _rr_live and _rr_res["action"] == "INVERT":'
    new_rr = (
        '                if (False and _rr_live and _rr_res["action"] == "INVERT"):'
        '  # FLIP disabled Jun-3'
    )
    if old_rr not in text:
        raise SystemExit("reversion invert not found")
    text = text.replace(old_rr, new_rr, 1)

    p.write_text(text, encoding="utf-8")
    print("patched predictor.py")


def patch_run_bot():
    p = ROOT / "run_bot.py"
    backup(p)
    text = p.read_text(encoding="utf-8")
    old_flip = """                        if _act == "FLIP":
                            # Jun-1 FIX: previous code inverted probability (76%->24%) but kept
                            # entry_price unchanged -> downstream gates killed every flipped signal.
                            # New behavior: keep original probability (flip = trust opposite outcome
                            # with same conviction), and update entry to contra ask (1 - original).
                            _orig = _p.direction
                            _orig_entry = _p.entry_price if _p.entry_price > 0.05 else _p.poly_price
                            _contra_entry = max(0.01, min(0.99, 1.0 - _orig_entry))
                            _p.direction = "DOWN" if _p.direction == "UP" else "UP"
                            _p.entry_price = _contra_entry
                            _p.poly_price = _contra_entry
                            _p.edge = _p.probability - _contra_entry
                            _was_overridden = True  # protect prob from DAMPEN downstream
                            logger.info(
                                f"[EXHAUST FLIP] {_p.coin} {_orig}@{_orig_entry*100:.0f}c -> "
                                f"{_p.direction}@{_contra_entry*100:.0f}c | "
                                f"prob={_p.probability*100:.0f}% edge={_p.edge*100:.0f}%"
                            )
                        elif _act == "DAMPEN":"""
    new_flip = """                        if _act == "FLIP":
                            logger.info(
                                f"[EXHAUST FLIP DISABLED] {_p.coin} {_p.direction} "
                                f"score={_res.get('score', 0):.2f} — skip (no flip)"
                            )
                            try:
                                trade_audit.record_exhaust(
                                    _p, "FLIP_DISABLED", float(_res.get("score", 0) or 0))
                                trade_audit.log_decision(_p, "SKIP")
                            except Exception:
                                pass
                            continue
                        elif _act == "DAMPEN":"""
    if old_flip not in text:
        raise SystemExit("FLIP block not found")
    text = text.replace(old_flip, new_flip, 1)
    p.write_text(text, encoding="utf-8")
    print("patched run_bot.py")


def patch_regime_strategy_full():
    """Replace TRADE_INVERTED with TRADE_HALF + signal direction when invert off."""
    p = ROOT / "regime_aware" / "regime_strategy.py"
    text = p.read_text(encoding="utf-8")
    if "_invert_glob" not in text:
        patch_regime_strategy()
        text = p.read_text(encoding="utf-8")

    # After each TRADE_INVERTED block that returns inv_dir, add else branch - simpler: patch decide trending only for REVERTING
    old_rev = '        elif regime == "REVERTING":'
    new_rev = (
        '        elif regime == "REVERTING":\n'
        '            # Jun-3: reverting trades WITH momentum direction only (no invert)\n'
        '            if not _invert_glob:\n'
        '                if trend_abs >= self.trend_alpha_low and signal.edge >= 0.08:\n'
        '                    return Action(\n'
        '                        "TRADE_HALF", direction, 0.5,\n'
        '                        f"reverting+with-trend{trend_abs:.1f}",\n'
        '                    )\n'
        '                return Action("SKIP", direction, 0, "reverting+invert-off")\n'
    )
    if 'reverting+with-trend' not in text:
        if old_rev not in text:
            raise SystemExit("REVERTING block not found")
        text = text.replace(old_rev, new_rev, 1)
        p.write_text(text, encoding="utf-8")
        print("patched regime REVERTING with-trend path")


def patch_env():
    p = ROOT / ".env"
    backup(p)
    updates = {
        "REGIME_INVERT_ENABLED": "off",
        "REGIME_STRONG_TREND_INVERT": "off",
        "REVERSION_INVERT": "off",
        "REVERSION_RISK_INVERT": "off",
        "REGIME_TRAP_INVERT": "off",
        "TRENDING_ONLY_MARKET": "on",
        "BOOK_DIRECTION_ENFORCE": "on",
        "BOOK_DIRECTION_GAP": "0.06",
        "STRIKE_DIRECTION_MIN_DIST": "0.00005",
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
    patch_regime_strategy()
    patch_regime_strategy_full()
    patch_predictor()
    patch_run_bot()
    patch_env()
    print("OK")


if __name__ == "__main__":
    main()
