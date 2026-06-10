#!/usr/bin/env python3
"""Wire trade_audit.py into predictor + run_bot."""
import shutil
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
    if "import trade_audit" not in text:
        text = text.replace(
            "import logging\n",
            "import logging\nimport trade_audit\n",
            1,
        )
    anchor = """        try:
            setattr(_ra_pred, "_regime_size_factor", _ra_size_factor)
        except Exception:
            pass
        return _ra_pred"""
    insert = """        try:
            _taud = trade_audit.start(coin, int(window_start or 0))
            trade_audit.attach(_ra_pred, _taud)
            _rreg, _ract, _rrea = regime or "", "", ""
            try:
                _rreg = _ra_regime
                _ract = _ra_action.kind
                _rrea = _ra_action.reason or ""
            except NameError:
                pass
            trade_audit.record_signal(
                _ra_pred,
                prob=win_prob,
                edge=edge,
                ask=ask,
                trend=trend_score,
                dist_pct=dist_pct,
                regime=_rreg,
                regime_action=_ract,
                regime_reason=_rrea,
                sigma=sigma,
                T_sec=time_remaining,
            )
        except Exception:
            pass
        try:
            setattr(_ra_pred, "_regime_size_factor", _ra_size_factor)
        except Exception:
            pass
        return _ra_pred"""
    if anchor not in text:
        raise SystemExit("predictor return anchor not found")
    p.write_text(text.replace(anchor, insert, 1), encoding="utf-8")
    print("patched predictor.py")


def patch_run_bot():
    p = ROOT / "run_bot.py"
    backup(p)
    text = p.read_text(encoding="utf-8")
    if "import trade_audit" not in text:
        # after first import block
        text = text.replace(
            "import logging\n",
            "import logging\nimport trade_audit\n",
            1,
        )

    old_block = """                            logger.info(f"[EXHAUST BLOCK] {_p.coin} {_p.direction} skipped (score={_res.get('score', 0):.2f})")
                            continue"""

    new_block = """                            logger.info(f"[EXHAUST BLOCK] {_p.coin} {_p.direction} skipped (score={_res.get('score', 0):.2f})")
                            try:
                                trade_audit.record_exhaust(
                                    _p, "BLOCK", float(_res.get("score", 0) or 0))
                                trade_audit.log_decision(_p, "SKIP")
                            except Exception:
                                pass
                            continue"""

    if old_block not in text:
        raise SystemExit("exhaust block anchor not found")
    text = text.replace(old_block, new_block, 1)

    # After exhaust kept (append to _kept) - find _kept.append
    old_kept = "                        _kept.append(_p)\n                    predictions = _kept"
    new_kept = """                        try:
                            _ov = ""
                            if _was_overridden:
                                _ov = "override"
                            trade_audit.record_exhaust(
                                _p, _act, float(_res.get("score", 0) or 0), _ov)
                        except Exception:
                            pass
                        _kept.append(_p)
                    predictions = _kept"""
    if old_kept not in text:
        raise SystemExit("kept append anchor not found")
    text = text.replace(old_kept, new_kept, 1)

    old_morn = """                                        _success = orders.place_bet(_best_m)
                                        if _success and _best_m.coin in orders.positions:"""
    new_morn = """                                        try:
                                            trade_audit.record_morning(
                                                _best_m, _phase, "APPROVED")
                                            _clob_a = orders.get_clob_ask(_best_m.token_id)
                                            if _clob_a:
                                                trade_audit.record_clob(
                                                    _best_m, _clob_a,
                                                    _best_m.probability - _clob_a)
                                            trade_audit.log_decision(_best_m, "ATTEMPT")
                                        except Exception:
                                            pass
                                        _success = orders.place_bet(_best_m)
                                        if _success and _best_m.coin in orders.positions:"""
    if old_morn not in text:
        raise SystemExit("morning place_bet anchor not found")
    text = text.replace(old_morn, new_morn, 1)

    old_morn_ok = """                                            logger.info(
                                                f"[MORNING P{_phase} TRADE] {_best_m.coin} {_best_m.direction} "
                                                f"placed (half-Kelly)"
                                            )"""
    new_morn_ok = """                                            try:
                                                _pos = orders.positions[_best_m.coin]
                                                trade_audit.log_decision(
                                                    _best_m, "FILLED",
                                                    cost=float(_pos.get("cost", 0) or 0),
                                                    shares=float(_pos.get("shares", 0) or 0),
                                                    fill_c=float(_pos.get("entry", _best_m.entry_price) or 0),
                                                )
                                            except Exception:
                                                pass
                                            logger.info(
                                                f"[MORNING P{_phase} TRADE] {_best_m.coin} {_best_m.direction} "
                                                f"placed (half-Kelly)"
                                            )"""
    if old_morn_ok not in text:
        raise SystemExit("morning trade log anchor not found")
    text = text.replace(old_morn_ok, new_morn_ok, 1)

    old_morn_fail = """                                            logger.info(f"[MORNING UNLOCK] {_best_m.coin}: order failed")"""
    new_morn_fail = """                                            try:
                                                trade_audit.log_decision(_best_m, "ORDER_FAIL")
                                            except Exception:
                                                pass
                                            logger.info(f"[MORNING UNLOCK] {_best_m.coin}: order failed")"""
    if old_morn_fail not in text:
        raise SystemExit("morning unlock anchor not found")
    text = text.replace(old_morn_fail, new_morn_fail, 1)

    old_pm = """                        filled = orders.place_bet(best)
                        if not filled:"""
    new_pm = """                        try:
                            trade_audit.record_clob(best, clob_ask, real_edge, "PM")
                            trade_audit.log_decision(best, "ATTEMPT")
                        except Exception:
                            pass
                        filled = orders.place_bet(best)
                        if filled:
                            try:
                                if best.coin in orders.positions:
                                    _pos = orders.positions[best.coin]
                                    trade_audit.log_decision(
                                        best, "FILLED",
                                        cost=float(_pos.get("cost", 0) or 0),
                                        shares=float(_pos.get("shares", 0) or 0),
                                        fill_c=float(_pos.get("entry", best.entry_price) or 0),
                                    )
                            except Exception:
                                pass
                        if not filled:"""
    if old_pm not in text:
        raise SystemExit("PM place_bet anchor not found")
    text = text.replace(old_pm, new_pm, 1)

    old_pm_fail = """                            logger.info(f"[UNLOCK] {best.coin}: order failed, window unlocked for retry")"""
    new_pm_fail = """                            try:
                                trade_audit.log_decision(best, "ORDER_FAIL")
                            except Exception:
                                pass
                            logger.info(f"[UNLOCK] {best.coin}: order failed, window unlocked for retry")"""
    if old_pm_fail not in text:
        raise SystemExit("PM unlock anchor not found")
    text = text.replace(old_pm_fail, new_pm_fail, 1)

    # CLOB/trap rejects - log audit skip
    for old, gate in [
        (
            '                            logger.info(\n                                f"[CLOB REJECT] {best.coin}',
            "CLOB_REJECT",
        ),
    ]:
        pass  # skip optional for v1

    p.write_text(text, encoding="utf-8")
    print("patched run_bot.py")


def main():
    shutil.copy2(
        Path(__file__).parent / "trade_audit.py",
        ROOT / "trade_audit.py",
    )
    print("copied trade_audit.py")
    patch_predictor()
    patch_run_bot()
    print("OK")


if __name__ == "__main__":
    main()
