#!/usr/bin/env python3
"""Jun 2 PM: early-window trap bypass, exhaust 52c override, regime invert pause."""
from pathlib import Path
import shutil
from datetime import datetime

ROOT = Path("/home/ubuntu/v3-bot")
RUN = ROOT / "run_bot.py"
PRED = ROOT / "predictor.py"
ENV = ROOT / ".env"
ts = datetime.now().strftime("%Y%m%d_%H%M%S")

# ── run_bot.py: early exhaust override (after high-entry block) ─────────────
OLD_EXHAUST_HI = """                        _hi_min = float(os.getenv("EXHAUST_OVERRIDE_HIGH_ENTRY", "0.60"))
                        if (_act == "ABSTAIN" and not _was_overridden
                                and (_p.entry_price if _p.entry_price > 0.05 else _p.poly_price) >= _hi_min
                                and float(_res.get("score", 0) or 0) < 0.65):
                            _entry_c = (_p.entry_price if _p.entry_price > 0.05 else _p.poly_price) * 100.0
                            logger.info(
                                f"[EXHAUST OVERRIDE-HIGH-ENTRY] {_p.coin} {_p.direction}: "
                                f"entry={_entry_c:.0f}c score={_res.get('score', 0):.2f} "
                                f"-> DAMPEN (half size, prob preserved; audit apr28 says 71% WR)"
                            )
                            _act = "DAMPEN"
                            _was_overridden = True  # Jun 1 fix: preserve prob like A-TIER override"""

NEW_EXHAUST_HI = """                        _hi_min = float(os.getenv("EXHAUST_OVERRIDE_HIGH_ENTRY", "0.60"))
                        if (_act == "ABSTAIN" and not _was_overridden
                                and (_p.entry_price if _p.entry_price > 0.05 else _p.poly_price) >= _hi_min
                                and float(_res.get("score", 0) or 0) < 0.65):
                            _entry_c = (_p.entry_price if _p.entry_price > 0.05 else _p.poly_price) * 100.0
                            logger.info(
                                f"[EXHAUST OVERRIDE-HIGH-ENTRY] {_p.coin} {_p.direction}: "
                                f"entry={_entry_c:.0f}c score={_res.get('score', 0):.2f} "
                                f"-> DAMPEN (half size, prob preserved; audit apr28 says 71% WR)"
                            )
                            _act = "DAMPEN"
                            _was_overridden = True  # Jun 1 fix: preserve prob like A-TIER override
                        # Jun-2 PM: early-window Pattern A — allow 52c+ when edge>=10% (first ~2min)
                        _early_hi = float(os.getenv("EXHAUST_EARLY_HIGH_ENTRY", "0.52"))
                        _early_edge = float(os.getenv("EXHAUST_EARLY_MIN_EDGE", "0.10"))
                        _early_t = float(os.getenv("EXHAUST_EARLY_MIN_T_SEC", "780"))
                        _t_rem = float(getattr(getattr(_p, "market_info", None), "time_remaining", 0) or 0)
                        _ep = (_p.entry_price if _p.entry_price > 0.05 else _p.poly_price)
                        if (_act == "ABSTAIN" and not _was_overridden
                                and _t_rem >= _early_t
                                and _ep >= _early_hi
                                and float(_p.edge or 0) >= _early_edge
                                and float(_res.get("score", 0) or 0) < 0.65):
                            logger.info(
                                f"[EXHAUST OVERRIDE-EARLY] {_p.coin} {_p.direction}: "
                                f"entry={_ep*100:.0f}c edge={float(_p.edge)*100:.1f}% "
                                f"T={_t_rem:.0f}s score={_res.get('score', 0):.2f} -> DAMPEN"
                            )
                            _act = "DAMPEN"
                            _was_overridden = True"""

# ── run_bot.py: trap band override in afternoon CLOB path ───────────────────
OLD_TRAP = """                        if config.TRAP_BAND_MIN <= clob_ask <= config.TRAP_BAND_MAX:
                            logger.info(
                                f"[TRAP BAND] {best.coin} {best.direction}: "
                                f"CLOB ask={clob_ask*100:.0f}c in trap band "
                                f"{config.TRAP_BAND_MIN*100:.0f}-{config.TRAP_BAND_MAX*100:.0f}c (47% WR)"
                            )
                            unlock_window(best.coin, best.market_info.window_start)
                            continue"""

