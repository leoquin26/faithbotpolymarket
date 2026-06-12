#!/usr/bin/env python3
"""
Add data-driven ETH UP guardrail to morning_strategy.py.

May 7 W/L audit (8 days, 18 ETH trades) shows ETH UP has 33% WR / -$17.58 net.
But all 4 losing ETH UP trades had prob >= 81% AND |trend| >= 0.96, while both
winning ETH UP trades had prob <= 79% AND |trend| <= 0.78. Block the loser
profile while keeping the winner profile.
"""
from pathlib import Path

p = Path("morning_strategy.py")
text = p.read_text(encoding="utf-8")

CONST_BLOCK = """
# may07 W/L audit: ETH UP signals show inverse-correlation between conviction
# and outcome (4 losses at prob>=81%/|trend|>=0.96, 2 wins at prob<=79%/|trend|<=0.78).
# Block ETH UP unless it fits the historically-winning low-conviction profile.
ETH_UP_FILTER_ENABLED = int(_os_getenv("ETH_UP_FILTER_ENABLED", "1"))
ETH_UP_MAX_PROB = float(_os_getenv("ETH_UP_MAX_PROB", "0.79"))
ETH_UP_MAX_TREND = float(_os_getenv("ETH_UP_MAX_TREND", "0.85"))
"""

ANCHOR = "P3_ALLOWED = {\"BTC\", \"ETH\", \"SOL\", \"XRP\"}"
if CONST_BLOCK.strip() not in text:
    if ANCHOR not in text:
        raise SystemExit("FAIL: anchor not found")
    text = text.replace(ANCHOR, CONST_BLOCK + "\n" + ANCHOR, 1)

GUARD_BLOCK = """    # may07 W/L audit: ETH UP guardrail (applied to all phases).
    if (
        ETH_UP_FILTER_ENABLED
        and pred.coin == "ETH"
        and pred.direction == "UP"
        and (pred.probability > ETH_UP_MAX_PROB or abs(trend_score) > ETH_UP_MAX_TREND)
    ):
        logger.info(
            f"[ETH UP BLOCK] prob={pred.probability*100:.0f}% (max {ETH_UP_MAX_PROB*100:.0f}%) "
            f"|trend|={abs(trend_score):.2f} (max {ETH_UP_MAX_TREND}) — "
            f"blocked by data-driven filter (4/4 historical losses match this profile)"
        )
        return None

"""

GUARD_ANCHOR = "    phase = get_morning_phase()\n    if phase is None:\n        return None\n"
if "[ETH UP BLOCK]" not in text:
    if GUARD_ANCHOR not in text:
        raise SystemExit("FAIL: guard anchor not found")
    text = text.replace(GUARD_ANCHOR, GUARD_ANCHOR + "\n" + GUARD_BLOCK, 1)

p.write_text(text, encoding="utf-8")
print("PATCH OK: ETH UP filter added to morning_strategy.py")
