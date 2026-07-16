#!/usr/bin/env python3
"""LIVE pair-arb executor (15m crypto UP/DOWN) — buy BOTH sides when combined
ask < $1 after the 7% taker fee. Risk-free at resolution (one side pays $1)
EXCEPT legging risk, which is capped hard:
  - FOK both legs (scarcer-depth leg first); no resting orders
  - if leg B can't fill at net>=ARB_MIN_LOCK, immediately FOK-sell leg A back
  - daily circuit breakers: max arbs/day, max legging loss/day
  - leaves ARB_RESERVE USDC untouched for clean_bot
  - touch ~/v3-bot/.arb_stop to halt entries instantly
Profit realizes at window resolution (~<=15 min), auto-redeemed like any position.
"""
import force_tor  # noqa: applies proxy policy on import (no-op on Ireland)
import json, os, sys, time
from datetime import datetime, timezone

from market_data import get_market_info
from order_manager import OrderManager
from py_clob_client_v2.clob_types import OrderArgs, OrderType, PartialCreateOrderOptions
from py_clob_client_v2.order_builder.constants import BUY, SELL
from loguru import logger
import telegram_notifier as tg

logger.remove()
logger.add(sys.stdout, format="{time:HH:mm:ss} | {message}")
logger.add("arb_executor.log", rotation="20 MB", format="{time:YYYY-MM-DD HH:mm:ss} | {message}")

COINS = ["BTC", "ETH", "SOL"]
FEE = lambda p: 0.07 * p * (1 - p)                     # taker fee per share
MIN_NET   = float(os.getenv("ARB_MIN_NET", "0.015"))   # flag >= 1.5c net/pair to enter
MIN_LOCK  = float(os.getenv("ARB_MIN_LOCK", "0.005"))  # leg B must still lock >= 0.5c/pair
MAX_COST  = float(os.getenv("ARB_MAX_COST", "6.0"))    # max $ spent per arb (both legs)
MAX_DAY   = int(os.getenv("ARB_MAX_PER_DAY", "12"))    # max locked arbs per UTC day
MAX_LEG_LOSS = float(os.getenv("ARB_MAX_LEG_LOSS", "2.0"))  # daily legging-loss stop
MIN_TREM  = int(os.getenv("ARB_MIN_TREM_MIN", "2"))    # min minutes left (no closing-window legs)
RESERVE   = float(os.getenv("ARB_RESERVE", "15.0"))    # USDC left untouched for clean_bot
MIN_SH    = 5                                          # exchange minimum
STATE = "arb_state.json"
STOP_FILE = ".arb_stop"

om = OrderManager()


def _load():
    try:
        return json.loads(open(STATE, encoding="utf-8").read())
    except Exception:
        return {}


def _save(st):
    open(STATE, "w", encoding="utf-8").write(json.dumps(st))


def _roll(st):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if st.get("day") != today:
        if st.get("day"):
            logger.info(f"[DAY] {st.get('day')}: locked={st.get('locked', 0)} "
                        f"profit_locked=${st.get('profit', 0):+.2f} leg_loss=${st.get('leg_loss', 0):.2f}")
        st.update({"day": today, "locked": 0, "profit": 0.0, "leg_loss": 0.0, "miss": 0})
    return st


def _fok(token, price, shares, side):
    """FOK order; returns matched shares (0 = killed). Mirrors clean_bot taker path."""
    try:
        res = om.client.create_and_post_order(
            OrderArgs(price=round(price, 2), size=shares, side=side, token_id=token),
            PartialCreateOrderOptions(tick_size="0.01"), OrderType.FOK)
        oid = (res or {}).get("orderID") or (res or {}).get("orderId")
        matched = float((res or {}).get("size_matched") or (res or {}).get("sizeMatched") or 0)
        if matched <= 0 and oid:
            try:
                od = om.client.get_order(oid) or {}
                matched = float(od.get("size_matched") or od.get("sizeMatched") or 0)
            except Exception:
                pass
        return int(matched)
    except Exception as e:
        emsg = str(e).lower()
        if "filled or killed" in emsg or "fully filled" in emsg:
            return 0
        logger.warning(f"[ORDER FAIL] {side} {shares}x@{price:.2f}: {str(e)[:120]}")
        return 0


def _depth_at(token, limit_px):
    """Shares available at or under limit_px on the ask side."""
    try:
        d = om.get_full_depth(token) or {}
        return sum(s for p, s in d.get("asks", []) if p <= limit_px + 1e-9)
    except Exception:
        return 0.0


def _unwind(coin, token, shares, paid, st):
    """Sell back a naked first leg. Returns realized loss (>=0)."""
    for i in range(3):
        bk = om.get_clob_book(token) or {}
        bid = bk.get("bid")
        if not bid:
            time.sleep(1)
            continue
        px = round(float(bid), 2)
        got = _fok(token, px, shares, SELL)
        if got > 0:
            loss = (paid - px) * got + FEE(paid) * got + FEE(px) * got
            logger.info(f"[ARB UNWIND] {coin} sold {got}x@{px:.2f} (paid {paid:.2f}) "
                        f"leg loss ~${loss:.2f}")
            return max(0.0, loss)
        time.sleep(1)
    logger.warning(f"[ARB STUCK] {coin} holding naked {shares}x@{paid:.2f} to resolution")
    tg._send(f"⚠️ ARB STUCK {coin}: naked {shares} shares @ {paid:.2f}, riding to resolution",
             dedup_key=f"arb-stuck-{coin}-{int(time.time()//900)}")
    return paid * shares * 0.5  # book a conservative half-stake expected loss vs breakers