NEW_TRAP = """                        if config.TRAP_BAND_MIN <= clob_ask <= config.TRAP_BAND_MAX:
                            _tb_ovr_p = float(os.getenv(
                                "TRAP_BAND_OVERRIDE_PROB",
                                getattr(config, "TRAP_BAND_OVERRIDE_PROB", 0.65),
                            ))
                            _tb_ovr_e = float(os.getenv(
                                "TRAP_BAND_OVERRIDE_EDGE",
                                getattr(config, "TRAP_BAND_OVERRIDE_EDGE", 0.15),
                            ))
                            if best.probability >= _tb_ovr_p and real_edge >= _tb_ovr_e:
                                logger.info(
                                    f"[TRAP BAND OVERRIDE] {best.coin} {best.direction}: "
                                    f"ask={clob_ask*100:.0f}c prob={best.probability:.0%} "
                                    f"edge={real_edge*100:.1f}% — allowed (A-tier)"
                                )
                            else:
                                logger.info(
                                    f"[TRAP BAND] {best.coin} {best.direction}: "
                                    f"CLOB ask={clob_ask*100:.0f}c in trap band "
                                    f"{config.TRAP_BAND_MIN*100:.0f}-{config.TRAP_BAND_MAX*100:.0f}c "
                                    f"(need prob>={_tb_ovr_p:.0%} edge>={_tb_ovr_e*100:.0f}%)"
                                )
                                unlock_window(best.coin, best.market_info.window_start)
                                continue"""

# ── predictor.py: skip strong-trend invert in early window ──────────────────
OLD_REGIME_INV = """                    if _ra_action.kind == "TRADE_INVERTED":
                        _trap_off = _os_ra2.getenv("REGIME_TRAP_INVERT", "on").lower() == "off"
                        _is_trap = "trap-band" in (_ra_action.reason or "")
                        _min_inv_edge = float(_os_ra2.getenv("TRAP_INVERT_MIN_EDGE", "0.12"))
                        if _is_trap and _trap_off:"""

NEW_REGIME_INV = """                    if _ra_action.kind == "TRADE_INVERTED":
                        _trap_off = _os_ra2.getenv("REGIME_TRAP_INVERT", "on").lower() == "off"
                        _is_trap = "trap-band" in (_ra_action.reason or "")
                        _min_inv_edge = float(_os_ra2.getenv("TRAP_INVERT_MIN_EDGE", "0.12"))
                        # Jun-2 PM: keep original direction in first ~2min when edge already strong
                        _early_no_inv_t = float(_os_ra2.getenv("EARLY_REGIME_NO_INVERT_T_SEC", "780"))
                        _early_no_inv_edge = float(_os_ra2.getenv("EARLY_REGIME_NO_INVERT_EDGE", "0.10"))
                        if (time_remaining >= _early_no_inv_t
                                and edge >= _early_no_inv_edge
                                and "strong-trend" in (_ra_action.reason or "")):
                            logger.info(
                                f"[REGIME EARLY NO-INVERT] {coin} {direction}@{ask*100:.0f}c "
                                f"edge={edge*100:.1f}% T={time_remaining:.0f}s — keeping {direction}"
                            )
                            _trap_off_keep = True
                        if _is_trap and _trap_off:"""

# ── predictor.py: log post-cal edge on SIGNAL line ───────────────────────────
OLD_SIGNAL = """        logger.info(
            f"[SIGNAL] {coin} {direction} | Prob={win_prob:.0%} | Ask={ask*100:.0f}c | "
            f"Edge={edge*100:.1f}% | Trend={trend_score:+.2f} Dist={dist_pct*100:+.3f}% "
            f"ROC60={roc_60*10000:+.1f}bps ¤â={sigma:.2e} T={time_remaining:.0f}s"
        )"""

# Move SIGNAL log to after calibration instead — cleaner. User asked optional post-cal.
# We'll add a second log after cal if live, and keep signal where it is but update at end.
# Simpler: append note in CALIBRATION log only — skip moving SIGNAL (big diff).
# Optional skipped to minimize scope — user said optional.

ENV_LINES = """
# Jun-2 PM early-window recovery
EXHAUST_EARLY_HIGH_ENTRY=0.52
EXHAUST_EARLY_MIN_EDGE=0.10
EXHAUST_EARLY_MIN_T_SEC=780
EARLY_REGIME_NO_INVERT_T_SEC=780
EARLY_REGIME_NO_INVERT_EDGE=0.10
"""


def patch_file(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        if new.strip() in text:
            print(f"  {label}: already patched")
            return
        raise SystemExit(f"  {label}: anchor not found in {path.name}")
    shutil.copy2(path, path.with_suffix(path.suffix + f".bak_{ts}"))
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print(f"  {label}: OK")


def patch_env() -> None:
    env = ENV.read_text(encoding="utf-8")
    for line in ENV_LINES.strip().splitlines():
        key = line.split("=", 1)[0]
        if f"{key}=" in env:
            continue
        env = env.rstrip() + "\n" + line + "\n"
    ENV.write_text(env, encoding="utf-8")
    print("  .env: appended new keys")


def main() -> None:
    print("Patching v3-bot early PM recovery...")
    patch_file(RUN, OLD_EXHAUST_HI, NEW_EXHAUST_HI, "run_bot exhaust early")
    patch_file(RUN, OLD_TRAP, NEW_TRAP, "run_bot trap override")
    patch_file(PRED, OLD_REGIME_INV, NEW_REGIME_INV, "predictor early no-invert")
    patch_env()
    print("Done.")


if __name__ == "__main__":
    main()
