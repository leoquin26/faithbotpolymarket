#!/usr/bin/env python3
"""HOUR-BOT — standalone MAKER audition of Polymarket HOURLY crypto markets.

The final unmeasured cell of the 5-week map. Evidence basis (1,812 resolved
hourly windows, chronological 70/30 split): the ask is calibrated (no taker
edge anywhere), but early-hour maker entries priced at bid+1c measured +3..+8%
ROI in BOTH splits — a number that is mostly the 2-3c spread and is only real
if resting fills are benign. That cannot be known from snapshots. This bot
answers it live, minimally:

  - favourite only (higher-priced side), ask 0.55-0.92
  - entry: rest GTC at min(bid+1c, ask-1c) when 30-55min remain
  - cancel unfilled below 30min; a miss costs $0
  - 5 shares flat, ONE live bet at a time across all coins (no correlation)
  - hold to settlement; winner from gamma outcomePrices (census-proven pattern)
  - HARD AUDIT STOPS, pre-registered: quit forever at 40 settles (report
    verdict) or net <= -$15, whichever first. No scale-ups. No second chances.

Runs beside (not inside) the retired clean_bot. State: hour_bot_state.json.
"""
import json, os, time
import urllib.request

os.environ.setdefault("PROXY_PORT", "9055")
import force_tor  # noqa: proxy for CLOB orders, same as clean_bot

from order_manager import OrderManager
from py_clob_client_v2.clob_types import OrderArgs, OrderType, PartialCreateOrderOptions
from py_clob_client_v2.order_builder.constants import BUY
from loguru import logger
import telegram_notifier as tg

V3 = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(V3, "micro_bot_state.json")
GATE = os.path.join(V3, "gate_state.json")
logger.remove()
import sys
logger.add(sys.stdout, level="INFO", format="{time:HH:mm:ss} | {message}")
logger.add(os.path.join(V3, "micro_bot.log"), level="INFO",
           format="{time:YYYY-MM-DD HH:mm:ss} | {message}", rotation="20 MB")

COINS = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "XRP": "xrp"}
SHARES = 3            # MICRO-AUDITION v2 (seat_scan 2026-08-13): high-band favourites
MIN_ASK, MAX_ASK = 0.75, 0.85  # the band that survived — five adjacent cells z+2.0..+3.1, green last-14d
MIN_MID = 0.55        # trivially true up here; kept for brain_dump compatibility
MAX_SPREAD = 0.03     # tight books only — the wide-book lesson carried over
T_ENTRY_MAX, T_ENTRY_MIN, T_CANCEL = 3000, 1200, 1200   # entry 20-50min left
STOP_N, STOP_NET = 40, -10.0   # micro stops: full stop-out still leaves bankroll > $77.84 launch
ET_OFFSET = 4          # EDT; capture tool falls back to 5 (EST) on miss — we try both


