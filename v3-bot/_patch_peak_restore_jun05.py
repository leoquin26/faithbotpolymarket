#!/usr/bin/env python3
"""
Peak engine restore + fix bad number reads:
  1. Strike: never use live Chainlink mid-window (use kline open at window start)
  2. Spot: Chainlink for dist when available (matches Polymarket resolution)
  3. Restore FLIP GUARD, CHOPPY_STRICT, peak thresholds
  4. Re-enable ACCURACY + CONSENSUS gates
  5. Block expensive UP without real dist
"""
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path("/home/ubuntu/v3-bot")
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")


def backup(p: Path):
    shutil.copy2(p, p.with_suffix(p.suffix + f".bak_{STAMP}"))


def patch_poly_resolution():
    p = ROOT / "poly_resolution.py"
    backup(p)
    text = p.read_text(encoding="utf-8")
    old = '''    strike = 0.0
    source = "unknown"

    try:
        import chainlink_ws
        cl = chainlink_ws.get_price(coin)
        if cl and cl > 0:
            strike = cl
            source = "chainlink_rtds"
    except Exception:
        pass

    if strike <= 0:
        from market_data import get_threshold_from_binance
        b = get_threshold_from_binance(coin, event_start_unix_ts, timeframe)
        if b and b > 0:
            strike = b
            source = "binance_kline_fallback"'''
    new = '''    strike = 0.0
    source = "unknown"
    window_age = max(0.0, time.time() - float(event_start_unix_ts or 0))

    # PRIMARY: 1m candle OPEN at window start (stable, not live price)
    from market_data import get_threshold_from_binance
    b = get_threshold_from_binance(coin, event_start_unix_ts, timeframe)
    if b and b > 0:
        strike = b
        source = "binance_kline_open"

    # Chainlink snapshot ONLY within first 20s of window (window-open oracle)
    _cl_max_age = float(__import__("os").getenv("STRIKE_CHAINLINK_MAX_AGE", "20"))
    if window_age <= _cl_max_age:
        try:
            import chainlink_ws
            cl = chainlink_ws.get_price(coin)
            if cl and cl > 0:
                strike = cl
                source = "chainlink_window_open"
        except Exception:
            pass
    elif strike <= 0:
        logger.warning(
            f"[STRIKE] {coin} {slug}: mid-window cache miss (age={window_age:.0f}s) "
            f"— refusing live Chainlink as strike"
        )'''
    if "binance_kline_open" not in text:
        if old not in text:
            raise SystemExit("poly_resolution get_strike block not found")
        text = text.replace(old, new, 1)
        p.write_text(text, encoding="utf-8")
    print("patched poly_resolution.py")


def patch_market_data():
    p = ROOT / "market_data.py"
    backup(p)
    text = p.read_text(encoding="utf-8")
    old = "        time_remaining = (end_time - current_time) // 60"
    new = "        time_remaining = max(0, end_time - current_time)  # seconds (was wrongly // 60)"
    if old in text:
        text = text.replace(old, new, 1)
        p.write_text(text, encoding="utf-8")
        print("patched market_data.py time_remaining")
    else:
        print("market_data time_remaining already fixed or missing")


def patch_run_bot():
    p = ROOT / "run_bot.py"
    backup(p)
    text = p.read_text(encoding="utf-8")
    old = '''def _multi_price(coin: str):
    p = binance_ws.get_price(coin)
    if p and p > 0:
        return p
    if _BYBIT_OK and bybit_ws is not None:
        try:
            p2 = bybit_ws.get_price(coin)
            if p2 and p2 > 0:
                return p2
        except Exception:
            pass
    return None'''
    new = '''def _multi_price(coin: str):
    """Chainlink first for dist vs strike (Polymarket resolves on Chainlink)."""
    if _CHAINLINK_OK and _chainlink_ws is not None:
        try:
            cl = _chainlink_ws.get_price(coin)
            if cl and cl > 0:
                return cl
        except Exception:
            pass
    p = binance_ws.get_price(coin)
    if p and p > 0:
        return p
    if _BYBIT_OK and bybit_ws is not None:
        try:
            p2 = bybit_ws.get_price(coin)
            if p2 and p2 > 0:
                return p2
        except Exception:
            pass
    return None'''
    if "Chainlink first for dist" not in text:
        if old not in text:
            raise SystemExit("run_bot _multi_price not found")
        text = text.replace(old, new, 1)
        p.write_text(text, encoding="utf-8")
    print("patched run_bot.py chainlink-first spot")


