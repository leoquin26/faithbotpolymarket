#!/usr/bin/env python3
"""Precise Chainlink strike snapshotter — THE FIX for the 43% Binance-fallback bug.
At each 15m window boundary it captures the Chainlink data-stream price AT the
boundary (the exact strike Polymarket settles on) and pre-populates
data/strike_cache.json, so the bot/research NEVER fall back to the wrong Binance
strike. Result: drift is measured against the correct reference every window."""
import force_tor, chainlink_ws, time, sys
import poly_resolution as pr
from loguru import logger

logger.remove()
logger.add(sys.stdout, format="{time:HH:mm:ss} | {message}")
logger.add("strike_snap.log", rotation="10 MB", format="{time:YYYY-MM-DD HH:mm:ss} | {message}")

COINS = ["BTC", "ETH", "SOL"]
chainlink_ws.start()
time.sleep(6)
logger.info(f"=== STRIKE SNAPSHOTTER start | {','.join(COINS)} | captures exact chainlink boundary strike ===")
done = set()

while True:
    now = time.time()
    ws = int(now // 900) * 900
    age = now - ws
    if age <= 25:                                  # capture window: first 25s after boundary
        for coin in COINS:
            if (coin, ws) in done:
                continue
            ticks = chainlink_ws.get_ticks(coin, 40)
            if not ticks:
                continue
            ts0, px0 = min(ticks, key=lambda t: abs(t[0] - ws))   # tick closest to the boundary
            if px0 and px0 > 0 and abs(ts0 - ws) <= 10:
                try:
                    slug = pr.market_slug(coin, ws, "15m")
                    cache = pr._load_strike_cache()
                    cache[slug] = {"strike": float(px0), "source": "chainlink_window_open"}
                    pr._save_strike_cache(cache)
                    done.add((coin, ws))
                    logger.info(f"[SNAP] {coin} {slug} strike={px0} "
                                f"(tick {abs(ts0-ws):.1f}s from boundary)")
                except Exception as e:
                    logger.warning(f"[SNAP] {coin} write failed: {e}")
    if len(done) > 300:
        done = set(list(done)[-60:])
    time.sleep(1)
