#!/usr/bin/env python3
"""T3 LIVE — the LATE-digital seat with REAL money (CYCLE_LAW clock 2026-09-02).

Seat = late_shadow's decision, unchanged: BTC only, last 10 minutes of the 1h
window, P(UP)=Φ(ln(S/S0)/(σ√τ)) from Binance spot + realized σ (180s), maker
px = min(bid+1c, ask-1c), fire when fair - px >= 4c, px in [0.20, 0.85],
fair in (0.10, 0.90), spread <= 4c. ONE order per hour, 5 shares (exchange
min), GTC maker, hold to settlement. Unfilled dies at T-30s ($0).
Stops (pre-registered): n=40 settles or net <= -$12 -> halt forever.
This process exists only because the 06:00 UTC clock read >= +0.03 EV/$ on
the filtered paper ledger. Auto-launch stays revoked: a human starts it.
"""
import json, os, sys, time, urllib.request

os.environ.setdefault("PROXY_PORT", "9055")
import force_tor  # noqa: same CLOB path as micro_bot / t1_live

from order_manager import OrderManager
from py_clob_client_v2.clob_types import OrderArgs, OrderType, PartialCreateOrderOptions
from py_clob_client_v2.order_builder.constants import BUY
from loguru import logger
import telegram_notifier as tg

V3 = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, V3)
import binance_ws
from hour_bot import http_json, hour_slug, winner_for
from research_brain.digital import maker_px, p_up

STATE = os.path.join(V3, "t3_live_state.json")
SEAT_ID = "T3-late-digital-btc"
logger.remove()
logger.add(sys.stdout, level="INFO", format="{time:HH:mm:ss} | {message}")
logger.add(os.path.join(V3, "t3_live.log"), level="INFO",
           format="{time:YYYY-MM-DD HH:mm:ss} | {message}", rotation="20 MB")

COIN, NAME, SYM = "BTC", "bitcoin", "BTCUSDT"
T_LO, T_HI = 30, 600
EDGE_MIN, MAX_SPREAD = 0.04, 0.04
MIN_PX, MAX_PX = 0.20, 0.85
FAIR_LO, FAIR_HI = 0.10, 0.90
SIGMA_LOOKBACK = 180
SHARES = 5
STOP_N, STOP_NET = 40, -12.0
T_CANCEL = T_LO


