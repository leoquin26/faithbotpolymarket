"""
V8 Bot — Empirical Trend Trader with Atomic Dedup.

Key changes from V5/run_bot:
- FIX 1: Single atomic traded_this_window lock (threading.Lock + set)
         prevents machine-gunning multiple orders per coin per window.
- FIX 5: Edge computed HERE with fresh CLOB ask at order time,
         not in predictor with stale ask from scan time.
- Predictor is stateless — only returns direction + win_probability.
"""

import os
import force_tor
import sys
import time
import threading
import warnings

warnings.filterwarnings("ignore", message=".*found in sys.modules.*")

from dotenv import load_dotenv
load_dotenv()

import logging
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

from loguru import logger
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import config
import binance_ws
from market_data import get_market_info, MarketInfo
from predictor import Predictor, Prediction
from order_manager import OrderManager

logger.remove()
logger.add(
    sys.stderr,
    level=config.LOG_LEVEL,
    format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | <level>{message}</level>",
)
logger.add(
    "v3_bot.log",
    level="DEBUG",
    format="{time:HH:mm:ss} | {level:<8} | {message}",
    rotation="10 MB",
    retention="3 days",
)

import functools
print = functools.partial(print, flush=True)

# ======================================================================
# FIX 1: Atomic one-trade-per-window lock
# ======================================================================
_trade_lock = threading.Lock()
_traded_set: set = set()


def is_window_locked(coin: str, window_start: int) -> bool:
    key = f"{coin}_{window_start}"
    with _trade_lock:
        return key in _traded_set


def lock_window(coin: str, window_start: int) -> bool:
    """Try to lock this coin+window for trading. Returns True if we got the lock."""
    key = f"{coin}_{window_start}"
    with _trade_lock:
        if key in _traded_set:
            return False
        _traded_set.add(key)
        return True


def cleanup_old_windows():
    """Remove window locks older than 20 minutes to prevent memory leak."""
    now = int(time.time())
    with _trade_lock:
        stale = [k for k in _traded_set if int(k.split("_")[-1]) < now - 1200]
        for k in stale:
            _traded_set.discard(k)


# ======================================================================
# Trading hour filter
# ======================================================================
def is_good_trading_hour() -> tuple:
    if not config.SKIP_NIGHT_HOURS:
        return True, ""
    utc_hour = datetime.now(timezone.utc).hour
    start = config.NIGHT_START_HOUR
    end = config.NIGHT_END_HOUR
    if start > end:
        if utc_hour >= start or utc_hour < end:
            return False, f"[NIGHT] Skipping: {utc_hour}h UTC (night {start}-{end})"
    else:
        if start <= utc_hour < end:
            return False, f"[NIGHT] Skipping: {utc_hour}h UTC (night {start}-{end})"
    return True, ""


def find_arbitrage(info: MarketInfo) -> dict | None:
    combined = info.up_poly_price + info.down_poly_price
    arb_min_profit = float(os.getenv("ARB_MIN_PROFIT", "0.02"))
    if combined < (1.0 - arb_min_profit):
        profit_pct = (1.0 - combined) / combined * 100
        return {
            "coin": info.coin,
            "up_price": info.up_poly_price,
            "down_price": info.down_poly_price,
            "combined": combined,
            "profit_pct": profit_pct,
            "up_token": info.up_token_id,
            "down_token": info.down_token_id,
            "window_start": info.window_start,
        }
    return None


