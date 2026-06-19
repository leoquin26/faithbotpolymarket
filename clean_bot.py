#!/usr/bin/env python3
"""CleanBot — a minimal, single-purpose early-drift trader for 15m crypto Up/Down.

ONE validated edge, ZERO accreted gates (the old engine had ~10 stacked gates
that paralysed it). Strategy, backed by 767-window + real-ask analysis:

  • Window age 60-300s (first ~5 min, T>=600s left): if price has drifted
    >= DRIFT_BPS from the window-open strike, that direction predicts the 15m
    close (65-72%). Bet it.
  • Maker-first: rest a GTC bid 1c below ask  ->  capture spread, pay 0 fee.
  • Only if ask <= MAX_ASK  ->  never overpay a move the market already priced.
  • ETH/SOL only, 5-share min, $6 net daily stop.

Reuses proven infra: OrderManager's authed CLOB client (signing), market_data,
binance feed, Ireland proxy (force_tor), gamma (Chainlink) resolution.
Restart-safe (state persisted). DRY by default — set CLEAN_DRY=false to go live.
"""
from __future__ import annotations
import os, sys, time, json, threading, datetime
from dataclasses import dataclass, field

os.environ.setdefault("PROXY_PORT", "9055")          # Ireland tunnel for orders
import force_tor  # noqa: applies proxy on import

import httpx
import binance_ws
from market_data import get_market_info
from order_manager import OrderManager
from py_clob_client_v2.clob_types import OrderArgs, OrderType, PartialCreateOrderOptions
from py_clob_client_v2.order_builder.constants import BUY
from loguru import logger
import telegram_notifier as tg

V3 = os.path.expanduser("~/v3-bot")
logger.remove()
logger.add(sys.stdout, level="INFO", format="{time:HH:mm:ss} | {message}")
logger.add(os.path.join(V3, "clean_bot.log"), level="INFO",
           format="{time:YYYY-MM-DD HH:mm:ss} | {message}", rotation="20 MB")


@dataclass
class Cfg:
    coins: tuple = ("ETH", "SOL")
    drift_bps: float = float(os.getenv("CLEAN_DRIFT_BPS", "7"))      # min early move
    min_t: int = int(os.getenv("CLEAN_MIN_T", "600"))               # only enter with >=10min left
    warmup: int = int(os.getenv("CLEAN_WARMUP", "60"))              # let strike settle
    max_ask: float = float(os.getenv("CLEAN_MAX_ASK", "0.66"))      # never overpay
    min_ask: float = float(os.getenv("CLEAN_MIN_ASK", "0.45"))      # avoid junk longshots
    maker_offset: float = float(os.getenv("CLEAN_MAKER_OFFSET", "0.01"))
    shares: int = int(os.getenv("CLEAN_SHARES", "5"))               # exchange min
    daily_stop: float = float(os.getenv("CLEAN_DAILY_STOP", "6.0")) # net $ stop
    gtc_max_age: int = int(os.getenv("CLEAN_GTC_MAX_AGE", "180"))   # cancel unfilled after
    dry: bool = os.getenv("CLEAN_DRY", "true").lower() in ("1", "true", "yes", "on")


CFG = Cfg()
STATE = os.path.join(V3, "clean_bot_state.json")
GAMMA = "https://gamma-api.polymarket.com"
_h = httpx.Client(timeout=12, trust_env=False)   # gamma+binance reachable direct


# ── truthful resolution (Chainlink via gamma; needs closed=true) ──────────
def gamma_winner(coin: str, ws: int):
    try:
        r = _h.get(f"{GAMMA}/markets", params={"slug": f"{coin.lower()}-updown-15m-{ws}",
                                               "closed": "true"})
        arr = r.json()
        if not arr:
            return None
        m = arr[0]
        outs, pr = m.get("outcomes"), m.get("outcomePrices")
        if isinstance(outs, str):
            outs = json.loads(outs)
        if isinstance(pr, str):
            pr = json.loads(pr)
        if not outs or not pr:
            return None
        pr = [float(x) for x in pr]
        if max(pr) < 0.99:                      # not decisively settled yet
            return None
        return "UP" if str(outs[pr.index(max(pr))]).lower().startswith("up") else "DOWN"
    except Exception:
        return None


