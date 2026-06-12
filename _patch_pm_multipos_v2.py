"""PM Multi-Position Support v2 (May 21, 2026 PM) - CORRECTED INDENTATION.

Replace the entire PM trade dispatch block atomically. Loop through unique[]
instead of taking unique[0]. Re-indent properly.
"""
from pathlib import Path

p = Path("/home/ubuntu/v3-bot/run_bot.py")
text = p.read_text()

if "PM MULTI-POS" in text:
    print("PM multi-pos already present — skipping")
    raise SystemExit(0)

# Exact original block, end-to-end
old = """            if unique and can_trade and _is_afternoon and _consec_losses < 2:
                active_count = len(orders.positions) + len(orders.active_gtc)
                if active_count >= 2:
                    if scan_count % 20 == 0:
                        logger.debug(f"[MAX POS] {active_count} active, skipping new trades")
                else:
                    best = unique[0]

                    # FIX 1: Atomic lock — only one trade per coin per window
                    if not lock_window(best.coin, best.market_info.window_start):
                        logger.debug(f"[LOCKED] {best.coin} already traded this window")
                    else:
                        # FIX 5: Re-fetch CLOB ask and recompute edge with fresh price
                        clob_ask = orders.get_clob_ask(best.token_id)
                        if clob_ask is not None:
                            real_edge = best.probability - clob_ask
                            best.entry_price = clob_ask
                            best.edge = real_edge

                            if real_edge < config.MIN_EDGE:
                                logger.info(
                                    f"[CLOB REJECT] {best.coin} {best.direction}: "
                                    f"CLOB ask={clob_ask*100:.0f}c prob={best.probability:.0%} "
                                    f"real_edge={real_edge*100:.1f}% < {config.MIN_EDGE*100:.0f}%"
                                )
                                unlock_window(best.coin, best.market_info.window_start)
                            elif clob_ask < config.ENTRY_MIN or clob_ask > config.ENTRY_MAX:
                                logger.info(
                                    f"[CLOB RANGE] {best.coin} {best.direction}: "
                                    f"CLOB ask={clob_ask*100:.0f}c outside "
                                    f"{config.ENTRY_MIN*100:.0f}-{config.ENTRY_MAX*100:.0f}c"
                                )
                                unlock_window(best.coin, best.market_info.window_start)
                            elif _is_afternoon and clob_ask > config.PM_ENTRY_MAX:
                                # PM R:R collapses above this price (backfill: 66-69c R:R=0.49, >=69c R:R=0.35)
                                logger.info(
                                    f"[PM ENTRY CAP] {best.coin} {best.direction}: "
                                    f"CLOB ask={clob_ask*100:.0f}c > PM cap {config.PM_ENTRY_MAX*100:.0f}c — R:R too thin"
                                )
                                unlock_window(best.coin, best.market_info.window_start)
                            elif config.TRAP_BAND_MIN <= clob_ask <= config.TRAP_BAND_MAX:
                                # Option A apr28: 60-63c entry band has 47% WR / R:R 0.75
                                # in our 8-day backfill — confirmed structural loser.
                                logger.info(
                                    f"[TRAP BAND] {best.coin} {best.direction}: "
                                    f"CLOB ask={clob_ask*100:.0f}c in trap band "
                                    f"{config.TRAP_BAND_MIN*100:.0f}-{config.TRAP_BAND_MAX*100:.0f}c (47% WR)"
                                )
                                unlock_window(best.coin, best.market_info.window_start)
                            elif _is_afternoon and best.coin in config.PM_BLOCKED_COINS:
                                # Option A apr28: XRP is 50% WR / -$3.80 net — skip in PM.
                                logger.info(
                                    f"[PM COIN BLOCK] {best.coin} {best.direction}: "
                                    f"{best.coin} blocked in PM (50% WR / negative EV)"
                                )
                                unlock_window(best.coin, best.market_info.window_start)
                            else:
                                print(
                                    f"\\n[{now}] #{scan_count} TRADE -> {best.coin} {best.direction} | "
                                    f"Prob: {best.probability:.0%} | Ask: {clob_ask*100:.0f}c | "
                                    f"Edge: {real_edge*100:.1f}% | Depth: {best.depth_ratio:.1f}x | "
                                    f"{best.confidence}"
                                )
                                print(f"  {best.reasoning}")
                                filled = orders.place_bet(best)
                                if not filled:
                                    unlock_window(best.coin, best.market_info.window_start)
                                    logger.info(f"[UNLOCK] {best.coin}: order failed, window unlocked for retry")
                        else:
                            logger.info(f"[NO ASK] {best.coin} {best.direction}: no valid CLOB ask at execution")
                            unlock_window(best.coin, best.market_info.window_start)"""