def main():
    issues = config.validate()
    if issues:
        for i in issues:
            print(f"  [ERROR] {i}")
        sys.exit(1)

    binance_ws.start()
    time.sleep(2)

    print("=" * 60)
    print("  V8 BOT — Empirical Trend Trader")
    print("=" * 60)
    print(f"  Mode:         {'DRY RUN' if config.DRY_RUN else 'LIVE TRADING'}")
    print(f"  Coins:        {', '.join(config.SYMBOLS.keys())}")
    print(f"  Strategy:     Trend + Momentum + Distance (no GBM MC)")
    print(f"  Entry zone:   {config.ENTRY_MIN*100:.0f}c - {config.ENTRY_MAX*100:.0f}c")
    print(f"  Min edge:     {config.MIN_EDGE*100:.0f}%")
    print(f"  Min win prob: {getattr(config, 'MIN_WIN_PROB', 0.68)*100:.0f}%")
    print(f"  Min distance: {config.MIN_DISTANCE_PCT*100:.2f}%")
    print(f"  Min cross age:{getattr(config, 'MIN_CROSS_AGE', 45)}s")
    print(f"  Warmup:       {getattr(config, 'WARMUP_SEC', 90)}s")
    print(f"  Bankroll:     ${config.BANKROLL_BALANCE:.0f}")
    print(f"  Stop-loss:    {'$' + str(config.DAILY_LOSS_LIMIT) if config.USE_DAILY_STOP_LOSS else 'OFF'}")
    ws_status = "CONNECTED" if binance_ws.is_connected() else "CONNECTING..."
    print(f"  Binance WS:   {ws_status}")
    print("=" * 60)

    predictor = Predictor()
    orders = OrderManager()
    executor = ThreadPoolExecutor(max_workers=4)

    scan_count = 0
    arb_enabled = os.getenv("ARB_ENABLED", "true").lower() == "true"

    try:
        while True:
            scan_count += 1
            now = datetime.now(timezone.utc).strftime("%H:%M:%S")

            ok, reason = is_good_trading_hour()
            if not ok:
                if scan_count % 60 == 1:
                    print(f"[{now}] {reason}")
                time.sleep(config.SCAN_INTERVAL)
                continue

            if orders.active_gtc:
                orders.check_gtc_fills()
                orders.cancel_stale_gtc()

            if scan_count % 100 == 0:
                cleanup_old_windows()

            def scan_coin(coin: str):
                info = get_market_info(coin)
                if not info:
                    return None, None

                if info.time_remaining < config.MIN_TIME_REMAINING:
                    return info, None

                # FIX 1: Check atomic lock BEFORE calling predictor
                if is_window_locked(coin, info.window_start):
                    return info, None
                if orders.is_window_traded(coin, info.window_start):
                    return info, None

                ws_price = binance_ws.get_price(coin)
                if ws_price and ws_price > 0:
                    info.current_crypto_price = ws_price

                realized_vol = binance_ws.get_realized_vol(coin, 180)
                ticks = binance_ws.get_tick_history(coin, 300)

                up_book = {}
                down_book = {}
                try:
                    up_book = orders.get_clob_book(info.up_token_id)
                except Exception:
                    pass
                try:
                    down_book = orders.get_clob_book(info.down_token_id)
                except Exception:
                    pass

                pred = predictor.predict(
                    info,
                    ws_price=info.current_crypto_price,
                    realized_vol=realized_vol,
                    up_ask=up_book.get("ask") or 0.0,
                    down_ask=down_book.get("ask") or 0.0,
                    up_mid=up_book.get("mid") or 0.0,
                    down_mid=down_book.get("mid") or 0.0,
                    up_depth=up_book.get("depth_ratio", 0.0),
                    down_depth=down_book.get("depth_ratio", 0.0),
                    ticks=ticks,
                )
                return info, pred

            futures_map = {executor.submit(scan_coin, c): c for c in config.SYMBOLS}
            predictions = []
            arb_candidates = []

            for future in as_completed(futures_map):
                coin_name = futures_map[future]
                try:
                    info, pred = future.result()
                    if info and arb_enabled:
                        arb = find_arbitrage(info)
                        if arb:
                            arb_candidates.append(arb)
                    if pred:
                        predictions.append(pred)
                except Exception as e:
                    logger.error(f"Scan error for {coin_name}: {e}")

            if arb_candidates:
                best = max(arb_candidates, key=lambda a: a["profit_pct"])
                print(f"\n[{now}] #{scan_count} ARB: {best['coin']} UP {best['up_price']*100:.0f}c + DOWN {best['down_price']*100:.0f}c = {best['combined']*100:.0f}c | Profit: {best['profit_pct']:.1f}%")
                orders.execute_arb(
                    best["coin"], best["up_token"], best["down_token"],
                    best["up_price"], best["down_price"], best["window_start"],
                )
                time.sleep(config.SCAN_INTERVAL)
                continue

            actionable = [
                p for p in predictions
                if p.confidence in ("HIGH", "MEDIUM")
                and p.edge >= config.MIN_EDGE
            ]

            seen_coins = set()
            unique = []
            for p in sorted(actionable, key=lambda x: x.probability, reverse=True):
                if p.coin not in seen_coins:
                    unique.append(p)
                    seen_coins.add(p.coin)

            if unique:
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
                            elif clob_ask < config.ENTRY_MIN or clob_ask > config.ENTRY_MAX:
                                logger.info(
                                    f"[CLOB RANGE] {best.coin} {best.direction}: "
                                    f"CLOB ask={clob_ask*100:.0f}c outside "
                                    f"{config.ENTRY_MIN*100:.0f}-{config.ENTRY_MAX*100:.0f}c"
                                )
                            else:
                                print(
                                    f"\n[{now}] #{scan_count} TRADE -> {best.coin} {best.direction} | "
                                    f"Prob: {best.probability:.0%} | Ask: {clob_ask*100:.0f}c | "
                                    f"Edge: {real_edge*100:.1f}% | Depth: {best.depth_ratio:.1f}x | "
                                    f"{best.confidence}"
                                )
                                print(f"  {best.reasoning}")
                                orders.place_bet(best)
                        else:
                            logger.info(f"[NO ASK] {best.coin} {best.direction}: no valid CLOB ask at execution")
            else:
                if scan_count % 20 == 0:
                    active_pos = list(orders.positions.keys())
                    gtc_coins = [i["coin"] for i in orders.active_gtc.values()]

                    now_ts = int(time.time())
                    window_sec = 900
                    current_window = (now_ts // window_sec) * window_sec
                    window_age = now_ts - current_window
                    phase = f"SCANNING ({window_age}s)"

                    ws_coins = sum(1 for c in config.SYMBOLS if binance_ws.get_price(c))
                    print(
                        f"[{now}] #{scan_count} {phase} | "
                        f"WS: {ws_coins}/{len(config.SYMBOLS)} | "
                        f"Pos: {active_pos or 'none'} | "
                        f"Trades: {orders.daily_trades}"
                    )

            current_time = int(time.time())
            expired = []
            for coin, pos in orders.positions.items():
                ws = pos.get("window_start", 0)
                if ws > 0 and current_time > ws + 900 + 60:
                    expired.append(coin)
            for coin in expired:
                pos = orders.positions.pop(coin)
                logger.info(f"[RESOLVED] {coin} {pos['side']} position expired (window ended)")

            time.sleep(config.SCAN_INTERVAL)

    except KeyboardInterrupt:
        print("\n\n  V8 Bot stopped by user.")
        if orders.active_gtc:
            print(f"  Cancelling {len(orders.active_gtc)} pending GTC orders...")
            for oid in list(orders.active_gtc):
                try:
                    orders.client.cancel(oid)
                except Exception:
                    pass
        print("  Goodbye!")
        sys.exit(0)
    except Exception as e:
        logger.error(f"[FATAL] Unhandled exception: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise


if __name__ == "__main__":
    import traceback
    MAX_RESTARTS = 50
    restarts = 0
    while restarts < MAX_RESTARTS:
        try:
            main()
            break
        except KeyboardInterrupt:
            break
        except Exception as e:
            restarts += 1
            msg = f"[CRASH #{restarts}] {e}"
            print(f"\n  {msg}")
            traceback.print_exc()
            try:
                from loguru import logger as _lg
                _lg.error(msg)
                _lg.error(traceback.format_exc())
            except Exception:
                pass
            if restarts < MAX_RESTARTS:
                import time as _t
                wait = min(10, restarts * 2)
                print(f"  Restarting in {wait}s...")
                _t.sleep(wait)
            else:
                print("  Max restarts reached. Exiting.")
