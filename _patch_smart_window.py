#!/usr/bin/env python3
"""
Apply data-driven SMART WINDOW relaxation to predictor.py + run_bot.py.

Audit on 8 days of EC2 logs (2026-04-28..2026-05-07) found:
  - CONSENSUS-blocked trades for BTC/SOL: 70%+ WR (data: 124W/54L BTC, 117W/52L SOL)
  - EXHAUST-blocked trades for BTC/SOL: 85%+ WR (131W/22L BTC, 127W/20L SOL)
  - ETH: opposite — keep blocking (49% / 33% WR)
  - Best hours: 9, 14, 15, 16 Lima

Action: in those hours, for BTC/SOL only, bypass CONSENSUS reject and downgrade
EXHAUST ABSTAIN -> DAMPEN (half size). Marked with _dampened flag so order_manager
already halves the size.

All env-toggleable: SMART_WINDOW_ENABLED, SMART_WINDOW_COINS, SMART_WINDOW_HOURS.
"""
from pathlib import Path

# ── predictor.py: CONSENSUS bypass ───────────────────────────────
PRED = Path("predictor.py")
text = PRED.read_text(encoding="utf-8")

OLD_CONSENSUS = """            if majority and direction != majority:
                self._diag_log(
                    f"consensus-{coin}",
                    f"[CONSENSUS] {coin} {direction}: market consensus is {majority} "
                    f"({up_count}UP/{down_count}DOWN) — blocking minority bet",
                    15.0,
                )
                return None"""

NEW_CONSENSUS = """            if majority and direction != majority:
                # may07 W/L audit: BTC/SOL minority bets in profitable hours win
                # 70-80% (124W/54L BTC, 117W/52L SOL across 8 days). Bypass and
                # let the trade through, marked for half-size dampening.
                _smart_enabled = bool(int(getattr(config, "SMART_WINDOW_ENABLED", 1) or 0))
                _smart_coins = str(getattr(config, "SMART_WINDOW_COINS", "BTC,SOL") or "BTC,SOL")
                _smart_hours_str = str(getattr(config, "SMART_WINDOW_HOURS", "9,14,15,16") or "9,14,15,16")
                _smart_hours = {int(h.strip()) for h in _smart_hours_str.split(",") if h.strip()}
                try:
                    from zoneinfo import ZoneInfo as _ZI
                    from datetime import datetime as _dt
                    _hr = _dt.now(_ZI("America/Lima")).hour
                except Exception:
                    from datetime import datetime as _dt
                    _hr = _dt.now().hour
                if (
                    _smart_enabled
                    and coin in {c.strip() for c in _smart_coins.split(",")}
                    and _hr in _smart_hours
                ):
                    self._diag_log(
                        f"consensus-bypass-{coin}",
                        f"[CONSENSUS BYPASS] {coin} {direction}: smart-window override "
                        f"(h={_hr} Lima, BTC/SOL only) — minority bet ALLOWED at half size "
                        f"(consensus was {majority} {up_count}U/{down_count}D)",
                        15.0,
                    )
                    self._smart_window_pending = getattr(self, "_smart_window_pending", set()) | {coin}
                else:
                    self._diag_log(
                        f"consensus-{coin}",
                        f"[CONSENSUS] {coin} {direction}: market consensus is {majority} "
                        f"({up_count}UP/{down_count}DOWN) — blocking minority bet",
                        15.0,
                    )
                    return None"""

if "[CONSENSUS BYPASS]" not in text:
    if OLD_CONSENSUS not in text:
        raise SystemExit("FAIL: CONSENSUS anchor not found in predictor.py")
    text = text.replace(OLD_CONSENSUS, NEW_CONSENSUS, 1)

# Mark the resulting Prediction with the _smart_window_dampened flag
OLD_RETURN = """        return Prediction(
            coin=coin,
            direction=direction,
            probability=win_prob,"""

NEW_RETURN = """        # may07: tag predictions rescued by smart-window CONSENSUS bypass
        _smart_pending = getattr(self, "_smart_window_pending", set())
        _smart_dampen = coin in _smart_pending
        if _smart_dampen:
            try:
                _smart_pending.discard(coin)
            except Exception:
                pass
        _pred = Prediction(
            coin=coin,
            direction=direction,
            probability=win_prob,"""

