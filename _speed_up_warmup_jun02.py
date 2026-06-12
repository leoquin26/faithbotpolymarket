"""
Jun 2 PM — speed up warmup so bot acts in early window before consensus.

Symptom: every 15m window the bot was burning 120s + 75s + 30 ticks = ~3 min
in warmup. By the time it could act, the market had consensus-formed and
all asks were >72c, blocking every signal.

Fix: surgical edits to predictor.py:
  1. Hard warmup floor 15m: 120s -> 60s
  2. MIN_TICKS: 30 -> 15
  3. Make both env-tunable for future adjustments.
"""
import re
import sys
from pathlib import Path

PRED = Path("/home/ubuntu/v3-bot/predictor.py")


def patch(text: str, old: str, new: str, label: str) -> str:
    if new in text and old not in text:
        print(f"[SKIP] {label}: already patched")
        return text
    if old not in text:
        print(f"[FAIL] {label}: source pattern not found")
        sys.exit(1)
    print(f"[OK]   {label}")
    return text.replace(old, new, 1)


def main():
    text = PRED.read_text()

    # === Patch 1: MIN_TICKS class constant -> env-driven, lower default ===
    text = patch(
        text,
        "    MIN_TICKS = 30\n",
        "    MIN_TICKS = int(os.getenv(\"PREDICTOR_MIN_TICKS\", \"15\"))  # Jun-2: 30 -> 15, env-tunable\n",
        "MIN_TICKS class constant -> env-driven",
    )

    # === Patch 2: Hard warmup floor -> env-driven, lower default ===
    text = patch(
        text,
        '        _warmup_min  = {"5m": 30,  "15m": 120, "1h": 600}.get(_tf, 120)\n',
        '        # Jun-2: 15m hard floor 120s -> 60s. Env-tunable via HARD_WARMUP_15M.\n'
        '        _hard_15m = int(os.getenv("HARD_WARMUP_15M", "60"))\n'
        '        _warmup_min  = {"5m": 30,  "15m": _hard_15m, "1h": 600}.get(_tf, _hard_15m)\n',
        "hard warmup floor -> env-driven",
    )

    PRED.write_text(text)
    print()
    print("Patched. Run: python3 -m py_compile predictor.py")


if __name__ == "__main__":
    main()