def patch_predictor():
    p = ROOT / "predictor.py"
    backup(p)
    text = p.read_text(encoding="utf-8")

    # ── Chainlink spot + log sources ──
    old_spot = '''        coin = info.coin
        current_price = ws_price if ws_price > 0 else info.current_crypto_price
        strike = info.threshold_price'''
    new_spot = '''        coin = info.coin
        current_price = ws_price if ws_price > 0 else info.current_crypto_price
        _spot_src = getattr(info, "strike_source", "binance") and "binance"
        try:
            import chainlink_ws as _cl_spot
            _cl_px = _cl_spot.get_price(coin)
            if _cl_px and _cl_px > 0:
                current_price = _cl_px
                _spot_src = "chainlink"
        except Exception:
            _spot_src = "binance"
        strike = info.threshold_price
        _strike_src = getattr(info, "strike_source", "") or "unknown"'''
    if "_spot_src" not in text:
        text = text.replace(old_spot, new_spot, 1)

    # ── Warmup 90s ──
    text = text.replace(
        'warmup = int(os.getenv("HARD_WARMUP_15M", os.getenv("WARMUP_SEC", "20")))',
        'warmup = int(os.getenv("HARD_WARMUP_15M", os.getenv("WARMUP_SEC", "90")))',
    )

    # ── Dist weights: 500 -> 250 early (peak used 200) ──
    text = text.replace(
        "w_dist, w_r60, w_r120, w_r300, w_mom = 500.0, 150.0, 300.0, 400.0, 200.0",
        "w_dist, w_r60, w_r120, w_r300, w_mom = 250.0, 400.0, 300.0, 350.0, 300.0",
    )
    text = text.replace(
        "w_dist, w_r60, w_r120, w_r300, w_mom = 400.0, 400.0, 350.0, 250.0, 300.0",
        "w_dist, w_r60, w_r120, w_r300, w_mom = 200.0, 400.0, 350.0, 300.0, 300.0",
    )

    # ── MIN_TREND peak 0.40 ──
    text = text.replace(
        '_min_trend = float(os.getenv("MIN_TREND_SCORE", "0.22"))',
        '_min_trend = float(os.getenv("MIN_TREND_SCORE", os.getenv("MIN_TREND_ABS", "0.40")))',
    )

    # ── CHOPPY_STRICT gate (peak) ──
    chop_anchor = '''        else:
            _min_trend = float(os.getenv("MIN_TREND_SCORE", os.getenv("MIN_TREND_ABS", "0.40")))
            if abs(trend_score) < _min_trend:'''
    chop_strict = '''        # Peak: stricter |trend| when chop detector says choppy
        if is_chop:
            _min_tr = float(getattr(config, "CHOPPY_MIN_TREND_ABS", 0.48))
            if abs(trend_score) < _min_tr:
                self._diag_log(
                    f"chopstrict-{coin}",
                    f"[CHOPPY STRICT] {coin}: |trend|={abs(trend_score):.3f} < {_min_tr} — skip",
                    15.0,
                )
                return None

        else:
            _min_trend = float(os.getenv("MIN_TREND_SCORE", os.getenv("MIN_TREND_ABS", "0.40")))
            if abs(trend_score) < _min_trend:'''
    if "[CHOPPY STRICT]" not in text and chop_anchor in text:
        text = text.replace(chop_anchor, chop_strict, 1)

    # ── Combined prob: peak 70/30, book NOT in direction blend ──
    old_blend = '''        combined_prob = 0.55 * raw_prob + 0.25 * base_up_prob + 0.20 * book_up
        combined_prob = max(0.01, min(0.99, combined_prob))'''
    new_blend = '''        # Peak blend: trend + BS only (book used in DIR VOTE, not prob poison)
        combined_prob = 0.70 * raw_prob + 0.30 * base_up_prob
        combined_prob = max(0.01, min(0.99, combined_prob))'''
    if "book used in DIR VOTE" not in text:
        text = text.replace(old_blend, new_blend, 1)

    # ── book_up from asks when mids missing (fix bad read) ──
    old_book = '''        book_up = up_mid if up_mid > 0.01 else 0.0
        if book_up <= 0.01 and down_mid > 0.01:
            book_up = max(0.01, min(0.99, 1.0 - down_mid))
        if book_up <= 0.01 and up_ask > 0.01:
            book_up = max(0.01, min(0.99, up_ask))
        if book_up <= 0.01:
            book_up = 0.5'''
    new_book = '''        # Book implied UP prob: prefer ask-based (executable), not stale mid
        _ua_b, _da_b = float(up_ask or 0), float(down_ask or 0)
        if _ua_b > 0.02 and _da_b > 0.02:
            book_up = _ua_b / (_ua_b + _da_b)
        elif up_mid > 0.01 and down_mid > 0.01:
            book_up = up_mid / (up_mid + down_mid)
        elif up_mid > 0.01:
            book_up = up_mid
        elif down_mid > 0.01:
            book_up = 1.0 - down_mid
        else:
            book_up = 0.5
        book_up = max(0.01, min(0.99, book_up))'''
    if "ask-based (executable)" not in text:
        text = text.replace(old_book, new_book, 1)

    # ── Fix indentation: strike conflict outside engine lock ──
    broken = '''                return None

                # Spot vs strike: never buy DOWN above strike / UP below strike
        try:'''
    fixed = '''                return None

        # Spot vs strike: never buy DOWN above strike / UP below strike
        try:'''
    if broken in text:
        text = text.replace(broken, fixed, 1)

    # ── FLIP GUARD + expensive UP + momentum align (before entry filters) ──
    flip_anchor = '''        # Entry price filters (early window allows cheaper asks)
        entry_max = getattr(config, "ENTRY_MAX", 0.75)'''
    flip_block = '''        # ── FLIP GUARD (peak): block weak direction flips ──
        try:
            recent_hist = list(self._chop_detector._history[-4:])
        except Exception:
            recent_hist = []
        if len(recent_hist) >= 3:
            opposite = sum(1 for d in recent_hist if d and d != direction)
            FLIP_TREND_MIN = float(getattr(config, "FLIP_TREND_MIN_15M", 1.5))
            if opposite >= 3 and abs(trend_score) < FLIP_TREND_MIN:
                self._diag_log(
                    f"flipguard-{coin}",
                    f"[FLIP GUARD] {coin} {direction}: recent={'->'.join(recent_hist)} "
                    f"trend={trend_score:+.2f} — need |trend|>={FLIP_TREND_MIN}",
                    12.0,
                )
                return None

        # ── Momentum must agree with direction ──
        _mom_align = os.getenv("MOMENTUM_ALIGN_ON", "on").lower() not in ("off", "0", "false")
        if _mom_align:
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
                return None

        # ── Expensive UP needs real distance (Tier 1) ──
        _exp_up_max = float(os.getenv("EXPENSIVE_UP_MAX_ASK", "0.58"))
        _exp_up_dist = float(os.getenv("EXPENSIVE_UP_MIN_DIST", "0.0015"))
        if direction == "UP" and ask >= _exp_up_max and abs(dist_pct) < _exp_up_dist:
            self._diag_log(
                f"exp-up-{coin}",
                f"[EXPENSIVE UP] {coin}: ask={ask*100:.0f}c dist={dist_pct*100:+.3f}% "
                f"< {_exp_up_dist*100:.2f}% — skip",
                12.0,
            )
            return None

        # Entry price filters (early window allows cheaper asks)
        entry_max = getattr(config, "ENTRY_MAX", 0.75)'''
    if "[FLIP GUARD]" not in text:
        if flip_anchor not in text:
            raise SystemExit("flip guard anchor not found")
        text = text.replace(flip_anchor, flip_block, 1)

    # ── record_direction for flip guard history ──
    old_commit = '''        self._window_direction = direction  # legacy global
        self._window_directions[coin] = direction
        # ChopDetector records actual market outcome in run_bot.py, NOT bot's trade direction'''
    new_commit = '''        self._window_direction = direction  # legacy global
        self._window_directions[coin] = direction
        self._chop_detector.record_direction(direction)
        # ChopDetector feeds FLIP GUARD history'''
    if "self._chop_detector.record_direction(direction)" not in text:
        text = text.replace(old_commit, new_commit, 1)

    # ── SIGNAL log: show price/strike sources ──
    old_sig = '''            f"ROC60={roc_60*10000:+.1f} ROC300={roc_300*10000:+.1f}bps σ={sigma:.2e} T={time_remaining:.0f}s"
        )'''
    new_sig = '''            f"ROC60={roc_60*10000:+.1f}bps ROC300={roc_300*10000:+.1f}bps "
            f"σ={sigma:.2e} T={time_remaining:.0f}s spot={_spot_src} strike={_strike_src}"
        )'''
    if "spot={_spot_src}" not in text:
        text = text.replace(old_sig, new_sig, 1)

    # ── HIGH_ASK stricter at 58c ──
    text = text.replace(
        '_hi_ask = float(os.getenv("HIGH_ASK_EDGE_MIN_ASK", "0.62"))',
        '_hi_ask = float(os.getenv("HIGH_ASK_EDGE_MIN_ASK", "0.58"))',
    )
    text = text.replace(
        '_hi_edge = float(os.getenv("HIGH_ASK_EDGE_MIN_EDGE", "0.12"))',
        '_hi_edge = float(os.getenv("HIGH_ASK_EDGE_MIN_EDGE", "0.18"))',
    )

    p.write_text(text, encoding="utf-8")
    print("patched predictor.py")


