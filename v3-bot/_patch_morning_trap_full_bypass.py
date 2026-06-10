#!/usr/bin/env python3
"""Make trap-band override bypass ALL P3 gates (prob, edge, trend), not just prob.
These are inverted half-size trades — the safety is in the size, not the gate stack."""
import py_compile
import re
import sys

PATH = "/home/ubuntu/v3-bot/morning_strategy.py"


def main():
    with open(PATH, encoding="utf-8") as f:
        text = f.read()

    # Find the P3 block and replace with full-bypass version
    OLD_P3 = """    if phase == 3:
        if coin not in P3_ALLOWED:
            return None
        if prob < P3_MIN_PROB:
            if not _trap_band_override(prob, edge):
                logger.debug(f"[MORNING P3] {coin} prob {prob:.0%} < {P3_MIN_PROB:.0%}")
                return None
            logger.info(
                f"[MORNING P3 TRAP-OVERRIDE] {coin} {pred.direction}: "
                f"prob={prob:.0%} edge={edge*100:.1f}% (high-edge inverted trap-band)"
            )
        if edge < P3_MIN_EDGE:
            logger.debug(f"[MORNING P3] {coin} edge {edge*100:.1f}% < {P3_MIN_EDGE*100:.0f}%")
            return None
        if abs_trend < P3_MIN_TREND:
            logger.debug(f"[MORNING P3] {coin} |trend| {abs_trend:.2f} < {P3_MIN_TREND}")
            return None
        logger.info(
            f"[MORNING P3] {coin} {pred.direction} APPROVED | "
            f"Prob={prob:.0%} Edge={edge*100:.1f}% |Trend|={abs_trend:.2f}"
        )
        return pred"""

    NEW_P3 = """    if phase == 3:
        if coin not in P3_ALLOWED:
            return None
        # Trap-band inverted trades: bypass prob/edge/trend gates entirely.
        # Safety is enforced via size_factor=0.5 in regime_strategy + Kelly cap.
        if _trap_band_override(prob, edge):
            logger.info(
                f"[MORNING P3 TRAP-OVERRIDE] {coin} {pred.direction} APPROVED | "
                f"prob={prob:.0%} edge={edge*100:.1f}% |trend|={abs_trend:.2f} "
                f"(half-size trap-band invert)"
            )
            return pred
        if prob < P3_MIN_PROB:
            logger.debug(f"[MORNING P3] {coin} prob {prob:.0%} < {P3_MIN_PROB:.0%}")
            return None
        if edge < P3_MIN_EDGE:
            logger.debug(f"[MORNING P3] {coin} edge {edge*100:.1f}% < {P3_MIN_EDGE*100:.0f}%")
            return None
        if abs_trend < P3_MIN_TREND:
            logger.debug(f"[MORNING P3] {coin} |trend| {abs_trend:.2f} < {P3_MIN_TREND}")
            return None
        logger.info(
            f"[MORNING P3] {coin} {pred.direction} APPROVED | "
            f"Prob={prob:.0%} Edge={edge*100:.1f}% |Trend|={abs_trend:.2f}"
        )
        return pred"""

    if NEW_P3.split("\n", 1)[0] in text and "TRAP-OVERRIDE" in text and "APPROVED |" in text and "(half-size trap-band invert)" in text:
        print("already patched")
        return

    if OLD_P3 not in text:
        sys.exit("P3 anchor not found - was the prior patch applied?")

    text = text.replace(OLD_P3, NEW_P3, 1)

    # Also do the same for P1
    OLD_P1 = """        if prob < P1_MIN_PROB and not _trap_band_override(prob, edge):
            logger.debug(f"[MORNING P1] {coin} prob {prob:.0%} < {P1_MIN_PROB:.0%}")
            return None
        if edge < P1_MIN_EDGE:"""

    NEW_P1 = """        # Trap-band inverted trades: full bypass (P1 too)
        if _trap_band_override(prob, edge):
            logger.info(
                f"[MORNING P1 TRAP-OVERRIDE] {coin} {pred.direction} APPROVED | "
                f"prob={prob:.0%} edge={edge*100:.1f}% |trend|={abs_trend:.2f} "
                f"(half-size trap-band invert)"
            )
            return pred
        if prob < P1_MIN_PROB:
            logger.debug(f"[MORNING P1] {coin} prob {prob:.0%} < {P1_MIN_PROB:.0%}")
            return None
        if edge < P1_MIN_EDGE:"""

    if OLD_P1 in text:
        text = text.replace(OLD_P1, NEW_P1, 1)
        print("P1 patched")

    with open(PATH, "w", encoding="utf-8") as f:
        f.write(text)

    py_compile.compile(PATH, doraise=True)
    print("OK:", PATH)


if __name__ == "__main__":
    main()
