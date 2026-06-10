"""
Jun 2 PM — lower EXHAUST HIGH-ENTRY override threshold from 0.63 -> 0.60.

CONTEXT: at 11:57:00, the bot generated a textbook Pattern A signal:
  [SIGNAL] ETH DOWN | Prob=80% | Ask=62c | Edge=17.5% | Trend=-0.27
But got killed by [EXHAUST BLOCK score=0.10] because the HIGH-ENTRY rescue
override requires entry_price >= 0.63, and 0.62 missed by 1 cent.

AUDIT (apr28): entries 63c+ that got EXHAUST-blocked won 71% WR on resolve.
60-62c bucket likely has similar dynamics (just barely beneath audit cutoff).

CHANGES:
  1. run_bot.py: replace hardcoded 0.63 with env var EXHAUST_OVERRIDE_HIGH_ENTRY
  2. .env: set EXHAUST_OVERRIDE_HIGH_ENTRY=0.60

SAFETY:
  - Env-driven => instant revert via .env
  - Override still only allows DAMPEN (half size), not full
  - Decisive blocks (score >= 0.65) still ABSTAIN — unchanged
  - No change to MIN_WIN_PROB, ENTRY_MIN, ENTRY_MAX, KELLY sizing
"""
from pathlib import Path

RB = Path("/home/ubuntu/v3-bot/run_bot.py")
ENV = Path("/home/ubuntu/v3-bot/.env")


def patch_run_bot():
    text = RB.read_text()
    old = (
        "                        if (_act == \"ABSTAIN\" and not _was_overridden\n"
        "                                and (_p.entry_price if _p.entry_price > 0.05 else _p.poly_price) >= 0.63\n"
        "                                and float(_res.get(\"score\", 0) or 0) < 0.65):\n"
    )
    new = (
        "                        # Jun-2 PM: HIGH-ENTRY threshold env-driven (default 0.60 down from 0.63).\n"
        "                        # Catches Pattern A trades like ETH DOWN @ 62c missed today at 11:57.\n"
        "                        _hi_min = float(os.getenv(\"EXHAUST_OVERRIDE_HIGH_ENTRY\", \"0.60\"))\n"
        "                        if (_act == \"ABSTAIN\" and not _was_overridden\n"
        "                                and (_p.entry_price if _p.entry_price > 0.05 else _p.poly_price) >= _hi_min\n"
        "                                and float(_res.get(\"score\", 0) or 0) < 0.65):\n"
    )
    if "Jun-2 PM: HIGH-ENTRY threshold env-driven" in text:
        print("[SKIP] run_bot already patched")
    elif old not in text:
        print("[FAIL] HIGH-ENTRY anchor not found")
        return False
    else:
        # Fix indentation: original has 28 spaces; new lines must match
        # The original lines are deep nested; the comment + var need same indent.
        indent = " " * 24  # matches " " * 24 used in the file? Let's check by searching
        # actually inspecting source: the `if (_act == "ABSTAIN"` line has 24 spaces indent
        # Use raw replace; old already has 24-space indent in pattern.
        RB.write_text(text.replace(old, new, 1))
        print("[OK] patched run_bot.py with env-driven HIGH-ENTRY threshold")
    return True


def patch_env():
    text = ENV.read_text()
    if "EXHAUST_OVERRIDE_HIGH_ENTRY" in text:
        print("[SKIP] .env already has EXHAUST_OVERRIDE_HIGH_ENTRY")
        return
    # Append at end of .env
    text = text.rstrip() + "\n\n# Jun-2 PM: lower HIGH-ENTRY EXHAUST rescue from 0.63 -> 0.60 (env-driven)\nEXHAUST_OVERRIDE_HIGH_ENTRY=0.60\n"
    ENV.write_text(text)
    print("[OK] added EXHAUST_OVERRIDE_HIGH_ENTRY=0.60 to .env")


if __name__ == "__main__":
    if patch_run_bot():
        patch_env()
