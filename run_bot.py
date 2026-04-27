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
# ── analytics hook apr23 ──
try:
    from analytics import event_logger as _alog
    from analytics import resolver as _aresolver
except Exception as _e:  # analytics is optional
    _alog = None
    _aresolver = None
import telegram_notifier as tg

from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import config
from morning_predictor import MorningPredictor
import morning_strategy as morn
import binance_ws
from market_data import get_market_info, MarketInfo
from predictor import Predictor, Prediction
import exhaustion_detector as exhaust
from order_manager import OrderManager

logger.remove()
# Single stderr sink — the bot is run as `python3 run_bot.py >> v3_bot.log 2>&1`,
# so stderr is captured to v3_bot.log exactly once. Adding a loguru FileHandler
# on top of that caused EVERY line to be written twice (fix applied 2026-04-22).
logger.add(
    sys.stderr,
    level="DEBUG",
    format="{time:HH:mm:ss} | {level:<8} | {message}",
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
# FIX 1: Atomic one-trade-per-window lock  (persistent across restarts)
# ======================================================================
import json as _json_lock
_trade_lock = threading.Lock()
_traded_set: set = set()
_TRADED_SET_PATH = "/home/ubuntu/v3-bot/traded_windows.json"


def _persist_traded_set_unlocked():
    """Write the current _traded_set to disk. Caller holds _trade_lock."""
    try:
        with open(_TRADED_SET_PATH, "w") as f:
            _json_lock.dump(sorted(_traded_set), f)
    except Exception as e:
        try:
            import logging as _lg
            _lg.getLogger(__name__).warning(f"[TRADED SET] persist failed: {e}")
        except Exception:
            pass


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
        _persist_traded_set_unlocked()
        return True


def unlock_window(coin: str, window_start: int):
    """Release a window lock (e.g. when FOK order fails)."""
    key = f"{coin}_{window_start}"
    with _trade_lock:
        _traded_set.discard(key)
        _persist_traded_set_unlocked()


def cleanup_old_windows():
    """Remove window locks older than 20 minutes to prevent memory leak."""
    now = int(time.time())
    with _trade_lock:
        stale = [k for k in _traded_set if int(k.split("_")[-1]) < now - 1200]
        for k in stale:
            _traded_set.discard(k)
        if stale:
            _persist_traded_set_unlocked()


def bootstrap_traded_set():
    """
    Rehydrate _traded_set on startup from three sources (any is enough):
      1. /home/ubuntu/v3-bot/traded_windows.json (previous process's state)
      2. CLOB open positions (proxyWallet positions with slug containing a ts)
      3. Today's [FILLED] log lines

    Only windows within the last 20 minutes (still live) are loaded.
    """
    import logging as _lg, re, os as _os
    _log = _lg.getLogger(__name__)
    now = int(time.time())
    cutoff = now - 1200  # 20 min

    loaded = set()

    # ---- 1. disk ----
    try:
        import os as _os
        if _os.path.exists(_TRADED_SET_PATH):
            with open(_TRADED_SET_PATH) as f:
                keys = _json_lock.load(f) or []
            for k in keys:
                try:
                    ts = int(str(k).split("_")[-1])
                except Exception:
                    continue
                if ts >= cutoff:
                    loaded.add(k)
    except Exception as e:
        _log.warning(f"[TRADED SET] disk load failed: {e}")

    # ---- 2. CLOB open positions ----
    try:
        import requests as _rq
        addr = _os.getenv("POLYMARKET_FUNDER_ADDRESS") or _os.getenv("POLY_ADDRESS") or ""
        if addr:
            r = _rq.get(
                f"https://data-api.polymarket.com/positions?user={addr}&sizeThreshold=0.1",
                timeout=10,
            )
            if r.ok:
                for p in r.json() or []:
                    slug = (p.get("slug") or "")
                    m = re.search(r"-15m-(\d{10})$", slug)
                    if not m:
                        continue
                    ws = int(m.group(1))
                    if ws < cutoff:
                        continue
                    title = (p.get("title") or "").lower()
                    coin = None
                    for c, needles in {
                        "BTC": ("bitcoin", "btc"),
                        "ETH": ("ethereum", "eth"),
                        "SOL": ("solana", "sol"),
                        "XRP": ("xrp",),
                    }.items():
                        if any(n in title for n in needles):
                            coin = c
                            break
                    if coin:
                        loaded.add(f"{coin}_{ws}")
    except Exception as e:
        _log.warning(f"[TRADED SET] CLOB load failed: {e}")

    # ---- 3. today's fill log ----
    try:
        import os as _os
        logpath = "/home/ubuntu/v3-bot/v3_bot.log"
        if _os.path.exists(logpath):
            today_prefix = datetime.now().strftime("%Y-%m-%d")
            # Only scan last ~500 KB; fills happen rarely
            with open(logpath, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 500_000))
                tail = f.read().decode(errors="ignore")
            # Parse lines like: "14:52:15 | INFO | [FILLED] BTC DOWN | ..."
            # We need the epoch; use today + HH:MM:SS
            for line in tail.splitlines():
                m = re.match(r"^(\d{2}):(\d{2}):(\d{2}).*\[FILLED\]\s+(BTC|ETH|SOL|XRP)\s+(UP|DOWN)", line)
                if not m:
                    continue
                hh, mm, ss, coin, _dir = m.groups()
                try:
                    from datetime import datetime as _dt, timezone as _tz
                    today = _dt.now().date()
                    lt = _dt(today.year, today.month, today.day, int(hh), int(mm), int(ss))
                    # bot logs in server local time; approximate ws from this
                    epoch = int(lt.timestamp())
                except Exception:
                    continue
                # round down to the 15-min window
                ws = epoch - (epoch % 900)
                if ws >= cutoff:
                    loaded.add(f"{coin}_{ws}")
    except Exception as e:
        _log.warning(f"[TRADED SET] log load failed: {e}")

    with _trade_lock:
        _traded_set.update(loaded)
        _persist_traded_set_unlocked()

    if loaded:
        _log.info(
            f"[TRADED SET] bootstrapped {len(loaded)} active window locks: "
            + ", ".join(sorted(loaded))
        )
    else:
        _log.info("[TRADED SET] bootstrap found no live windows to restore")


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
    weekday = now_lima.weekday()  # Mon=0, Fri=4, Sat=5, Sun=6
    # Unified weekend mode: Fri 17:00+ -> Sat/Sun all day -> Mon <09:00 all blocked
    _is_sat_sun      = weekday >= 5
    _is_fri_evening  = (weekday == 4) and (lima_hour >= 17)
    _is_mon_premarket = (weekday == 0) and (lima_hour < 9)
    if _is_sat_sun or _is_fri_evening or _is_mon_premarket:
        stamp = now_lima.strftime("%a %H:%M")
        return False, f"[WEEKEND MODE] {stamp} Lima — blocked until Monday 09:00 Lima"
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
    print("  V11 BOT — Black-Scholes Binary Engine")
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
    morning_pred = MorningPredictor(predictor)
    bootstrap_traded_set()
    # ── analytics hook apr23 ── kick off resolver daemon
    try:
        if _aresolver is not None:
            _aresolver.start_background()
            logger.info('[ANALYTICS] resolver thread started')
    except Exception as _e:
        logger.debug(f'[ANALYTICS] resolver start failed: {_e}')
    orders = OrderManager()
    executor = ThreadPoolExecutor(max_workers=4)

    tg.test()
    tg.notify_startup()

    scan_count = 0
    _consec_losses = 0
    _morning_consec_losses = 0
    # Fix A apr23: track last EXHAUST=ABSTAIN per coin (monotonic epoch)
    _last_exhaust_abstain: dict = {}
    _morning_total_losses = 0.0
    MORNING_LOSS_CAP = 12.0  # hard stop for morning; afternoon unaffected
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
                _ws = int(time.time()) % 900
                if _ws < 90:
                    if _consec_losses >= 2:
                        logger.info(f"[LOSS BREAKER RESET] New window -- resuming afternoon")
                        _consec_losses = 0
                    if _morning_consec_losses >= 2:
                        logger.info(f"[MORNING BREAKER RESET] New window -- resuming morning")
                        _morning_consec_losses = 0

            # Fix A: raw cross-coin orderbook snapshot (populated inside scan_coin)
            # Keyed by coin, value = (up_ask, down_ask). Used to feed the
            # exhaustion detector's breadth signal with ALL coins, not just
            # those that survived filters.
            _raw_coin_info = {}

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

                # Fix A: snapshot raw directional bias for exhaustion breadth
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

            # ── Exhaustion detector (SHADOW MODE) ──
            # Evaluates every prediction for exhaustion signals. Logs only;
            # does NOT alter trade decisions while SHADOW_MODE=True.
            if predictions:
                try:
                    # Fix A: build RAW cross-coin direction map using orderbook asks
                    # (covers coins filtered by EXPENSIVE/WEAK/COLD before predictions).
                    _wt = {}
                    _pp = {}
                    for _c, (_ua, _da) in _raw_coin_info.items():
                        if _ua >= 0.60 and _ua < 0.99:
                            _wt[_c] = "UP"
                            _pp[_c] = _ua
                        elif _da >= 0.60 and _da < 0.99:
                            _wt[_c] = "DOWN"
                            _pp[_c] = _da
                    # Overlay predicted coins (they may be neutral in orderbook
                    # but still have a directional signal we want to count).
                    for _p in predictions:
                        _wt[_p.coin] = _p.direction
                        _pp[_p.coin] = (_p.entry_price if _p.entry_price > 0.05 else _p.poly_price)
                    # Fix A.2: ENFORCE actions. evaluate() logs, we filter by action.
                    _kept = []
                    for _p in predictions:
                        _tk = binance_ws.get_tick_history(_p.coin, 300)
                        _res = exhaust.evaluate(_p, _tk, _wt, _pp)
                        _act = _res.get("action", "CLEAN") if isinstance(_res, dict) else "CLEAN"
                        # ── analytics hook apr23 ── EXHAUST verdict
                        try:
                            if _alog is not None:
                                _tid = _alog.new_trade_id()
                                setattr(_p, "_trade_id", _tid)
                                _alog.log_signal(_tid, _p, getattr(_p, "trend_score", 0.0))
                                _alog.log_exhaust(
                                    _tid, _p.coin, _p.direction, _act,
                                    float(_res.get("score", 0) or 0),
                                    session_range=_res.get("range"),
                                    breadth=_res.get("breadth"),
                                    decel=_res.get("decel"),
                                    window_start=getattr(_p.market_info, "window_start", None),
                                    token_id=getattr(_p, "token_id", None),
                                )
                        except Exception:
                            pass
                        # ── Fix apr27: edge-priority override ──
                        # When signal is A-tier (prob>=82% AND edge>=18%),
                        # downgrade EXHAUST ABSTAIN -> DAMPEN. Top-tier signals
                        # historically win 80%+; size still halved by DAMPEN flag.
                        if _act == "ABSTAIN" and _p.probability >= 0.82 and _p.edge >= 0.18:
                            logger.info(
                                f"[EXHAUST OVERRIDE] {_p.coin} {_p.direction}: "
                                f"prob={_p.probability:.0%} edge={_p.edge*100:.1f}% — "
                                f"ABSTAIN(score={_res.get('score', 0):.2f}) -> DAMPEN"
                            )
                            _act = "DAMPEN"
                        if _act == "ABSTAIN":
                            # ── Fix A apr23: sticky EXHAUST ABSTAIN memory ──
                            _last_exhaust_abstain[_p.coin] = time.time()
                            try:
                                if _alog is not None:
                                    _alog.log_blocked(
                                        getattr(_p, "_trade_id", None),
                                        _p.coin, _p.direction, "EXHAUST_ABSTAIN",
                                        score=float(_res.get("score", 0) or 0),
                                        window_start=getattr(_p.market_info, "window_start", None),
                                    )
                            except Exception:
                                pass
                            logger.info(f"[EXHAUST BLOCK] {_p.coin} {_p.direction} skipped (score={_res.get('score', 0):.2f})")
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
                            # Fix F (apr21): mark dampened so order_manager cuts size 50%
                            setattr(_p, "_dampened", True)
                            logger.info(f"[EXHAUST DAMPEN] {_p.coin} {_p.direction} p={_pre:.2f}->{_p.probability:.2f} (size will be halved)")
                        _kept.append(_p)
                    predictions = _kept
                except Exception as _ex:
                    logger.debug(f"[EXHAUST] eval error: {_ex}")

            if arb_candidates and can_trade:
                best = max(arb_candidates, key=lambda a: a["profit_pct"])
                print(f"\n[{now}] #{scan_count} ARB: {best['coin']} UP {best['up_price']*100:.0f}c + DOWN {best['down_price']*100:.0f}c = {best['combined']*100:.0f}c | Profit: {best['profit_pct']:.1f}%")
                orders.execute_arb(
                    best["coin"], best["up_token"], best["down_token"],
                    best["up_price"], best["down_price"], best["window_start"],
                )
                time.sleep(config.SCAN_INTERVAL)
                continue

            # ── Morning strategy (9am-2pm Lima): separate, conservative predictor ──
            from zoneinfo import ZoneInfo as _ZI
            _lima_now = datetime.now(_ZI("America/Lima"))
            _is_morning = 9 <= _lima_now.hour < 14
            _is_afternoon = 14 <= _lima_now.hour < 17



            # Morning strategy (9am-2pm Lima) - ISOLATED from afternoon
            # Uses main predictor signals but with phase-specific filters.
            # Morning outcomes DO NOT feed predictor.record_outcome() - isolation.
            if _is_morning and can_trade and _morning_consec_losses < 2 and _morning_total_losses < MORNING_LOSS_CAP:
                _phase = morn.get_morning_phase()
                if _phase in (1, 3):
                    _morning_candidates = []
                    for _p in predictions:
                        _ts = getattr(_p, "trend_score", 0.0)
                        _filtered = morn.filter_morning_signal(_p, _ts)
                        if _filtered is not None:
                            _morning_candidates.append(_filtered)

                    if _morning_candidates:
                        _best_m = max(_morning_candidates, key=lambda x: x.probability)
                        _active_count = len(orders.positions) + len(orders.active_gtc)
                        # ── Fix A apr23: sticky EXHAUST ABSTAIN memory ──
                        _abstain_age = time.time() - _last_exhaust_abstain.get(_best_m.coin, 0)
                        if _abstain_age < 30:
                            try:
                                if _alog is not None:
                                    _alog.log_blocked(
                                        getattr(_best_m, "_trade_id", None),
                                        _best_m.coin, _best_m.direction,
                                        "MORNING_STICKY_EXHAUST",
                                        abstain_age_s=_abstain_age,
                                        window_start=getattr(_best_m.market_info, "window_start", None),
                                    )
                            except Exception:
                                pass
                            logger.info(
                                f"[MORNING STICKY EXHAUST] {_best_m.coin} {_best_m.direction}: "
                                f"ABSTAIN {_abstain_age:.0f}s ago — skipping to avoid oscillation"
                            )
                            time.sleep(config.SCAN_INTERVAL)
                            continue
                        # ── Fix C apr23: tighter morning concurrency after any loss ──
                        # After first morning loss of the session, allow only 1 open position
                        _morning_cap = 1 if _morning_consec_losses >= 1 else 2
                        if _active_count < _morning_cap:
                            if not is_window_locked(_best_m.coin, _best_m.market_info.window_start):
                                if lock_window(_best_m.coin, _best_m.market_info.window_start):
                                    # CLOB re-check (same as afternoon)
                                    _clob_ask = orders.get_clob_ask(_best_m.token_id)
                                    if _clob_ask is not None:
                                        _real_edge = _best_m.probability - _clob_ask
                                        _best_m.entry_price = _clob_ask
                                        _best_m.edge = _real_edge

                                    # Half Kelly sizing for morning (temporarily)
                                    import os as _os2
                                    _orig_frac = _os2.environ.get("KELLY_FRACTION", "0.25")
                                    _os2.environ["KELLY_FRACTION"] = str(float(_orig_frac) * 0.5)
                                    try:
                                        _success = orders.place_bet(_best_m)
                                        if _success and _best_m.coin in orders.positions:
                                            # Tag position as morning for isolated resolution
                                            orders.positions[_best_m.coin]["is_morning"] = True
                                            # ── analytics hook apr23 ── morning FIRED
                                            try:
                                                if _alog is not None:
                                                    _pos = orders.positions[_best_m.coin]
                                                    _alog.log_fired(
                                                        getattr(_best_m, '_trade_id', None),
                                                        _best_m.coin, _best_m.direction,
                                                        entry=_pos.get('entry', _best_m.entry_price),
                                                        shares=_pos.get('shares', 0),
                                                        cost=_pos.get('cost', 0),
                                                        phase=f'MORNING_P{_phase}',
                                                        window_start=getattr(_best_m.market_info, 'window_start', None),
                                                        token_id=getattr(_best_m, 'token_id', None),
                                                    )
                                            except Exception:
                                                pass
                                            logger.info(
                                                f"[MORNING P{_phase} TRADE] {_best_m.coin} {_best_m.direction} "
                                                f"placed (half-Kelly)"
                                            )
                                        else:
                                            unlock_window(_best_m.coin, _best_m.market_info.window_start)
                                            logger.info(f"[MORNING UNLOCK] {_best_m.coin}: order failed")
                                    finally:
                                        _os2.environ["KELLY_FRACTION"] = _orig_frac

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

            if unique and can_trade and _is_afternoon and _consec_losses < 2:
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
                if ws > 0 and current_time > ws + 900 + 180:
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
                _resolved = False
                # Defer counter: how many scan cycles we have been waiting.
                # Stored on the position dict itself.
                _deferred = pos.get("_resolve_deferred", 0)
                if token_id and ws > 0:
                    _slug = f"{coin.lower()}-updown-15m-{ws}"
                    _http = orders._get_direct_http()
                    import ast as _ast
                    # Try to resolve via Gamma API. Up to 20 attempts * 30s = 10 minutes.
                    # Decisive thresholds: > 0.98 = WIN, < 0.02 = LOSS.
                    for _attempt in range(20):
                        try:
                            _resp = _http.get(
                                f"https://gamma-api.polymarket.com/events?slug={_slug}",
                                timeout=5,
                            )
                            if _resp.status_code == 200:
                                _data = _resp.json()
                                if _data and _data[0].get("markets"):
                                    _mkt = _data[0]["markets"][0]
                                    _closed = _mkt.get("closed", False)
                                    _op = _mkt.get("outcomePrices", [])
                                    if isinstance(_op, str):
                                        _op = _ast.literal_eval(_op)
                                    _toks = _mkt.get("clobTokenIds", [])
                                    if isinstance(_toks, str):
                                        _toks = _ast.literal_eval(_toks)
                                    # Parse the `outcomes` list too; we resolve by position
                                    # SIDE (UP/DOWN) matched against outcomes label — independent of
                                    # any upstream token_id bugs.
                                    _outs = _mkt.get("outcomes", [])
                                    if isinstance(_outs, str):
                                        _outs = _ast.literal_eval(_outs)
                                    _target_label = "Up" if side == "UP" else "Down"
                                    _idx_by_outcome = _outs.index(_target_label) if _target_label in _outs else -1
                                    _idx_by_token = _toks.index(token_id) if (token_id and token_id in _toks) else -1

                                    # Pick the authoritative index: outcomes+side is ground truth.
                                    # If both available, verify they agree; if they diverge log loudly
                                    # so we can fix the upstream token_id bug.
                                    if _idx_by_outcome >= 0 and _idx_by_token >= 0 and _idx_by_outcome != _idx_by_token:
                                        logger.error(
                                            f"[TOKEN MISMATCH] {coin} {side}: position token_id maps to "
                                            f"idx={_idx_by_token} ({_outs[_idx_by_token]}) but side={side} "
                                            f"wants idx={_idx_by_outcome} ({_target_label}). "
                                            f"Using SIDE as truth. token={token_id[-10:]}"
                                        )
                                    _idx = _idx_by_outcome if _idx_by_outcome >= 0 else _idx_by_token

                                    if len(_op) == 2 and _idx >= 0:
                                        _price = float(_op[_idx])
                                        # Accept decisive outcomes regardless of closed flag.
                                        # Polymarket sometimes sets outcomePrices before closed=True.
                                        if _price >= 0.98:
                                            won = True
                                            _resolved = True
                                        elif _price <= 0.02:
                                            won = False
                                            _resolved = True
                                        if _resolved:
                                            logger.info(
                                                f"[RESOLVE POLY] {coin} {side}: outcomePrice={_price:.4f} "
                                                f"(outcomes={_outs} prices={_op}) closed={_closed} -> "
                                                f"{'WIN' if won else 'LOSS'} (attempt {_attempt+1})"
                                            )
                                            break
                                        else:
                                            logger.debug(
                                                f"[RESOLVE WAIT] {coin} {side}: price={_price:.4f} closed={_closed} (attempt {_attempt+1}/20)"
                                            )
                        except Exception as _e:
                            logger.debug(f"[RESOLVE ERROR] {coin} attempt {_attempt+1}: {_e}")
                        if _attempt < 19 and not _resolved:
                            time.sleep(30)

                if not _resolved:
                    # Defer: put the position back and retry on the next expiry scan.
                    # Only give up and use Binance fallback after 2 deferrals (~30+ min past close).
                    if _deferred < 2:
                        pos["_resolve_deferred"] = _deferred + 1
                        orders.positions[coin] = pos  # put back; will retry next cycle
                        logger.warning(
                            f"[RESOLVE DEFERRED] {coin} {side}: Polymarket not resolved after 10min; "
                            f"will retry (defer #{_deferred + 1})"
                        )
                        continue  # skip win/loss bookkeeping this round
                    else:
                        # Last resort after multiple deferrals: compare Binance price with strict >= tie-break.
                        try:
                            final_price = binance_ws.get_price(coin)
                            strike = pos.get("strike", 0)
                            if strike > 0 and final_price > 0:
                                # Treat exact ties as LOSS for UP (Polymarket resolves ties to DOWN most often)
                                went_up = final_price > strike  # strict inequality; tie = not went_up
                                won = (side == "UP" and went_up) or (side == "DOWN" and not went_up)
                            logger.warning(
                                f"[RESOLVE BINANCE FALLBACK] {coin} {side}: price={final_price:.2f} "
                                f"strike={strike:.2f} -> {'WIN' if won else 'LOSS'} (last resort)"
                            )
                        except Exception as _e:
                            logger.debug(f"[RESOLVE BINANCE ERROR] {coin}: {_e}")

                _is_morning_trade = pos.get("is_morning", False)
                _tag = "MORNING" if _is_morning_trade else "PM"
                # ── analytics hook apr23 ── RESOLVED event
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
                            phase=_tag,
                            resolution_source="live",
                        )
                except Exception:
                    pass
                if won:
                    pnl = payout - cost
                    logger.info(f"[WIN {_tag}] {coin} {side} | +${pnl:.2f} | Entry: {entry*100:.0f}c x{shares} | Payout: ${payout:.2f}")
                    tg.notify_result(coin, side, True, cost, payout)
                    if _is_morning_trade:
                        _morning_consec_losses = 0
                    else:
                        predictor.record_outcome(True)
                        _consec_losses = 0
                else:
                    logger.info(f"[LOSS {_tag}] {coin} {side} | -${cost:.2f} | Entry: {entry*100:.0f}c x{shares}")
                    tg.notify_result(coin, side, False, cost)
                    if _is_morning_trade:
                        _morning_consec_losses += 1
                        _morning_total_losses += cost
                        if _morning_total_losses >= MORNING_LOSS_CAP:
                            logger.warning(f"[MORNING CAP] Morning loss cap hit (${_morning_total_losses:.2f}) -- morning disabled until tomorrow; afternoon UNAFFECTED")
                        elif _morning_consec_losses >= 2:
                            logger.warning(f"[MORNING LOSS BREAKER] {_morning_consec_losses} consecutive -- pausing morning until next window")
                    else:
                        predictor.record_outcome(False)
                        orders.daily_losses += cost
                        _consec_losses += 1
                        if _consec_losses >= 2:
                            logger.warning(f"[LOSS BREAKER] {_consec_losses} consecutive losses -- pausing afternoon until next window")

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
