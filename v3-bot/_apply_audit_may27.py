"""
_apply_audit_may27.py — applies the AUDIT_MAY27 production code patches
in-place on /home/ubuntu/v3-bot.

Idempotent: each replacement looks for an exact anchor string and either
applies the edit, or skips it if the patched form is already present.
Aborts loudly if neither anchor nor patched form is found.

Patches applied:
  predictor.py:
    P1) Track polymarket BID history in addition to ask history.
    P2) Pass window_start into RegimeStrategy Signal so trap-band stickiness
        knows which 15m window each call belongs to.
    P3) Use per-coin regime via get_regime(coin=coin).
    P4) Compute & log [MICRO SHADOW] microstructure feature snapshot.
    P5) Pass bid_history + spread_bps to compute_reversion_risk so the
        confirmation_score and spread_score components can fire.

  run_bot.py:
    R1) Dedup CLOB API calls — scan_coin now computes the arb candidate
        in-line (4 fewer calls per coin per scan).

Run on EC2:
    cd /home/ubuntu/v3-bot && python3 _apply_audit_may27.py
"""
from __future__ import annotations

import os
import sys
from typing import List, Tuple


REPO = "/home/ubuntu/v3-bot"


def patch_file(path: str, edits: List[Tuple[str, str, str]]) -> int:
    """edits = [(label, anchor_text, replacement_text), ...].

    Returns the number of edits actually applied. Skips an edit if the
    replacement is already in the file. Raises if the anchor is missing
    AND the replacement is not present (we don't know how to recover).
    """
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
                f"({src.count(anchor)}) — anchor is too generic"
            )
        src = src.replace(anchor, replacement, 1)
        applied += 1
        print(f"  [done] {label}")
    if applied:
        # Atomic write
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(src)
        os.replace(tmp, path)
    return applied


# ── predictor.py edits ───────────────────────────────────────────────────────
PREDICTOR_EDITS: List[Tuple[str, str, str]] = [
    (
        "P1: track polymarket bid history alongside ask history",
        # Anchor: the existing comment + ask deque
        '''        # ── [REVERSION-RISK v1] (May 27) ──
        # Per-side rolling history of polymarket asks. Keyed by "{coin}:{UP|DOWN}".
        # Used by reversion_risk to compute ask velocity (microstructure signal).
        self._poly_ask_history: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=120)
        )''',
        # Replacement: ask + bid + book snapshots
        '''        # ── [REVERSION-RISK v1] (May 27) ──
        # Per-side rolling history of polymarket asks. Keyed by "{coin}:{UP|DOWN}".
        # Used by reversion_risk to compute ask velocity (microstructure signal).
        self._poly_ask_history: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=120)
        )
        # ── [AUDIT MAY27 P1] bid history & per-side book snapshot ──
        # Bid history confirms two-sided book pressure (reversion_risk
        # confirmation_score). Latest book lets us compute spread_bps.
        self._poly_bid_history: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=120)
        )
        self._poly_book_latest: Dict[str, dict] = {}''',
    ),
    (
        "P1.b: record bid + book snapshot in feed_books section",
        '''        # ── [REVERSION-RISK v1] record polymarket ask history per side ──
        # Done early so we accumulate samples even when later filters skip the trade.
        try:
            _now_ph = time.time()
            if up_ask and 0.01 < up_ask < 0.99:
                self._poly_ask_history[f"{coin}:UP"].append((_now_ph, float(up_ask)))
            if down_ask and 0.01 < down_ask < 0.99:
                self._poly_ask_history[f"{coin}:DOWN"].append((_now_ph, float(down_ask)))
        except Exception:
            pass''',
        '''        # ── [REVERSION-RISK v1] record polymarket ask history per side ──
        # Done early so we accumulate samples even when later filters skip the trade.
        try:
            _now_ph = time.time()
            if up_ask and 0.01 < up_ask < 0.99:
                self._poly_ask_history[f"{coin}:UP"].append((_now_ph, float(up_ask)))
            if down_ask and 0.01 < down_ask < 0.99:
                self._poly_ask_history[f"{coin}:DOWN"].append((_now_ph, float(down_ask)))
            # [AUDIT MAY27 P1] also track bid history per side; passed via kwargs.
            _up_bid = float(kwargs.get("up_bid") or 0.0)
            _down_bid = float(kwargs.get("down_bid") or 0.0)
            if _up_bid and 0.01 < _up_bid < 0.99:
                self._poly_bid_history[f"{coin}:UP"].append((_now_ph, _up_bid))
            if _down_bid and 0.01 < _down_bid < 0.99:
                self._poly_bid_history[f"{coin}:DOWN"].append((_now_ph, _down_bid))
            # Snapshot the latest book per side for spread + depth_skew computations.
            self._poly_book_latest[f"{coin}:UP"] = {
                "ask": float(up_ask or 0.0), "bid": _up_bid,
                "depth_ratio": float(up_depth or 0.0),
            }
            self._poly_book_latest[f"{coin}:DOWN"] = {
                "ask": float(down_ask or 0.0), "bid": _down_bid,
                "depth_ratio": float(down_depth or 0.0),
            }
        except Exception:
            pass''',
    ),
    (
        "P2+P3: pass window_start into Signal & request per-coin regime",
        '''                _ra_sig = _RASignal(
                    coin=coin, direction=direction, prob=win_prob,
                    ask=ask, edge=edge, trend=trend_score,
                )
                _ra_regime = self._regime_detector.get_regime()''',
        '''                _ra_sig = _RASignal(
                    coin=coin, direction=direction, prob=win_prob,
                    ask=ask, edge=edge, trend=trend_score,
                    window_start=int(window_start or 0),
                )
                # [AUDIT MAY27 P3] per-coin regime; falls back to global below 4 samples.
                _ra_regime = self._regime_detector.get_regime(coin=coin)''',
    ),
    (
        "P4+P5: extend reversion_risk call with bid history + spread, log [MICRO]",
        '''                _rr_live = _os_rr.getenv("REVERSION_RISK_LIVE", "off").lower() == "on"
                _rr_hist = list(self._poly_ask_history.get(f"{coin}:{direction}", []))
                _rr_res = _rr_compute(
                    direction=direction,
                    ask_history=_rr_hist,
                    roc60=roc_60,
                    roc120=roc_120,
                    T_sec=time_remaining,
                )''',
        '''                _rr_live = _os_rr.getenv("REVERSION_RISK_LIVE", "off").lower() == "on"
                _rr_hist = list(self._poly_ask_history.get(f"{coin}:{direction}", []))
                _rr_bid_hist = list(self._poly_bid_history.get(f"{coin}:{direction}", []))
                # [AUDIT MAY27 P4] microstructure features (spread, depth_skew, vels).
                # Logged in shadow on every signal for offline analysis.
                _rr_book_side = self._poly_book_latest.get(f"{coin}:{direction}", {}) or {}
                _rr_book_opp = self._poly_book_latest.get(
                    f"{coin}:{('DOWN' if direction == 'UP' else 'UP')}", {}
                ) or {}
                _rr_spread_bps = None
                try:
                    from regime_aware.poly_microstructure import (
                        spread_bps as _ms_spread_bps,
                        features_for_side as _ms_features,
                        format_log_line as _ms_log,
                    )
                    _rr_spread_bps = _ms_spread_bps(_rr_book_side)
                    _ms_feats = _ms_features(
                        direction=direction,
                        side_ask_history=_rr_hist,
                        side_bid_history=_rr_bid_hist,
                        side_book=_rr_book_side,
                        opposite_book=_rr_book_opp,
                    )
                    logger.info(_ms_log(coin, direction, _ms_feats))
                except Exception as _e_ms:
                    logger.debug(f"[MICRO] feature compute failed: {_e_ms}")
                _rr_res = _rr_compute(
                    direction=direction,
                    ask_history=_rr_hist,
                    bid_history=_rr_bid_hist,
                    roc60=roc_60,
                    roc120=roc_120,
                    T_sec=time_remaining,
                    spread_bps=_rr_spread_bps,
                )''',
    ),
]