# Replace with multi-position loop. The body is indented at 16 spaces (4 more than before)
# because we now nest it inside a for-loop.
new = """            # ── PM MULTI-POS (May 21): loop through unique signals in prob-desc order ──
            # Previously took only unique[0]. When BTC AND SOL both signal simultaneously,
            # we now try both up to PM_MAX_CONCURRENT total open positions.
            _pm_max_concur = int(os.getenv("PM_MAX_CONCURRENT", "3"))
            if unique and can_trade and _is_afternoon and _consec_losses < 2:
                active_count = len(orders.positions) + len(orders.active_gtc)
                if active_count >= _pm_max_concur:
                    if scan_count % 20 == 0:
                        logger.debug(f"[MAX POS] {active_count} active >= cap {_pm_max_concur}")
                else:
                    for best in unique:
                        # Stop conditions checked at start of each iteration
                        _ac = len(orders.positions) + len(orders.active_gtc)
                        if _ac >= _pm_max_concur:
                            break
                        if _consec_losses >= 2:
                            break

                        # FIX 1: Atomic lock — only one trade per coin per window
                        if not lock_window(best.coin, best.market_info.window_start):
                            logger.debug(f"[LOCKED] {best.coin} already traded this window")
                            continue

                        # FIX 5: Re-fetch CLOB ask and recompute edge with fresh price
                        clob_ask = orders.get_clob_ask(best.token_id)
                        if clob_ask is None:
                            logger.info(f"[NO ASK] {best.coin} {best.direction}: no valid CLOB ask at execution")
                            unlock_window(best.coin, best.market_info.window_start)
                            continue

                        real_edge = best.probability - clob_ask
                        best.entry_price = clob_ask
                        best.edge = real_edge

                        if real_edge < config.MIN_EDGE:
                            logger.info(
                                f"[CLOB REJECT] {best.coin} {best.direction}: "
                                f"CLOB ask={clob_ask*100:.0f}c prob={best.probability:.0%} "
                                f"real_edge={real_edge*100:.1f}% < {config.MIN_EDGE*100:.0f}%"
                            )
                            unlock_window(best.coin, best.market_info.window_start)
                            continue
                        if clob_ask < config.ENTRY_MIN or clob_ask > config.ENTRY_MAX:
                            logger.info(
                                f"[CLOB RANGE] {best.coin} {best.direction}: "
                                f"CLOB ask={clob_ask*100:.0f}c outside "
                                f"{config.ENTRY_MIN*100:.0f}-{config.ENTRY_MAX*100:.0f}c"
                            )
                            unlock_window(best.coin, best.market_info.window_start)
                            continue
                        if _is_afternoon and clob_ask > config.PM_ENTRY_MAX:
                            logger.info(
                                f"[PM ENTRY CAP] {best.coin} {best.direction}: "
                                f"CLOB ask={clob_ask*100:.0f}c > PM cap {config.PM_ENTRY_MAX*100:.0f}c — R:R too thin"
                            )
                            unlock_window(best.coin, best.market_info.window_start)
                            continue
                        if config.TRAP_BAND_MIN <= clob_ask <= config.TRAP_BAND_MAX:
                            logger.info(
                                f"[TRAP BAND] {best.coin} {best.direction}: "
                                f"CLOB ask={clob_ask*100:.0f}c in trap band "
                                f"{config.TRAP_BAND_MIN*100:.0f}-{config.TRAP_BAND_MAX*100:.0f}c (47% WR)"
                            )
                            unlock_window(best.coin, best.market_info.window_start)
                            continue
                        if _is_afternoon and best.coin in config.PM_BLOCKED_COINS:
                            logger.info(
                                f"[PM COIN BLOCK] {best.coin} {best.direction}: "
                                f"{best.coin} blocked in PM (50% WR / negative EV)"
                            )
                            unlock_window(best.coin, best.market_info.window_start)
                            continue

                        print(
                            f"\\n[{now}] #{scan_count} TRADE -> {best.coin} {best.direction} | "
                            f"Prob: {best.probability:.0%} | Ask: {clob_ask*100:.0f}c | "
                            f"Edge: {real_edge*100:.1f}% | Depth: {best.depth_ratio:.1f}x | "
                            f"{best.confidence}"
                        )
                        print(f"  {best.reasoning}")
                        filled = orders.place_bet(best)
                        if not filled:
                            unlock_window(best.coin, best.market_info.window_start)
                            logger.info(f"[UNLOCK] {best.coin}: order failed, window unlocked for retry")"""

if old not in text:
    raise SystemExit("EXACT PM block marker not found — aborting")

text = text.replace(old, new, 1)
p.write_text(text)

import py_compile
try:
    py_compile.compile(str(p), doraise=True)
    print("PM multi-pos v2 installed, syntax OK")
except py_compile.PyCompileError as e:
    print(f"SYNTAX ERROR: {e}")
    raise
