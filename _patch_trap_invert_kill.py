#!/usr/bin/env python3
"""Emergency: disable trap-band inverts; require min edge on any remaining invert."""
import py_compile
import sys

PATH = "/home/ubuntu/v3-bot/predictor.py"

OLD = """                    if _ra_action.kind == "TRADE_INVERTED":
                        _ra_new_dir = _ra_action.direction"""

NEW = """                    if _ra_action.kind == "TRADE_INVERTED":
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

OLD2 = """                            edge = win_prob - ask
                            confidence = "REGIME-INVERT"
                            logger.info(
                                f"[REGIME INVERT-PROB] {coin} {_ra_new_dir}@{ask*100:.0f}c "
                                f"prob={win_prob:.2f} edge={edge*100:+.1f}% src={_ra_inv_src}"
                            )"""

NEW2 = """                            edge = win_prob - ask
                            if _is_trap and edge < _min_inv_edge:
                                logger.info(
                                    f"[TRAP INVERT EDGE] {coin} {_ra_new_dir}@{ask*100:.0f}c "
                                    f"edge={edge*100:.1f}% < {_min_inv_edge*100:.0f}% — skipped"
                                )
                                return None
                            confidence = "REGIME-INVERT"
                            logger.info(
                                f"[REGIME INVERT-PROB] {coin} {_ra_new_dir}@{ask*100:.0f}c "
                                f"prob={win_prob:.2f} edge={edge*100:.1f}% src={_ra_inv_src}"
                            )"""


def main():
    with open(PATH, encoding="utf-8") as f:
        text = f.read()
    if "TRAP INVERT OFF" in text:
        print("already patched")
        return
    if OLD not in text or OLD2 not in text:
        sys.exit("anchor not found")
    text = text.replace(OLD, NEW, 1).replace(OLD2, NEW2, 1)
    with open(PATH, "w", encoding="utf-8") as f:
        f.write(text)
    py_compile.compile(PATH, doraise=True)
    print("OK")


if __name__ == "__main__":
    main()
