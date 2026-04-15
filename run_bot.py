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
import telegram_notifier as tg

from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import config
import morning_strategy as morn
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

# Persistent daily log (survives restarts, never loses data)
import os as _os
_log_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "logs")
_os.makedirs(_log_dir, exist_ok=True)
logger.add(
    _os.path.join(_log_dir, "bot_{time:YYYY-MM-DD}.log"),
    rotation="50 MB",
    retention="30 days",
    level="DEBUG",
    format="{time:HH:mm:ss} | {level:<8} | {message}",
    enqueue=True,
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


def unlock_window(coin: str, window_start: int):
    """Release a window lock (e.g. when FOK order fails)."""
    key = f"{coin}_{window_start}"
    with _trade_lock:
        _traded_set.discard(key)


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
    """Returns (can_trade, message). Uses Lima time (UTC-5) directly."""
    if not config.SKIP_NIGHT_HOURS:
        return True, ""
    from zoneinfo import ZoneInfo
    lima = ZoneInfo("America/Lima")
    now_lima = datetime.now(lima)
    lima_hour = now_lima.hour
    weekday = now_lima.weekday()
    if weekday >= 5:
        day_name = "Saturday" if weekday == 5 else "Sunday"
        return False, f"[WEEKEND] {day_name} {lima_hour}:00 Lima — no trading on weekends"
    if lima_hour < 9 or lima_hour >= 17:
        return False, f"[OFF HOURS] {lima_hour}:{now_lima.minute:02d} Lima — trade window 9am-5pm Lima (scanning active)"
    return True, ""


def find_arbitrage(info: MarketInfo, up_ask: float = 0, down_ask: float = 0) -> dict | None:
    ua = up_ask if up_ask and up_ask > 0.01 else info.up_poly_price
    da = down_ask if down_ask and down_ask > 0.01 else info.down_poly_price
    if ua <= 0.20 or da <= 0.20:
        return None
    combined = ua + da
    fee_pct = 0.02
    net_payout = 1.0 - fee_pct
    arb_min_profit = float(os.getenv("ARB_MIN_PROFIT", "0.015"))
    if combined < (net_payout - arb_min_profit):
        profit_pct = (net_payout - combined) / combined * 100
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
    print("  V12 BOT — Black-Scholes + Morning Strategy")
    print("=" * 60)
    print(f"  Mode:         {'DRY RUN' if config.DRY_RUN else 'LIVE TRADING'}")
    print(f"  Coins:        {', '.join(config.SYMBOLS.keys())}")
    print(f"  Strategy:     Black-Scholes + EWMA + Momentum (logit-space)")
    print(f"  Entry zone:   {config.ENTRY_MIN*100:.0f}c - {config.ENTRY_MAX*100:.0f}c")
    print(f"  Min edge:     {config.MIN_EDGE*100:.0f}%")
    print(f"  Min win prob: {getattr(config, 'MIN_WIN_PROB', 0.68)*100:.0f}%")
    print(f"  Min distance: {config.MIN_DISTANCE_PCT*100:.2f}%")
    print(f"  Predictor:    Black-Scholes + EWMA(0.94) + Momentum")
    print(f"  Warmup:       {getattr(config, 'WARMUP_SEC', 90)}s")
    print(f"  Bankroll:     ${config.BANKROLL_BALANCE:.0f}")
    print(f"  Stop-loss:    {'$' + str(config.DAILY_LOSS_LIMIT) if config.USE_DAILY_STOP_LOSS else 'OFF'}")
    ws_status = "CONNECTED" if binance_ws.is_connected() else "CONNECTING..."
    print(f"  Binance WS:   {ws_status}")
    print("=" * 60)

    predictor = Predictor()
    orders = OrderManager()
    executor = ThreadPoolExecutor(max_workers=4)

    tg.test()
    tg.notify_startup()

    scan_count = 0
    arb_enabled = os.getenv("ARB_ENABLED", "true").lower() == "true"

    try:
        while True:
            scan_count += 1
            now = datetime.now(timezone.utc).strftime("%H:%M:%S")

            can_trade, night_reason = is_good_trading_hour()
            if not can_trade and scan_count % 200 == 1:
                print(f"[{now}] {night_reason}")

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
                    if info and arb_enabled and not is_window_locked(info.coin, info.window_start):
                        try:
                            ub = orders.get_clob_book(info.up_token_id)
                            db = orders.get_clob_book(info.down_token_id)
                            arb = find_arbitrage(info, up_ask=ub.get("ask") or 0, down_ask=db.get("ask") or 0)
                            if arb:
                                arb_candidates.append(arb)
                        except Exception:
                            pass
                    if pred:
                        predictions.append(pred)
                except Exception as e:
                    logger.error(f"Scan error for {coin_name}: {e}")

            if arb_candidates and can_trade:
                best = max(arb_candidates, key=lambda a: a["profit_pct"])
                print(f"\n[{now}] #{scan_count} ARB: {best['coin']} UP {best['up_price']*100:.0f}c + DOWN {best['down_price']*100:.0f}c = {best['combined']*100:.0f}c | Profit: {best['profit_pct']:.1f}%")
                orders.execute_arb(
                    best["coin"], best["up_token"], best["down_token"],
                    best["up_price"], best["down_price"], best["window_start"],
                )
                time.sleep(config.SCAN_INTERVAL)
                continue

            # ── Time phase detection ──
            from zoneinfo import ZoneInfo as _ZI
            _lima_now = datetime.now(_ZI("America/Lima"))
            _is_morning = 9 <= _lima_now.hour < 14
            _is_afternoon = 14 <= _lima_now.hour < 17

            # ── Morning strategy (9am-2pm): stricter filters, half Kelly ──
            if _is_morning and can_trade and predictions:
                morning_approved = []
                for p in predictions:
                    if p.confidence not in ("HIGH", "MEDIUM"):
                        continue
                    if p.edge < config.MIN_EDGE:
                        continue
                    filtered = morn.filter_morning_signal(p, p.trend_score)
                    if filtered:
                        morning_approved.append(filtered)

                if morning_approved:
                    active_count = len(orders.positions) + len(orders.active_gtc)
                    if active_count < 1:  # max 1 position in morning (conservative)
                        best_m = max(morning_approved, key=lambda x: x.probability)
                        if not lock_window(best_m.coin, best_m.market_info.window_start):
                            logger.debug(f"[LOCKED] {best_m.coin} already traded this window")
                        else:
                            clob_ask = orders.get_clob_ask(best_m.token_id)
                            if clob_ask is not None:
                                real_edge = best_m.probability - clob_ask
                                best_m.entry_price = clob_ask
                                best_m.edge = real_edge

                                if real_edge < config.MIN_EDGE:
                                    logger.info(f"[MORNING REJECT] {best_m.coin}: edge={real_edge*100:.1f}% too low")
                                    unlock_window(best_m.coin, best_m.market_info.window_start)
                                elif clob_ask < config.ENTRY_MIN or clob_ask > config.ENTRY_MAX:
                                    logger.info(f"[MORNING RANGE] {best_m.coin}: ask={clob_ask*100:.0f}c outside range")
                                    unlock_window(best_m.coin, best_m.market_info.window_start)
                                else:
                                    phase = morn.get_morning_phase()
                                    logger.info(
                                        f"[MORNING TRADE] P{phase} {best_m.coin} {best_m.direction} | "
                                        f"Prob={best_m.probability:.0%} | Ask={clob_ask*100:.0f}c | "
                                        f"Edge={real_edge*100:.1f}% | {best_m.confidence}"
                                    )
                                    # Half Kelly for morning trades
                                    import os as _os2
                                    _orig_frac = _os2.environ.get("KELLY_FRACTION", "0.25")
                                    _os2.environ["KELLY_FRACTION"] = str(float(_orig_frac) * 0.5)
                                    filled_m = orders.place_bet(best_m)
                                    _os2.environ["KELLY_FRACTION"] = _orig_frac
                                    if not filled_m:
                                        unlock_window(best_m.coin, best_m.market_info.window_start)
                                        logger.info(f"[UNLOCK] {best_m.coin}: morning order failed")
                            else:
                                logger.info(f"[MORNING NO ASK] {best_m.coin}: no valid CLOB ask")
                                unlock_window(best_m.coin, best_m.market_info.window_start)

            # ── Afternoon strategy (2pm-5pm): main predictor, unchanged ──
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

            if unique and can_trade and _is_afternoon:
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
                            else:
                                print(
                                    f"\n[{now}] #{scan_count} TRADE -> {best.coin} {best.direction} | "
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
                            unlock_window(best.coin, best.market_info.window_start)
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
                side = pos.get("side", "?")
                entry = pos.get("entry_price", 0)
                shares = pos.get("shares", 0)
                cost = entry * shares
                payout = shares * 1.0

                won = False
                try:
                    final_price = binance_ws.get_price(coin)
                    strike = pos.get("strike", 0)
                    if strike > 0 and final_price > 0:
                        went_up = final_price > strike
                        won = (side == "UP" and went_up) or (side == "DOWN" and not went_up)
                except Exception:
                    pass

                if won:
                    pnl = payout - cost
                    logger.info(f"[WIN] {coin} {side} | +${pnl:.2f} | Entry: {entry*100:.0f}c x{shares} | Payout: ${payout:.2f}")
                    predictor.record_outcome(True)
                    tg.notify_result(coin, side, True, cost, payout)
                else:
                    logger.info(f"[LOSS] {coin} {side} | -${cost:.2f} | Entry: {entry*100:.0f}c x{shares}")
                    predictor.record_outcome(False)
                    tg.notify_result(coin, side, False, cost)

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
