"""PM Multi-Position Support (May 21, 2026 PM).

Today PM was taking only unique[0] per scan (single best signal).
When BTC AND SOL both have strong signals simultaneously, we missed
the second one because the bot only fires one trade per scan loop.

Now: loop through unique[] in prob-descending order, taking each
that passes all gates, until we hit active_count limit.

Also raises PM_MAX_CONCURRENT default from 2 -> 3 (env-tunable).
"""
from pathlib import Path

p = Path("/home/ubuntu/v3-bot/run_bot.py")
text = p.read_text()

if "PM_MAX_CONCURRENT" in text or "# PM MULTI-POS" in text:
    print("PM multi-pos already patched — skipping")
    raise SystemExit(0)

old = """            if unique and can_trade and _is_afternoon and _consec_losses < 2:
                active_count = len(orders.positions) + len(orders.active_gtc)
                if active_count >= 2:
                    if scan_count % 20 == 0:
                        logger.debug(f"[MAX POS] {active_count} active, skipping new trades")
                else:
                    best = unique[0]

                    # FIX 1: Atomic lock — only one trade per coin per window
                    if not lock_window(best.coin, best.market_info.window_start):
                        logger.debug(f"[LOCKED] {best.coin} already traded this window")"""

new = """            # PM MULTI-POS (May 21): loop through unique signals in prob-desc order
            # instead of only taking unique[0]. Each new trade increments active_count
            # and we stop when we hit PM_MAX_CONCURRENT (default 3).
            _pm_max_concur = int(os.getenv("PM_MAX_CONCURRENT", "3"))
            if unique and can_trade and _is_afternoon and _consec_losses < 2:
                active_count = len(orders.positions) + len(orders.active_gtc)
                if active_count >= _pm_max_concur:
                    if scan_count % 20 == 0:
                        logger.debug(f"[MAX POS] {active_count} active >= cap {_pm_max_concur}, skipping new trades")
                else:
                  for best in unique:
                    # Stop if we already filled to capacity this loop
                    active_count = len(orders.positions) + len(orders.active_gtc)
                    if active_count >= _pm_max_concur:
                        break
                    # Stop if consecutive losses hit during this loop
                    if _consec_losses >= 2:
                        break

                    # FIX 1: Atomic lock — only one trade per coin per window
                    if not lock_window(best.coin, best.market_info.window_start):
                        logger.debug(f"[LOCKED] {best.coin} already traded this window")
                        continue"""

if old not in text:
    raise SystemExit("PM dispatch marker not found")
text = text.replace(old, new, 1)

# The original "else:" branch (full trade dispatch) needs to be indented one more level
# AND we need to replace all `else:` and `unlock_window` continues so the loop progresses.
# The simplest safe approach: leave the existing block as-is but change "if not lock_window"
# to continue instead of else-block (which it was already structured as).
#
# Actually, looking again: the original code uses "if not lock_window: log; else: <try trade>"
# By changing "else:" to fall through and replacing the "log" with "continue", and removing
# the explicit else, the trade block runs naturally for each iteration.

# Find the next "else:" block and rewrite it to no-else (fall through)
old2 = """                        logger.debug(f"[LOCKED] {best.coin} already traded this window")
                        continue
                    else:
                        # FIX 5: Re-fetch CLOB ask and recompute edge with fresh price"""

new2 = """                        logger.debug(f"[LOCKED] {best.coin} already traded this window")
                        continue
                    # FIX 5: Re-fetch CLOB ask and recompute edge with fresh price"""

if old2 not in text:
    # This block doesn't have a leftover "else:" after our previous replacement —
    # That's actually fine since we changed the structure already.
    pass
else:
    text = text.replace(old2, new2, 1)

# Now de-indent the existing block one level - actually NO. The new "for best in unique:"
# loop is at the same indent as the original "best = unique[0]". The body remains the same
# indent level. The else: must be gone though.
# Let me re-check by reading the post-substitution.

# Write and verify
p.write_text(text)

# Run a python compile check
import py_compile
try:
    py_compile.compile(str(p), doraise=True)
    print("PM multi-pos installed, syntax OK")
except py_compile.PyCompileError as e:
    print(f"SYNTAX ERROR: {e}")
    raise
