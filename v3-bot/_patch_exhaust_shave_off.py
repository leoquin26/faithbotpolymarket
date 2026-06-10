#!/usr/bin/env python3
"""Make EXHAUST DAMPEN's probability shave env-controlled (default: no shave).
Size still halves; we just stop killing the probability."""
import py_compile
import sys

PATH = "/home/ubuntu/v3-bot/run_bot.py"

OLD = """                            if not _was_overridden:
                                # Normal DAMPEN: shave probability AND halve size
                                _p.probability = max(0.01, _p.probability * 0.85)
                                _entry = _p.entry_price if _p.entry_price > 0.05 else _p.poly_price
                                _p.edge = _p.probability - _entry"""

NEW = """                            if not _was_overridden:
                                _shave = os.getenv("EXHAUST_DAMPEN_PROB_SHAVE", "off").lower() == "on"
                                if _shave:
                                    _p.probability = max(0.01, _p.probability * 0.85)
                                    _entry = _p.entry_price if _p.entry_price > 0.05 else _p.poly_price
                                    _p.edge = _p.probability - _entry"""


def main():
    with open(PATH, encoding="utf-8") as f:
        text = f.read()
    if "EXHAUST_DAMPEN_PROB_SHAVE" in text:
        print("already patched")
        return
    if OLD not in text:
        sys.exit("anchor not found")
    text = text.replace(OLD, NEW, 1)
    with open(PATH, "w", encoding="utf-8") as f:
        f.write(text)
    py_compile.compile(PATH, doraise=True)
    print("OK")


if __name__ == "__main__":
    main()