st = _roll(_load())
logger.info(f"=== ARB EXECUTOR LIVE | {','.join(COINS)} | min_net={MIN_NET} max_cost=${MAX_COST} "
            f"max/day={MAX_DAY} leg_stop=${MAX_LEG_LOSS} reserve=${RESERVE} ===")
tg._send("🤝 ARB executor started (pair-arb, capped)", dedup_key="arb-start")
cooldown = {}

while True:
    time.sleep(3)
    st = _roll(st)
    if os.path.exists(STOP_FILE):
        continue
    if st["locked"] >= MAX_DAY or st["leg_loss"] >= MAX_LEG_LOSS:
        continue
    for coin in COINS:
        try:
            if time.time() < cooldown.get(coin, 0):
                continue
            info = get_market_info(coin)
            if not info or not info.up_token_id or not info.down_token_id:
                continue
            if (info.time_remaining or 0) < MIN_TREM:
                continue
            ub = om.get_clob_book(info.up_token_id) or {}
            db = om.get_clob_book(info.down_token_id) or {}
            ua, da = ub.get("ask"), db.get("ask")
            if not ua or not da:
                continue
            ua, da = float(ua), float(da)
            net = 1.0 - ua - da - FEE(ua) - FEE(da)
            if net < MIN_NET:
                continue
            # sized: how many pairs are really there
            du = _depth_at(info.up_token_id, ua)
            dd = _depth_at(info.down_token_id, da)
            shares = int(min(du, dd, MAX_COST / (ua + da)))
            if shares < MIN_SH:
                logger.info(f"[ARB THIN] {coin} net=+{net:.3f} but depth "
                            f"u={du:.0f}/d={dd:.0f} < {MIN_SH} pairs")
                cooldown[coin] = time.time() + 10
                continue
            bal = om.get_live_bankroll()
            cost = (ua + da) * shares
            if bal - cost < RESERVE:
                logger.info(f"[ARB SKIP] {coin} balance ${bal:.2f} - cost ${cost:.2f} < reserve")
                continue
            # scarcer leg first
            legs = [("UP", info.up_token_id, ua, du), ("DOWN", info.down_token_id, da, dd)]
            legs.sort(key=lambda x: x[3])
            (n1, t1, p1, _), (n2, t2, p2, _) = legs
            got1 = _fok(t1, p1, shares, BUY)
            if got1 <= 0:
                st["miss"] += 1
                logger.info(f"[ARB MISS] {coin} leg1 {n1}@{p1:.2f} FOK killed (no loss)")
                cooldown[coin] = time.time() + 8
                continue
            # leg 2: refresh ask, must still lock
            locked = False
            for attempt in range(3):
                bk2 = om.get_clob_book(t2) or {}
                a2 = bk2.get("ask")
                if a2:
                    a2 = float(a2)
                    net2 = 1.0 - p1 - a2 - FEE(p1) - FEE(a2)
                    if net2 >= MIN_LOCK:
                        got2 = _fok(t2, a2, got1, BUY)
                        if got2 >= got1:
                            fees = (FEE(p1) + FEE(a2)) * got1
                            profit = (1.0 - p1 - a2) * got1 - fees
                            st["locked"] += 1
                            st["profit"] += profit
                            logger.info(f"[ARB LOCKED] {coin} {n1}@{p1:.2f}+{n2}@{a2:.2f} "
                                        f"x{got1} cost=${(p1+a2)*got1:.2f} fees=${fees:.2f} "
                                        f"LOCKED=+${profit:.2f} (pays at resolution) "
                                        f"| today {st['locked']}/{MAX_DAY} +${st['profit']:.2f}")
                            tg._send(f"🤝 ARB LOCKED {coin} +${profit:.2f} "
                                     f"({st['locked']}/{MAX_DAY} today)",
                                     dedup_key=f"arb-lock-{coin}-{int(time.time()//60)}")
                            locked = True
                            break
                        elif got2 > 0:
                            # partial second leg (shouldn't happen with FOK) — treat rest naked
                            loss = _unwind(coin, t1, got1 - got2, p1, st)
                            st["leg_loss"] += loss
                            locked = True
                            break
                time.sleep(0.7)
            if not locked:
                loss = _unwind(coin, t1, got1, p1, st)
                st["leg_loss"] += loss
                logger.info(f"[ARB LEGGED] {coin} leg2 never locked; day leg_loss "
                            f"${st['leg_loss']:.2f}/{MAX_LEG_LOSS}")
            _save(st)
            cooldown[coin] = time.time() + 8
        except Exception as e:
            logger.warning(f"[ARB LOOP ERR] {coin}: {str(e)[:150]}")
            cooldown[coin] = time.time() + 15