class CleanBot:
    def __init__(self):
        self.om = OrderManager()                # proven authed client + get_clob_book
        self.client = self.om.client
        self.open_orders = {}    # oid -> {coin, ws, dir, token, price, shares, ts}
        self.positions = {}      # "coin:ws" -> {dir, entry, shares, status}
        self.traded = set()      # (coin, ws) dedup
        self.day = self._today()
        self.wins = 0.0
        self.losses = 0.0
        self._stop_notified = False
        self._load()

    # ── persistence ──────────────────────────────────────────────────
    def _today(self):
        return datetime.datetime.now().strftime("%Y-%m-%d")

    def _save(self):
        try:
            json.dump({"day": self.day, "wins": self.wins, "losses": self.losses,
                       "positions": self.positions,
                       "traded": [list(t) for t in self.traded]},
                      open(STATE, "w"))
        except Exception as e:
            logger.warning(f"state save failed: {e}")

    def _load(self):
        if not os.path.exists(STATE):
            return
        try:
            d = json.load(open(STATE))
            if d.get("day") == self.day:
                self.wins = d.get("wins", 0.0); self.losses = d.get("losses", 0.0)
            self.positions = d.get("positions", {})
            self.traded = {tuple(t) for t in d.get("traded", [])}
            logger.info(f"state reloaded: {len(self.positions)} positions, day pnl "
                        f"net={self.wins - self.losses:+.2f}")
        except Exception as e:
            logger.warning(f"state load failed: {e}")

    # ── risk ─────────────────────────────────────────────────────────
    def _roll_day(self):
        t = self._today()
        if t != self.day:
            logger.info(f"=== new day {t}: resetting daily pnl (was net "
                        f"{self.wins - self.losses:+.2f}) ===")
            self.day = t; self.wins = 0.0; self.losses = 0.0
            self._stop_notified = False
            self.traded = {k for k in self.traded}  # keep; windows are unique by epoch

    def _stopped(self):
        return (self.losses - self.wins) >= CFG.daily_stop

    # ── entry ────────────────────────────────────────────────────────
    def scan(self, coin: str):
        info = get_market_info(coin)
        if not info:
            return
        ws = info.window_start
        key = (coin, ws)
        if key in self.traded:
            return
        now = time.time()
        t_rem = ws + 900 - now
        age = now - ws
        if age < CFG.warmup or t_rem < CFG.min_t:     # early-only window
            return
        strike = float(info.threshold_price or 0)
        px = binance_ws.get_price(coin) or info.current_crypto_price
        px = float(px or 0)
        if strike <= 0 or px <= 0:
            return
        dist = (px - strike) / strike
        if abs(dist) < CFG.drift_bps / 10000.0:        # need a clear early drift
            return
        is_up = dist > 0
        token = info.up_token_id if is_up else info.down_token_id
        direction = "UP" if is_up else "DOWN"
        book = {}
        try:
            book = self.om.get_clob_book(token) or {}
        except Exception:
            pass
        ask = book.get("ask")
        if not ask or not (CFG.min_ask <= float(ask) <= CFG.max_ask):
            return
        maker = round(max(0.02, float(ask) - CFG.maker_offset), 2)
        self.traded.add(key)
        logger.info(f"[ENTER] {coin} {direction} drift={dist*100:+.3f}% ask={float(ask)*100:.0f}c "
                    f"-> maker {maker*100:.0f}c x{CFG.shares} T={t_rem:.0f}s"
                    + (" [DRY]" if CFG.dry else ""))
        if CFG.dry:
            self._save()
            return
        try:
            res = self.client.create_and_post_order(
                OrderArgs(price=maker, size=CFG.shares, side=BUY, token_id=token),
                PartialCreateOrderOptions(tick_size="0.01"), OrderType.GTC)
            oid = (res or {}).get("orderID") or (res or {}).get("orderId")
            if oid:
                self.open_orders[oid] = {"coin": coin, "ws": ws, "dir": direction,
                                         "token": token, "price": maker,
                                         "shares": CFG.shares, "ts": now}
                logger.info(f"[GTC] resting {coin} {direction} @ {maker*100:.0f}c oid={oid[:10]}")
            else:
                logger.warning(f"[ORDER] no oid in result: {res}")
        except Exception as e:
            logger.warning(f"[ORDER FAIL] {coin} {direction}: {e}")
        self._save()

    # ── fills ────────────────────────────────────────────────────────
    def check_orders(self):
        now = time.time()
        for oid, o in list(self.open_orders.items()):
            try:
                od = self.client.get_order(oid) or {}
            except Exception:
                continue
            matched = float(od.get("size_matched") or od.get("sizeMatched") or 0)
            status = str(od.get("status", "")).upper()
            if matched > 0:
                self.positions[f"{o['coin']}:{o['ws']}"] = {
                    "coin": o["coin"], "ws": o["ws"], "dir": o["dir"],
                    "entry": o["price"], "shares": int(matched), "status": "filled"}
                logger.info(f"[FILLED] {o['coin']} {o['dir']} @ {o['price']*100:.0f}c "
                            f"x{int(matched)}")
                tg._send(f"🤖 <b>FILLED</b> {o['coin']} {o['dir']} @ {o['price']*100:.0f}c "
                         f"x{int(matched)}", dedup_key=f"fill-{oid}")
                self.open_orders.pop(oid, None); self._save()
            elif status in ("CANCELED", "EXPIRED") or now - o["ts"] > CFG.gtc_max_age \
                    or o["ws"] + 900 - now < 90:
                try:
                    self.client.cancel(oid)
                except Exception:
                    pass
                logger.info(f"[CANCEL] unfilled {o['coin']} {o['dir']} @ {o['price']*100:.0f}c")
                self.open_orders.pop(oid, None); self._save()

    # ── resolution ───────────────────────────────────────────────────
    def resolve(self):
        now = time.time()
        for k, p in list(self.positions.items()):
            if p["status"] != "filled" or now < p["ws"] + 960:
                continue
            w = gamma_winner(p["coin"], p["ws"])
            if not w:
                continue
            won = (p["dir"] == w)
            entry = p["entry"]; sh = p["shares"]
            pnl = (1 - entry) * sh if won else -entry * sh
            if won:
                self.wins += (1 - entry) * sh
            else:
                self.losses += entry * sh
            p["status"] = "resolved"; p["pnl"] = round(pnl, 2)
            net = self.wins - self.losses
            logger.info(f"[{'WIN' if won else 'LOSS'}] {p['coin']} {p['dir']} @ "
                        f"{entry*100:.0f}c -> {w} | {pnl:+.2f} | day net {net:+.2f}")
            tg._send(f"{'✅ <b>WIN</b>' if won else '❌ <b>LOSS</b>'} {p['coin']} {p['dir']} @ "
                     f"{entry*100:.0f}c → {w} | {pnl:+.2f} | day net {net:+.2f}",
                     dedup_key=f"res-{k}")
            self._save()

    # ── loop ─────────────────────────────────────────────────────────
    def run(self):
        logger.info(f"=== CleanBot start | {'DRY' if CFG.dry else 'LIVE'} | coins={CFG.coins} "
                    f"| drift>={CFG.drift_bps}bps T>={CFG.min_t}s ask<={CFG.max_ask} "
                    f"shares={CFG.shares} stop=${CFG.daily_stop} ===")
        tg._send(f"🤖 <b>CleanBot started</b> {'LIVE' if not CFG.dry else 'DRY'} | "
                 f"{'/'.join(CFG.coins)} · early-drift ≥{CFG.drift_bps}bps · maker · "
                 f"stop ${CFG.daily_stop}")
        binance_ws.start()
        time.sleep(90)
        n = 0
        while True:
            n += 1
            self._roll_day()
            try:
                self.check_orders()
                self.resolve()
                if not self._stopped():
                    for c in CFG.coins:
                        self.scan(c)
                else:
                    if not self._stop_notified:
                        tg._send(f"🛑 <b>CleanBot daily stop</b> | net {self.wins - self.losses:+.2f} "
                                 f"≤ -{CFG.daily_stop} — no new entries today")
                        self._stop_notified = True
                    if n % 40 == 1:
                        logger.info(f"[STOP] daily net {self.wins - self.losses:+.2f} "
                                    f"<= -{CFG.daily_stop} — no new entries today")
            except Exception as e:
                logger.warning(f"loop error: {e}")
            if n % 40 == 1:
                logger.info(f"… alive scan#{n} open={len(self.open_orders)} "
                            f"positions={len(self.positions)} day_net={self.wins-self.losses:+.2f}")
            time.sleep(5)


if __name__ == "__main__":
    CleanBot().run()