if "_smart_dampen = coin in _smart_pending" not in text:
    if OLD_RETURN not in text:
        raise SystemExit("FAIL: Prediction return anchor not found in predictor.py")
    text = text.replace(OLD_RETURN, NEW_RETURN, 1)

# Convert "return Prediction(" original closing to use _pred and mark flag
OLD_RETURN_END = """            trend_score=trend_score,
            market_regime=regime,
        )"""

NEW_RETURN_END = """            trend_score=trend_score,
            market_regime=regime,
        )
        if _smart_dampen:
            try:
                setattr(_pred, "_dampened", True)
                setattr(_pred, "_smart_window_rescued", True)
            except Exception:
                pass
        return _pred"""

if "_smart_window_rescued" not in text:
    if OLD_RETURN_END not in text:
        raise SystemExit("FAIL: Prediction trailing anchor not found in predictor.py")
    text = text.replace(OLD_RETURN_END, NEW_RETURN_END, 1)

# Remove the original `return Prediction(` (now replaced by `_pred = Prediction(`)
# Note: the previous replacements turned `return Prediction(` into `_pred = Prediction(`,
# and added a new explicit `return _pred` block after the closing paren, so we're done.

PRED.write_text(text, encoding="utf-8")
print("predictor.py PATCH OK: CONSENSUS BYPASS + smart-window dampen flag")

# ── run_bot.py: EXHAUST bypass to DAMPEN ─────────────────────────
RB = Path("run_bot.py")
text2 = RB.read_text(encoding="utf-8")

OLD_EXBLOCK = """                            logger.info(f"[EXHAUST BLOCK] {_p.coin} {_p.direction} skipped (score={_res.get('score', 0):.2f})")
                            continue"""

NEW_EXBLOCK = """                            # may07 W/L audit: BTC/SOL exhaust blocks in hours 9/14/15/16
                            # would have won 85%+ (131W/22L BTC, 127W/20L SOL). Smart-window
                            # downgrade: ABSTAIN -> DAMPEN (half size).
                            _sw_enabled = bool(int(getattr(config, "SMART_WINDOW_ENABLED", 1) or 0))
                            _sw_coins = str(getattr(config, "SMART_WINDOW_COINS", "BTC,SOL") or "BTC,SOL")
                            _sw_hours_str = str(getattr(config, "SMART_WINDOW_HOURS", "9,14,15,16") or "9,14,15,16")
                            _sw_hours = {int(h.strip()) for h in _sw_hours_str.split(",") if h.strip()}
                            try:
                                from zoneinfo import ZoneInfo as _ZI
                                from datetime import datetime as _dt
                                _sw_hr = _dt.now(_ZI("America/Lima")).hour
                            except Exception:
                                from datetime import datetime as _dt
                                _sw_hr = _dt.now().hour
                            if (
                                _sw_enabled
                                and _p.coin in {c.strip() for c in _sw_coins.split(",")}
                                and _sw_hr in _sw_hours
                            ):
                                logger.info(
                                    f"[EXHAUST SMART-WINDOW BYPASS] {_p.coin} {_p.direction}: "
                                    f"score={_res.get('score', 0):.2f} ABSTAIN -> DAMPEN (half size; "
                                    f"h={_sw_hr} Lima, BTC/SOL only — data: 85%+ WR)"
                                )
                                _act = "DAMPEN"
                                setattr(_p, "_smart_window_rescued", True)
                            else:
                                logger.info(f"[EXHAUST BLOCK] {_p.coin} {_p.direction} skipped (score={_res.get('score', 0):.2f})")
                                continue"""

if "[EXHAUST SMART-WINDOW BYPASS]" not in text2:
    if OLD_EXBLOCK not in text2:
        raise SystemExit("FAIL: EXHAUST BLOCK anchor not found in run_bot.py")
    text2 = text2.replace(OLD_EXBLOCK, NEW_EXBLOCK, 1)

RB.write_text(text2, encoding="utf-8")
print("run_bot.py PATCH OK: EXHAUST SMART-WINDOW BYPASS")
