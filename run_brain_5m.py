"""
5-Minute Brain Bot (Apr 28) — runs alongside the 15m main bot.

Architecture: REUSES the same engine modules (predictor, order_manager,
exhaustion_detector, telegram_notifier, market_data, binance_ws) so it
benefits from every existing fix. Differences are configuration-only:

  - 5m markets (Polymarket slug pattern: {coin}-updown-5m-{ts})
  - 5m windows: 300s instead of 900s (predictor reads info.timeframe)
  - Fixed test sizing: $3 per bet via OrderManager(force_size_usd=...)
  - Tighter filters: MIN_EDGE 15%, MIN_TREND 0.80
  - Separate traded_windows_5m.json so 15m bot's locks are untouched
  - Daily loss cap $5 (separate from 15m bot's cap)
  - Morning hours only initially (9-12 Lima)
  - BTC + SOL only initially (configurable via M5_COINS)

Process isolation:
  - Runs as its own python process (separate PID)
  - Has its own binance_ws subscription (doubles WS load, negligible)
  - Has its own predictor instance with its own EWMA/momentum state
  - Has its own OrderManager instance (separate state, shared CLOB wallet)

Bankroll safety:
  - All 5m bets are tagged in logs/Telegram with [5M] prefix
  - $5 daily loss cap is a HARD STOP — when hit, loop sleeps until
    midnight Lima.
  - Test-week sizing is FIXED at $3, ignoring Kelly (which would be
    too aggressive on 5m's noisier signals).

Resolution: handled inline (5min + 60s grace, slug uses '5m' timeframe).

Failure isolation:
  - 5m bot crashes do not affect 15m bot.
  - Wallet balance contention is mitigated because at $3-fixed sizing,
    even 10 simultaneous 5m bets total $30 — well under bankroll.
"""
from __future__ import annotations

import os
import sys
import time
import threading
import warnings

warnings.filterwarnings("ignore", message=".*found in sys.modules.*")

import force_tor  # noqa: F401  (sets up SOCKS proxy for CLOB calls)

from dotenv import load_dotenv
load_dotenv()

import logging
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

from loguru import logger

try:
    from analytics import event_logger as _alog
except Exception:
    _alog = None

import telegram_notifier as tg
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import config
import binance_ws
from market_data import get_market_info, MarketInfo
from predictor import Predictor, Prediction
import exhaustion_detector as exhaust
from order_manager import OrderManager

# ──────────────────────────────────────────────────────────────────
# Logging setup — separate sinks so 5m and 15m logs are distinguishable.
# stderr goes to v3_bot_5m.log via shell redirection, just like 15m bot.
# ──────────────────────────────────────────────────────────────────
logger.remove()
logger.add(
    sys.stderr,
    level="DEBUG",
    format="{time:HH:mm:ss} | {level:<8} | [5M] {message}",
)

import os as _os
_log_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "logs")
_os.makedirs(_log_dir, exist_ok=True)
logger.add(
    _os.path.join(_log_dir, "bot_5m_{time:YYYY-MM-DD}.log"),
    rotation="50 MB",
    retention="14 days",
    level="DEBUG",
    format="{time:HH:mm:ss} | {level:<8} | {message}",
    enqueue=True,
)

import functools
print = functools.partial(print, flush=True)


# ──────────────────────────────────────────────────────────────────
# Per-coin window lock (in-memory; OrderManager handles disk persistence)
# Same pattern as 15m bot but isolated.
# ──────────────────────────────────────────────────────────────────
_trade_lock = threading.Lock()
_traded_set: set[tuple[str, int]] = set()


def is_window_locked(coin: str, window_start: int) -> bool:
    with _trade_lock:
        return (coin, window_start) in _traded_set


def lock_window(coin: str, window_start: int) -> bool:
    with _trade_lock:
        key = (coin, window_start)
        if key in _traded_set:
            return False
        _traded_set.add(key)
        return True


def unlock_window(coin: str, window_start: int):
    with _trade_lock:
        _traded_set.discard((coin, window_start))


def cleanup_old_windows():
    now = int(time.time())
    cutoff = now - 1800  # 30 minutes
    with _trade_lock:
        stale = {(c, w) for (c, w) in _traded_set if w < cutoff}
        _traded_set.difference_update(stale)