# ── run_bot.py edits ─────────────────────────────────────────────────────────
RUN_BOT_EDITS: List[Tuple[str, str, str]] = [
    (
        "R1.a: scan_coin now computes arb in-line and returns a 3-tuple",
        '''                pred = predictor.predict(
                    info,
                    ws_price=info.current_crypto_price,
                    realized_vol=realized_vol,
                    up_ask=up_book.get("ask") or 0.0,
                    down_ask=down_book.get("ask") or 0.0,
                    up_mid=up_book.get("mid") or 0.0,
                    down_mid=down_book.get("mid") or 0.0,
                    up_depth=up_book.get("depth_ratio", 0.0),
                    down_depth=down_book.get("depth_ratio", 0.0),
                    ticks=ticks,
                )
                return info, pred''',
        '''                pred = predictor.predict(
                    info,
                    ws_price=info.current_crypto_price,
                    realized_vol=realized_vol,
                    up_ask=up_book.get("ask") or 0.0,
                    down_ask=down_book.get("ask") or 0.0,
                    up_bid=up_book.get("bid") or 0.0,
                    down_bid=down_book.get("bid") or 0.0,
                    up_mid=up_book.get("mid") or 0.0,
                    down_mid=down_book.get("mid") or 0.0,
                    up_depth=up_book.get("depth_ratio", 0.0),
                    down_depth=down_book.get("depth_ratio", 0.0),
                    ticks=ticks,
                )
                # [AUDIT MAY27 R1] compute arb here using books we already
                # fetched, instead of re-fetching them in the main loop below.
                arb = None
                if not is_window_locked(coin, info.window_start):
                    try:
                        arb = find_arbitrage(
                            info,
                            up_ask=up_book.get("ask") or 0.0,
                            down_ask=down_book.get("ask") or 0.0,
                        )
                    except Exception:
                        arb = None
                return info, pred, arb''',
    ),
    (
        "R1.b: main loop unpacks 3-tuple and skips redundant get_clob_book pair",
        '''            for future in as_completed(futures_map):
                coin_name = futures_map[future]
                try:
                    info, pred = future.result()
                    if info and arb_enabled and not is_window_locked(info.coin, info.window_start):
                        try:
                            ub = orders.get_clob_book(info.up_token_id)
                            db = orders.get_clob_book(info.down_token_id)
                            arb = find_arbitrage(info, up_ask=ub.get("ask") or 0, down_ask=db.get("ask") or 0)
                            if arb:
                                arb_candidates.append(arb)
                        except Exception:
                            pass
                    if pred:
                        predictions.append(pred)
                except Exception as e:
                    logger.error(f"Scan error for {coin_name}: {e}")''',
        '''            for future in as_completed(futures_map):
                coin_name = futures_map[future]
                try:
                    info, pred, arb = future.result()
                    if arb and arb_enabled:
                        arb_candidates.append(arb)
                    if pred:
                        predictions.append(pred)
                except Exception as e:
                    logger.error(f"Scan error for {coin_name}: {e}")''',
    ),
]


def main() -> int:
    paths_to_compile: List[str] = []
    print("=" * 64)
    print("  Applying AUDIT_MAY27 patches to /home/ubuntu/v3-bot")
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

    # Sanity: compile every touched file
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