def rest_json(url, timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent": "t3-live"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode())


def candle_open(hs):
    k = rest_json(f"https://api.binance.com/api/v3/klines?symbol={SYM}&interval=1h"
                  f"&startTime={int(hs)*1000}&limit=1")
    return float(k[0][1]) if k else None


def market_for():
    for off in (4, 5):
        try:
            ev = http_json("https://gamma-api.polymarket.com/events?slug=" + hour_slug(NAME, off))
        except Exception:
            continue
        if not ev:
            continue
        mk = (ev[0].get("markets") or [None])[0]
        if not mk:
            continue
        ids, outs = mk.get("clobTokenIds"), mk.get("outcomes")
        if isinstance(ids, str): ids = json.loads(ids)
        if isinstance(outs, str): outs = json.loads(outs)
        if ids and outs and len(ids) == 2:
            return dict(zip([o.upper() for o in outs], ids)), mk.get("slug") or hour_slug(NAME, off)
    return None


class T3Live:
    def __init__(self):
        self.om = OrderManager()
        self.client = self.om.client
        self.s = {"bets": [], "net": 0.0, "done": "", "open": None, "order": None,
                  "seat": SEAT_ID}
        if os.path.exists(STATE):
            try:
                self.s.update(json.load(open(STATE)))
            except Exception:
                pass
        self.opens = {}      # (hs) -> candle open

    def save(self):
        json.dump(self.s, open(STATE, "w"))

    def book_top(self, token):
        try:
            bk = http_json("https://clob.polymarket.com/book?token_id=" + token)
            asks = [float(a["price"]) for a in bk.get("asks", []) if 0.01 < float(a["price"]) < 0.99]
            bids = [float(b["price"]) for b in bk.get("bids", []) if 0.01 < float(b["price"]) < 0.99]
            return (max(bids) if bids else None), (min(asks) if asks else None)
        except Exception:
            return None, None

    def matched_of(self, oid):
        for attempt in (0, 1):
            try:
                od = self.client.get_order(oid) or {}
                return float(od.get("size_matched") or od.get("sizeMatched") or 0)
            except Exception:
                if attempt == 0:
                    time.sleep(1.0)
        return 0.0

    def cancel_oid(self, oid):
        try:
            self.client.cancel(oid)
        except Exception:
            pass

    def verdict_check(self):
        real = [b for b in self.s["bets"] if b.get("px", 0) > 0 and b.get("sh", 0) > 0]
        n = len(real)
        if self.s["done"]:
            return True
        if n >= STOP_N or self.s["net"] <= STOP_NET:
            w = sum(1 for b in real if b["won"])
            why = f"n>={STOP_N}" if n >= STOP_N else f"net<={STOP_NET}"
            self.s["done"] = f"AUDIT COMPLETE n={n} {w}W/{n-w}L net={self.s['net']:+.2f} (stop: {why})"
            logger.info("[T3 VERDICT] " + self.s["done"])
            bank = (f"\n🏦 <b>BANKING LAW</b>: withdraw ${self.s['net']*0.5:.2f} now (50%)."
                    if self.s["net"] > 0 else "")
            tg._send(f"🏁 <b>T3 LIVE COMPLETE</b> — {self.s['done']}{bank}")
            self.save()
            return True
        return False

    def step(self):
        now = time.time()
        hs = int(now // 3600) * 3600
        t_left = hs + 3600 - now

        # 1) settle
        p = self.s.get("open")
        if p and now > p["hs"] + 3600 + 90:
            wn = winner_for(p["slug"])
            if not wn:
                return
            won = (wn == p["dir"])
            pnl = (1 - p["px"]) * p["sh"] if won else -p["px"] * p["sh"]
            self.s["net"] = round(self.s["net"] + pnl, 2)
            self.s["bets"].append({"hs": p["hs"], "coin": COIN, "dir": p["dir"], "px": p["px"],
                                   "sh": p["sh"], "won": won, "pnl": round(pnl, 2),
                                   "fair": p.get("fair"), "fill_s": p.get("fill_s"), "seat": SEAT_ID})
            n = len(self.s["bets"]); w = sum(1 for b in self.s["bets"] if b["won"])
            logger.info(f"[{'WIN' if won else 'LOSS'}] {p['dir']} @ {p['px']*100:.0f}c fair "
                        f"{(p.get('fair') or 0)*100:.0f}c -> {wn} | {pnl:+.2f} | net {self.s['net']:+.2f} n={n} ({w}W)")
            tg._send(f"{'✅' if won else '❌'} <b>T3 {'WIN' if won else 'LOSS'}</b> BTC {p['dir']} @ "
                     f"{p['px']*100:.0f}c ×{p['sh']} | {pnl:+.2f} | net {self.s['net']:+.2f} (n={n}) REAL")
            self.s["open"] = None
            self.save()
            return

        if self.s.get("open") or self.verdict_check():
            o = self.s.get("order")
            if self.s.get("done") and o:
                self.cancel_oid(o["oid"]); self.s["order"] = None; self.save()
            return

        # 2) manage resting order
        o = self.s.get("order")
        if o:
            m = self.matched_of(o["oid"])
            if m >= 1:
                sh = max(1, int(m))
                self.s["open"] = {"hs": o["hs"], "dir": o["dir"], "px": o["px"], "sh": sh,
                                  "slug": o["slug"], "fair": o.get("fair"),
                                  "fill_s": round(now - o.get("rest_ts", now), 1)}
                self.s["order"] = None
                logger.info(f"[FILLED] {o['dir']} @ {o['px']*100:.0f}c x{sh} fill {self.s['open']['fill_s']:.0f}s")
                tg._send(f"🤖 <b>T3 FILL</b> BTC {o['dir']} @ {o['px']*100:.0f}c ×{sh}")
                self.save()
                return
            if o["hs"] != hs or t_left < T_CANCEL:
                self.cancel_oid(o["oid"])
                time.sleep(1.5)
                m = self.matched_of(o["oid"])
                if m >= 1:
                    sh = max(1, int(m))
                    self.s["open"] = {"hs": o["hs"], "dir": o["dir"], "px": o["px"], "sh": sh,
                                      "slug": o["slug"], "fair": o.get("fair"),
                                      "fill_s": round(time.time() - o.get("rest_ts", now), 1)}
                    logger.info(f"[FILLED-RACE] {o['dir']} @ {o['px']*100:.0f}c x{sh}")
                else:
                    logger.info(f"[CANCEL] unfilled {o['dir']} @ {o['px']*100:.0f}c ($0)")
                self.s["order"] = None
                self.save()
            return

        # 3) decide once per hour inside the last 10 minutes
        if self.s.get("decided_hs") == hs or not (T_LO <= t_left <= T_HI):
            return
        if any(b["hs"] == hs for b in self.s["bets"]):
            self.s["decided_hs"] = hs; self.save(); return
        mk = market_for()
        if not mk:
            return
        toks, slug = mk
        opn = self.opens.get(hs)
        if opn is None:
            try:
                opn = candle_open(hs); self.opens[hs] = opn
            except Exception:
                return
        spot = binance_ws.get_price(COIN)
        sig = binance_ws.get_realized_vol(COIN, SIGMA_LOOKBACK)
        if not (spot and opn and sig):
            return
        pu = p_up(spot, opn, sig, t_left)
        if pu is None:
            return
        ub, ua = self.book_top(toks.get("UP", ""))
        db, da = self.book_top(toks.get("DOWN", ""))
        if not (ua and ub and da and db):
            return
        best = None
        for d, ask, bid, fair in (("UP", ua, ub, pu), ("DOWN", da, db, 1.0 - pu)):
            if (ask - bid) > MAX_SPREAD:
                continue
            px = maker_px(ask, bid)
            if not (MIN_PX <= px <= MAX_PX) or not (FAIR_LO < fair < FAIR_HI):
                continue
            edge = fair - px
            if edge >= EDGE_MIN and (best is None or edge > best["edge"]):
                best = {"dir": d, "px": px, "fair": round(fair, 4), "edge": round(edge, 4),
                        "ask": ask, "bid": bid, "tok": toks[d]}
        if not best:
            if t_left <= 45:
                self.s["decided_hs"] = hs; self.save()
                logger.info(f"[T3 SKIP] hs={hs} no 4c edge in last 10m")
            return
        self.s["decided_hs"] = hs
        try:
            res = self.client.create_and_post_order(
                OrderArgs(price=best["px"], size=SHARES, side=BUY, token_id=best["tok"]),
                PartialCreateOrderOptions(tick_size="0.01"), OrderType.GTC)
            oid = (res or {}).get("orderID") or (res or {}).get("orderId")
            if not oid:
                logger.warning(f"[ORDER FAIL] no oid {res}"); self.save(); return
            self.s["order"] = {"oid": oid, "hs": hs, "dir": best["dir"], "px": best["px"],
                               "fair": best["fair"], "slug": slug, "rest_ts": now}
            logger.info(f"[REST] {best['dir']} @ {best['px']*100:.0f}c x{SHARES} fair "
                        f"{best['fair']*100:.0f}c edge {best['edge']*100:.1f}c T={t_left:.0f}s LIVE")
            tg._send(f"⏱ <b>T3 LIVE REST</b> BTC <b>{best['dir']}</b> @ {best['px']*100:.0f}c ×{SHARES} "
                     f"fair {best['fair']*100:.0f}c edge {best['edge']*100:.1f}c, {t_left:.0f}s left. REAL money.",
                     dedup_key=f"t3live-{hs}")
            self.save()
        except Exception as e:
            logger.warning(f"[ORDER FAIL] {e}"); self.save()

    def run(self):
        n = len(self.s["bets"]); w = sum(1 for b in self.s["bets"] if b.get("won"))
        logger.info(f"=== T3 LIVE start | {SEAT_ID} | settles {n} ({w}W) net {self.s['net']:+.2f} "
                    f"| 5sh stop -${abs(STOP_NET):.0f} n={STOP_N} | last 10m BTC Φ ===")
        binance_ws.start()
        while True:
            try:
                self.step()
            except Exception as e:
                logger.warning(f"loop error: {e}")
            now = time.time(); t_left = 3600 - (now % 3600)
            time.sleep(2.0 if t_left <= T_HI + 30 else 15.0)


if __name__ == "__main__":
    T3Live().run()