def patch_env():
    p = ROOT / ".env"
    backup(p)
    updates = {
        # Peak quality gates
        "ACCURACY_GATE_ON": "on",
        "CONSENSUS_GATE_ON": "on",
        "ACCURACY_CONFIRM_SCANS": "2",
        "MIN_WIN_PROB": "0.68",
        "MIN_TREND_SCORE": "0.40",
        "MIN_TREND_ABS": "0.40",
        "CHOPPY_MIN_TREND_ABS": "0.48",
        "HARD_WARMUP_15M": "90",
        "WARMUP_SEC": "90",
        "ENTRY_MAX": "0.58",
        "EARLY_ENTRY_MIN": "0.35",
        "MIN_EDGE_THRESHOLD": "0.10",
        "HIGH_ASK_EDGE_MIN_ASK": "0.58",
        "HIGH_ASK_EDGE_MIN_EDGE": "0.18",
        # Bad-read fixes
        "STRIKE_CHAINLINK_MAX_AGE": "20",
        "MOMENTUM_ALIGN_ON": "on",
        "EXPENSIVE_UP_MAX_ASK": "0.58",
        "EXPENSIVE_UP_MIN_DIST": "0.0015",
        # Dead modules off (not wired in predictor)
        "REGIME_AWARE": "off",
        "COMPOUND_MODE": "off",
        "HYBRID_MODE": "off",
        "ENGINE_CONVICTION_ON": "on",
        "BOOK_DIRECTION_ENFORCE": "on",
        "STRIKE_DIRECTION_MIN_DIST": "0.00015",
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
    out.append("")
    out.append("# === PEAK RESTORE Jun5 2026 — active gates above override legacy junk below ===")
    p.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("patched .env")


def verify():
    for rel in ("predictor.py", "poly_resolution.py", "run_bot.py", "market_data.py"):
        r = subprocess.run(
            [sys.executable, "-m", "py_compile", str(ROOT / rel)],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            print(r.stderr)
            raise SystemExit(f"syntax failed: {rel}")
    print("syntax OK all files")


def main():
    patch_poly_resolution()
    patch_market_data()
    patch_run_bot()
    patch_predictor()
    patch_env()
    verify()
    print("PEAK RESTORE COMPLETE")


if __name__ == "__main__":
    main()
