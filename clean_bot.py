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
import os, sys, time, json, csv, threading, datetime
from dataclasses import dataclass, field

os.environ.setdefault("PROXY_PORT", "9055")          # Ireland tunnel for orders
import force_tor  # noqa: applies proxy on import

import httpx
import binance_ws
import chainlink_ws   # CRITICAL: Polymarket settles on Chainlink — strike+spot must be Chainlink, not Binance (~10bps cross-feed basis flips near-money bets)
from market_data import get_market_info
from order_manager import OrderManager
from py_clob_client_v2.clob_types import OrderArgs, OrderType, PartialCreateOrderOptions
from py_clob_client_v2.order_builder.constants import BUY, SELL
from loguru import logger
import telegram_notifier as tg

V3 = os.path.expanduser("~/v3-bot")
RESEARCH_CSV = os.path.join(V3, "clean_bot_research.csv")  # per-window feature+outcome dataset
RESEARCH_COLS = ["ts", "window_start", "coin", "dir", "drift_pct", "roc60_bps", "roc300_bps",
                 "sigma", "fav_ask", "up_ask", "down_ask", "btc_drift_pct", "sol_drift_pct",
                 "confirmed", "model_prob", "decision", "reason", "t_left", "winner", "drift_correct"]
logger.remove()
logger.add(sys.stdout, level="INFO", format="{time:HH:mm:ss} | {message}")
logger.add(os.path.join(V3, "clean_bot.log"), level="INFO",
           format="{time:YYYY-MM-DD HH:mm:ss} | {message}", rotation="20 MB")


VERSION = "1.9.0"   # bump on EVERY change + add a CHANGELOG.md entry + git tag cleanbot-vX.Y.Z


@dataclass
class Cfg:
    coins: tuple = ("ETH", "SOL")
    drift_bps: float = float(os.getenv("CLEAN_DRIFT_BPS", "7"))      # min early move
    min_t: int = int(os.getenv("CLEAN_MIN_T", "600"))               # only enter with >=10min left
    warmup: int = int(os.getenv("CLEAN_WARMUP", "60"))              # let strike settle
    max_ask: float = float(os.getenv("CLEAN_MAX_ASK", "0.66"))      # never overpay
    min_ask: float = float(os.getenv("CLEAN_MIN_ASK", "0.45"))      # avoid junk longshots
    maker_offset: float = float(os.getenv("CLEAN_MAKER_OFFSET", "0.01"))
    shares: int = int(os.getenv("CLEAN_SHARES", "5"))               # exchange min (floor)
    gtc_max_age: int = int(os.getenv("CLEAN_GTC_MAX_AGE", "180"))   # cancel unfilled after
    dry: bool = os.getenv("CLEAN_DRY", "true").lower() in ("1", "true", "yes", "on")
    # ── compounding: bet size scales with the bankroll (half-Kelly) ──
    compound: bool = os.getenv("CLEAN_COMPOUND", "true").lower() in ("1", "true", "yes", "on")
    start_bankroll: float = float(os.getenv("CLEAN_START_BANKROLL", "48"))  # seed; set to real balance
    kelly_frac: float = float(os.getenv("CLEAN_KELLY_FRAC", "0.08"))   # half-Kelly (~8% of bankroll)
    max_bet_pct: float = float(os.getenv("CLEAN_MAX_BET_PCT", "0.10")) # never >10% of bankroll/bet
    max_open_pct: float = float(os.getenv("CLEAN_MAX_OPEN_PCT", "0.25"))  # cap total open exposure
    stop_pct: float = float(os.getenv("CLEAN_STOP_PCT", "0.15"))       # daily stop = 15% of bankroll
    daily_stop_floor: float = float(os.getenv("CLEAN_DAILY_STOP", "6.0"))  # $ floor for the stop
    # ── whipsaw protection: pause after N losses in a row ──
    loss_breaker: int = int(os.getenv("CLEAN_LOSS_BREAKER", "3"))      # 0 = off
    breaker_cooldown: int = int(os.getenv("CLEAN_BREAKER_COOLDOWN", "1800"))  # pause sec (30m)
    # ── cross-coin confirmation: follower coins (ETH) only trade when the broader
    # market drifts the same way (ETH-solo/divergent drifts revert ~22%/0%) ──
    confirm_coins: tuple = tuple(c for c in os.getenv("CLEAN_CONFIRM_COINS", "ETH").split(",") if c)
    confirm_market: tuple = tuple(c for c in os.getenv("CLEAN_CONFIRM_MARKET", "BTC,SOL").split(",") if c)
    confirm_bps: float = float(os.getenv("CLEAN_CONFIRM_BPS", "3"))    # proxy lean threshold
    # ── research data capture (read-only; every real-move window, traded or not) ──
    research: bool = os.getenv("CLEAN_RESEARCH", "on").lower() in ("1", "true", "yes", "on")
    research_min_bps: float = float(os.getenv("CLEAN_RESEARCH_MIN_BPS", "3"))  # log windows >= this drift
    # ── ML model gate (v1.6): calibrated P(drift wins) from drift_model_band.joblib ──
    model_path: str = os.getenv("CLEAN_MODEL_PATH", "drift_model_band.joblib")
    model_gate: bool = os.getenv("CLEAN_MODEL_GATE", "off").lower() in ("1", "true", "yes", "on")
    model_min_prob: float = float(os.getenv("CLEAN_MODEL_MIN_PROB", "0.80"))
    # ── active exit (v1.9): book the favorable move; dodge the late settlement reversal ──
    tp_enabled: bool = os.getenv("CLEAN_TP", "on").lower() in ("1", "true", "yes", "on")
    tp_delta: float = float(os.getenv("CLEAN_TP_DELTA", "0.12"))       # sell when token gains >= this (lock profit)
    exit_before_s: int = int(os.getenv("CLEAN_EXIT_BEFORE", "180"))    # sell N sec before close (dodge last-3min reversal)
    tp_stop_delta: float = float(os.getenv("CLEAN_TP_STOP", "0.20"))   # cut early if token drops >= this


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


