"""
_hotfix_scan_coin_tuple.py — patch scan_coin's 4 early returns to 3-tuple.

The R1 patch from _apply_audit_may27 changed scan_coin's terminal `return`
to a 3-tuple `(info, pred, arb)`. The four early-return paths still return
2-tuples, which breaks the unpacking in the main loop.

Idempotent. Runs on EC2 in /home/ubuntu/v3-bot.
"""
import os
import py_compile
import sys

PATH = "/home/ubuntu/v3-bot/run_bot.py"

# (anchor, replacement) — anchored on enough surrounding context to be unique.
EDITS = [
    (
        """            def scan_coin(coin: str):
                info = get_market_info(coin)
                if not info:
                    return None, None

                if info.time_remaining < config.MIN_TIME_REMAINING:
                    return info, None

                # FIX 1: Check atomic lock BEFORE calling predictor
                if is_window_locked(coin, info.window_start):
                    return info, None
                if orders.is_window_traded(coin, info.window_start):
                    return info, None""",
        """            def scan_coin(coin: str):
                info = get_market_info(coin)
                if not info:
                    return None, None, None

                if info.time_remaining < config.MIN_TIME_REMAINING:
                    return info, None, None

                # FIX 1: Check atomic lock BEFORE calling predictor
                if is_window_locked(coin, info.window_start):
                    return info, None, None
                if orders.is_window_traded(coin, info.window_start):
                    return info, None, None""",
    ),
]


def main() -> int:
    with open(PATH, "r", encoding="utf-8") as f:
        src = f.read()
    applied = 0
    for anchor, replacement in EDITS:
        if replacement in src:
            print("[skip] hotfix already present")
            continue
        if anchor not in src:
            print("[FAIL] anchor not found — bailing out for safety")
            return 2
        if src.count(anchor) > 1:
            print("[FAIL] anchor matches multiple times — bailing out")
            return 2
        src = src.replace(anchor, replacement, 1)
        applied += 1

    if applied:
        tmp = PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(src)
        os.replace(tmp, PATH)
        print(f"[done] {applied} edit(s) applied")

    try:
        py_compile.compile(PATH, doraise=True)
        print("[OK] run_bot.py compiles")
    except py_compile.PyCompileError as e:
        print(f"[FAIL] py_compile: {e}")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
