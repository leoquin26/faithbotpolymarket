#!/usr/bin/env python3
"""Trap-band: only invert expensive UP (60-70c). Expensive DOWN -> SKIP, not flip to UP."""
import py_compile
import sys

PATH = "/home/ubuntu/v3-bot/regime_aware/regime_strategy.py"

OLD_STICKY = """        if window_start > 0 and sticky_key in self._trap_sticky and regime != "TRENDING":
            inv_dir = "DOWN" if direction == "UP" else "UP"
            return Action(
                kind="TRADE_INVERTED",
                direction=inv_dir,
                size_factor=0.5,
                reason=f"trap-band-sticky({ask*100:.0f}c)",
            )"""

NEW_STICKY = """        if window_start > 0 and sticky_key in self._trap_sticky and regime != "TRENDING":
            # Sticky only applies to UP-trap inverts (May 28: DOWN@61c -> UP lost).
            if direction == "UP":
                return Action(
                    kind="TRADE_INVERTED",
                    direction="DOWN",
                    size_factor=0.5,
                    reason=f"trap-band-sticky({ask*100:.0f}c)",
                )
            return Action("SKIP", direction, 0, f"trap-band-sticky-skip-DOWN({ask*100:.0f}c)")"""

OLD_TRAP = """        if self._TRAP_BAND_BAND_MIN <= ask <= self._TRAP_BAND_BAND_MAX and regime != "TRENDING":
            inv_dir = "DOWN" if direction == "UP" else "UP"
            if window_start > 0:
                self._trap_sticky.add(sticky_key)
            return Action(
                kind="TRADE_INVERTED",
                direction=inv_dir,
                size_factor=0.5,
                reason=f"trap-band({ask*100:.0f}c)",
            )"""

NEW_TRAP = """        if self._TRAP_BAND_BAND_MIN <= ask <= self._TRAP_BAND_BAND_MAX and regime != "TRENDING":
            if direction == "UP":
                if window_start > 0:
                    self._trap_sticky.add(sticky_key)
                return Action(
                    kind="TRADE_INVERTED",
                    direction="DOWN",
                    size_factor=0.5,
                    reason=f"trap-band({ask*100:.0f}c)",
                )
            # Expensive DOWN entry (bad R:R) — do NOT invert to UP against trend.
            return Action("SKIP", direction, 0, f"trap-band-expensive-DOWN({ask*100:.0f}c)")"""


def main():
    with open(PATH, encoding="utf-8") as f:
        text = f.read()

    if "trap-band-expensive-DOWN" in text:
        print("already patched")
        return

    if OLD_STICKY not in text:
        sys.exit("sticky anchor not found")
    if OLD_TRAP not in text:
        sys.exit("trap anchor not found")

    text = text.replace(OLD_STICKY, NEW_STICKY, 1)
    text = text.replace(OLD_TRAP, NEW_TRAP, 1)

    with open(PATH, "w", encoding="utf-8") as f:
        f.write(text)

    py_compile.compile(PATH, doraise=True)
    print("OK:", PATH)


if __name__ == "__main__":
    main()
