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
import os, sys, time, json, csv, math, threading, datetime
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
try:
    from hmm_regime import fmt as _hmm_fmt   # SHADOW regime posterior for research rows (never trades)
except Exception:
    def _hmm_fmt(coin):
        return ""

V3 = os.path.expanduser("~/v3-bot")
RESEARCH_CSV = os.path.join(V3, "clean_bot_research.csv")  # per-window feature+outcome dataset
RESEARCH_COLS = ["ts", "window_start", "coin", "dir", "drift_pct", "roc60_bps", "roc300_bps",
                 "sigma", "fav_ask", "up_ask", "down_ask", "btc_drift_pct", "sol_drift_pct",
                 "confirmed", "model_prob", "decision", "reason", "t_left", "winner", "drift_correct",
                 "er",       # efficiency ratio (regime: trend vs chop) — for regime-conditional sizing analysis
                 "flow60",   # order-flow: 60s buy/sell PRESSURE [-1..+1] (volume direction) — testing as a leading signal
                 "hmm",      # v1.34 SHADOW: 3-state HMM regime posterior 'T0.62/C0.31/P0.07' — verifier decides if it beats ER/signal-health
                 "phase",    # v1.36: 'early' (normal capture) or 'late' (last ~2-3min snapshot — momentum-into-close audition)
                 "book_imb"] # v1.41: Binance top-of-book size imbalance [-1..+1] (bid vs ask depth) — leading microstructure signal, product data-enrichment phase 1
logger.remove()
logger.add(sys.stdout, level="INFO", format="{time:HH:mm:ss} | {message}")
logger.add(os.path.join(V3, "clean_bot.log"), level="INFO",
           format="{time:YYYY-MM-DD HH:mm:ss} | {message}", rotation="20 MB")


VERSION = "1.42.0"  # bump on EVERY change + add a CHANGELOG.md entry + git tag cleanbot-vX.Y.Z


