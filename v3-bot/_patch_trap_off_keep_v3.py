#!/usr/bin/env python3
"""Replace the trap-off block: keep original signal instead of returning None.
Idempotent — detects already-patched state."""
import py_compile
import sys

PATH = "/home/ubuntu/v3-bot/predictor.py"

OLD = """                if not _ra_dry:
                    if _ra_action.kind == "SKIP":
                        return None
                    if _ra_action.kind == "TRADE_INVERTED":
                        _trap_off = _os_ra2.getenv("REGIME_TRAP_INVERT", "on").lower() == "off"
                        _is_trap = "trap-band" in (_ra_action.reason or "")
                        _min_inv_edge = float(_os_ra2.getenv("TRAP_INVERT_MIN_EDGE", "0.12"))
                        if _is_trap and _trap_off:
                            logger.info(
                                f"[TRAP INVERT OFF] {coin} {direction}@{ask*100:.0f}c "
                                f"would invert -> {_ra_action.direction} — skipped"
                            )
                            return None
                        _ra_new_dir = _ra_action.direction"""

NEW = """                if not _ra_dry:
                    if _ra_action.kind == "SKIP":
                        return None
                    _trap_off_keep = False
                    if _ra_action.kind == "TRADE_INVERTED":
                        _trap_off = _os_ra2.getenv("REGIME_TRAP_INVERT", "on").lower() == "off"
                        _is_trap = "trap-band" in (_ra_action.reason or "")
                        _min_inv_edge = float(_os_ra2.getenv("TRAP_INVERT_MIN_EDGE", "0.12"))
                        if _is_trap and _trap_off:
                            logger.info(
                                f"[TRAP INVERT OFF] {coin} {direction}@{ask*100:.0f}c "
                                f"keeping original direction (trap inverts disabled)"
                            )
                            _trap_off_keep = True
                    if _ra_action.kind == "TRADE_INVERTED" and not _trap_off_keep:
                        _ra_new_dir = _ra_action.direction"""

OLD_TAIL = """                            logger.info(
                                f"[REGIME INVERT-PROB] {coin} {_ra_new_dir}@{ask*100:.0f}c "
                                f"prob={win_prob:.2f} edge={edge*100:.1f}% src={_ra_inv_src}"
                            )
                    _ra_size_factor = float(_ra_action.size_factor)"""

NEW_TAIL = """                            logger.info(
                                f"[REGIME INVERT-PROB] {coin} {_ra_new_dir}@{ask*100:.0f}c "
                                f"prob={win_prob:.2f} edge={edge*100:.1f}% src={_ra_inv_src}"
                            )
                    if _trap_off_keep:
                        _ra_size_factor = 1.0
                        confidence = "MEDIUM"
                    else:
                        _ra_size_factor = float(_ra_action.size_factor)"""


def main():
    with open(PATH, encoding="utf-8") as f:
        text = f.read()
    if "_trap_off_keep" in text:
        print("already patched")
        return
    if OLD not in text:
        sys.exit("OLD anchor not found")
    if OLD_TAIL not in text:
        sys.exit("OLD_TAIL anchor not found")
    text = text.replace(OLD, NEW, 1).replace(OLD_TAIL, NEW_TAIL, 1)
    with open(PATH, "w", encoding="utf-8") as f:
        f.write(text)
    py_compile.compile(PATH, doraise=True)
    print("OK")


if __name__ == "__main__":
    main()