# ──────────────────────────────────────────────────────────────────
# Trading hours gate — 5m runs morning-only initially
# ──────────────────────────────────────────────────────────────────
def is_5m_trading_hour() -> tuple[bool, str]:
    """Return (can_trade, reason)."""
    from zoneinfo import ZoneInfo
    lima = datetime.now(ZoneInfo("America/Lima"))
    weekday = lima.weekday()  # 0=Mon, 6=Sun

    # Match 15m bot's weekend rule: Fri 17:00 → Sun all → Mon <09:00 blocked.
    if weekday == 4 and lima.hour >= 17:
        return False, "weekend mode (Fri PM -> Mon)"
    if weekday in (5, 6):
        return False, "weekend mode (Sat/Sun)"
    if weekday == 0 and lima.hour < 9:
        return False, "weekend mode (Mon pre-9am)"

    if not (config.M5_TRADE_HOURS_START <= lima.hour < config.M5_TRADE_HOURS_END):
        return False, f"outside 5m hours {config.M5_TRADE_HOURS_START:02d}-{config.M5_TRADE_HOURS_END:02d} Lima (now {lima.hour:02d}:{lima.minute:02d})"

    return True, ""


# ──────────────────────────────────────────────────────────────────
# Inline resolution for 5m positions (5min window + 60s grace)
# ──────────────────────────────────────────────────────────────────
def resolve_expired_positions(orders: OrderManager, predictor: Predictor):
    import ast as _ast
    current_time = int(time.time())
    expired = []
    for coin, pos in list(orders.positions.items()):
        ws = pos.get("window_start", 0)
        if ws > 0 and current_time > ws + 300 + 60:
            expired.append(coin)

    for coin in expired:
        pos = orders.positions.pop(coin)
        side = pos.get("side", "?")
        entry = pos.get("entry_price", 0)
        shares = pos.get("shares", 0)
        cost = entry * shares
        payout = shares * 1.0

        won = False
        token_id = pos.get("token_id", "")
        ws = pos.get("window_start", 0)
        _resolved = False
        _deferred = pos.get("_resolve_deferred", 0)

        if token_id and ws > 0:
            _slug = f"{coin.lower()}-updown-5m-{ws}"
            _http = orders._get_direct_http()
            for _attempt in range(8):  # 8 * 30s = 4 min max wait (vs 10 for 15m)
                try:
                    _resp = _http.get(
                        f"https://gamma-api.polymarket.com/events?slug={_slug}",
                        timeout=5,
                    )
                    if _resp.status_code == 200:
                        _data = _resp.json()
                        if _data and _data[0].get("markets"):
                            _mkt = _data[0]["markets"][0]
                            _op = _mkt.get("outcomePrices", [])
                            if isinstance(_op, str):
                                _op = _ast.literal_eval(_op)
                            _outs = _mkt.get("outcomes", [])
                            if isinstance(_outs, str):
                                _outs = _ast.literal_eval(_outs)
                            _target_label = "Up" if side == "UP" else "Down"
                            _idx = _outs.index(_target_label) if _target_label in _outs else -1

                            if len(_op) == 2 and _idx >= 0:
                                _price = float(_op[_idx])
                                if _price >= 0.98:
                                    won = True
                                    _resolved = True
                                elif _price <= 0.02:
                                    won = False
                                    _resolved = True
                                if _resolved:
                                    logger.info(
                                        f"[RESOLVE POLY] {coin} {side}: outcomePrice={_price:.4f} "
                                        f"(outcomes={_outs} prices={_op}) -> "
                                        f"{'WIN' if won else 'LOSS'} (attempt {_attempt+1})"
                                    )
                                    break
                except Exception as _e:
                    logger.debug(f"[RESOLVE ERROR] {coin} attempt {_attempt+1}: {_e}")
                if _attempt < 7 and not _resolved:
                    time.sleep(30)

        if not _resolved:
            if _deferred < 1:
                pos["_resolve_deferred"] = _deferred + 1
                orders.positions[coin] = pos
                logger.warning(f"[RESOLVE DEFERRED] {coin} {side}: not resolved after 4min (defer #1)")
                continue
            else:
                try:
                    final_price = binance_ws.get_price(coin)
                    strike = pos.get("strike", 0)
                    if strike > 0 and final_price > 0:
                        went_up = final_price > strike
                        won = (side == "UP" and went_up) or (side == "DOWN" and not went_up)
                    logger.warning(
                        f"[RESOLVE BINANCE FALLBACK] {coin} {side}: price={final_price:.2f} "
                        f"strike={strike:.2f} -> {'WIN' if won else 'LOSS'} (last resort)"
                    )
                except Exception:
                    pass

        # Analytics
        try:
            if _alog is not None:
                _alog.log_resolved(
                    trade_id=pos.get("trade_id"),
                    coin=coin, side=side,
                    window_start=int(pos.get("window_start", 0) or 0),
                    won=bool(won),
                    cost=float(cost or 0),
                    payout=float(payout or 0),
                    pnl=float((payout - cost) if won else -cost),
                    phase="5M",
                    resolution_source="live",
                )
        except Exception:
            pass

        if won:
            pnl = payout - cost
            logger.info(f"[WIN 5M] {coin} {side} | +${pnl:.2f} | Entry: {entry*100:.0f}c x{shares}")
            tg.notify_result(f"[5M] {coin}", side, True, cost, payout)
            orders.daily_wins += pnl
        else:
            logger.info(f"[LOSS 5M] {coin} {side} | -${cost:.2f} | Entry: {entry*100:.0f}c x{shares}")
            tg.notify_result(f"[5M] {coin}", side, False, cost, 0)
            orders.daily_losses += cost