def http_json(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": "hour-bot"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode())


def hour_slug(name, offset_h):
    et = time.gmtime(time.time() - offset_h * 3600)
    h = et.tm_hour % 12 or 12
    ap = "am" if et.tm_hour < 12 else "pm"
    month = time.strftime("%B", et).lower()
    return f"{name}-up-or-down-{month}-{et.tm_mday}-{et.tm_year}-{h}{ap}-et"


def market_for(coin):
    """(condition_id, {outcome: token_id}) for the CURRENT hour, or None."""
    for off in (ET_OFFSET, 5):
        try:
            ev = http_json("https://gamma-api.polymarket.com/events?slug="
                           + hour_slug(COINS[coin], off))
        except Exception:
            continue
        if not ev:
            continue
        mk = (ev[0].get("markets") or [None])[0]
        if not mk:
            continue
        ids = mk.get("clobTokenIds")
        outs = mk.get("outcomes")
        if isinstance(ids, str):
            ids = json.loads(ids)
        if isinstance(outs, str):
            outs = json.loads(outs)
        if ids and outs and len(ids) == 2:
            return mk.get("conditionId"), dict(zip([o.upper() for o in outs], ids)), \
                mk.get("slug") or hour_slug(COINS[coin], off)
    return None


def winner_for(slug):
    """UP/DOWN once resolved, else None (gamma outcomePrices, census pattern)."""
    try:
        ev = http_json("https://gamma-api.polymarket.com/events?slug=" + slug)
        mk = (ev[0].get("markets") or [None])[0] if ev else None
        op = mk.get("outcomePrices") if mk else None
        outs = mk.get("outcomes") if mk else None
        if isinstance(op, str):
            op = json.loads(op)
        if isinstance(outs, str):
            outs = json.loads(outs)
        if op and outs and len(op) == 2:
            hi = 0 if float(op[0]) > 0.5 else 1
            if abs(float(op[hi]) - 1.0) < 0.05:
                return outs[hi].upper()
    except Exception:
        pass
    return None


class HourBot:
    def __init__(self):
        self.om = OrderManager()
        self.client = self.om.client
        self.s = {"bets": [], "net": 0.0, "done": "", "open": None, "order": None}
        self._brain_mk = {}                     # (coin, hs) -> market_for cache
        if os.path.exists(STATE):
            try:
                self.s.update(json.load(open(STATE)))
            except Exception:
                pass

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
        # audit fix: one retry — a transient API error here in the cancel
        # branch could orphan a filled position (wallet-vs-ledger drift)
        for attempt in (0, 1):
            try:
                od = self.client.get_order(oid) or {}
                return float(od.get("size_matched") or od.get("sizeMatched") or 0)
            except Exception:
                if attempt == 0:
                    time.sleep(1.0)
        return 0.0

    def verdict_check(self):
        # audit fix: count only real bets (px>0, sh>0) — legacy phantom
        # rows (sub-share fills) must not advance the verdict counter
        real = [b for b in self.s["bets"] if b.get("px", 0) > 0 and b.get("sh", 0) > 0]
        n = len(real)
        if self.s["done"]:
            return True
        try:  # KILL SWITCH: unseen-data gate says MIRAGE -> halt instantly
            if json.load(open(GATE)).get("status") == "MIRAGE":
                self.s["done"] = "GATE MIRAGE — micro-audition halted by unseen-data kill switch"
                logger.info(f"[MICRO VERDICT] {self.s['done']}")
                tg._send("🔴 <b>MICRO HALT</b> — " + self.s["done"])
                self.save()
                return True
        except Exception:
            pass
        if n >= STOP_N or self.s["net"] <= STOP_NET:
            w = sum(1 for b in real if b["won"])
            self.s["done"] = (f"AUDIT COMPLETE n={n} {w}W/{n-w}L net={self.s['net']:+.2f} "
                              f"(stop: {f'n>={STOP_N}' if n >= STOP_N else f'net<={STOP_NET}'})")
            logger.info(f"[HOUR VERDICT] {self.s['done']}")
            bank = (f"\n🏦 <b>BANKING LAW</b>: cycle passed — withdraw "
                    f"${self.s['net'] * 0.5:.2f} (50% of profit) from the trading "
                    f"wallet now. Banked money never rides again."
                    if self.s["net"] > 0 else "")
            tg._send(f"🏁 <b>MICRO AUDIT COMPLETE</b> — {self.s['done']}{bank}")
            self.save()
            return True
        return False

    def run(self):
        w = sum(1 for b in self.s["bets"] if b["won"])
        n = len(self.s["bets"])
        logger.info(f"=== micro-bot start | settles {n} ({w}W) net {self.s['net']:+.2f} "
                    f"| stops: n>={STOP_N} or net<={STOP_NET} ===")
        while True:
            try:
                self.step()
            except Exception as e:
                logger.warning(f"loop error: {e}")
            try:
                _now = time.time()
                _hs = int(_now // 3600) * 3600
                self.brain_dump(_hs, _hs + 3600 - _now)
            except Exception:
                pass
            time.sleep(20)

    def brain_dump(self, hs, t_left):
        """Live telemetry of the decision process — what the bot SEES each tick.
        Read by quantum_dash; fully guarded so it can never touch the trade path."""
        coins = {}
        for coin in COINS:
            if (coin, hs) not in self._brain_mk:
                self._brain_mk[(coin, hs)] = market_for(coin)
                for k in [k for k in self._brain_mk if k[1] < hs - 3600]:
                    del self._brain_mk[k]
            info = self._brain_mk.get((coin, hs))
            if not info:
                coins[coin] = {"err": "market not found"}
                continue
            _c, toks, _sl = info
            ub, ua = self.book_top(toks.get("UP", ""))
            db, da = self.book_top(toks.get("DOWN", ""))
            fav = px = None
            verdict = "no favourite"
            if ua and (not da or ua > da):
                fav, px = "UP", ua
            elif da:
                fav, px = "DOWN", da
            if fav:
                fb = ub if fav == "UP" else db
                mid = (fb + px) / 2 if fb else None
                if px > MAX_ASK:
                    verdict = "too certain (>" + str(int(MAX_ASK * 100)) + "c)"
                elif px < MIN_ASK:
                    verdict = "coinflip (<" + str(int(MIN_ASK * 100)) + "c)"
                elif mid is None or mid < MIN_MID:
                    verdict = "wide book (mid<" + str(int(MIN_MID * 100)) + "c)"
                else:
                    verdict = "IN BAND OK"
            coins[coin] = {"ub": ub, "ua": ua, "db": db, "da": da,
                           "fav": fav, "px": px, "verdict": verdict}
        b = [x for x in self.s["bets"] if x.get("sh", 0) > 0]
        state = ("DONE" if self.s.get("done") else
                 "HOLDING" if self.s.get("open") else
                 "RESTING" if self.s.get("order") else
                 "ENTRY BAND OPEN" if T_ENTRY_MIN <= t_left <= T_ENTRY_MAX else
                 "WAITING FOR BAND")
        json.dump({"ts": time.time(), "hs": hs, "t_left": int(t_left), "state": state,
                   "coins": coins, "open": self.s.get("open"), "order": self.s.get("order"),
                   "n": len(b), "w": sum(1 for x in b if x["won"]),
                   "net": round(self.s.get("net", 0.0), 2)},
                  open(os.path.join(V3, "micro_bot_brain.json"), "w"))

    def step(self):
        now = time.time()
        hs = int(now // 3600) * 3600
        t_left = hs + 3600 - now

        # 1) settle any held position whose hour has closed
        p = self.s.get("open")
        if p and now > p["hs"] + 3600 + 90:
            wn = winner_for(p["slug"])
            if wn:
                won = (wn == p["dir"])
                pnl = (1 - p["px"]) * p["sh"] if won else -p["px"] * p["sh"]
                self.s["net"] = round(self.s["net"] + pnl, 2)
                self.s["bets"].append({"hs": p["hs"], "coin": p["coin"], "dir": p["dir"],
                                       "px": p["px"], "sh": p["sh"], "won": won,
                                       "pnl": round(pnl, 2), "fill_s": p.get("fill_s")})
                n = len(self.s["bets"]); wtot = sum(1 for b in self.s["bets"] if b["won"])
                logger.info(f"[{'WIN' if won else 'LOSS'}] {p['coin']} {p['dir']} @ "
                            f"{p['px']*100:.0f}c -> {wn} | {pnl:+.2f} | audit net "
                            f"{self.s['net']:+.2f} | n={n} ({wtot}W)")
                tg._send(f"{'✅' if won else '❌'} <b>HOURLY {'WIN' if won else 'LOSS'}</b> "
                         f"{p['coin']} {p['dir']} @ {p['px']*100:.0f}c | {pnl:+.2f} | "
                         f"net {self.s['net']:+.2f} (n={n})")
                self.s["open"] = None
                self.save()
            return                                     # one thing at a time

        if self.s.get("open") or self.verdict_check():
            return

        # 2) manage a resting order
        o = self.s.get("order")
        if o:
            m = self.matched_of(o["oid"])
            if int(m) >= 1:
                sh = int(m)
                fill_s = round(now - o.get("rest_ts", now), 1)
                self.s["open"] = {"hs": o["hs"], "coin": o["coin"], "dir": o["dir"],
                                  "px": o["px"], "sh": sh, "slug": o["slug"],
                                  "fill_s": fill_s}
                self.s["order"] = None
                logger.info(f"[FILLED] {o['coin']} {o['dir']} @ {o['px']*100:.0f}c x{sh} "
                            f"(maker, fee $0, fill {fill_s:.0f}s)")
                tg._send(f"🤖 <b>MICRO FILL</b> {o['coin']} {o['dir']} @ {o['px']*100:.0f}c x{sh}")
                self.save()
                return
            expired = (o["hs"] != hs) or (t_left < T_CANCEL)
            if expired:
                try:
                    self.client.cancel(o["oid"])
                except Exception:
                    pass
                time.sleep(1.5)                        # v1.61.1 lesson: async fills lie
                m = self.matched_of(o["oid"])
                if int(m) >= 1:
                    sh = int(m)
                    self.s["open"] = {"hs": o["hs"], "coin": o["coin"], "dir": o["dir"],
                                      "px": o["px"], "sh": sh, "slug": o["slug"],
                                      "fill_s": round(time.time() - o.get("rest_ts", now), 1)}
                    logger.info(f"[FILLED-RACE] {o['coin']} {o['dir']} @ {o['px']*100:.0f}c x{sh}")
                else:
                    logger.info(f"[CANCEL] unfilled {o['coin']} {o['dir']} @ {o['px']*100:.0f}c ($0 lost)")
                self.s["order"] = None
                self.save()
            return

        # 3) maybe place one new rest (entry band only, one per hour window)
        if not (T_ENTRY_MIN <= t_left <= T_ENTRY_MAX):
            return
        if any(b["hs"] == hs for b in self.s["bets"]):
            return
        cands = []
        for coin in COINS:
            mk = market_for(coin)
            if not mk:
                continue
            cond, toks, slug = mk
            ub_bid, ub_ask = self.book_top(toks.get("UP", ""))
            db_bid, db_ask = self.book_top(toks.get("DOWN", ""))
            if ub_ask and (not db_ask or ub_ask > db_ask):
                d, ask, bid, tok = "UP", ub_ask, ub_bid, toks["UP"]
            elif db_ask:
                d, ask, bid, tok = "DOWN", db_ask, db_bid, toks["DOWN"]
            else:
                continue
            if not bid or not (MIN_ASK <= ask <= MAX_ASK):
                continue
            if (bid + ask) / 2 < MIN_MID or (ask - bid) > MAX_SPREAD:
                logger.info(f"[SKIP] {coin} {d} ask {ask*100:.0f}c spread "
                            f"{(ask-bid)*100:.0f}c — book too wide for the micro seat")
                continue
            px = round(min(bid + 0.01, ask - 0.01), 2)
            if px < 0.02:
                continue
            cands.append((round(ask - bid, 2), coin, d, ask, bid, px, tok, slug))
        # tightest spread first: tight books were the only split-consistent
        # positive slice (<=2c: +2.7% ROI both halves); wide books fake favourites
        for spread, coin, d, ask, bid, px, tok, slug in sorted(cands):
            if len(cands) > 1:
                logger.info(f"[PICK] {coin} (spread {spread*100:.0f}c) over "
                            + ", ".join(f"{c[1]} ({c[0]*100:.0f}c)" for c in sorted(cands)[1:]))
            try:
                res = self.client.create_and_post_order(
                    OrderArgs(price=px, size=SHARES, side=BUY, token_id=tok),
                    PartialCreateOrderOptions(tick_size="0.01"), OrderType.GTC)
                oid = (res or {}).get("orderID") or (res or {}).get("orderId")
                if oid:
                    self.s["order"] = {"oid": oid, "hs": hs, "coin": coin, "dir": d,
                                       "px": px, "slug": slug, "rest_ts": now}
                    logger.info(f"[REST] {coin} {d} @ {px*100:.0f}c x{SHARES} "
                                f"(bid {bid*100:.0f}/ask {ask*100:.0f}) T={t_left/60:.0f}min")
                    # SIGNAL FEED: the decision itself, pushed the moment it's made —
                    # the owner can mirror it manually or just watch the engine work.
                    tg._send(f"📡 <b>MICRO SIGNAL 75-85c</b> — {coin} favourite is <b>{d}</b> "
                             f"(ask {ask*100:.0f}c). Bot resting {px*100:.0f}c ×{SHARES}, "
                             f"{t_left/60:.0f}min to close. Mirror: buy {d} ≤ {px*100:.0f}c "
                             f"on the {coin} 1h market, hold to settlement.",
                             dedup_key=f"msig-{coin}-{hs}")
                    self.save()
                    return
            except Exception as e:
                logger.warning(f"[ORDER FAIL] {coin}: {e}")
        return


if __name__ == "__main__":
    HourBot().run()