@dataclass
class Cfg:
    coins: tuple = ("ETH", "SOL")
    drift_bps: float = float(os.getenv("CLEAN_DRIFT_BPS", "5"))      # min early move (verifier: 55-74c/d>=5 is the only OOS-PASS config — frequency gives statistical power)
    min_t: int = int(os.getenv("CLEAN_MIN_T", "660"))               # v1.30.1: 720→660 (age ≤240s). Owner caught a confound in the v1.30 cut: the "late loses" full-history sample was polluted by the v1.21 forced-late era + pre-guard disasters. Post-guard-only data: 180-240s is fine (75%, small n); only 240s+ is bad in EVERY era — that tail stays cut. Re-audit at n≥30 clean-era fills.
    warmup: int = int(os.getenv("CLEAN_WARMUP", "60"))              # let strike settle
    entry_min_age: int = int(os.getenv("CLEAN_ENTRY_MIN_AGE", "60"))  # v1.28.1: reverted 150→60 (=warmup). The 150s delay was unvalidated (verifier OOS n=5, EV<0) and choked the widened config — the verified edge is on EARLY entries (t_left>750). Enter as soon as the strike settles.
    max_ask: float = float(os.getenv("CLEAN_MAX_ASK", "0.70"))      # v1.35.1: 0.74→0.70 while the book is small. KELLY GEOMETRY, not EV: at $20 the 5-share floor forces ~18%/bet; at 73c even the measured 77% WR compounds at +0.35%/trade (≈zero) and goes NEGATIVE below 75% WR, while 60c entries compound ~+1.8%. Arithmetic EV ≠ geometric growth at forced sizing. RESTORE 0.74 when bankroll ≥ ~$35 (forced fraction ≤10%).
    min_ask: float = float(os.getenv("CLEAN_MIN_ASK", "0.55"))      # widened floor (verifier-approved aggregate; the band's mix stays +EV OOS)
    maker_offset: float = float(os.getenv("CLEAN_MAKER_OFFSET", "0.01"))
    shares: int = int(os.getenv("CLEAN_SHARES", "5"))               # exchange min (floor)
    gtc_max_age: int = int(os.getenv("CLEAN_GTC_MAX_AGE", "180"))   # cancel unfilled after
    dry: bool = os.getenv("CLEAN_DRY", "true").lower() in ("1", "true", "yes", "on")
    # ── compounding: bet size scales with the bankroll (half-Kelly) ──
    compound: bool = os.getenv("CLEAN_COMPOUND", "true").lower() in ("1", "true", "yes", "on")
    start_bankroll: float = float(os.getenv("CLEAN_START_BANKROLL", "48"))  # seed; set to real balance
    kelly_frac: float = float(os.getenv("CLEAN_KELLY_FRAC", "0.08"))   # half-Kelly (~8% of bankroll)
    # ── tiered Kelly (v1.12.1): conservative while rebuilding; size UP once the bankroll
    # recovers past the bump threshold so wins compound bigger as you grow. ──
    kelly_bump: float = float(os.getenv("CLEAN_KELLY_BUMP", "0.10"))   # Kelly fraction above the threshold
    kelly_bump_at: float = float(os.getenv("CLEAN_KELLY_BUMP_AT", "70"))  # bankroll $ to start sizing up
    max_bet_pct: float = float(os.getenv("CLEAN_MAX_BET_PCT", "0.12")) # never >this % of bankroll/bet
    max_open_pct: float = float(os.getenv("CLEAN_MAX_OPEN_PCT", "0.25"))  # cap total open exposure
    corr_pair_frac: float = float(os.getenv("CLEAN_CORR_PAIR_FRAC", "0.5"))  # ETH+SOL same dir same window = 1 correlated bet, not 2: size each leg at this frac (skip 2nd leg if half < exchange min)
    corr_full_at: float = float(os.getenv("CLEAN_CORR_FULL_AT", "55"))  # v1.31: bankroll $ at which same-dir pairs trade BOTH legs full-size. Legs are +EV (aligned 69% vs 64% BE, n=884); the cap is risk-concentration: at $55+ a pair = ~12% of book (policy-sized) and a paired loss no longer eats the daily stop. Auto-unlocks as the book grows.
    corr_opposite_block: bool = os.getenv("CLEAN_CORR_OPPOSITE_BLOCK", "on").lower() in ("1","true","yes","on")  # skip a coin bet OPPOSITE a held correlated leg (divergent pairs = 55% coinflip in the data)
    position_keep_h: int = int(os.getenv("CLEAN_POSITION_KEEP_H", "48"))  # prune resolved positions older than this (state hygiene)
    stop_pct: float = float(os.getenv("CLEAN_STOP_PCT", "0.15"))       # daily stop = 15% of bankroll
    daily_stop_floor: float = float(os.getenv("CLEAN_DAILY_STOP", "6.0"))  # $ floor for the stop
    kill_floor: float = float(os.getenv("CLEAN_KILL_FLOOR", "0"))      # v1.39 HARD kill-switch: bankroll floor below which ALL new entries stop permanently (not just for the day). 0 = off. Set for the deposit test (e.g. 70 on a $100 book) = pre-committed max drawdown.
    # ── whipsaw protection: pause after N losses in a row ──
    loss_breaker: int = int(os.getenv("CLEAN_LOSS_BREAKER", "3"))      # 0 = off
    breaker_cooldown: int = int(os.getenv("CLEAN_BREAKER_COOLDOWN", "1200"))  # base pause sec (20m)
    # ── ADAPTIVE regime backoff (v1.11.1): each repeat breaker trip pauses LONGER
    # (chop persisting), capped; a win-streak resets it (regime recovered). Re-probes —
    # never a permanent block, so it keeps trading the moment the trend returns. ──
    breaker_escalate: bool = os.getenv("CLEAN_BREAKER_ESCALATE", "on").lower() in ("1", "true", "yes", "on")
    breaker_max_cooldown: int = int(os.getenv("CLEAN_BREAKER_MAX", "5400"))   # cap (90m) — still re-probes
    breaker_reset_wins: int = int(os.getenv("CLEAN_BREAKER_RESET_WINS", "2")) # win-streak that clears escalation
    # ── cross-coin confirmation: follower coins (ETH) only trade when the broader
    # market drifts the same way (ETH-solo/divergent drifts revert ~22%/0%) ──
    confirm_coins: tuple = tuple(c for c in os.getenv("CLEAN_CONFIRM_COINS", "ETH").split(",") if c)
    confirm_market: tuple = tuple(c for c in os.getenv("CLEAN_CONFIRM_MARKET", "BTC,ETH,SOL").split(",") if c)
    confirm_bps: float = float(os.getenv("CLEAN_CONFIRM_BPS", "3"))    # proxy lean threshold
    # ── research data capture (read-only; every real-move window, traded or not) ──
    research: bool = os.getenv("CLEAN_RESEARCH", "on").lower() in ("1", "true", "yes", "on")
    research_min_bps: float = float(os.getenv("CLEAN_RESEARCH_MIN_BPS", "3"))  # log windows >= this drift
    research_coins: tuple = tuple(c for c in os.getenv("CLEAN_RESEARCH_COINS", "BTC,XRP").split(",") if c)  # extra coins to SHADOW-LOG (data only, never traded) — gather edge data before expanding markets
    # ── ML model gate (v1.6): calibrated P(drift wins) from drift_model_band.joblib ──
    model_path: str = os.getenv("CLEAN_MODEL_PATH", "drift_model_band.joblib")
    model_gate: bool = os.getenv("CLEAN_MODEL_GATE", "off").lower() in ("1", "true", "yes", "on")
    model_min_prob: float = float(os.getenv("CLEAN_MODEL_MIN_PROB", "0.80"))
    # ── active exit (v1.9.1): ride SOLID winners to the full reward, bail on shaky ──
    tp_enabled: bool = os.getenv("CLEAN_TP", "on").lower() in ("1", "true", "yes", "on")
    exit_before_s: int = int(os.getenv("CLEAN_EXIT_BEFORE", "180"))    # near-close window: bail if NOT solid (deep ITM)
    deep_itm: float = float(os.getenv("CLEAN_DEEP_ITM", "0.85"))       # near close + token bid>=this → HOLD to settlement for full $1
    trail_arm: float = float(os.getenv("CLEAN_TRAIL_ARM", "0.08"))     # arm trailing stop after token gains >= this from entry
    trail_delta: float = float(os.getenv("CLEAN_TRAIL_DELTA", "0.06")) # then sell if token drops >= this from its PEAK (it turned)
    tp_stop_delta: float = float(os.getenv("CLEAN_TP_STOP", "0.20"))   # hard stop: cut if token drops >= this from entry
    # ── daytime trend confirmation (v1.10): overnight edge is trend-following. During
    # the choppier US/EU hours only bet WITH a confirmed macro trend. OVERNIGHT IS
    # UNCHANGED — this filter applies ONLY when day_start<=Lima_hour<day_end. ──
    day_trend: bool = os.getenv("CLEAN_DAY_TREND", "on").lower() in ("1", "true", "yes", "on")
    day_start: int = int(os.getenv("CLEAN_DAY_START", "9"))      # Lima hour daytime begins
    day_end: int = int(os.getenv("CLEAN_DAY_END", "20"))         # Lima hour daytime ends
    day_trend_min: float = float(os.getenv("CLEAN_DAY_TREND_MIN", "0.12"))   # min macro move % to confirm
    day_trend_lookback: int = int(os.getenv("CLEAN_DAY_TREND_LOOKBACK", "30"))  # macro lookback min (shorter = resets faster on reversal)
    rev_cooldown: int = int(os.getenv("CLEAN_REV_COOLDOWN", "150"))  # after a reversal flag, wait this many s before entering that coin — a counter-spike that un-flips the forming candle is the whipsaw that traps us (0=off)
    # ── give-back stop (v1.10): lock a winning day — pause once day P&L falls this much
    # from its peak (saves overnight gains from a reversal like the 9am one). 0=off. ──
    giveback: float = float(os.getenv("CLEAN_GIVEBACK", "10"))
    # ── PROFIT LOCK (v1.18: arms ONLY when actually up on the day). Trailing high-water-mark
    # stop on the REAL bankroll: once the day's peak is >= trail_stop ABOVE the day-start AND
    # bankroll gives back >= trail_stop from that peak, STOP — so it always locks REAL profit
    # (ends >= day-start), never a loss. hwm resets each day so a prior day's peak can't block
    # a fresh day. 0 = off. ──
    trail_stop: float = float(os.getenv("CLEAN_TRAIL_STOP", "10"))  # v1.29.1: 6→10. $6 was right for a ~$25 book; at ~$45+ it fired on a normal 2-loss burst mid-streak (positions resolve in clusters), interrupting verified-+EV volume. $10 ≈ 20% of peak still locks a real giveback.
    # ── NIGHT-ONLY w/ strong-trend daytime exception (v1.11): night (20-09) trades
    # freely; daytime (09-20) only on a STRONG macro trend; after N daytime losses in a
    # row, daytime is BLOCKED until night. ──
    night_only: bool = os.getenv("CLEAN_NIGHT_ONLY", "on").lower() in ("1", "true", "yes", "on")
    day_strong_trend: float = float(os.getenv("CLEAN_DAY_STRONG", "0.25"))  # macro % needed for a daytime trade
    day_loss_block: int = int(os.getenv("CLEAN_DAY_LOSS_BLOCK", "2"))       # daytime losses in a row -> block till night
    # ── ADAPTIVE ACCURACY (v1.12): measure rolling win rate; when accuracy drops, RAISE
    # the drift bar to take only higher-quality setups; when it's high, trade freely.
    # Learns from every resolved trade — quality knob, NOT a hard block. ──
    adapt: bool = os.getenv("CLEAN_ADAPT", "on").lower() in ("1", "true", "yes", "on")  # keeps outcome recording + [ADAPT] logging (dashboard)
    adapt_window: int = int(os.getenv("CLEAN_ADAPT_WINDOW", "15"))      # rolling trades measured
    adapt_target: float = float(os.getenv("CLEAN_ADAPT_TARGET", "0.60"))  # target win rate
    adapt_k: float = float(os.getenv("CLEAN_ADAPT_K", "0"))            # v1.35: 0 — the adaptive BAR is retired (recording stays). It DEADLOCKED Jul 3: bad-tape losses froze rolling WR ~47% → bar 12bps → no trades → WR can't update → blocked real 11bps in-band moves while the (verified, 10x-data, self-recovering) signal-health gate read +10 healthy. The gate supersedes the bar; the OOS-verified config (d≥5) was validated without the penalty.
    adapt_max_drift: float = float(os.getenv("CLEAN_ADAPT_MAX_DRIFT", "20"))  # cap the adaptive bar
    # ── PROACTIVE regime detector (v1.13): efficiency ratio = |net move| / total path
    # over the last hour. High = trending (our edge), low = chop. In chop we demand a
    # STRONGER drift so we lean into trends and sit out the noise — measured BEFORE betting. ──
    er_filter: bool = os.getenv("CLEAN_ER_FILTER", "off").lower() in ("1", "true", "yes", "on")  # v1.29: OFF — verifier convicted it: in-band d>=5 signals in CHOP win 72.7% OOS (n=88, z=1.67, EV+0.129); the chop bar was blocking ~95 +EV windows/day. Disaster modes stay guarded (counter-trend, rev-cooldown, $8 stop).
    er_trend: float = float(os.getenv("CLEAN_ER_TREND", "0.32"))        # ER below this = choppy regime
    er_chop_drift: float = float(os.getenv("CLEAN_ER_CHOP_DRIFT", "16"))  # min drift bar when choppy
    er_deep: float = float(os.getenv("CLEAN_ER_DEEP", "0.15"))  # v1.32: skip entries in DEEP chop (er < this). n=375 re-test: deep chop 60.6% vs 64.3% BE (below water, the Jul-3 overnight bleed); mid-chop 0.15-0.32 is the sweet spot (73.1%, z=+1.95) and stays OPEN. 0=off
    # ── SIGNAL-HEALTH gate (v1.33): trade only when the MARKET-WIDE drift signal is winnable.
    # Rolling drift-accuracy over ALL logged in-band windows (traded or not — research logging
    # never stops, so recovery is detected while standing down). Jul-3: signal hit 57% vs 66%
    # BE (−9pts) market-wide — no entry filter survives an anti-predictive tape. ──
    sig_window: int = int(os.getenv("CLEAN_SIG_WINDOW", "40"))       # rolling window of resolved in-band research rows (0 = gate off)
    sig_min_edge: float = float(os.getenv("CLEAN_SIG_MIN_EDGE", "-2"))  # stand down when rolling (WR − break-even) drops below this many pts
    # ── LATE-WINDOW audition (Strategy #3, v1.38): live micro-test of the "momentum-into-close"
    # edge — verified AUDITION-grade (n=80, z=+2.50, OOS EV +0.136). INDEPENDENT of the early
    # signal-health gate (different edge: it trades even while early stands down). SOL/XRP only
    # (where the edge lives: SOL 82%, XRP 85%; ETH weak 60%). One min-size maker per window in
    # the last ~3min. Shares the daily-stop + breaker. Default OFF. ──
    late_live: bool = os.getenv("CLEAN_LATE_LIVE", "off").lower() in ("1", "true", "yes", "on")
    late_coins: tuple = tuple(c for c in os.getenv("CLEAN_LATE_COINS", "SOL,XRP").split(",") if c)
    late_t_min: float = float(os.getenv("CLEAN_LATE_T_MIN", "60"))    # last-window band (matches the shadow-capture zone)
    late_t_max: float = float(os.getenv("CLEAN_LATE_T_MAX", "210"))
    late_min_ask: float = float(os.getenv("CLEAN_LATE_MIN_ASK", "0.55"))
    late_max_ask: float = float(os.getenv("CLEAN_LATE_MAX_ASK", "0.70"))
    late_drift_bps: float = float(os.getenv("CLEAN_LATE_DRIFT_BPS", "0"))  # v1.38.1: the late edge is drift-INDEPENDENT (OOS: drift<5 slice z=+1.53 EV+0.159, STRONGER than drift>=5). The 55-70c ask band already selects "modest favorite"; a drift floor here just discards 2/3 of the verified windows. 0 = no floor.
    late_mom_agree: bool = os.getenv("CLEAN_LATE_MOM_AGREE", "off").lower() in ("1", "true", "yes", "on")  # v1.38.2 (DEPRECATED, default off): roc60-based; unreliable (roc60 present only ~30% of late windows → fails open). Superseded by late_skip_fading.
    late_skip_fading: bool = os.getenv("CLEAN_LATE_SKIP_FADING", "on").lower() in ("1", "true", "yes", "on")  # v1.39: skip FADING leaders (favorite ahead but its lead SHRANK from the early→late snapshot, same dir). Uses Chainlink settlement-feed drift trajectory (96% coverage, reliable). Verified: whole band 76.5%/EV+0.167 → skip-fading 81.5%/EV+0.244, recent30% EV+0.202 z+1.27. Keeps growing+reversed leads.
    # ── VOL-DIVERGENCE engine (v1.42, Strategy #4 — PRICING edge, not direction): bet where the
    # market's price diverges >= vol_div_min from the vol-priced probability of the lead holding,
    # p = Phi(drift/(sigma*sqrt(t_left))). Bets BOTH sides (leader underpriced OR underdog over-
    # priced). Audit (_accuracy_audit.py, 3233 windows): |div|>=5% → OOS EV +$0.044/$, z=+1.07
    # (AUDITION grade, below 1.64 proof) — deployed min-size at owner's direction, kill-floor $80. ──
    vol_div_live: bool = os.getenv("CLEAN_VOLDIV", "off").lower() in ("1", "true", "yes", "on")
    vol_div_min: float = float(os.getenv("CLEAN_VOLDIV_MIN", "0.05"))   # min |model − market| to bet
    vol_div_coins: tuple = tuple(c for c in os.getenv("CLEAN_VOLDIV_COINS", "ETH,SOL,BTC").split(",") if c)
    vol_div_min_ask: float = float(os.getenv("CLEAN_VOLDIV_MIN_ASK", "0.25"))  # sane price band for either side
    vol_div_max_ask: float = float(os.getenv("CLEAN_VOLDIV_MAX_ASK", "0.90"))
    # ── MOMENTUM CONFIRMATION (v1.13.1): the data says fading moves (drift one way but
    # 5-min momentum the other) are the reversals. Only bet WITH momentum. ──
    mom_filter: bool = os.getenv("CLEAN_MOM_FILTER", "on").lower() in ("1", "true", "yes", "on")
    flow_filter: bool = os.getenv("CLEAN_FLOW_FILTER", "on").lower() in ("1", "true", "yes", "on")  # LIVE TEST: veto a bet when 60s order-flow strongly OPPOSES it (volume fighting the move). off = shadow-log only
    flow_min: float = float(os.getenv("CLEAN_FLOW_MIN", "0.4"))  # only veto when |opposing flow| >= this (0..1); conservative so it rarely over-blocks
    trend_guard: bool = os.getenv("CLEAN_TREND_GUARD", "on").lower() in ("1", "true", "yes", "on")  # never bet AGAINST a strong ~30m macro trend (all hours) — stops counter-trend dip-shorting
    trend_guard_min: float = float(os.getenv("CLEAN_TREND_GUARD_MIN", "0.25"))  # macro % move that counts as a "strong trend" not to fight
    mom_lookback: int = int(os.getenv("CLEAN_MOM_LOOKBACK", "300"))    # seconds of momentum
    mom_min_bps: float = float(os.getenv("CLEAN_MOM_MIN_BPS", "2"))    # skip if momentum opposes drift by > this
    # ── cross-coin agreement boost (data: |drift|>=10 + both coins agree = 80%->84%) ──
    mom_need_coin: bool = os.getenv("CLEAN_MOM_NEED_COIN", "off").lower() in ("1", "true", "yes", "on")


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
        self._macro_cache = {}                  # coin -> (ts, pct) daytime trend cache
        self._rev_until = {}                    # coin -> ts: block entries until (reversal whipsaw cooldown)
        self._er_cache = {}                     # coin -> (ts, er) efficiency-ratio cache
        self.day_peak = 0.0                     # peak day P&L (for the give-back stop)
        self.hwm = 0.0                          # bankroll high-water mark (profit-lock trail stop)
        self.day_start_bankroll = CFG.start_bankroll  # chain-reconciled bankroll at session/day start (honest day net)
        self.day_loss_streak = 0                # consecutive DAYTIME losses
        self.day_blocked = False                # daytime trading blocked until night
        self.breaker_trips = 0                  # repeat breaker firings (escalating regime backoff)
        self.win_streak = 0                     # consecutive wins (resets the escalation)
        self.recent_trades = []                 # rolling 1/0 outcomes (adaptive accuracy)
        self.recent_ev = []                     # rolling (won, pnl, stake) — live accuracy+EV meter
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
                       "hwm": round(self.hwm, 2),
                       "day_blocked": self.day_blocked, "day_loss_streak": self.day_loss_streak,
                       "recent_trades": self.recent_trades[-60:],
                       "recent_ev": self.recent_ev[-100:],
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
            self.hwm = d.get("hwm", 0.0)
            self.day_blocked = d.get("day_blocked", False)
            self.day_loss_streak = d.get("day_loss_streak", 0)
            self.recent_trades = d.get("recent_trades", [])
            self.recent_ev = [tuple(x) for x in d.get("recent_ev", [])]
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
            self.day = t; self.wins = 0.0; self.losses = 0.0; self.day_peak = 0.0
            self.day_start_bankroll = self.bankroll  # re-anchor honest day net at midnight
            self.hwm = self.bankroll           # reset profit-lock peak daily (a prior day's peak must not block a fresh day)
            self.breaker_trips = 0              # fresh regime-escalation each day
            # NOTE: hwm (profit-lock peak) does NOT reset here — it's a ROLLING peak so the
            # overnight high is protected across midnight (the night session spans the day-roll).
            self._stop_notified = False
            self.traded = {k for k in self.traded}  # keep; windows are unique by epoch

    def _stop_amount(self):
        # daily stop scales with bankroll, with a $ floor
        return max(CFG.daily_stop_floor, self.bankroll * CFG.stop_pct)

    def _stopped(self):
        net = self.wins - self.losses
        if (self.losses - self.wins) >= self._stop_amount():       # absolute daily loss stop
            return True
        # give-back stop: locked a winning day, now handing it back -> pause for the day
        if CFG.giveback > 0 and self.day_peak > 0 and (self.day_peak - net) >= CFG.giveback:
            return True
        # PROFIT LOCK: arms ONLY once the day's peak is >= trail_stop above the day-start
        # (we genuinely went green), then fires when we give back trail_stop from that peak.
        # Guarantees we end >= day-start — locks REAL profit, never a loss.
        if (CFG.trail_stop > 0
                and (self.hwm - self.day_start_bankroll) >= CFG.trail_stop
                and (self.hwm - self.bankroll) >= CFG.trail_stop):
            return True
        return False

    def _open_exposure(self):
        exp = sum(o["price"] * o["shares"] for o in self.open_orders.values())
        exp += sum(p["entry"] * p["shares"] for p in self.positions.values()
                   if p.get("status") == "filled")
        return exp

    def _corr_sibling(self, coin, ws, direction):
        """True if ANOTHER coin already has a live bet (resting order or filled position)
        in the SAME 15m window and the SAME direction. ETH/SOL are ~0.85 correlated, so
        a same-window same-direction pair is one 2x bet, not two — a wrong call loses both
        legs at once (e.g. 2026-06-26 01:46 ETH+SOL Down both lost, -$7.25 in one window)."""
        for o in self.open_orders.values():
            if o.get("coin") != coin and o.get("ws") == ws and o.get("dir") == direction:
                return True
        for p in self.positions.values():
            if (p.get("coin") != coin and p.get("ws") == ws and p.get("dir") == direction
                    and p.get("status") in ("filled", "open")):
                return True
        return False

    def _corr_opposite(self, coin, ws, direction):
        """True if ANOTHER coin already has a live bet in the SAME window but the OPPOSITE
        direction. Data (1067 windows): divergent correlated pairs win only 55% (n=168) vs
        69% when aligned — betting ETH/SOL to decorrelate is a coinflip that loses at favorite
        prices (e.g. 2026-06-29 SOL DOWN won but ETH UP lost, both actually closed DOWN)."""
        for o in self.open_orders.values():
            if o.get("coin") != coin and o.get("ws") == ws and o.get("dir") != direction:
                return True
        for p in self.positions.values():
            if (p.get("coin") != coin and p.get("ws") == ws and p.get("dir") != direction
                    and p.get("status") in ("filled", "open")):
                return True
        return False

    def _size_shares(self, price):
        """Tiered Kelly: conservative while rebuilding, bigger once bankroll recovers past
        kelly_bump_at — so wins compound up as you grow. Share-floored + capped."""
        if not CFG.compound:
            return CFG.shares
        kf = CFG.kelly_bump if self.bankroll >= CFG.kelly_bump_at else CFG.kelly_frac
        stake = min(self.bankroll * kf, self.bankroll * CFG.max_bet_pct)
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
            hour = datetime.datetime.fromtimestamp(ws, datetime.timezone.utc).hour
            feats = MF.compute(strike, early, btc_d, hour, coin)
            if feats is None:
                return None
            result = float(self.model["model"].predict_proba([feats])[0][1])
            self._mp_cache[ck] = result          # cache successes only (allow retry on errors)
            return result
        except Exception as e:
            logger.debug(f"model_prob error {coin}: {e}")
            return None

    def _research_scan(self, coin, phase="early"):
        """Capture EVERY real-move window (drift >= research_min_bps), traded or not,
        with full features + the actual decision. Resolved later via gamma. Fully
        isolated from trading (caller wraps in try/except) — it never places orders.
        v1.36: phase='late' additionally snapshots the LAST ~2-3 min of each window to
        test the 'momentum-into-close' thesis (Novals83/5min-btc repo): does a strong
        established move near expiry beat its then-price? Shadow data only."""
        if not CFG.research:
            return
        info = get_market_info(coin)
        if not info:
            return
        ws = info.window_start
        rk = (coin, ws, phase)
        if rk in self._research_seen:
            return
        now = time.time(); t_rem = ws + 900 - now; age = now - ws
        if phase == "late":
            if not (60 <= t_rem <= 210):
                return
        elif age < CFG.warmup or t_rem < CFG.min_t:
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
        if (coin, ws) in self.traded:
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
        if phase == "early":
            logger.info(f"[WATCH] {coin} {direction} drift={dist*1e4:+.1f}bps "
                        f"ask={int(round(fav_ask*100)) if fav_ask else '?'}c t={int(t_rem)}s "
                        f"-> {decision}" + (f":{reason}" if reason else ""))
        self._research[rk] = {
            "ts": datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds"),
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
            "decision": decision, "reason": reason, "t_left": int(t_rem),
            "er": (lambda e: round(e, 3) if e is not None else "")(self._efficiency_ratio(coin)),
            "flow60": (lambda fl: round(fl, 3) if fl is not None else "")(binance_ws.get_order_flow(coin, 60)),
            "hmm": _hmm_fmt(coin),
            "phase": phase,
            "book_imb": (lambda bi: round(bi, 3) if bi is not None else "")(binance_ws.get_book_imbalance(coin))}

    def _research_resolve(self):
        """Resolve logged research windows via gamma; append the complete row
        (features + decision + true outcome) to clean_bot_research.csv."""
        now = time.time()
        for rk, row in list(self._research.items()):
            coin, ws = rk[0], rk[1]
            if now < ws + 960:
                continue
            w = gamma_winner(coin, ws)
            if not w:
                continue
            row["winner"] = w
            row["drift_correct"] = int(row["dir"] == w)
            # re-label decision from the FINAL traded state (scan-time capture can
            # predate the entry → a traded window may have been logged as SKIP).
            if (rk[0], rk[1]) in self.traded:
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
    def _macro_trend(self, coin):
        """Returns (net_pct, last_candle_pct) over the recent Binance 15m candles
        (cached 45s). net = overall trend; last = the most-recent candle, used to
        detect a fresh reversal so the daytime trend RESETS instead of staying stuck
        on a dead trend. Direction matches Chainlink over this horizon."""
        now = time.time()
        c = self._macro_cache.get(coin)
        if c and now - c[0] < 45:
            return c[1]
        net, last = 0.0, 0.0
        sym = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT",
               "XRP": "XRPUSDT"}.get(coin, coin + "USDT")
        n = max(2, CFG.day_trend_lookback // 15 + 1)
        for base in ("https://api.binance.us/api/v3/klines",
                     "https://api.binance.com/api/v3/klines",
                     "https://data-api.binance.vision/api/v3/klines"):
            try:
                r = _h.get(base, params={"symbol": sym, "interval": "15m", "limit": n})
                if r.status_code == 200:
                    ks = r.json()
                    if len(ks) >= 2:
                        net = (float(ks[-1][4]) - float(ks[0][1])) / float(ks[0][1]) * 100
                        last = (float(ks[-1][4]) - float(ks[-1][1])) / float(ks[-1][1]) * 100
                    break
            except Exception:
                continue
        self._macro_cache[coin] = (now, (net, last))
        return net, last

    def _is_daytime(self):
        return CFG.day_start <= ((time.gmtime().tm_hour - 5) % 24) < CFG.day_end  # Lima = UTC-5

    def _efficiency_ratio(self, coin):
        """Kaufman efficiency ratio over the last hour (12x 5m candles): |net move| /
        total path. ~1 = clean trend (our edge), ~0 = chop. Cached 60s."""
        now = time.time()
        c = self._er_cache.get(coin)
        if c and now - c[0] < 60:
            return c[1]
        er = None
        sym = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT",
               "XRP": "XRPUSDT"}.get(coin, coin + "USDT")
        for base in ("https://api.binance.us/api/v3/klines",
                     "https://api.binance.com/api/v3/klines",
                     "https://data-api.binance.vision/api/v3/klines"):
            try:
                r = _h.get(base, params={"symbol": sym, "interval": "5m", "limit": 13})
                if r.status_code == 200:
                    closes = [float(k[4]) for k in r.json()]
                    if len(closes) >= 4:
                        net = abs(closes[-1] - closes[0])
                        path = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
                        er = (net / path) if path > 0 else 0.0
                    break
            except Exception:
                continue
        self._er_cache[coin] = (now, er)
        return er

    def _rolling_wr(self):
        """Win rate over the last adapt_window resolved trades (None if too few)."""
        r = self.recent_trades[-CFG.adapt_window:]
        return (sum(r) / len(r)) if len(r) >= 5 else None

    def _eff_drift(self):
        """Adaptive drift bar: when rolling accuracy is below target, RAISE the bar so
        only stronger (historically higher-WR) setups qualify; at/above target, base bar.
        This is the learn-and-adjust knob — concentrates on quality when losing."""
        if not CFG.adapt:
            return CFG.drift_bps
        wr = self._rolling_wr()
        if wr is None or wr >= CFG.adapt_target:
            return CFG.drift_bps
        return min(CFG.drift_bps + (CFG.adapt_target - wr) * CFG.adapt_k, CFG.adapt_max_drift)

    def _signal_health(self):
        """Rolling market-wide drift-signal edge, in points vs break-even, over the last
        sig_window resolved IN-BAND research rows (ALL windows, traded or not — ~10x the
        data of trade outcomes). Cached 240s. None = insufficient data (fail-open)."""
        now = time.time()
        c = getattr(self, "_sig_cache", None)
        if c and now - c[0] < 240:
            return c[1]
        edge = None
        try:
            rows = list(csv.DictReader(open(RESEARCH_CSV, encoding="utf-8", errors="ignore")))[-400:]
            sel = []
            for r in rows:
                if r.get("drift_correct") not in ("0", "1"):
                    continue
                try:
                    ask = float(r["fav_ask"]); dr = abs(float(r["drift_pct"])) * 100
                except Exception:
                    continue
                if CFG.min_ask * 100 <= ask <= CFG.max_ask * 100 and dr >= CFG.drift_bps:
                    sel.append((int(r["drift_correct"]), ask / 100))
            sel = sel[-CFG.sig_window:]
            if len(sel) >= max(15, CFG.sig_window // 2):
                wr = sum(w for w, _ in sel) / len(sel)
                be = sum(p for _, p in sel) / len(sel)
                edge = round((wr - be) * 100, 1)
        except Exception:
            edge = None
        self._sig_cache = (now, edge)
        return edge

    def _prune_positions(self):
        """Drop resolved positions older than position_keep_h. They're settled (not counted
        in open_cost), so they only bloat the state file + dashboard. Keep a recent window
        for the trades table / equity curve. Returns how many were pruned."""
        cutoff = time.time() - CFG.position_keep_h * 3600
        stale = [k for k, p in self.positions.items()
                 if p.get("status") == "resolved" and p.get("ws", 0) < cutoff]
        for k in stale:
            del self.positions[k]
        if stale:
            logger.info(f"[PRUNE] removed {len(stale)} resolved positions older than "
                        f"{CFG.position_keep_h}h ({len(self.positions)} kept)")
            self._save()                        # persist immediately so disk/dashboard match memory
        return len(stale)

    def _sync_bankroll(self):
        """Reconcile bankroll to the REAL on-chain USDC + open-position cost, so sizing
        AND the dashboard show reality. The internal win/loss ledger drifts above the
        chain (inconsistent proxy fills), so trust the chain balance as the source of truth."""
        try:
            from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType
            col = self.client.get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)) or {}
            usdc = int(col.get("balance", "0")) / 1e6
            open_cost = sum(p["entry"] * p["shares"] for p in self.positions.values()
                            if p.get("status") == "filled")
            real = round(usdc + open_cost, 2)
            if real > 0:
                # profit-lock peak tracks CHAIN TRUTH, not the drifting ledger — else the
                # win/loss ledger creeps ~$0.75/win above chain and inflates hwm, firing the
                # lock on a phantom peak (2026-06-30: hwm $53.38 vs real peak $50.38 → locked
                # after a $3.20 giveback that looked like $6.20). Update from `real` only.
                self.hwm = max(self.hwm, real)
                if abs(real - self.bankroll) > 0.75:
                    logger.info(f"[BANKROLL SYNC] ${self.bankroll:.2f} -> ${real:.2f} "
                                f"(on-chain USDC ${usdc:.2f} + open ${open_cost:.2f})")
                    self.bankroll = real
                    self._save()
        except Exception as e:
            logger.debug(f"bankroll sync err {e}")

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
        # wait for the move to ESTABLISH before entering (entry_min_age) — don't chase the
        # first-2-min twitch that fakes out (data: 600-750s left = 81% vs 750-900s = 66%).
        if age < max(CFG.warmup, CFG.entry_min_age) or t_rem < CFG.min_t:
            return
        # SIGNAL-HEALTH gate (v1.33): stand down while the MARKET-WIDE drift signal is
        # anti-predictive (rolling in-band accuracy below break-even). Research logging keeps
        # running while stood down, so recovery is detected and trading auto-resumes at full
        # frequency — no rarer entry signals, just "only play when the game is winnable".
        if CFG.sig_window > 0:
            sh = self._signal_health()
            if sh is not None and sh < CFG.sig_min_edge:
                if (coin, ws) not in self._nc_logged:
                    logger.info(f"[SIGNAL-HEALTH] market drift-signal edge {sh:+.1f}pts vs "
                                f"break-even (< {CFG.sig_min_edge:+.0f}) — standing down; "
                                f"auto-resumes when the signal recovers")
                    self._nc_logged.add((coin, ws))
                return
        strike = float(info.threshold_price or 0)
        px = info.current_crypto_price or binance_ws.get_price(coin)   # Chainlink spot (settlement feed) first
        px = float(px or 0)
        if strike <= 0 or px <= 0:
            return
        # NEVER trade on a non-Chainlink strike — Binance strike vs Chainlink spot is
        # a ~10bps cross-feed basis that flips near-strike direction (the reversals).
        if not str(info.strike_source or "").startswith("chainlink"):
            if (coin, ws) not in self._nc_logged:
                logger.info(f"[STRIKE SKIP] {coin} strike_source={info.strike_source} "
                            f"(not chainlink) — skip to avoid wrong-feed direction")
                self._nc_logged.add((coin, ws))
            return
        dist = (px - strike) / strike
        # DEEP-CHOP guard (v1.32): er<er_deep = random walk, drift is anti-predictive (60.6%
        # vs 64.3% BE, n=99). Mid-chop (0.15-0.32) stays OPEN (73.1%, the sweet spot).
        if CFG.er_deep > 0:
            _er = self._efficiency_ratio(coin)
            if _er is not None and _er < CFG.er_deep:
                if (coin, ws) not in self._nc_logged:
                    logger.info(f"[DEEP CHOP SKIP] {coin} er={_er:.2f} < {CFG.er_deep} — "
                                f"random walk, drift anti-predictive; wait for structure")
                    self._nc_logged.add((coin, ws))
                return
        eff_drift = self._eff_drift()                  # reactive: tighter when recently losing
        regime = ""
        if CFG.er_filter:
            er = self._efficiency_ratio(coin)          # PROACTIVE: trend vs chop, measured before betting
            if er is not None and er < CFG.er_trend:   # choppy regime -> demand a stronger drift
                eff_drift = max(eff_drift, CFG.er_chop_drift)
                regime = f" chop(ER={er:.2f})"
        if abs(dist) < eff_drift / 10000.0:            # need a clear early drift (adaptive quality bar)
            if abs(dist) >= CFG.drift_bps / 10000.0 and (coin, ws) not in self._nc_logged:
                logger.info(f"[REGIME SKIP] {coin} drift={dist*1e4:+.1f}bps < bar {eff_drift:.0f}bps{regime}")
                self._nc_logged.add((coin, ws))
            return
        is_up = dist > 0
        token = info.up_token_id if is_up else info.down_token_id
        direction = "UP" if is_up else "DOWN"
        # COUNTER-TREND GUARD (v1.27, ALL hours incl. night): never fight a strong established
        # macro trend. 2026-07-01: 6/6 losses were DOWN bets shorting dips into a sustained
        # overnight UP-trend (night had no macro guard). If the ~30m macro move is strong and
        # the bet OPPOSES it, skip. Fires only vs a strong trend → doesn't over-block chop.
        if CFG.trend_guard:
            mnet, _ = self._macro_trend(coin)
            if abs(mnet) >= CFG.trend_guard_min and ((mnet > 0) != is_up):
                if (coin, ws) not in self._nc_logged:
                    logger.info(f"[COUNTER-TREND SKIP] {coin} {direction} vs macro {mnet:+.2f}% "
                                f"(>={CFG.trend_guard_min}%) — not fighting a strong trend")
                    self._nc_logged.add((coin, ws))
                return
        # ORDER-FLOW confirmation (LIVE TEST, v1.26 — revert via CLEAN_FLOW_FILTER=off): veto
        # only when 60s aggressive volume STRONGLY opposes the bet (buying into a DOWN bet, or
        # selling into an UP bet) — the volume is fighting the price move. Hypothesis: flow
        # agrees with direction; if live results worsen, flip the toggle back to shadow-log.
        if CFG.flow_filter:
            flow = binance_ws.get_order_flow(coin, 60)
            if flow is not None and (-flow if is_up else flow) >= CFG.flow_min:
                if (coin, ws) not in self._nc_logged:
                    logger.info(f"[FLOW SKIP] {coin} {direction} — 60s order-flow {flow:+.2f} "
                                f"strongly opposes (volume fighting the move); skip")
                    self._nc_logged.add((coin, ws))
                return
        # MOMENTUM CONFIRMATION (data: fading moves = the reversals). Skip when the 5-min
        # momentum opposes the drift; optionally require the broader market to agree too.
        if CFG.mom_filter:
            roc = _roc(chainlink_ws.get_ticks(coin, CFG.mom_lookback + 40), CFG.mom_lookback) * 1e4
            if (roc if is_up else -roc) < -CFG.mom_min_bps:
                if (coin, ws) not in self._nc_logged:
                    logger.info(f"[MOM SKIP] {coin} {direction} drift={dist*1e4:+.1f}bps but "
                                f"roc{CFG.mom_lookback}s={roc:+.1f}bps (fading/reversing) — skip")
                    self._nc_logged.add((coin, ws))
                return
            if CFG.mom_need_coin and not self._market_confirms(coin, direction):
                if (coin, ws) not in self._nc_logged:
                    logger.info(f"[MOM SKIP] {coin} {direction} — broader market doesn't agree")
                    self._nc_logged.add((coin, ws))
                return
        # DAYTIME trend confirmation (overnight UNCHANGED): the edge is trend-following;
        # during the choppier US/EU hours only bet when a real macro trend AGREES with
        # the drift. Skips daytime chop / counter-trend bounces. (Won't catch sharp
        # turning points — the give-back stop protects gains there.)
        if CFG.day_trend and self._is_daytime():
            # daytime needs a STRONG trend (night-only) or the normal trend (filter mode).
            # (Persistent chop is handled by the adaptive re-probing breaker, not a hard block.)
            net, last = self._macro_trend(coin)
            thr = CFG.day_strong_trend if CFG.night_only else CFG.day_trend_min
            trend_ok = abs(net) >= thr and ((net > 0) if is_up else (net < 0))
            recent_ok = (last > 0) if is_up else (last < 0)   # latest candle still our way (not reversing)
            # hysteresis: a reversal flag (macro trend intact but the forming candle flipped
            # against us) arms a cooldown. Early in a window the forming candle is mostly noise,
            # so a sharp counter-spike can briefly un-flip it and trip an entry — that flip-flop
            # IS the chop that traps us (e.g. 2026-06-26 17:31 SOL DOWN @69c: skipped reversing,
            # then a 45s down-spike re-flipped the candle, entered, reversed back up, lost).
            # Only ARM the cooldown in a CHOPPY regime (low efficiency ratio). In a trend a
            # candle flip is usually a pullback to buy, and reversals are rare — so this never
            # suppresses trending-regime trades; it only waits out whipsaws where they trap us.
            nowt = time.time()
            er = self._efficiency_ratio(coin)
            choppy = er is not None and er < CFG.er_trend
            if CFG.rev_cooldown and choppy and trend_ok and not recent_ok:
                self._rev_until[coin] = nowt + CFG.rev_cooldown
            if nowt < self._rev_until.get(coin, 0):
                if (coin, ws) not in self._nc_logged:
                    logger.info(f"[REV COOLDOWN] {coin} {direction} net={net:+.2f}% last={last:+.2f}% "
                                f"er={er:.2f} — reversal flagged in chop <{CFG.rev_cooldown}s ago, waiting it out")
                    self._nc_logged.add((coin, ws))
                return
            if not (trend_ok and recent_ok):
                if (coin, ws) not in self._nc_logged:
                    why = "no STRONG trend" if not trend_ok else "trend REVERSING (last candle flipped)"
                    logger.info(f"[DAY-TREND SKIP] {coin} {direction} net={net:+.2f}% last={last:+.2f}% "
                                f"(need >={thr}% same dir) — {why}")
                    self._nc_logged.add((coin, ws))
                return
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
        # divergent correlated bet: ETH/SOL move ~0.85 together, so betting one OPPOSITE a leg
        # already held this window is a bet on decorrelation — 55% in the data (vs 69% aligned),
        # a loser at favorite prices. Skip it (this is the ETH-UP-while-SOL-DOWN case).
        if CFG.corr_opposite_block and self._corr_opposite(coin, ws, direction):
            if (coin, ws, "div") not in self._nc_logged:
                logger.info(f"[CORR DIVERGE] {coin} {direction} — opposite a held correlated leg "
                            f"this window (divergence = 55% coinflip in the data); skip")
                self._nc_logged.add((coin, ws, "div"))
            return
        # correlated-pair control: ETH+SOL same dir, same window = one 2x bet, not two.
        # Size each leg at corr_pair_frac so the pair ~= one normal position. If half falls
        # below the exchange share floor (small bankroll), take ONE leg only (skip the 2nd).
        # v1.31: above corr_full_at, BOTH legs trade full-size — each leg is +EV (aligned
        # 69% vs 64% BE) and at that book size a pair fits the ~12% sizing policy.
        if (CFG.corr_pair_frac < 1.0 and self.bankroll < CFG.corr_full_at
                and self._corr_sibling(coin, ws, direction)):
            half = int(round(self._size_shares(maker) * CFG.corr_pair_frac))
            if half >= CFG.shares:
                logger.info(f"[CORR HALF] {coin} {direction} — pairs with sibling same dir/window, "
                            f"sizing {half}sh ({CFG.corr_pair_frac:.0%}) to cap correlated risk")
                shares = half
            else:
                if (coin, ws, "corr") not in self._nc_logged:
                    logger.info(f"[CORR SKIP] {coin} {direction} — already long the same direction "
                                f"in another coin this window; not doubling a correlated bet")
                    self._nc_logged.add((coin, ws, "corr"))
                return
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

    def _late_entry(self, coin: str):
        """Strategy #3 (v1.38) — LIVE micro-audition of the late-window 'momentum-into-close'
        edge. Verified AUDITION-grade on shadow data (band 55-70c: n=80, WR 78.8% vs 65.4% BE,
        z=+2.50, OOS EV +0.136; concentrated in SOL 82% / XRP 85%). Deliberately INDEPENDENT of
        the early signal-health gate — it's a different edge and trades even while early stands
        down. One minimum-size (5-share) maker per window, last ~3min, established move, in the
        55-70c band. Caller (loop) only invokes it when NOT daily-stopped and NOT in breaker
        cooldown, so it shares those risk controls. Env-gated: CLEAN_LATE_LIVE (default off)."""
        if not CFG.late_live or coin not in CFG.late_coins:
            return
        info = get_market_info(coin)
        if not info:
            return
        ws = info.window_start
        key = (coin, ws)
        if key in self.traded:                       # one entry per window (shared with early)
            return
        now = time.time()
        t_rem = ws + 900 - now
        if not (CFG.late_t_min <= t_rem <= CFG.late_t_max):
            return
        strike = float(info.threshold_price or 0)
        px = float(info.current_crypto_price or binance_ws.get_price(coin) or 0)
        if strike <= 0 or px <= 0:
            return
        if not str(info.strike_source or "").startswith("chainlink"):
            return                                   # same wrong-feed protection as early
        dist = (px - strike) / strike
        if abs(dist) < CFG.late_drift_bps / 10000.0:  # late edge is drift-independent; 55-70c band does the selecting
            return
        is_up = dist > 0
        # SKIP FADING LEADERS (v1.39): the late edge = the leader holding, but a FADING leader
        # (still ahead, same direction as early, but its lead SHRANK from early→late) is the weak
        # bucket (68% vs 81% for growing/reversed leads). Measured on the Chainlink settlement
        # feed via the window's own early snapshot (drift_pct stored as dist*100) — reliable,
        # 96% coverage. Verified: skip-fading lifts the band 76.5%→81.5% WR, EV +0.167→+0.244,
        # holds recent (EV +0.202). Fail-open if no early snapshot for this window.
        if CFG.late_skip_fading:
            er = self._research.get((coin, ws, "early"))
            if er:
                try:
                    e_drift = float(er.get("drift_pct"))       # early lead, in dist*100 units
                    same_dir = (e_drift > 0) == is_up
                    if same_dir and abs(dist * 100) < abs(e_drift):
                        return                                  # fading leader — skip
                except Exception:
                    pass
        token = info.up_token_id if is_up else info.down_token_id
        direction = "UP" if is_up else "DOWN"
        book = {}
        try:
            book = self.om.get_clob_book(token) or {}
        except Exception:
            pass
        ask = book.get("ask")
        if not ask or not (CFG.late_min_ask <= float(ask) <= CFG.late_max_ask):
            return
        maker = round(max(0.02, float(ask) - CFG.maker_offset), 2)
        shares = CFG.shares                          # MIN size for the audition (no compounding)
        # CORRELATION GUARD (v1.39.1): BTC/ETH/SOL move ~0.85 together. Betting the SAME
        # direction across them in ONE window = a single 3x bet, not three — one wrong call
        # loses all three at once (2026-07-07: BTC+ETH+SOL all DOWN same window, all lost).
        # Mirror the early path: skip same-direction correlated stacking, and skip betting
        # OPPOSITE a held sibling (divergence = coinflip). One late leg per window/direction.
        if self._corr_sibling(coin, ws, direction):
            return
        if CFG.corr_opposite_block and self._corr_opposite(coin, ws, direction):
            return
        if self._open_exposure() + maker * shares > self.bankroll * CFG.max_open_pct:
            return                                   # respect the simultaneous-exposure cap
        self.traded.add(key)
        logger.info(f"[LATE ENTER] {coin} {direction} drift={dist*100:+.3f}% ask={float(ask)*100:.0f}c "
                    f"-> maker {maker*100:.0f}c x{shares} (${maker*shares:.2f}, bankroll ${self.bankroll:.0f}) "
                    f"T={t_rem:.0f}s [AUDITION]" + (" [DRY]" if CFG.dry else ""))
        if CFG.dry:
            self.positions[f"{coin}:{ws}"] = {"coin": coin, "ws": ws, "dir": direction,
                                              "entry": maker, "shares": shares, "token": token,
                                              "status": "filled", "sim": True, "late": True}
            logger.info(f"[SIM FILL] {coin} {direction} @ {maker*100:.0f}c x{shares} (paper, late)")
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
                                         "shares": shares, "ts": now, "late": True}
                logger.info(f"[GTC] resting LATE {coin} {direction} @ {maker*100:.0f}c x{shares} oid={oid[:10]}")
            else:
                logger.warning(f"[LATE ORDER] no oid in result: {res}")
        except Exception as e:
            logger.warning(f"[LATE ORDER FAIL] {coin} {direction}: {e}")
        self._save()

    def _vol_div_entry(self, coin: str):
        """Strategy #4 (v1.42) — VOL-DIVERGENCE, a PRICING edge (not direction prediction).
        Fair prob of the lead holding: p = Phi(drift / (sigma*sqrt(t_left))) (driftless BM).
        If the market prices the leading side >= vol_div_min BELOW p, buy the leader; if it
        prices it >= vol_div_min ABOVE p, buy the underdog. Audit: OOS EV +$0.044/$ (z=+1.07,
        AUDITION grade). Min-size only; same order path/guards as the late engine."""
        if not CFG.vol_div_live or coin not in CFG.vol_div_coins:
            return
        info = get_market_info(coin)
        if not info:
            return
        ws = info.window_start
        key = (coin, ws)
        if key in self.traded:                        # one entry per window (shared dedup)
            return
        now = time.time()
        t_rem = ws + 900 - now
        if not (60 <= t_rem <= 840):
            return
        strike = float(info.threshold_price or 0)
        px = float(info.current_crypto_price or binance_ws.get_price(coin) or 0)
        if strike <= 0 or px <= 0:
            return
        if not str(info.strike_source or "").startswith("chainlink"):
            return                                    # never trade a basis-tainted strike
        sigma = binance_ws.get_realized_vol(coin, 180)
        if not sigma or sigma <= 0:
            return
        dist = (px - strike) / strike
        lead_up = dist > 0
        p_lead = 0.5 * (1.0 + math.erf(abs(dist) / (sigma * math.sqrt(t_rem)) / math.sqrt(2)))
        up_b = down_b = {}
        try: up_b = self.om.get_clob_book(info.up_token_id) or {}
        except Exception: pass
        try: down_b = self.om.get_clob_book(info.down_token_id) or {}
        except Exception: pass
        lead_ask = (up_b if lead_up else down_b).get("ask")
        dog_ask = (down_b if lead_up else up_b).get("ask")
        side = None
        if lead_ask and (p_lead - float(lead_ask)) >= CFG.vol_div_min:
            side, ask = ("UP" if lead_up else "DOWN"), float(lead_ask)      # leader underpriced
            token = info.up_token_id if lead_up else info.down_token_id
            edge = p_lead - ask
        elif dog_ask and (float(dog_ask) - (1.0 - p_lead)) <= -CFG.vol_div_min:
            side, ask = ("DOWN" if lead_up else "UP"), float(dog_ask)       # underdog overpriced side is cheap
            token = info.down_token_id if lead_up else info.up_token_id
            edge = (1.0 - p_lead) - ask
        if not side:
            return
        if not (CFG.vol_div_min_ask <= ask <= CFG.vol_div_max_ask):
            return
        # same correlation guards as the late engine: never stack a correlated same-direction
        # leg, never bet opposite a held sibling (one wrong macro call must cost ONE bet).
        if self._corr_sibling(coin, ws, side):
            return
        if CFG.corr_opposite_block and self._corr_opposite(coin, ws, side):
            return
        maker = round(max(0.02, ask - CFG.maker_offset), 2)
        shares = CFG.shares                           # MIN size — audition grade
        if self._open_exposure() + maker * shares > self.bankroll * CFG.max_open_pct:
            return
        self.traded.add(key)
        logger.info(f"[VOLDIV ENTER] {coin} {side} p_model={p_lead:.2f} ask={ask*100:.0f}c "
                    f"edge={edge*100:+.0f}% -> maker {maker*100:.0f}c x{shares} "
                    f"(${maker*shares:.2f}, bankroll ${self.bankroll:.0f}) T={t_rem:.0f}s [AUDITION]"
                    + (" [DRY]" if CFG.dry else ""))
        if CFG.dry:
            self.positions[f"{coin}:{ws}"] = {"coin": coin, "ws": ws, "dir": side,
                                              "entry": maker, "shares": shares, "token": token,
                                              "status": "filled", "sim": True, "voldiv": True}
            self._save()
            return
        try:
            res = self.client.create_and_post_order(
                OrderArgs(price=maker, size=shares, side=BUY, token_id=token),
                PartialCreateOrderOptions(tick_size="0.01"), OrderType.GTC)
            oid = (res or {}).get("orderID") or (res or {}).get("orderId")
            if oid:
                self.open_orders[oid] = {"coin": coin, "ws": ws, "dir": side,
                                         "token": token, "price": maker,
                                         "shares": shares, "ts": now, "voldiv": True}
                logger.info(f"[GTC] resting VOLDIV {coin} {side} @ {maker*100:.0f}c x{shares} oid={oid[:10]}")
            else:
                logger.warning(f"[VOLDIV ORDER] no oid in result: {res}")
        except Exception as e:
            logger.warning(f"[VOLDIV ORDER FAIL] {coin} {side}: {e}")
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
                # A cancel can LOSE a race with a fill: the order fills on-chain right as we
                # cancel, leaving a phantom position that silently drains the wallet when it
                # loses (e.g. 2026-06-28 06:36 "canceled" SOL DOWN @69c actually filled → −$3.45
                # untracked). ALWAYS re-verify before assuming unfilled, and track any real fill.
                matched_after = 0.0
                try:
                    od2 = self.client.get_order(oid) or {}
                    matched_after = float(od2.get("size_matched") or od2.get("sizeMatched") or 0)
                except Exception:
                    matched_after = 0.0
                if matched_after > 0:
                    self.positions[f"{o['coin']}:{o['ws']}"] = {
                        "coin": o["coin"], "ws": o["ws"], "dir": o["dir"], "token": o["token"],
                        "entry": o["price"], "shares": int(matched_after), "status": "filled"}
                    logger.warning(f"[FILLED-RACE] {o['coin']} {o['dir']} @ {o['price']*100:.0f}c "
                                   f"x{int(matched_after)} — order filled during cancel; now TRACKED "
                                   f"(this was the silent leak)")
                    tg._send(f"⚠️ <b>FILLED (cancel race)</b> {o['coin']} {o['dir']} @ "
                             f"{o['price']*100:.0f}c x{int(matched_after)} — now tracked",
                             dedup_key=f"race-{oid}")
                else:
                    logger.info(f"[CANCEL] unfilled {o['coin']} {o['dir']} @ {o['price']*100:.0f}c")
                self.open_orders.pop(oid, None); self._save()

    # ── strike snapshot: cache the Chainlink strike AT window-open (v1.9.3) ─────
    def _snapshot_strikes(self):
        """Runs every loop. The instant a window opens (age<45s) it caches the live
        Chainlink price as that window's strike — using get_price, which is proven to
        work (it's the same feed the spot reads). Robust replacement for the strike
        snapshotter (which hung): so get_strike always serves the correct feed and the
        bot never reverts to the Binance strike that flips direction."""
        import poly_resolution as pr
        now = time.time()
        ws = int(now // 900) * 900
        age = now - ws
        if age > 45:
            return
        # include shadow/research coins (v1.35.2: XRP was missing → per-scan "cache miss →
        # Binance kline" fallback spam + basis-tainted XRP research strikes)
        for coin in dict.fromkeys(tuple(CFG.coins) + tuple(CFG.research_coins) + ("BTC",)):
            slug = f"{coin.lower()}-updown-15m-{ws}"
            try:
                cache = pr._load_strike_cache()
                cur = cache.get(slug)
                if cur and str(cur.get("source", "")).startswith("chainlink"):
                    continue
                px = chainlink_ws.get_price(coin)
                if px and px > 0:
                    cache[slug] = {"coin": coin, "strike": float(px),
                                   "source": "chainlink_window_open", "ts": int(now)}
                    pr._save_strike_cache(cache)
                    logger.info(f"[STRIKE-SNAP] {coin} {slug} ${px:.2f} (age {age:.0f}s)")
            except Exception as e:
                logger.debug(f"snap err {e}")

    # ── active exit: ride solid winners to the full reward, bail on shaky (v1.9.1) ──
    def manage_positions(self):
        """Exit policy that tells SOLID from SHAKY by the token's own price:
        • deep ITM near the close (bid>=deep_itm) → HOLD to settlement for the full $1
          (price is far from strike, reversal unlikely, and settlement pays no fee);
        • trailing stop → let a winner run (peak rises), sell only when it turns off the peak;
        • hard stop → cut a clear loser;
        • near close & NOT deep ITM → bail before the settlement coin-flip."""
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
            entry = p["entry"]
            peak = max(p.get("peak", bid), bid)
            p["peak"] = peak
            gain = bid - entry
            t_left = p["ws"] + 900 - now
            near_close = 0 < t_left <= CFG.exit_before_s
            # 1. SOLID: deep ITM near the close → hold for the full reward (don't sell the winner short)
            if near_close and bid >= CFG.deep_itm:
                if p.get("_holding") != True:
                    p["_holding"] = True
                    logger.info(f"[HOLD] {p['coin']} {p['dir']} bid={bid*100:.0f}c solid & "
                                f"{int(t_left)}s left → riding to settlement for full reward")
                continue
            # 2. hard stop — clear loser
            if gain <= -CFG.tp_stop_delta:
                self._close_position(k, p, bid, "STOP")
            # 3. trailing stop — was winning, now turned off the peak → lock the gain
            elif peak - entry >= CFG.trail_arm and bid <= peak - CFG.trail_delta:
                self._close_position(k, p, bid, "TRAIL")
            # 4. near close & not solid → dodge the settlement coin-flip
            elif near_close:
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
        real_resolved = False
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
            # ADAPTIVE regime backoff: chop persisting -> escalate the pause; a win-streak
            # resets it (regime recovered). Re-probes after each cooldown — never permanent.
            if won:
                self.consec_losses = 0
                self.win_streak += 1
                if self.breaker_trips and self.win_streak >= CFG.breaker_reset_wins:
                    logger.info(f"[BREAKER] regime recovered ({self.win_streak} wins in a row) — "
                                f"escalation reset, trading freely")
                    self.breaker_trips = 0
            else:
                self.consec_losses += 1
                self.win_streak = 0
                if CFG.loss_breaker > 0 and self.consec_losses >= CFG.loss_breaker:
                    self.breaker_trips += 1
                    mult = self.breaker_trips if CFG.breaker_escalate else 1
                    cd = min(CFG.breaker_cooldown * mult, CFG.breaker_max_cooldown)
                    self.breaker_until = time.time() + cd
                    logger.info(f"[BREAKER] trip #{self.breaker_trips}: {self.consec_losses} losses in a row "
                                f"— pause {cd // 60}min (chop regime; escalating, re-probes after)")
                    tg._send(f"🧊 <b>Regime backoff</b> #{self.breaker_trips}: pause {cd // 60}min (chop). "
                             f"Auto-resumes; a {CFG.breaker_reset_wins}-win streak clears it.")
                    self.consec_losses = 0   # fresh start after the cooldown
            net = self.wins - self.losses
            # ADAPTIVE ACCURACY: record this outcome + re-measure -> the drift bar self-adjusts
            if CFG.adapt:
                self.recent_trades.append(1 if won else 0)
                if len(self.recent_trades) > 60:
                    self.recent_trades = self.recent_trades[-60:]
                _wr = self._rolling_wr()
                if _wr is not None:
                    logger.info(f"[ADAPT] rolling WR {_wr*100:.0f}% (last "
                                f"{len(self.recent_trades[-CFG.adapt_window:])}) -> drift bar "
                                f"{self._eff_drift():.1f}bps (base {CFG.drift_bps:.0f})")
            logger.info(f"[{'WIN' if won else 'LOSS'}] {p['coin']} {p['dir']} @ "
                        f"{entry*100:.0f}c -> {w} | {pnl:+.2f} | bankroll ${self.bankroll:.2f} "
                        f"| day net {net:+.2f}" + (" [SIM]" if p.get("sim") else ""))
            # LIVE ACCURACY + EV METER: the honest read on whether constant-betting is net-positive.
            # WR alone lies (favorite pricing); EV/$ (net PnL per $ staked) is the real compounding rate.
            self.recent_ev.append((1 if won else 0, pnl, entry * p.get("shares", CFG.shares)))
            self.recent_ev = self.recent_ev[-100:]
            if len(self.recent_ev) >= 10:
                _w = sum(x[0] for x in self.recent_ev); _n = len(self.recent_ev)
                _net = sum(x[1] for x in self.recent_ev); _stk = sum(x[2] for x in self.recent_ev)
                _ev = _net / _stk if _stk else 0.0
                logger.info(f"[TRACK] last {_n}: {_w}/{_n}={100*_w/_n:.0f}%WR | net {_net:+.2f} | "
                            f"EV/$ {_ev:+.3f} | {'COMPOUNDING ✓' if _net > 0 else 'break-even/bleeding'}")
            tg._send(f"{'🧪 ' if p.get('sim') else ''}{'✅ <b>WIN</b>' if won else '❌ <b>LOSS</b>'}"
                     f"{' (sim)' if p.get('sim') else ''} {p['coin']} {p['dir']} @ "
                     f"{entry*100:.0f}c → {w} | {pnl:+.2f} | 💰 ${self.bankroll:.2f} | day net {net:+.2f}",
                     dedup_key=f"res-{k}")
            if not p.get("sim"):
                real_resolved = True
            self._save()
        # after a real resolution batch: reconcile to the chain so the logged/dashboard
        # bankroll is the TRUTH (the running ledger drifts above chain on proxy fills/fees),
        # and prune settled positions so the state file doesn't bloat.
        if real_resolved:
            before = self.bankroll
            self._sync_bankroll()
            self._prune_positions()
            day = self.bankroll - self.day_start_bankroll
            logger.info(f"[RECONCILED] bankroll ${self.bankroll:.2f} (chain truth) | "
                        f"session net {day:+.2f}" + (f" | ledger said ${before:.2f}"
                        if abs(before - self.bankroll) > 0.01 else ""))
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
        self._sync_bankroll()       # start from the REAL on-chain balance, not the saved ledger
        self.hwm = self.bankroll    # fresh profit-lock peak each run (rolling within the run, incl. across midnight)
        self.day_start_bankroll = self.bankroll   # anchor honest day net to the reconciled start
        self._prune_positions()     # clear stale resolved positions accumulated across runs
        n = 0
        while True:
            n += 1
            self._roll_day()
            if not self._is_daytime():          # night clears the daytime block, fresh for next day
                self.day_blocked = False
                self.day_loss_streak = 0
            try:
                self._snapshot_strikes()        # cache the Chainlink strike at window-open (correct feed)
                self.check_orders()
                self.manage_positions()         # active exit: take profit / dodge late reversal
                self.resolve()
                self.day_peak = max(self.day_peak, self.wins - self.losses)
                # hwm is updated from CHAIN TRUTH inside _sync_bankroll (not here) so the
                # profit-lock peak can't be inflated by ledger drift and fire prematurely.
                if CFG.kill_floor > 0 and self.bankroll <= CFG.kill_floor:
                    # HARD kill-switch (v1.39): pre-committed max drawdown for the deposit test.
                    # Permanent — no new entries of ANY strategy until the owner resets it.
                    if n % 40 == 1:
                        logger.info(f"[KILL-SWITCH] bankroll ${self.bankroll:.2f} <= floor "
                                    f"${CFG.kill_floor:.2f} — all trading stopped (owner reset required)")
                    if not self._stop_notified:
                        tg._send(f"🛑 <b>KILL-SWITCH HIT</b> — bankroll ${self.bankroll:.2f} reached the "
                                 f"${CFG.kill_floor:.0f} floor. All trading stopped. The test is over; "
                                 f"reset CLEAN_KILL_FLOOR to resume.")
                        self._stop_notified = True
                elif self._stopped():
                    if not self._stop_notified:
                        if (CFG.trail_stop > 0 and (self.hwm - self.day_start_bankroll) >= CFG.trail_stop
                                and (self.hwm - self.bankroll) >= CFG.trail_stop):
                            tg._send(f"🔒 <b>PROFIT-LOCK</b> — peaked ${self.hwm:.2f} (day start ${self.day_start_bankroll:.2f}), "
                                     f"stopped at ${self.bankroll:.2f}, locked +${self.bankroll - self.day_start_bankroll:.2f}. "
                                     f"No new entries today (restart to resume).")
                        else:
                            tg._send(f"🛑 <b>CleanBot daily stop</b> | net {self.wins - self.losses:+.2f} "
                                     f"— no new entries today")
                        self._stop_notified = True
                    if n % 40 == 1:
                        _net = self.wins - self.losses
                        if (CFG.trail_stop > 0 and (self.hwm - self.day_start_bankroll) >= CFG.trail_stop
                                and (self.hwm - self.bankroll) >= CFG.trail_stop):
                            _why = (f"PROFIT-LOCK 🔒 (peak ${self.hwm:.2f} -> ${self.bankroll:.2f}, "
                                    f"locked +${self.bankroll - self.day_start_bankroll:.2f} vs day start)")
                        elif CFG.giveback > 0 and self.day_peak > 0 and (self.day_peak - _net) >= CFG.giveback:
                            _why = f"give-back (day-peak {self.day_peak:+.2f})"
                        else:
                            _why = "daily loss"
                        logger.info(f"[STOP] {_why}: net {_net:+.2f} bankroll ${self.bankroll:.2f} — no new entries today")
                elif time.time() < self.breaker_until:
                    if n % 40 == 1:
                        logger.info(f"[BREAKER] cooldown {int((self.breaker_until - time.time()) / 60)}m "
                                    f"left — no new entries")
                else:
                    for c in CFG.coins:
                        self.scan(c)
                    # Strategy #3 live audition — independent of the early signal-health gate,
                    # but shares this daily-stop / breaker guard. SOL/XRP only, min-size.
                    if CFG.late_live:
                        for c in CFG.late_coins:
                            self._late_entry(c)
                    # Strategy #4 vol-divergence audition — pricing edge, min-size, shares the
                    # same daily-stop / breaker / kill-floor guards.
                    if CFG.vol_div_live:
                        for c in CFG.vol_div_coins:
                            self._vol_div_entry(c)
            except Exception as e:
                logger.warning(f"loop error: {e}")
            # research data capture — fully isolated, never places orders / affects trading.
            # Includes research_coins (BTC/XRP) for SHADOW-LOGGING only: they're scanned for
            # data here but NEVER passed to scan()/trading above, so no real bets are placed.
            try:
                for c in tuple(CFG.coins) + tuple(c for c in CFG.research_coins if c not in CFG.coins):
                    self._research_scan(c)
                    self._research_scan(c, "late")
                self._research_resolve()
            except Exception as e:
                logger.debug(f"research error: {e}")
            if n % 40 == 1:
                logger.info(f"… alive scan#{n} open={len(self.open_orders)} "
                            f"positions={len(self.positions)} bankroll=${self.bankroll:.2f} "
                            f"day_net={self.wins-self.losses:+.2f}")
                try:  # v1.36.1: periodic signal-edge point so the dashboard can plot the timeline
                    _sig = self._signal_health()
                    if _sig is not None:
                        logger.info(f"[SIG] edge={_sig:+.1f}pts")
                except Exception:
                    pass
                self._sync_bankroll()       # keep bankroll honest vs the chain (~every 40 scans)
            time.sleep(5)


if __name__ == "__main__":
    CleanBot().run()
