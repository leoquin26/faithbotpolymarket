"""V7 Phase-1 patch: make FLIP_TREND_MIN timeframe-aware for the 5m bot.

15m bot keeps threshold at 1.5 (current behaviour, no risk to V6).
5m bot drops to 1.2 to capture missed UP/DOWN flip setups blocked by
the previous DOWN/UP commit streak. Counterfactual showed 40+ A-tier
windows on 5m were lost to this gate.

Concurrent .env change (M5_MAX_CONCURRENT 1 -> 2) is handled separately.
"""
import sys, re, pathlib

PATH = pathlib.Path("/home/ubuntu/v3-bot/predictor.py")
src = PATH.read_text(encoding="utf-8")

OLD = '            FLIP_TREND_MIN = 1.5\n            if opposite >= 3 and abs(trend_score) < FLIP_TREND_MIN:'
NEW = '            # V7 Phase-1 (2026-05-12): lower 5m flip-guard from 1.5 to 1.2\n            # to capture A-tier setups the bot was locked out of by a\n            # stale DOWN/UP commit streak. 15m unchanged (V6 stays at 1.5).\n            FLIP_TREND_MIN = 1.2 if _tf == "5m" else 1.5\n            if opposite >= 3 and abs(trend_score) < FLIP_TREND_MIN:'

if OLD not in src:
    print("ANCHOR NOT FOUND — aborting", file=sys.stderr)
    sys.exit(1)

if 'FLIP_TREND_MIN = 1.2 if _tf == "5m"' in src:
    print("Already patched, no-op")
    sys.exit(0)

src2 = src.replace(OLD, NEW, 1)
PATH.write_text(src2, encoding="utf-8")
print("Patched predictor.py: FLIP_TREND_MIN now timeframe-aware (5m=1.2, 15m=1.5)")