def _roc(ticks, sec):
    """Rate-of-change (fraction) over `sec` seconds from a tick list (robust to
    (ts,price) vs (price,ts) ordering)."""
    try:
        if not ticks or len(ticks) < 2:
            return 0.0
        def ts(t): return t[0] if t[0] > 1e8 else t[1]
        def px(t): return t[1] if t[0] > 1e8 else t[0]
        now_t = ts(ticks[-1]); now_p = px(ticks[-1]); base = None
        for t in reversed(ticks):
            if now_t - ts(t) >= sec:
                base = px(t); break
        if base is None:
            base = px(ticks[0])
        return (now_p - base) / base if base else 0.0
    except Exception:
        return 0.0


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
        self.bankroll = CFG.start_bankroll      # compounds with each resolved trade
        self.consec_losses = 0                  # whipsaw breaker counter
        self.breaker_until = 0.0                # cooldown end timestamp
        self._stop_notified = False
        self._nc_logged = set()                 # throttle [NO CONFIRM] logs (per window)
        self._research = {}                     # (coin,ws) -> research row pending resolution
        self._research_seen = set()             # one research row per window
        self.model = None                       # calibrated P(drift wins) model (v1.6)
        self._mp_cache = {}                     # (coin,ws) -> model prob (avoid refetch)
        try:
            import joblib
            self.model = joblib.load(CFG.model_path)
            logger.info(f"model loaded: {CFG.model_path} (feats {self.model['feats']}) "
                        f"gate={'ON' if CFG.model_gate else 'shadow'} min_prob={CFG.model_min_prob}")
        except Exception as e:
            logger.warning(f"model not loaded ({e}) — running without ML gate")
        self._load()

    # ── persistence ──────────────────────────────────────────────────
    def _today(self):
        return datetime.datetime.now().strftime("%Y-%m-%d")

    def _save(self):
        try:
            json.dump({"day": self.day, "wins": self.wins, "losses": self.losses,
                       "bankroll": round(self.bankroll, 2),
                       "mode": "DRY" if CFG.dry else "LIVE", "version": VERSION,
                       "consec_losses": self.consec_losses, "breaker_until": self.breaker_until,
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
            self.bankroll = d.get("bankroll", CFG.start_bankroll)   # persisted, compounds
            self.consec_losses = d.get("consec_losses", 0)
            self.breaker_until = d.get("breaker_until", 0.0)
            self.positions = d.get("positions", {})
            self.traded = {tuple(t) for t in d.get("traded", [])}
            logger.info(f"state reloaded: {len(self.positions)} positions, bankroll "
                        f"${self.bankroll:.2f}, day net={self.wins - self.losses:+.2f}")
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

    def _stop_amount(self):
        # daily stop scales with bankroll, with a $ floor
        return max(CFG.daily_stop_floor, self.bankroll * CFG.stop_pct)

    def _stopped(self):
        return (self.losses - self.wins) >= self._stop_amount()

    def _open_exposure(self):
        exp = sum(o["price"] * o["shares"] for o in self.open_orders.values())
        exp += sum(p["entry"] * p["shares"] for p in self.positions.values()
                   if p.get("status") == "filled")
        return exp

    def _size_shares(self, price):
        """Bet size = half-Kelly fraction of current bankroll, share-floored + capped."""
        if not CFG.compound:
            return CFG.shares
        stake = min(self.bankroll * CFG.kelly_frac, self.bankroll * CFG.max_bet_pct)
        return max(CFG.shares, int(round(stake / max(0.02, price))))

    def _market_confirms(self, coin, direction):
        """Soft cross-coin confirmation: is the broader market drifting the same
        way? Each proxy (BTC/SOL) that leans the same direction >= confirm_bps
        votes +1; opposing votes -1. Confirmed if net > 0. ETH-solo (market flat)
        and ETH-vs-market (divergent) → not confirmed (those revert ~22%/0%)."""
        want = 1.0 if direction.upper().startswith("UP") else -1.0
        thr = CFG.confirm_bps / 10000.0
        votes = have = 0
        for p in CFG.confirm_market:
            if p == coin:
                continue
            try:
                info = get_market_info(p)
                strike = float(info.threshold_price or 0) if info else 0
                px = float((info.current_crypto_price if info else 0) or binance_ws.get_price(p))
                if strike <= 0 or px <= 0:
                    continue
                d = (px - strike) / strike
            except Exception:
                continue
            have += 1
            if d * want >= thr:
                votes += 1
            elif d * want <= -thr:
                votes -= 1
        if have == 0:
            return True     # no market data (transient) → fail-open, don't halt ETH
        return votes > 0

    def _coin_drift(self, coin):
        """Current drift (fraction) of a coin vs its window-open strike, or None."""
        try:
            info = get_market_info(coin)
            strike = float(info.threshold_price or 0) if info else 0
            px = float(binance_ws.get_price(coin) or (info.current_crypto_price if info else 0))
            return (px - strike) / strike if (strike > 0 and px > 0) else None
        except Exception:
            return None

    def _model_prob(self, coin, ws):
        """Calibrated P(betting sign(drift) wins) from binance 1m klines via the
        shared feature module (parity with training). None if unavailable."""
        if not self.model:
            return None
        ck = (coin, ws)
        if ck in self._mp_cache:
            return self._mp_cache[ck]
        result = None
        try:
            import model_features as MF
            def kl(sym):
                u = (f"https://api.binance.us/api/v3/klines?symbol={sym}USDT"
                     f"&interval=1m&startTime={ws * 1000}&limit=6")
                return httpx.get(u, timeout=6, trust_env=False).json()
            kc = kl(coin)
            if not isinstance(kc, list) or len(kc) < 3:
                return None
            strike = float(kc[0][1])
            early = [float(b[4]) for b in kc[1:]]      # 1-min closes since open
            btc_d = None
            kb = kl("BTC")
            if isinstance(kb, list) and len(kb) >= 2:
                bs = float(kb[0][1]); btc_d = (float(kb[-1][4]) - bs) / bs
            hour = datetime.datetime.utcfromtimestamp(ws).hour
            feats = MF.compute(strike, early, btc_d, hour, coin)
            if feats is None:
                return None
            result = float(self.model["model"].predict_proba([feats])[0][1])
            self._mp_cache[ck] = result          # cache successes only (allow retry on errors)
            return result
        except Exception as e:
            logger.debug(f"model_prob error {coin}: {e}")
            return None

    def _research_scan(self, coin):
        """Capture EVERY real-move window (drift >= research_min_bps), traded or not,
        with full features + the actual decision. Resolved later via gamma. Fully
        isolated from trading (caller wraps in try/except) — it never places orders."""
        if not CFG.research:
            return
        info = get_market_info(coin)
        if not info:
            return
        ws = info.window_start
        rk = (coin, ws)
        if rk in self._research_seen:
            return
        now = time.time(); t_rem = ws + 900 - now; age = now - ws
        if age < CFG.warmup or t_rem < CFG.min_t:
            return
        strike = float(info.threshold_price or 0)
        px = float(info.current_crypto_price or binance_ws.get_price(coin) or 0)
        if strike <= 0 or px <= 0:
            return
        dist = (px - strike) / strike
        if abs(dist) < CFG.research_min_bps / 10000.0:
            return
        self._research_seen.add(rk)
        direction = "UP" if dist > 0 else "DOWN"
        ticks = binance_ws.get_tick_history(coin, 300)
        roc60 = _roc(ticks, 60); roc300 = _roc(ticks, 300)
        sigma = binance_ws.get_realized_vol(coin, 180)
        up_b = down_b = {}
        try: up_b = self.om.get_clob_book(info.up_token_id) or {}
        except Exception: pass
        try: down_b = self.om.get_clob_book(info.down_token_id) or {}
        except Exception: pass
        up_ask = up_b.get("ask"); down_ask = down_b.get("ask")
        fav_ask = up_ask if direction == "UP" else down_ask
        btc_d = self._coin_drift("BTC"); sol_d = self._coin_drift("SOL")
        confirmed = (coin not in CFG.confirm_coins) or self._market_confirms(coin, direction)
        mp = self._model_prob(coin, ws)
        if rk in self.traded:
            decision, reason = "ENTER", ""
        elif abs(dist) < CFG.drift_bps / 10000.0:
            decision, reason = "SKIP", "weak_drift"
        elif not confirmed:
            decision, reason = "SKIP", "no_confirm"
        elif not fav_ask or not (CFG.min_ask <= float(fav_ask) <= CFG.max_ask):
            decision, reason = "SKIP", "ask_out_of_zone"
        else:
            decision, reason = "SKIP", "exposure_or_timing"
        # live visibility: one line per real-move window so the dashboard shows what's happening
        logger.info(f"[WATCH] {coin} {direction} drift={dist*1e4:+.1f}bps "
                    f"ask={int(round(fav_ask*100)) if fav_ask else '?'}c t={int(t_rem)}s "
                    f"-> {decision}" + (f":{reason}" if reason else ""))
        self._research[rk] = {
            "ts": datetime.datetime.utcnow().isoformat(timespec="seconds"),
            "window_start": ws, "coin": coin, "dir": direction,
            "drift_pct": round(dist * 100, 4),
            "roc60_bps": round(roc60 * 10000, 1), "roc300_bps": round(roc300 * 10000, 1),
            "sigma": round(float(sigma or 0), 6),
            "fav_ask": int(round(fav_ask * 100)) if fav_ask else "",
            "up_ask": int(round(up_ask * 100)) if up_ask else "",
            "down_ask": int(round(down_ask * 100)) if down_ask else "",
            "btc_drift_pct": round(btc_d * 100, 4) if btc_d is not None else "",
            "sol_drift_pct": round(sol_d * 100, 4) if sol_d is not None else "",
            "confirmed": int(bool(confirmed)),
            "model_prob": round(mp, 3) if mp is not None else "",
            "decision": decision, "reason": reason, "t_left": int(t_rem)}

    def _research_resolve(self):
        """Resolve logged research windows via gamma; append the complete row
        (features + decision + true outcome) to clean_bot_research.csv."""
        now = time.time()
        for rk, row in list(self._research.items()):
            coin, ws = rk
            if now < ws + 960:
                continue
            w = gamma_winner(coin, ws)
            if not w:
                continue
            row["winner"] = w
            row["drift_correct"] = int(row["dir"] == w)
            # re-label decision from the FINAL traded state (scan-time capture can
            # predate the entry → a traded window may have been logged as SKIP).
            if rk in self.traded:
                row["decision"] = "ENTER"; row["reason"] = ""
            new = (not os.path.exists(RESEARCH_CSV)) or os.path.getsize(RESEARCH_CSV) == 0
            try:
                with open(RESEARCH_CSV, "a", newline="") as f:
                    wr = csv.DictWriter(f, fieldnames=RESEARCH_COLS, extrasaction="ignore")
                    if new:
                        wr.writeheader()
                    wr.writerow(row)
            except Exception as e:
                logger.warning(f"research write failed: {e}")
            self._research.pop(rk, None)

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
        px = info.current_crypto_price or binance_ws.get_price(coin)   # Chainlink spot (settlement feed) first
        px = float(px or 0)
        if strike <= 0 or px <= 0:
            return
        dist = (px - strike) / strike
        if abs(dist) < CFG.drift_bps / 10000.0:        # need a clear early drift
            return
        is_up = dist > 0
        token = info.up_token_id if is_up else info.down_token_id
        direction = "UP" if is_up else "DOWN"
        # cross-coin confirmation for follower coins (ETH): only trade when the
        # broader market drifts the same way; skip ETH-solo / market-divergent.
        if coin in CFG.confirm_coins and not self._market_confirms(coin, direction):
            if (coin, ws) not in self._nc_logged:
                logger.info(f"[NO CONFIRM] {coin} {direction} drift={dist*100:+.3f}% "
                            f"— market not aligned, skip")
                self._nc_logged.add((coin, ws))
            return
        # ML gate (v1.6): calibrated P(drift wins). Always shadow-logs; only blocks if enabled.
        mp = self._model_prob(coin, ws)
        if mp is not None and CFG.model_gate and mp < CFG.model_min_prob:
            if (coin, ws) not in self._nc_logged:
                logger.info(f"[MODEL SKIP] {coin} {direction} prob={mp:.2f} < {CFG.model_min_prob}")
                self._nc_logged.add((coin, ws))
            return
        book = {}
        try:
            book = self.om.get_clob_book(token) or {}
        except Exception:
            pass
        ask = book.get("ask")
        if not ask or not (CFG.min_ask <= float(ask) <= CFG.max_ask):
            return
        maker = round(max(0.02, float(ask) - CFG.maker_offset), 2)
        shares = self._size_shares(maker)
        # cap total simultaneous exposure (correlated crypto risk)
        if self._open_exposure() + maker * shares > self.bankroll * CFG.max_open_pct:
            return  # too much already at risk right now — wait for a slot
        self.traded.add(key)
        _mp = f" prob={mp:.2f}" if mp is not None else ""
        logger.info(f"[ENTER] {coin} {direction} drift={dist*100:+.3f}% ask={float(ask)*100:.0f}c "
                    f"-> maker {maker*100:.0f}c x{shares} (${maker*shares:.2f}, bankroll ${self.bankroll:.0f})"
                    f"{_mp} T={t_rem:.0f}s" + (" [DRY]" if CFG.dry else ""))
        if CFG.dry:
            # paper-trade: assume the maker fills, then track the full lifecycle
            # (sim fill → gamma resolve → sim P&L/bankroll) exactly like a live trade.
            self.positions[f"{coin}:{ws}"] = {"coin": coin, "ws": ws, "dir": direction,
                                              "entry": maker, "shares": shares, "token": token,
                                              "status": "filled", "sim": True}
            logger.info(f"[SIM FILL] {coin} {direction} @ {maker*100:.0f}c x{shares} (paper)")
            self._save()
            return
        try:
            res = self.client.create_and_post_order(
                OrderArgs(price=maker, size=shares, side=BUY, token_id=token),
                PartialCreateOrderOptions(tick_size="0.01"), OrderType.GTC)
            oid = (res or {}).get("orderID") or (res or {}).get("orderId")
            if oid:
                self.open_orders[oid] = {"coin": coin, "ws": ws, "dir": direction,
                                         "token": token, "price": maker,
                                         "shares": shares, "ts": now}
                logger.info(f"[GTC] resting {coin} {direction} @ {maker*100:.0f}c x{shares} oid={oid[:10]}")
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
                    "coin": o["coin"], "ws": o["ws"], "dir": o["dir"], "token": o["token"],
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

    # ── active exit: book the move, dodge the late reversal (v1.9) ─────
    def manage_positions(self):
        """For each open position, take profit if the token appreciated, cut if it
        dropped, and ALWAYS exit a few minutes before close — so we capture the
        favorable mid-window move instead of riding it into the settlement reversal."""
        if not CFG.tp_enabled:
            return
        now = time.time()
        for k, p in list(self.positions.items()):
            if p["status"] != "filled" or not p.get("token"):
                continue
            try:
                bid = (self.om.get_clob_book(p["token"]) or {}).get("bid")
            except Exception:
                continue
            if bid is None:
                continue
            bid = float(bid)
            gain = bid - p["entry"]
            t_left = p["ws"] + 900 - now
            if gain >= CFG.tp_delta:
                self._close_position(k, p, bid, "TP")
            elif gain <= -CFG.tp_stop_delta:
                self._close_position(k, p, bid, "STOP")
            elif 0 < t_left <= CFG.exit_before_s:
                self._close_position(k, p, bid, "TIME")

    def _close_position(self, k, p, bid, why):
        """Sell the position now (marketable taker) and realize the P&L, instead of
        holding to settlement. 7% taker fee = 0.07*p*(1-p)/share."""
        sh = p["shares"]; entry = p["entry"]
        sell_px = round(max(0.01, bid - 0.01), 2)        # cross the bid to ensure the fill
        if not CFG.dry:
            try:
                res = self.client.create_and_post_order(
                    OrderArgs(price=sell_px, size=sh, side=SELL, token_id=p["token"]),
                    PartialCreateOrderOptions(tick_size="0.01"), OrderType.FOK)
                matched = float((res or {}).get("size_matched") or (res or {}).get("sizeMatched") or 0)
                if matched <= 0:
                    if why == "TIME":
                        logger.info(f"[EXIT-MISS] {p['coin']} {p['dir']} sell @ {sell_px*100:.0f}c "
                                    f"no fill ({why}) — will retry / fall through to settlement")
                    return
            except Exception as e:
                logger.warning(f"[EXIT FAIL] {p['coin']} {p['dir']}: {e}")
                return
        fee = 0.07 * sell_px * (1 - sell_px) * sh
        pnl = (sell_px - entry) * sh - fee
        p["status"] = "closed"; p["pnl"] = round(pnl, 2); p["exit"] = sell_px; p["exit_why"] = why
        self.bankroll += pnl
        if pnl >= 0:
            self.wins += pnl; self.consec_losses = 0
        else:
            self.losses += -pnl; self.consec_losses += 1
        net = self.wins - self.losses
        logger.info(f"[EXIT-{why}] {p['coin']} {p['dir']} {entry*100:.0f}c -> sold {sell_px*100:.0f}c "
                    f"| {pnl:+.2f} | bankroll ${self.bankroll:.2f} | day net {net:+.2f}"
                    + (" [SIM]" if p.get("sim") else ""))
        tg._send(f"{'🧪 ' if p.get('sim') else ''}🎯 <b>EXIT-{why}</b> {p['coin']} {p['dir']} "
                 f"{entry*100:.0f}c→{sell_px*100:.0f}c | {pnl:+.2f} | 💰 ${self.bankroll:.2f}",
                 dedup_key=f"exit-{k}")
        self._save()

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
            self.bankroll += pnl                       # ← compound
            # whipsaw breaker: pause after N losses in a row (choppy regimes invert the edge)
            if won:
                self.consec_losses = 0
            else:
                self.consec_losses += 1
                if CFG.loss_breaker > 0 and self.consec_losses >= CFG.loss_breaker:
                    self.breaker_until = time.time() + CFG.breaker_cooldown
                    logger.info(f"[BREAKER] {self.consec_losses} losses in a row — "
                                f"pausing {CFG.breaker_cooldown // 60}min (whipsaw)")
                    tg._send(f"🧊 <b>Loss-breaker</b>: {self.consec_losses} in a row → "
                             f"pause {CFG.breaker_cooldown // 60}min (whipsaw protection)")
                    self.consec_losses = 0   # fresh start after the cooldown
            net = self.wins - self.losses
            logger.info(f"[{'WIN' if won else 'LOSS'}] {p['coin']} {p['dir']} @ "
                        f"{entry*100:.0f}c -> {w} | {pnl:+.2f} | bankroll ${self.bankroll:.2f} "
                        f"| day net {net:+.2f}" + (" [SIM]" if p.get("sim") else ""))
            tg._send(f"{'🧪 ' if p.get('sim') else ''}{'✅ <b>WIN</b>' if won else '❌ <b>LOSS</b>'}"
                     f"{' (sim)' if p.get('sim') else ''} {p['coin']} {p['dir']} @ "
                     f"{entry*100:.0f}c → {w} | {pnl:+.2f} | 💰 ${self.bankroll:.2f} | day net {net:+.2f}",
                     dedup_key=f"res-{k}")
            self._save()

    # ── loop ─────────────────────────────────────────────────────────
    def run(self):
        _sz = (f"COMPOUND {int(CFG.kelly_frac*100)}%/bet" if CFG.compound
               else f"fixed {CFG.shares}sh")
        _cf = (f"confirm {'/'.join(CFG.confirm_coins)}<-{'/'.join(CFG.confirm_market)}"
               if CFG.confirm_coins else "no-confirm")
        logger.info(f"=== CleanBot v{VERSION} start | {'DRY' if CFG.dry else 'LIVE'} | "
                    f"{'/'.join(CFG.coins)} | drift>={CFG.drift_bps}bps T>={CFG.min_t}s "
                    f"ask {CFG.min_ask}-{CFG.max_ask} | {_cf} | breaker {CFG.loss_breaker}L/"
                    f"{CFG.breaker_cooldown // 60}m | {_sz} | research={'on' if CFG.research else 'off'} "
                    f"| model={('gate@'+str(CFG.model_min_prob)) if (self.model and CFG.model_gate) else ('shadow' if self.model else 'off')} "
                    f"| bankroll ${self.bankroll:.2f} stop ${self._stop_amount():.2f} ===")
        tg._send(f"🤖 <b>CleanBot v{VERSION}</b> started {'LIVE' if not CFG.dry else 'DRY'} | "
                 f"{'/'.join(CFG.coins)} · early-drift ≥{CFG.drift_bps}bps · maker · "
                 f"💰 ${self.bankroll:.2f} · {_sz} · stop ${self._stop_amount():.2f}")
        binance_ws.start()
        try:
            chainlink_ws.start()                 # settlement-feed strike+spot (Polymarket = Chainlink)
            logger.info("[CHAINLINK] feed started — strike/spot now on the settlement feed")
        except Exception as e:
            logger.warning(f"[CHAINLINK] start failed ({e}) — falling back to Binance (cross-feed basis risk)")
        time.sleep(90)
        n = 0
        while True:
            n += 1
            self._roll_day()
            try:
                self.check_orders()
                self.manage_positions()         # active exit: take profit / dodge late reversal
                self.resolve()
                if self._stopped():
                    if not self._stop_notified:
                        tg._send(f"🛑 <b>CleanBot daily stop</b> | net {self.wins - self.losses:+.2f} "
                                 f"≤ -{self._stop_amount():.2f} — no new entries today")
                        self._stop_notified = True
                    if n % 40 == 1:
                        logger.info(f"[STOP] daily net {self.wins - self.losses:+.2f} "
                                    f"<= -{self._stop_amount():.2f} — no new entries today")
                elif time.time() < self.breaker_until:
                    if n % 40 == 1:
                        logger.info(f"[BREAKER] cooldown {int((self.breaker_until - time.time()) / 60)}m "
                                    f"left — no new entries")
                else:
                    for c in CFG.coins:
                        self.scan(c)
            except Exception as e:
                logger.warning(f"loop error: {e}")
            # research data capture — fully isolated, never places orders / affects trading
            try:
                for c in CFG.coins:
                    self._research_scan(c)
                self._research_resolve()
            except Exception as e:
                logger.debug(f"research error: {e}")
            if n % 40 == 1:
                logger.info(f"… alive scan#{n} open={len(self.open_orders)} "
                            f"positions={len(self.positions)} bankroll=${self.bankroll:.2f} "
                            f"day_net={self.wins-self.losses:+.2f}")
            time.sleep(5)


if __name__ == "__main__":
    CleanBot().run()
