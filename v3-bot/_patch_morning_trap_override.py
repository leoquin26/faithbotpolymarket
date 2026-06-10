#!/usr/bin/env python3
"""Wire TRAP_BAND_OVERRIDE_* into morning P1/P3 (inverted trap-band trades)."""
import py_compile
import sys

PATH = "/home/ubuntu/v3-bot/morning_strategy.py"

ANCHOR = """P3_MIN_TREND = float(_os_getenv("P3_MIN_TREND", "0.50"))
"""

INSERT = """P3_MIN_TREND = float(_os_getenv("P3_MIN_TREND", "0.50"))

# High-edge trap-band / inverted trades: prob after calibration can be ~50-55%
# while edge stays 15-25%. Use edge OR prob override (same as order_manager intent).
TRAP_BAND_OVERRIDE_PROB = float(_os_getenv("TRAP_BAND_OVERRIDE_PROB", "0.78"))
TRAP_BAND_OVERRIDE_EDGE = float(_os_getenv("TRAP_BAND_OVERRIDE_EDGE", "0.15"))


def _trap_band_override(prob: float, edge: float) -> bool:
    return prob >= TRAP_BAND_OVERRIDE_PROB or edge >= TRAP_BAND_OVERRIDE_EDGE

"""

OLD_P1 = """        if prob < P1_MIN_PROB:
            logger.debug(f"[MORNING P1] {coin} prob {prob:.0%} < {P1_MIN_PROB:.0%}")
            return None
        if edge < P1_MIN_EDGE:"""

NEW_P1 = """        if prob < P1_MIN_PROB and not _trap_band_override(prob, edge):
            logger.debug(f"[MORNING P1] {coin} prob {prob:.0%} < {P1_MIN_PROB:.0%}")
            return None
        if edge < P1_MIN_EDGE:"""

OLD_P3 = """        if prob < P3_MIN_PROB:
            logger.debug(f"[MORNING P3] {coin} prob {prob:.0%} < {P3_MIN_PROB:.0%}")
            return None"""

NEW_P3 = """        if prob < P3_MIN_PROB and not _trap_band_override(prob, edge):
            if _trap_band_override(prob, edge):
                logger.info(
                    f"[MORNING P3 TRAP-OVERRIDE] {coin} {pred.direction}: "
                    f"prob={prob:.0%} edge={edge*100:.1f}% (prob<{P3_MIN_PROB:.0%} but edge/prob override)"
                )
            else:
                logger.debug(f"[MORNING P3] {coin} prob {prob:.0%} < {P3_MIN_PROB:.0%}")
                return None
        elif prob < P3_MIN_PROB:
            logger.info(
                f"[MORNING P3 TRAP-OVERRIDE] {coin} {pred.direction}: "
                f"prob={prob:.0%} edge={edge*100:.1f}%"
            )"""

# Simpler P3 replacement without duplicate logic
NEW_P3_SIMPLE = """        if prob < P3_MIN_PROB:
            if not _trap_band_override(prob, edge):
                logger.debug(f"[MORNING P3] {coin} prob {prob:.0%} < {P3_MIN_PROB:.0%}")
                return None
            logger.info(
                f"[MORNING P3 TRAP-OVERRIDE] {coin} {pred.direction}: "
                f"prob={prob:.0%} edge={edge*100:.1f}% (high-edge inverted trap-band)"
            )"""


def main():
    with open(PATH, encoding="utf-8") as f:
        text = f.read()

    if "_trap_band_override" in text and "TRAP_BAND_OVERRIDE_PROB" in text:
        print("Already patched")
        return

    if ANCHOR not in text:
        sys.exit("anchor for P3_MIN_TREND not found")

    text = text.replace(ANCHOR, INSERT, 1)

    if OLD_P1 not in text:
        sys.exit("P1 anchor not found")
    text = text.replace(OLD_P1, NEW_P1, 1)

    if OLD_P3 not in text:
        sys.exit("P3 anchor not found")
    text = text.replace(OLD_P3, NEW_P3_SIMPLE, 1)

    with open(PATH, "w", encoding="utf-8") as f:
        f.write(text)

    py_compile.compile(PATH, doraise=True)
    print("OK:", PATH)


if __name__ == "__main__":
    main()