# ──────────────────────────────────────────────────────────────────
# Main loop
# ──────────────────────────────────────────────────────────────────
def main():
    if not config.M5_ENABLED:
        print("[5M] M5_ENABLED=0 — exiting. Set M5_ENABLED=1 in .env to enable.")
        sys.exit(0)

    issues = config.validate()
    if issues:
        for i in issues:
            print(f"  [ERROR] {i}")
        sys.exit(1)

    binance_ws.start()
    time.sleep(2)

    print("=" * 60)
    print("  5M BOT — Test Week ($3 fixed sizing)")
    print("=" * 60)
    print(f"  Coins:        {', '.join(config.M5_COINS)}")
    print(f"  Hours:        {config.M5_TRADE_HOURS_START:02d}:00-{config.M5_TRADE_HOURS_END:02d}:00 Lima")
    print(f"  Bet size:     ${config.M5_TEST_SIZE_USD:.2f} (FIXED, Kelly bypassed)")
    print(f"  Min edge:     {config.M5_MIN_EDGE*100:.0f}%")
    print(f"  Min trend:    {config.M5_MIN_TREND:.2f}")
    print(f"  Daily loss:   ${config.M5_DAILY_LOSS_CAP:.2f} hard stop")
    print(f"  Entry zone:   {config.M5_ENTRY_MIN*100:.0f}c-{config.M5_ENTRY_MAX*100:.0f}c")
    print(f"  Max concurr:  {config.M5_MAX_CONCURRENT}")
    print("=" * 60)

    predictor = Predictor()
    orders = OrderManager(
        traded_file="/home/ubuntu/v3-bot/traded_windows_5m.json",
        force_size_usd=config.M5_TEST_SIZE_USD,
        daily_loss_cap=config.M5_DAILY_LOSS_CAP,
        bot_name="5M",
    )
    executor = ThreadPoolExecutor(max_workers=len(config.M5_COINS))

    tg._send("[5M] Bot started — test mode, $3 fixed size")

    scan_count = 0
    daily_loss_alerted = False

    try:
        while True:
            scan_count += 1
            now = datetime.now(timezone.utc).strftime("%H:%M:%S")

            # ── Daily loss hard stop ──
            if orders.daily_losses >= config.M5_DAILY_LOSS_CAP:
                if not daily_loss_alerted:
                    logger.warning(
                        f"[5M DAILY STOP] losses=${orders.daily_losses:.2f} "
                        f">= cap=${config.M5_DAILY_LOSS_CAP:.2f} — sleeping until tomorrow"
                    )
                    tg._send(f"[5M] Daily loss cap hit (${orders.daily_losses:.2f}). Bot paused for the day.")
                    daily_loss_alerted = True
                time.sleep(300)
                # Reset at midnight Lima
                from zoneinfo import ZoneInfo
                lima = datetime.now(ZoneInfo("America/Lima"))
                if lima.hour == 0 and lima.minute < 5:
                    orders.daily_losses = 0.0
                    orders.daily_wins = 0.0
                    orders.daily_trades = 0
                    daily_loss_alerted = False
                    logger.info("[5M DAILY RESET] new day, counters reset")
                continue

            # ── Hours gate ──
            can_trade, reason = is_5m_trading_hour()
            if not can_trade:
                if scan_count % 200 == 1:
                    print(f"[{now}] {reason}")

            # ── Periodic housekeeping ──
            if scan_count % 100 == 0:
                cleanup_old_windows()

            # ── Resolve expired positions every loop ──
            if orders.positions:
                resolve_expired_positions(orders, predictor)

            # ── Don't fire new bets when blocked ──
            if not can_trade:
                time.sleep(config.M5_SCAN_INTERVAL)
                continue

            # ── Concurrency cap ──
            active_count = len(orders.positions) + len(orders.active_gtc)
            if active_count >= config.M5_MAX_CONCURRENT:
                if scan_count % 30 == 0:
                    logger.debug(f"[5M MAX POS] {active_count} active >= cap {config.M5_MAX_CONCURRENT}")
                time.sleep(config.M5_SCAN_INTERVAL)
                continue

            # ── Scan markets ──
            _raw_coin_info = {}

            def scan_coin(coin: str):
                info = get_market_info(coin, timeframe="5m")
                if not info:
                    return None, None

                if info.time_remaining < 1:  # need at least 1 min left in 5m
                    return info, None

                if is_window_locked(coin, info.window_start):
                    return info, None
                if orders.is_window_traded(coin, info.window_start):
                    return info, None

                ws_price = binance_ws.get_price(coin)
                if ws_price and ws_price > 0:
                    info.current_crypto_price = ws_price

                realized_vol = binance_ws.get_realized_vol(coin, 60)  # shorter window for 5m
                ticks = binance_ws.get_tick_history(coin, 120)

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

                _raw_coin_info[coin] = (
                    float(up_book.get("ask") or 0.0),
                    float(down_book.get("ask") or 0.0),
                )

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

            futures_map = {executor.submit(scan_coin, c): c for c in config.M5_COINS}
            predictions = []

            for future in as_completed(futures_map):
                coin_name = futures_map[future]
                try:
                    _info, pred = future.result()
                    if pred:
                        predictions.append(pred)
                except Exception as e:
                    logger.error(f"[5M scan error] {coin_name}: {e}")

            if not predictions:
                time.sleep(config.M5_SCAN_INTERVAL)
                continue

            # ── EXHAUST detector (same engine as 15m) ──
            try:
                _wt = {}
                _pp = {}
                for _c, (_ua, _da) in _raw_coin_info.items():
                    if _ua >= 0.60 and _ua < 0.99:
                        _wt[_c] = "UP"
                        _pp[_c] = _ua
                    elif _da >= 0.60 and _da < 0.99:
                        _wt[_c] = "DOWN"
                        _pp[_c] = _da
                for _p in predictions:
                    _wt[_p.coin] = _p.direction
                    _pp[_p.coin] = (_p.entry_price if _p.entry_price > 0.05 else _p.poly_price)

                _kept = []
                for _p in predictions:
                    _tk = binance_ws.get_tick_history(_p.coin, 120)
                    _res = exhaust.evaluate(_p, _tk, _wt, _pp)
                    _act = _res.get("action", "CLEAN") if isinstance(_res, dict) else "CLEAN"

                    # ── Fix apr28: high-entry override (Option A from audit) ──
                    # Audit on 281 ABSTAIN events showed entries >= 63c blocked
                    # by EXHAUST resolve 71% WIN. Only allow DAMPEN (5m uses
                    # fixed $3 size so DAMPEN flag is informational only —
                    # the prob haircut is the real effect).
                    _entry_now = _p.entry_price if _p.entry_price > 0.05 else _p.poly_price
                    if (_act == "ABSTAIN" and _entry_now >= 0.63
                            and float(_res.get("score", 0) or 0) < 0.65):
                        logger.info(
                            f"[5M EXHAUST OVERRIDE-HIGH-ENTRY] {_p.coin} {_p.direction}: "
                            f"entry={_entry_now*100:.0f}c score={_res.get('score', 0):.2f} -> DAMPEN"
                        )
                        _act = "DAMPEN"

                    if _act == "ABSTAIN":
                        logger.info(
                            f"[EXHAUST BLOCK] {_p.coin} {_p.direction} skipped "
                            f"(score={_res.get('score', 0):.2f})"
                        )
                        continue
                    if _act == "FLIP":
                        _orig = _p.direction
                        _p.direction = "DOWN" if _p.direction == "UP" else "UP"
                        _p.probability = 1.0 - _p.probability
                        _entry = _p.entry_price if _p.entry_price > 0.05 else _p.poly_price
                        _p.edge = _p.probability - _entry
                        logger.info(f"[EXHAUST FLIP] {_p.coin} {_orig}->{_p.direction}")
                    elif _act == "DAMPEN":
                        _pre = _p.probability
                        _p.probability = max(0.01, _p.probability * 0.85)
                        _entry = _p.entry_price if _p.entry_price > 0.05 else _p.poly_price
                        _p.edge = _p.probability - _entry
                        # NOTE: 5m bot uses fixed sizing, so DAMPEN affects probability
                        # only — order_manager's dampen-size cut is a no-op when
                        # force_size_usd is set.
                        logger.info(f"[EXHAUST DAMPEN] {_p.coin} {_p.direction} p={_pre:.2f}->{_p.probability:.2f}")
                    _kept.append(_p)
                predictions = _kept
            except Exception as _ex:
                logger.debug(f"[EXHAUST] eval error: {_ex}")

            # ── Filter: tighter thresholds for 5m (noise is higher) ──
            actionable = [
                p for p in predictions
                if p.confidence in ("HIGH", "MEDIUM")
                and p.edge >= config.M5_MIN_EDGE
                and abs(getattr(p, "trend_score", 0.0)) >= config.M5_MIN_TREND
            ]

            seen_coins = set()
            unique = []
            for p in sorted(actionable, key=lambda x: x.probability, reverse=True):
                if p.coin not in seen_coins:
                    unique.append(p)
                    seen_coins.add(p.coin)

            if not unique:
                time.sleep(config.M5_SCAN_INTERVAL)
                continue

            best = unique[0]

            if not lock_window(best.coin, best.market_info.window_start):
                time.sleep(config.M5_SCAN_INTERVAL)
                continue

            # ── CLOB re-check + 5m-specific gates ──
            clob_ask = orders.get_clob_ask(best.token_id)
            if clob_ask is None:
                unlock_window(best.coin, best.market_info.window_start)
                time.sleep(config.M5_SCAN_INTERVAL)
                continue

            real_edge = best.probability - clob_ask
            best.entry_price = clob_ask
            best.edge = real_edge

            if real_edge < config.M5_MIN_EDGE:
                logger.info(
                    f"[CLOB REJECT] {best.coin} {best.direction}: "
                    f"ask={clob_ask*100:.0f}c real_edge={real_edge*100:.1f}% < {config.M5_MIN_EDGE*100:.0f}%"
                )
                unlock_window(best.coin, best.market_info.window_start)
            elif clob_ask < config.M5_ENTRY_MIN or clob_ask > config.M5_ENTRY_MAX:
                logger.info(
                    f"[CLOB RANGE] {best.coin} {best.direction}: "
                    f"ask={clob_ask*100:.0f}c outside {config.M5_ENTRY_MIN*100:.0f}-{config.M5_ENTRY_MAX*100:.0f}c"
                )
                unlock_window(best.coin, best.market_info.window_start)
            elif config.TRAP_BAND_MIN <= clob_ask <= config.TRAP_BAND_MAX:
                logger.info(
                    f"[TRAP BAND] {best.coin} {best.direction}: "
                    f"ask={clob_ask*100:.0f}c in 60-63c trap band — skip"
                )
                unlock_window(best.coin, best.market_info.window_start)
            else:
                print(
                    f"\n[{now}] #{scan_count} 5M TRADE -> {best.coin} {best.direction} | "
                    f"Prob: {best.probability:.0%} | Ask: {clob_ask*100:.0f}c | "
                    f"Edge: {real_edge*100:.1f}% | Trend: {getattr(best, 'trend_score', 0.0):+.2f} | "
                    f"{best.confidence}"
                )
                filled = orders.place_bet(best)
                if filled:
                    pos = orders.positions.get(best.coin)
                    if pos:
                        try:
                            tg.notify_fill(
                                f"[5M] {best.coin}", best.direction,
                                pos.get("shares", 0), pos.get("entry_price", clob_ask),
                                pos.get("cost", 0), real_edge, best.probability,
                            )
                        except Exception:
                            pass
                else:
                    unlock_window(best.coin, best.market_info.window_start)
                    logger.info(f"[5M UNLOCK] {best.coin}: order failed, window unlocked")

            time.sleep(config.M5_SCAN_INTERVAL)

    except KeyboardInterrupt:
        logger.info("[5M] Shutdown requested (Ctrl-C)")
    except Exception as e:
        logger.exception(f"[5M] Fatal error in main loop: {e}")
        try:
            tg.notify_error(f"[5M] CRASH: {e}")
        except Exception:
            pass
        raise
    finally:
        try:
            executor.shutdown(wait=False)
        except Exception:
            pass


if __name__ == "__main__":
    main()
