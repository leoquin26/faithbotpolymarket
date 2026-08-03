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
                 "book_imb", # v1.41: Binance top-of-book size imbalance [-1..+1] (bid vs ask depth) — leading microstructure signal, product data-enrichment phase 1
                 # v1.55 data-quality tags (filter OOS to chainlink/* only)
                 "strike_source", "spot_source", "roc_source", "sigma_source", "feed_ok"]
logger.remove()
logger.add(sys.stdout, level="INFO", format="{time:HH:mm:ss} | {message}")
logger.add(os.path.join(V3, "clean_bot.log"), level="INFO",
           format="{time:YYYY-MM-DD HH:mm:ss} | {message}", rotation="20 MB")


VERSION = "1.64.0"  # bump on EVERY change + add a CHANGELOG.md entry + git tag cleanbot-vX.Y.Z
EARLY_SNAP_PATH = os.path.join(V3, "data", "late_early_snaps.json")  # survive restarts for require_early


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
    # v1.60.8 RUIN GUARD: the exchange minimum (5 shares) is a FIXED dollar cost, so as the
    # bankroll shrinks it becomes an ever-larger share of the book (at $20, 5sh@65c = 16%;
    # at $12 it is 27%). Below the bankroll where even the MINIMUM bet exceeds this fraction,
    # no bet can be sized responsibly — stand down rather than gamble a quarter of the book.
    max_bet_hard_pct: float = float(os.getenv("CLEAN_MAX_BET_HARD_PCT", "0.20"))
    # v1.60.8 emergency brake thresholds (see the verdict block): retire an engine early when
    # EV is catastrophic, without waiting for the n>=40 sample the scale-up reset destroyed.
    emergency_n: int = int(os.getenv("CLEAN_EMERGENCY_N", "10"))
    emergency_ev: float = float(os.getenv("CLEAN_EMERGENCY_EV", "-0.15"))
    max_open_pct: float = float(os.getenv("CLEAN_MAX_OPEN_PCT", "0.25"))  # cap total open exposure
    corr_pair_frac: float = float(os.getenv("CLEAN_CORR_PAIR_FRAC", "0.5"))  # ETH+SOL same dir same window = 1 correlated bet, not 2: size each leg at this frac (skip 2nd leg if half < exchange min)
    corr_full_at: float = float(os.getenv("CLEAN_CORR_FULL_AT", "55"))  # v1.31: bankroll $ at which same-dir pairs trade BOTH legs full-size. Legs are +EV (aligned 69% vs 64% BE, n=884); the cap is risk-concentration: at $55+ a pair = ~12% of book (policy-sized) and a paired loss no longer eats the daily stop. Auto-unlocks as the book grows.
    corr_opposite_block: bool = os.getenv("CLEAN_CORR_OPPOSITE_BLOCK", "on").lower() in ("1","true","yes","on")  # skip a coin bet OPPOSITE a held correlated leg (divergent pairs = 55% coinflip in the data). v1.62.0: the LATE path now uses max_legs_per_window instead; this still gates the voldiv path.
    # v1.63.0 UNIFIED ENGINE — the only filter that survived out-of-sample validation.
    # Comma list of UTC hours the engine may trade ("" = all hours, pre-v1.63 behaviour).
    # Evidence (2,188 windows, maker economics, chronological 70/30 split):
    #   IN  18-24h UTC: n=570  WR 86.1% (BE 79.4%)  EV/$ +0.0978  z=+4.86
    #   OUT 00-18h UTC: n=1618 WR 78.8% (BE 79.6%)  EV/$ -0.0145  z=-1.07
    # Same prices, 7pp better win rate -> a genuine calibration gap, not a pricing one.
    # Survived: the ONLY 1 of 15 candidates to pass OOS; 6/6 hours positive; 5/5
    # walk-forward folds positive; label-shuffle placebo over all 24 six-hour blocks
    # p~0.001. Corroborated independently by the v1.48 Lima-session study (afternoon
    # +14.3pts = the same clock window). Every OTHER filter we believed in (lead state,
    # roc agreement, coin, price band) died out-of-sample: Spearman rho(train,test) = +0.10.
    # ── v1.64.0 FAV ENGINE — the rebuild around the wallet-census discovery ──────
    # 2,020,868 real trades: buying the FAVOURITE (55-90c) and holding to settlement
    # is +2..+5.6pp above break-even at EVERY price band — EXCEPT in the final ~4
    # minutes, where the edge decays to zero (t_rem 150-240s: +0.16% ROI, the exact
    # slot every previous engine entered). Peak: t_rem 480-660s = +5.6pp, ROI +7.1%,
    # z=+46, n=134,660. Holders beat traders (census: +0.99% vs -0.83% ROI).
    # NO drift model (measured +0.0pp when agreeing, -8.9pp when not), no lead
    # states, no roc, no hour gate (the census edge spans all hours). TAKER on
    # purpose: the 2M trades that proved the edge WERE taker fills — matching their
    # execution removes the fill-selection gap that ate the maker era (-4.7pp).
    fav_live: bool = os.getenv("CLEAN_FAV_LIVE", "off").lower() in ("1", "true", "yes", "on")
    fav_t_min: int = int(os.getenv("CLEAN_FAV_T_MIN", "480"))    # seconds remaining, band floor
    fav_t_max: int = int(os.getenv("CLEAN_FAV_T_MAX", "660"))    # band ceiling (eval fires on first scan inside)
    fav_min_ask: float = float(os.getenv("CLEAN_FAV_MIN_ASK", "0.55"))
    fav_max_ask: float = float(os.getenv("CLEAN_FAV_MAX_ASK", "0.92"))
    trade_hours_utc: str = os.getenv("CLEAN_TRADE_HOURS_UTC", "")
    max_legs_per_window: int = max(1, int(os.getenv("CLEAN_MAX_LEGS_PER_WINDOW", "1")))  # v1.62.0: how many coins may hold a leg in ONE 15m window. 1 = the pre-v1.62 one-coin-per-window behaviour. Raising it trades variance concentration (same-dir legs share a fate 80.7% of the time, n=259 windows) for frequency (+70% eligible legs at no measured EV cost). Bounded by max_bet_pct per leg and max_open_pct in aggregate.
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
    late_coins: tuple = tuple(c for c in os.getenv("CLEAN_LATE_COINS", "SOL,ETH").split(",") if c)  # v1.53: drop BTC (late 55-70 EV≈0); SOL/ETH carry the edge
    late_t_min: float = float(os.getenv("CLEAN_LATE_T_MIN", "60"))    # last-window band (matches the shadow-capture zone)
    late_t_max: float = float(os.getenv("CLEAN_LATE_T_MAX", "210"))
    # v1.59: FIXED-TIME evaluation — the shadow edge (n=295 all/89 OOS, EV/$ +0.114/+0.070)
    # was measured at an unconditional ~195s snapshot. Live "first moment criteria pass"
    # triggering buys short-term extremes (adverse selection) and ran -EV while the same
    # windows at fixed time were +EV. One evaluation per window at first tick <= eval_trem;
    # no re-triggering later in the window, no chasing below eval_floor (restart/lag guard).
    late_eval_once: bool = os.getenv("CLEAN_LATE_EVAL_ONCE", "on").lower() in ("1", "true", "yes", "on")
    late_eval_trem: float = float(os.getenv("CLEAN_LATE_EVAL_TREM", "195"))
    late_eval_floor: float = float(os.getenv("CLEAN_LATE_EVAL_FLOOR", "150"))
    # v1.60 HIBAND: confirmed 80-90c favorites at the same fixed decision time. Study
    # (12d, fixed-time): 80-94c blend afterFee -0.005 (never trade the blend), but
    # roc-agreeing >=3bps subset afterFee +0.017 all / +0.011 OOS (both halves positive,
    # z<1 = audition-grade). 90c+ confirmed is -0.05 -> hard cap 0.90. Separate engine
    # tag "hiband": own TRACK meter + own n>=40 verdict so a thin edge can't drag the
    # core 60-70c band's verdict. Flat exchange-min size while auditioning.
    late_hiband: bool = os.getenv("CLEAN_LATE_HIBAND", "on").lower() in ("1", "true", "yes", "on")
    hiband_min_ask: float = float(os.getenv("CLEAN_HIBAND_MIN_ASK", "0.80"))
    hiband_max_ask: float = float(os.getenv("CLEAN_HIBAND_MAX_ASK", "0.89"))  # v1.60.1: 0.90 exactly falls in the measured NEGATIVE 90-94c bucket — cap below it
    hiband_roc_bps: float = float(os.getenv("CLEAN_HIBAND_ROC_BPS", "3"))
    late_min_ask: float = float(os.getenv("CLEAN_LATE_MIN_ASK", "0.55"))
    # v1.56: 0.68 (was 0.66). Overblock audit: ask 66-70 under same filters still +EV; 0.68 = middle step.
    # ROLLBACK: CLEAN_LATE_MAX_ASK=0.66
    # v1.58.2: default 0.70 (was 0.68). Frequency: many live skips were ask 69–72¢ still
    # inside research sweet spot; 0.68 cut those. ROLLBACK: CLEAN_LATE_MAX_ASK=0.68
    late_max_ask: float = float(os.getenv("CLEAN_LATE_MAX_ASK", "0.70"))
    late_drift_bps: float = float(os.getenv("CLEAN_LATE_DRIFT_BPS", "0"))  # v1.38.1/v1.58.2: late edge is drift-INDEPENDENT (OOS: drift<5 stronger). Env had drifted to 3 and starved joins — default 0; ask band does selection.
    late_mom_agree: bool = os.getenv("CLEAN_LATE_MOM_AGREE", "off").lower() in ("1", "true", "yes", "on")  # v1.38.2 (DEPRECATED, default off): roc60-based; unreliable (roc60 present only ~30% of late windows → fails open). Superseded by late_skip_fading.
    late_taker: bool = os.getenv("CLEAN_LATE_TAKER", "on").lower() in ("1", "true", "yes", "on")  # v1.46 (owner diagnosed it): TAKE the ask at signal time instead of resting a maker order. Live maker fills ran 52% vs 63c (−11pts) — resting orders fill DURING the reversal (adverse selection); the verified +12-16pt shadow edge was measured AT THE ASK. Costs ~1c spread + ~1.6c taker fee; buys the edge as verified.
    late_night_off: bool = os.getenv("CLEAN_LATE_NIGHT_OFF", "on").lower() in ("1", "true", "yes", "on")  # v1.48 session study (both-halves stable): late edge by Lima session = NIGHT 00-07 +0.9pts (zero, fee-negative) vs MORNING +6.6 / AFTERNOON +14.3 / EVENING +12.8. Late engine sleeps 00-07 Lima (~26% of its volume at ~zero EV). Early engine keeps 24h (night +2.9 STABLE — cutting it would be overblocking).
    late_night_start: int = int(os.getenv("CLEAN_LATE_NIGHT_START", "0"))   # Lima hour the late-engine sleep begins
    late_night_end: int = int(os.getenv("CLEAN_LATE_NIGHT_END", "7"))       # Lima hour it wakes
    late_shade: bool = os.getenv("CLEAN_LATE_SHADE", "on").lower() in ("1", "true", "yes", "on")  # v1.45 A-S maker shading (only applies when late_taker=off): rest deeper when sigma*sqrt(t_rem) exposure is high.
    late_shade_bps: float = float(os.getenv("CLEAN_LATE_SHADE_BPS", "15"))  # bps of expected remaining move per +1c of shading (own-fill terciles: 13.4/30.2bps)
    late_skip_fading: bool = os.getenv("CLEAN_LATE_SKIP_FADING", "on").lower() in ("1", "true", "yes", "on")  # v1.39: skip FADING leaders (favorite ahead but its lead SHRANK from the early→late snapshot, same dir). Uses Chainlink settlement-feed drift trajectory (96% coverage, reliable). Verified: whole band 76.5%/EV+0.167 → skip-fading 81.5%/EV+0.244, recent30% EV+0.202 z+1.27. Keeps growing+reversed leads.
    # ── v1.52/v1.58 REVERSE-UNDERWAY: settlement-feed ROC that fights the lead.
    # v1.52 SKIPPED those (cut ~10% of late rows). v1.58 default: RE-POINT direction
    # toward the ROC (trade the reverse) when the corrected side is still in the ask band;
    # only skip if CLEAN_LATE_REV_AS_DIR=off. Never invent a hard block for missing roc.
    late_roc_oppose: bool = os.getenv("CLEAN_LATE_ROC_OPPOSE", "on").lower() in ("1", "true", "yes", "on")
    late_roc_lookback: int = int(os.getenv("CLEAN_LATE_ROC_LOOKBACK", "60"))   # seconds
    late_roc_oppose_bps: float = float(os.getenv("CLEAN_LATE_ROC_OPPOSE_BPS", "2"))  # min opposing move
    late_rev_as_dir: bool = os.getenv("CLEAN_LATE_REV_AS_DIR", "on").lower() in ("1", "true", "yes", "on")
    # ── v1.58/v1.58.1 multi-signal DIRECTION (not a filter): vote only on lead=flip.
    # v1.58 also voted on grow+roc-fight → DIR spam + dead-ends (corrected 2c / late 98c).
    # v1.58.1: vote=flip only; reverse-underway on grow tries roc side if in-band else keeps late.
    late_dir_vote: bool = os.getenv("CLEAN_LATE_DIR_VOTE", "on").lower() in ("1", "true", "yes", "on")
    late_dir_late_w: float = float(os.getenv("CLEAN_LATE_DIR_LATE_W", "1.0"))
    late_dir_early_w: float = float(os.getenv("CLEAN_LATE_DIR_EARLY_W", "1.35"))  # flip losses: early was right
    late_dir_roc_w: float = float(os.getenv("CLEAN_LATE_DIR_ROC_W", "1.5"))
    late_dir_btc_w: float = float(os.getenv("CLEAN_LATE_DIR_BTC_W", "0.75"))
    late_dir_roc_min_bps: float = float(os.getenv("CLEAN_LATE_DIR_ROC_MIN_BPS", "1.5"))
    # When vote re-points opposite late lead, the "correct" side is often the underdog
    # (<55c). Allow a slightly lower floor so we don't always fall back to the bad side.
    # ROLLBACK: CLEAN_LATE_DIR_MIN_ASK=0.55 (same as late_min_ask → pure fallback).
    late_dir_min_ask: float = float(os.getenv("CLEAN_LATE_DIR_MIN_ASK", "0.45"))
    # ── v1.53 join-quality (deep research Jul 11): require early anchor + EV-gated compound.
    # "NO early snapshot" late 55-70 = edge −7.5 / EV −0.075 (toxic). Growing lead = +21 pts.
    late_require_early: bool = os.getenv("CLEAN_LATE_REQUIRE_EARLY", "on").lower() in ("1", "true", "yes", "on")
    late_grow_mult: float = float(os.getenv("CLEAN_LATE_GROW_MULT", "1.25"))  # size boost when early→late lead GREW (not a skip)
    # v1.54/v1.56: skip thin early→late FLIP. Default 3bps (was 5) — audit: 5bps cut +EV rows.
    # ROLLBACK: CLEAN_LATE_FLIP_MIN_BPS=5
    late_flip_min_bps: float = float(os.getenv("CLEAN_LATE_FLIP_MIN_BPS", "3"))
    # Hard $ cap per late fill (0=off). Stops a single favorite loss from wiping multiple wins.
    late_max_usd: float = float(os.getenv("CLEAN_LATE_MAX_USD", "3.50"))
    # v1.56: one FOK retry at refreshed ask after unfilled/400 (Jul 12-13 ~21% enters failed FOK).
    # ROLLBACK: CLEAN_LATE_FOK_RETRY=off
    late_fok_retry: bool = os.getenv("CLEAN_LATE_FOK_RETRY", "on").lower() in ("1", "true", "yes", "on")
    late_fok_retry_sleep: float = float(os.getenv("CLEAN_LATE_FOK_RETRY_SLEEP", "0.35"))  # seconds before refresh
    # v1.60.7: FAK (fill-and-kill) instead of FOK for the taker path. FOK is all-or-nothing:
    # when the top-of-book turns over in the ~1s between the depth read and the order landing,
    # the whole order is killed (22% pre-fix / ~8% post-book-aware miss rate — all $0 losses
    # but missed trades). FAK fills WHAT IS THERE at <= our price and kills the remainder, so a
    # window is never fully missed — we take a (book-limited) partial position at the same price.
    # NO retry with FAK (a retry after a partial = double-fill risk, the v1.60.2 class).
    late_fak: bool = os.getenv("CLEAN_LATE_FAK", "on").lower() in ("1", "true", "yes", "on")
    # v1.60.9: which early→late lead trajectories may trade, for BOTH late and hiband.
    # Empty = allow all (legacy). Set CLEAN_LATE_LEAD_ALLOW=grow for the measured edge.
    late_lead_allow: tuple = tuple(
        x.strip() for x in os.getenv("CLEAN_LATE_LEAD_ALLOW", "").split(",") if x.strip())
    # v1.55: never use Binance for late direction/roc (settlement is Chainlink). Skip if CL missing.
    late_require_cl_spot: bool = os.getenv("CLEAN_LATE_REQUIRE_CL_SPOT", "on").lower() in ("1", "true", "yes", "on")
    late_roc_cl_only: bool = os.getenv("CLEAN_LATE_ROC_CL_ONLY", "on").lower() in ("1", "true", "yes", "on")
    # Compound only when rolling late EV/$ clears this bar (and n≥min). Else flat exchange-min.
    # Protects $42 book from sizing into a red live meter. Set CLEAN_COMPOUND_MIN_EV=-9 to disable.
    compound_min_ev: float = float(os.getenv("CLEAN_COMPOUND_MIN_EV", "0"))
    compound_min_ev_n: int = int(os.getenv("CLEAN_COMPOUND_MIN_EV_N", "15"))
    # ── v1.51 recovery sizing (frequency-preserving): compound late stakes + per-coin tilt.
    # Research (late 55-70c): SOL EV/$ +0.135, ETH +0.085, BTC +0.007. Tilt SIZE toward SOL.
    late_coin_mult: dict = field(default_factory=dict)
    target_bankroll: float = float(os.getenv("CLEAN_TARGET_BANKROLL", "100"))  # milestone log only (e.g. $46→$100)
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
    vol_div_kappa: float = float(os.getenv("CLEAN_VOLDIV_KAPPA", "0.25"))  # v1.43: momentum-persistence term in the fair-value model — p uses dist + kappa*mu*t_rem (mu = recent 5-min drift rate). Tested: kappa=0.25 lifts OOS EV +0.037->+0.048, z +0.90->+1.50, +40% signals; kappa=1 (full extrapolation) is WORSE — momentum ~quarter-persists. 0 = driftless (v1.42 behavior).
    z_bar: float = float(os.getenv("CLEAN_Z_BAR", "1.0"))  # v1.43: early entry bar in VOL-NORMALIZED units (|dist|/(sigma*sqrt(age)) >= z_bar). OOS-verified +4.3pts z=+1.77; sub-1.0 moves are noise. 0 = revert to raw-bps bar.
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

    def __post_init__(self):
        # Parse CLEAN_LATE_COIN_MULT once (default SOL-heavy; no BTC after v1.53 coin list).
        raw = os.getenv("CLEAN_LATE_COIN_MULT", "SOL=1.5,ETH=1.0")
        out = {}
        for part in (raw or "").split(","):
            part = part.strip()
            if not part or "=" not in part:
                continue
            k, v = part.split("=", 1)
            try:
                out[k.strip().upper()] = max(0.0, float(v.strip()))
            except Exception:
                continue
        self.late_coin_mult = out
        # v1.63.0: parse the traded-hours whitelist once. Empty -> every hour allowed.
        hrs = set()
        for part in (self.trade_hours_utc or "").split(","):
            part = part.strip()
            if not part:
                continue
            try:
                h = int(part)
            except ValueError:
                continue
            if 0 <= h <= 23:
                hrs.add(h)
        self.trade_hours_set = hrs


CFG = Cfg()
STATE = os.path.join(V3, "clean_bot_state.json")
GAMMA = "https://gamma-api.polymarket.com"
_h = httpx.Client(timeout=12, trust_env=False)   # gamma+binance reachable direct


# ── truthful resolution (Chainlink via gamma) ────────────────────────────
def gamma_winner(coin: str, ws: int):
    """Prefer closed=true; fall back to open market if prices already decisive (≥0.99).
    v1.52: after the Jul 11 ETH wire-reverse, gamma stayed open with Up=0.995 for minutes
    while closed=true returned [] — ledger lag made the bot look 'not resolved'."""
    slug = f"{coin.lower()}-updown-15m-{ws}"
    for closed in ("true", "false"):
        try:
            r = _h.get(f"{GAMMA}/markets", params={"slug": slug, "closed": closed})
            arr = r.json()
            if not arr:
                continue
            m = arr[0]
            outs, pr = m.get("outcomes"), m.get("outcomePrices")
            if isinstance(outs, str):
                outs = json.loads(outs)
            if isinstance(pr, str):
                pr = json.loads(pr)
            if not outs or not pr:
                continue
            pr = [float(x) for x in pr]
            if max(pr) < 0.99:                  # not decisively settled yet
                continue
            return "UP" if str(outs[pr.index(max(pr))]).lower().startswith("up") else "DOWN"
        except Exception:
            continue
    return None


def _roc(ticks, sec):
    """Rate-of-change (fraction) over `sec` seconds from a tick list (robust to
    (ts,price) vs (price,ts) ordering). Falls back to oldest tick if span short."""
    try:
        r = _roc_strict(ticks, sec, min_frac=0.0)
        return 0.0 if r is None else r
    except Exception:
        return 0.0


def _roc_strict(ticks, sec, min_frac: float = 0.55):
    """ROC only if tick span covers ≥ min_frac of `sec`. None = unusable (not 0)."""
    try:
        if not ticks or len(ticks) < 2:
            return None
        def ts(t): return t[0] if t[0] > 1e8 else t[1]
        def px(t): return t[1] if t[0] > 1e8 else t[0]
        now_t = ts(ticks[-1]); now_p = px(ticks[-1])
        base_p = base_t = None
        for t in reversed(ticks):
            if now_t - ts(t) >= sec:
                base_p, base_t = px(t), ts(t)
                break
        if base_p is None:
            base_p, base_t = px(ticks[0]), ts(ticks[0])
        span = now_t - base_t if base_t is not None else 0.0
        if span < float(sec) * float(min_frac) or not base_p:
            return None
        return (now_p - base_p) / base_p
    except Exception:
        return None


def _pick_favorite(up_ask, down_ask, lo: float, hi: float):
    """v1.64.0: which side is the FAVOURITE, and is it inside the tradeable band?
    The favourite is simply the higher-priced side. Returns ("UP"|"DOWN", ask) or
    None (no ask on either side, tie, or favourite outside [lo, hi]).
    Pure function — unit-tested in test_clean_bot_risk.py."""
    try:
        ua = float(up_ask) if up_ask is not None else None
        da = float(down_ask) if down_ask is not None else None
    except (TypeError, ValueError):
        return None
    if ua is None and da is None:
        return None
    if ua is not None and (da is None or ua > da):
        side, px = "UP", ua
    elif da is not None and (ua is None or da > ua):
        side, px = "DOWN", da
    else:
        return None                       # exact tie: no favourite
    if not (lo <= px <= hi):
        return None
    return side, round(px, 2)


def _taker_buy_fee(price: float, shares: float) -> float:
    """Polymarket crypto taker fee per fill: 0.07 * p * (1-p) per share (embedded in usdcSize).
    Makers pay 0. Used so settlement EV/$ reflects capital actually at risk on taker entries."""
    p = float(price)
    return max(0.0, 0.07 * p * (1.0 - p) * float(shares))


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
        self.recent_ev = []                     # rolling (won, pnl, stake, engine) — live accuracy+EV meter
        self.day_results = []                   # today's (won, pnl, stake, engine) — for the midnight [SCORE] board
        # SELF-GOVERNANCE (v1.47): the engine executes its own pre-registered verdicts at n>=40
        # per engine — EV/$ >= +0.03 scale up (x2, then x3 cap), -0.03..+0.03 hold, <= -0.03 OFF.
        self.engine_mult = {"early": 1.0, "late": 1.0, "voldiv": 1.0, "hiband": 1.0, "fav": 1.0}   # size multiplier per engine
        self.engine_off = {"early": False, "late": False, "voldiv": False, "hiband": False, "fav": False}  # retired by verdict
        self._fav_evaled = set()                # v1.64.0: one fav decision per (coin, ws)
        self.killed = False                     # kill-floor latch (v1.43.1): stays True once fired, owner reset only
        self.breaker_until = 0.0                # cooldown end timestamp
        self._stop_notified = False
        self._nc_logged = set()                 # throttle [NO CONFIRM] logs (per window)
        self._late_evaled = set()               # v1.59: one fixed-time late decision per window
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
                       "recent_ev": self.recent_ev[-150:],
                       "day_results": self.day_results[-200:],
                       "engine_mult": self.engine_mult, "engine_off": self.engine_off,
                       "killed": self.killed,
                       "positions": self.positions,
                       # v1.61.0: resting GTC orders must survive a restart — an orphaned
                       # maker order left on the book can fill unseen (Jun-28 phantom class)
                       "open_orders": self.open_orders,
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
            # backfill engine tag on pre-v1.44 3-tuples so old rows don't crash per-engine math
            self.recent_ev = [tuple(x) if len(x) > 3 else (x[0], x[1], x[2], "mixed")
                              for x in d.get("recent_ev", [])]
            self.day_results = [tuple(x) if len(x) > 3 else (x[0], x[1], x[2], "mixed")
                                for x in d.get("day_results", [])]
            self.engine_mult.update(d.get("engine_mult", {}))
            self.engine_off.update(d.get("engine_off", {}))
            self.killed = bool(d.get("killed", False))
            self.positions = d.get("positions", {})
            # v1.61.0: re-track resting GTC orders across restarts; check_orders will
            # poll each oid and either record the fill or cancel-with-verify by age.
            self.open_orders = d.get("open_orders", {}) or {}
            self.traded = {tuple(t) for t in d.get("traded", [])}
            logger.info(f"state reloaded: {len(self.positions)} positions, "
                        f"{len(self.open_orders)} resting orders, bankroll "
                        f"${self.bankroll:.2f}, day net={self.wins - self.losses:+.2f}")
        except Exception as e:
            logger.warning(f"state load failed: {e}")

    # ── risk ─────────────────────────────────────────────────────────
    def _score_line(self, results, label):
        """One honest scoreboard row: n, WR, avg entry (=break-even), EV/$ staked."""
        if not results:
            return f"  {label:8s} n=0"
        n = len(results); w = sum(r[0] for r in results)
        net = sum(r[1] for r in results); stk = sum(r[2] for r in results)
        ev = net / stk if stk else 0.0
        return (f"  {label:8s} n={n:3d} WR={100*w/n:.0f}% net={net:+.2f} EV/$={ev:+.3f}"
                f" {'✓' if net > 0 else ''}")

    def _roll_day(self):
        t = self._today()
        if t != self.day:
            # DAILY SCOREBOARD (v1.44): per-engine verdict for the ending day, then reset.
            if self.day_results:
                logger.info(f"[SCORE] {self.day} per-engine:")
                for tag in ("early", "late", "voldiv", "hiband"):
                    sub = [r for r in self.day_results if r[3] == tag]
                    if sub:
                        logger.info(self._score_line(sub, tag))
                logger.info(self._score_line(self.day_results, "TOTAL"))
            self.day_results = []
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
        legs at once (e.g. 2026-06-26 01:46 ETH+SOL Down both lost, -$7.25 in one window).
        v1.61.2: returns the CONFLICTING COIN (truthy) instead of True so the caller can
        say WHICH leg blocked it — these guards used to return silently, the exact
        'why isn't it betting?' blindness v1.58.1 fixed everywhere else."""
        for o in self.open_orders.values():
            if o.get("coin") != coin and o.get("ws") == ws and o.get("dir") == direction:
                return o.get("coin")
        for p in self.positions.values():
            if (p.get("coin") != coin and p.get("ws") == ws and p.get("dir") == direction
                    and p.get("status") in ("filled", "open")):
                return p.get("coin")
        return None

    def _corr_opposite(self, coin, ws, direction):
        """True if ANOTHER coin already has a live bet in the SAME window but the OPPOSITE
        direction. Data (1067 windows): divergent correlated pairs win only 55% (n=168) vs
        69% when aligned — betting ETH/SOL to decorrelate is a coinflip that loses at favorite
        prices (e.g. 2026-06-29 SOL DOWN won but ETH UP lost, both actually closed DOWN).
        v1.61.2: returns the conflicting coin (truthy) so the block can be logged."""
        for o in self.open_orders.values():
            if o.get("coin") != coin and o.get("ws") == ws and o.get("dir") != direction:
                return o.get("coin")
        for p in self.positions.values():
            if (p.get("coin") != coin and p.get("ws") == ws and p.get("dir") != direction
                    and p.get("status") in ("filled", "open")):
                return p.get("coin")
        return None

    def _window_legs(self, ws):
        """v1.62.0: every live leg (resting order or filled position) in this 15m window,
        as [(coin, dir), ...] — the basis for the per-window leg limit that REPLACED the
        blanket one-coin-per-window corr guards. Measured on 753 windows: legs blocked by
        those guards scored WR 77.4%/EV +0.041 (same-dir) and 75.9%/EV +0.047 (opposite)
        vs 74.6%/EV -0.010 for the leg we actually took — no evidence they were worse.
        The real cost is CORRELATION: same-direction legs share a fate 80.7% of the time,
        so the limit (not a blanket ban) plus max_bet_pct/max_open_pct is the honest control."""
        legs = [(o.get("coin"), o.get("dir")) for o in self.open_orders.values()
                if o.get("ws") == ws]
        legs += [(p.get("coin"), p.get("dir")) for p in self.positions.values()
                 if p.get("ws") == ws and p.get("status") in ("filled", "open")]
        return legs

    def _same_dir_legs(self, ws, direction):
        """v1.63.1: coins already holding THIS direction in THIS window. A second
        same-direction leg is LEVERAGE (measured 80.7% shared fate, n=259 windows),
        not diversification — v1.62.0's plain leg COUNT lost that distinction and on
        2026-07-27 let BTC DOWN 88c + ETH DOWN 60c ride the same window; both reversed."""
        return [c for c, d in self._window_legs(ws) if d == direction]

    def _size_shares(self, price):
        """Tiered Kelly: conservative while rebuilding, bigger once bankroll recovers past
        kelly_bump_at — so wins compound up as you grow. Share-floored + capped."""
        if not CFG.compound:
            return CFG.shares
        kf = CFG.kelly_bump if self.bankroll >= CFG.kelly_bump_at else CFG.kelly_frac
        stake = min(self.bankroll * kf, self.bankroll * CFG.max_bet_pct)
        return max(CFG.shares, int(round(stake / max(0.02, price))))

    def _late_engine_ev(self):
        """Rolling late EV/$ from recent_ev, or None if fewer than compound_min_ev_n samples."""
        eng = [x for x in self.recent_ev if len(x) > 3 and x[3] == "late"]
        if len(eng) < max(1, CFG.compound_min_ev_n):
            return None
        net = sum(x[1] for x in eng)
        stk = sum(x[2] for x in eng)
        return (net / stk) if stk else 0.0

    def _late_compound_ok(self) -> bool:
        """v1.53: only size-up when live late EV/$ is green enough. Flat min otherwise."""
        if not CFG.compound:
            return False
        if CFG.compound_min_ev <= -9:          # kill-switch: always compound if compound on
            return True
        ev = self._late_engine_ev()
        if ev is None:
            return False                       # not enough trades — stay min-size
        return ev > CFG.compound_min_ev

    def _early_snapshot(self, coin: str, ws: int):
        """Early research row for (coin, ws): memory first, then disk (survives restarts)."""
        er = self._research.get((coin, ws, "early"))
        if er:
            return er
        try:
            if os.path.isfile(EARLY_SNAP_PATH):
                data = json.loads(open(EARLY_SNAP_PATH, encoding="utf-8").read() or "{}")
                hit = data.get(f"{coin}:{ws}")
                if hit and "drift_pct" in hit:
                    return hit
        except Exception:
            pass
        return None

    def _persist_early_snapshot(self, coin: str, ws: int, row: dict):
        """v1.55: write early drift to disk so require_early works across restarts."""
        try:
            os.makedirs(os.path.dirname(EARLY_SNAP_PATH), exist_ok=True)
            data = {}
            if os.path.isfile(EARLY_SNAP_PATH):
                data = json.loads(open(EARLY_SNAP_PATH, encoding="utf-8").read() or "{}")
            data[f"{coin}:{ws}"] = {
                "drift_pct": row.get("drift_pct"),
                "dir": row.get("dir"),
                "ts": row.get("ts"),
                "strike_source": row.get("strike_source", ""),
                "spot_source": row.get("spot_source", ""),
            }
            # prune > 6h old keys by ws epoch
            cutoff = int(time.time()) - 6 * 3600
            data = {k: v for k, v in data.items()
                    if (int(k.split(":")[-1]) if ":" in k else 0) >= cutoff}
            open(EARLY_SNAP_PATH, "w", encoding="utf-8").write(json.dumps(data))
        except Exception as e:
            logger.debug(f"early snap persist err {e}")

    def _late_lead_state(self, coin: str, ws: int, dist: float, is_up: bool) -> str:
        """early→late trajectory: grow | fade | flip | none (no early)."""
        er = self._early_snapshot(coin, ws)
        if not er:
            return "none"
        try:
            e_drift = float(er.get("drift_pct"))
            same = (e_drift > 0) == is_up
            if not same:
                return "flip"
            if abs(dist * 100) + 1e-12 >= abs(e_drift):
                return "grow"
            return "fade"
        except Exception:
            return "none"

    def _late_roc_bps(self, coin: str):
        """v1.58: denser CL path ROC with lookback fallback 60→45→30. Returns (bps|None, n_ticks)."""
        try:
            lb = int(CFG.late_roc_lookback)
            lookbacks = [lb] + [x for x in (45, 30) if x < lb]
            ticks = chainlink_ws.get_ticks(coin, max(lookbacks) + 40)
            n = len(ticks) if ticks else 0
            if not ticks or n < 5:
                return None, n
            for sec in lookbacks:
                r = _roc_strict(ticks, sec, min_frac=0.55)
                if r is not None:
                    return r * 1e4, n
            return None, n
        except Exception:
            return None, 0

    def _late_vote_direction(self, coin: str, ws: int, late_up: bool, lead_state: str,
                             roc_bps, force_roc: bool = False):
        """v1.58 multi-signal direction vote. Returns (want_up, detail_str).
        Weights: late drift, early snap (heavier on flip), roc, BTC cross-asset.
        Does NOT skip — caller still places a bet if any side is in-band."""
        votes = {"UP": 0.0, "DOWN": 0.0}
        votes["UP" if late_up else "DOWN"] += float(CFG.late_dir_late_w)
        bits = [f"late={'UP' if late_up else 'DOWN'}"]

        early_up = None
        er = self._early_snapshot(coin, ws)
        if er:
            try:
                e_drift = float(er.get("drift_pct"))
                early_up = e_drift > 0
                # On flip, early was right on recent live losses — weight it higher.
                w = float(CFG.late_dir_early_w) if lead_state == "flip" else float(CFG.late_dir_early_w) * 0.6
                votes["UP" if early_up else "DOWN"] += w
                bits.append(f"early={'UP' if early_up else 'DOWN'}@{e_drift:+.3f}%")
            except Exception:
                pass

        if roc_bps is not None and (force_roc or abs(roc_bps) >= float(CFG.late_dir_roc_min_bps)):
            w = float(CFG.late_dir_roc_w)
            if force_roc:
                w += 0.75  # reverse-underway: lean harder into the path
            votes["UP" if roc_bps > 0 else "DOWN"] += w
            bits.append(f"roc={roc_bps:+.1f}")

        try:
            btc_d = self._coin_drift("BTC")
            if btc_d is not None and abs(btc_d) * 1e4 >= 3.0:
                votes["UP" if btc_d > 0 else "DOWN"] += float(CFG.late_dir_btc_w)
                bits.append(f"btc={btc_d*1e4:+.1f}bps")
        except Exception:
            pass

        # Soft microstructure (optional; never decisive alone)
        try:
            fl = binance_ws.get_order_flow(coin, 60)
            if fl is not None and abs(fl) >= 0.25:
                votes["UP" if fl > 0 else "DOWN"] += 0.4
                bits.append(f"flow={fl:+.2f}")
        except Exception:
            pass

        if votes["UP"] > votes["DOWN"]:
            pick_up = True
        elif votes["DOWN"] > votes["UP"]:
            pick_up = False
        else:
            # tie: flip → trust early if we have it; else late
            if lead_state == "flip" and early_up is not None:
                pick_up = early_up
                bits.append("tie→early")
            else:
                pick_up = late_up
                bits.append("tie→late")
        detail = f"U={votes['UP']:.2f}/D={votes['DOWN']:.2f} " + " ".join(bits)
        return pick_up, detail

    def _late_size_shares(self, coin: str, price: float, lead_state: str = "none") -> int:
        """v1.54 late sizing.
        - While live late EV not proven: EXACT exchange-min shares (no SOL cmult inflate).
          Bugfix: v1.53 still did 5×1.5=7sh on SOL in MIN mode → losses wipe 2 wins.
        - When EV green: compound × coin mult × grow boost, capped by max_bet_pct and late_max_usd.
        """
        price = max(0.02, float(price))
        if not self._late_compound_ok():
            # v1.60.6: the n>=40 verdict clears recent_ev, which re-locks this gate for
            # ~15 trades — muting the very scale-up the verdict just awarded. The earned
            # engine multiplier applies even in the min-size audition window (the verdict's
            # own design note says the next step is judged ON THE NEW SIZE).
            shares = max(CFG.shares, int(CFG.shares * float(self.engine_mult.get("late", 1.0))))
        else:
            base = self._size_shares(price)
            cmult = float(CFG.late_coin_mult.get(coin.upper(), 1.0))
            if lead_state == "grow" and CFG.late_grow_mult > 1.0:
                cmult *= CFG.late_grow_mult
            emult = float(self.engine_mult.get("late", 1.0))
            shares = max(CFG.shares, int(round(base * cmult * emult)))
        # v1.60.8 CRITICAL — bankroll risk cap now applies to *BOTH* branches.
        # v1.60.6 applied engine_mult inside the MIN branch but left it UNCAPPED, so a flat
        # 10-share bet stayed ~$6.50 while the bankroll fell $58 → $21 on 2026-07-23:
        # 12% → 30% of book per bet, a textbook ruin spiral (-$37 in 12 trades). The cap
        # below previously lived only in the compound branch — that asymmetry was the bug.
        max_sh = max(CFG.shares, int((self.bankroll * CFG.max_bet_pct) / price))
        shares = min(shares, max_sh)
        # $ notional cap (geometry): at 66c, $3.50 → 5sh floor still binds
        if CFG.late_max_usd > 0:
            cap_sh = max(CFG.shares, int(CFG.late_max_usd / price))
            shares = min(shares, cap_sh)
        return max(CFG.shares, shares)

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
        elif phase == "mid":
            # v1.49 (owner: "focus more on late entry — measure more"): the mid-window zone
            # (3.5-9min left) has NEVER been captured — zero rows. If fresh leads there carry
            # the late-style edge, it's a second late-type engine = the real frequency multiplier.
            # Shadow measurement only; verifier decides at n>=80 per the standard gate.
            if not (210 < t_rem <= 540):
                return
        elif age < CFG.warmup or t_rem < CFG.min_t:
            return
        strike = float(info.threshold_price or 0)
        strike_src = str(getattr(info, "strike_source", "") or "")
        # v1.55: prefer settlement-feed spot; tag source for research purity
        px = float(info.current_crypto_price or 0)
        spot_src = str(getattr(info, "spot_source", "") or "")
        if px <= 0 or not spot_src.startswith("chainlink"):
            cl = chainlink_ws.get_price(coin)
            if cl and cl > 0:
                px = float(cl); spot_src = "chainlink_rtds"
        if px <= 0:
            bn = binance_ws.get_price(coin)
            if bn and bn > 0:
                px = float(bn); spot_src = "binance"
        if strike <= 0 or px <= 0:
            return
        dist = (px - strike) / strike
        if abs(dist) < CFG.research_min_bps / 10000.0:
            return
        self._research_seen.add(rk)
        direction = "UP" if dist > 0 else "DOWN"
        # Settlement-feed momentum/vol (v1.57: RTDS+on-chain merge via get_ticks)
        cl_ticks = chainlink_ws.get_ticks(coin, 340)
        roc_source = sigma_source = ""
        if cl_ticks and len(cl_ticks) >= 5:
            roc60 = _roc(cl_ticks, 60); roc300 = _roc(cl_ticks, 300)
            roc_source = "chainlink_merged"
        else:
            bn_ticks = binance_ws.get_tick_history(coin, 300)
            roc60 = _roc(bn_ticks, 60); roc300 = _roc(bn_ticks, 300)
            roc_source = "binance" if bn_ticks else ""
        sig = chainlink_ws.get_realized_vol(coin, 180)
        if sig and sig > 0:
            sigma = sig; sigma_source = "chainlink_merged"
        else:
            sigma = binance_ws.get_realized_vol(coin, 180); sigma_source = "binance" if sigma else ""
        feed_ok = int(strike_src.startswith("chainlink") and spot_src.startswith("chainlink"))
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
                        f"feed={'OK' if feed_ok else 'MIXED'} "
                        f"-> {decision}" + (f":{reason}" if reason else ""))
        row = {
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
            "book_imb": (lambda bi: round(bi, 3) if bi is not None else "")(binance_ws.get_book_imbalance(coin)),
            "strike_source": strike_src,
            "spot_source": spot_src,
            "roc_source": roc_source,
            "sigma_source": sigma_source,
            "feed_ok": feed_ok,
        }
        self._research[rk] = row
        if phase == "early":
            self._persist_early_snapshot(coin, ws, row)

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
            try:
                # v1.55: if schema missing feed tags, rotate old file so DictWriter stays aligned
                if os.path.exists(RESEARCH_CSV) and os.path.getsize(RESEARCH_CSV) > 0:
                    with open(RESEARCH_CSV, encoding="utf-8", errors="ignore") as _hf:
                        _hdr = _hf.readline()
                    if "feed_ok" not in _hdr:
                        _bak = RESEARCH_CSV + ".pre_v155"
                        os.replace(RESEARCH_CSV, _bak)
                        logger.info(f"[RESEARCH] schema upgrade — archived {_bak}")
                new = (not os.path.exists(RESEARCH_CSV)) or os.path.getsize(RESEARCH_CSV) == 0
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
        if self.engine_off.get("early"):       # retired by its own 40-trade verdict (v1.47)
            return
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
        # SIGNAL BAR (v1.43): vol-NORMALIZED when possible — the literature-correct unit. A move
        # only means something scaled by current volatility: zscore = |dist| / (sigma*sqrt(age)).
        # Verified OOS: z>=1.0 windows carry the whole edge (+4.3pts, z=+1.77 PASS) while the
        # sub-1.0 windows the raw-bps bar admitted are noise (-1.6 OOS). Falls back to the old
        # raw-bps bar when sigma is unavailable (fail-open to previous behavior).
        _zsig = binance_ws.get_realized_vol(coin, 180)
        if CFG.z_bar > 0 and _zsig and _zsig > 0:
            zscore = abs(dist) / (_zsig * math.sqrt(max(30.0, age)))
            if zscore < CFG.z_bar or abs(dist) < 3 / 10000.0:   # 3bps dust floor (matches research capture)
                if abs(dist) >= CFG.drift_bps / 10000.0 and (coin, ws) not in self._nc_logged:
                    logger.info(f"[ZBAR SKIP] {coin} drift={dist*1e4:+.1f}bps z={zscore:.2f} < {CFG.z_bar} "
                                f"(move not significant vs current vol)")
                    self._nc_logged.add((coin, ws))
                return
        else:
            eff_drift = self._eff_drift()              # fallback: raw-bps adaptive bar (pre-v1.43)
            regime = ""
            if CFG.er_filter:
                er = self._efficiency_ratio(coin)      # PROACTIVE: trend vs chop, measured before betting
                if er is not None and er < CFG.er_trend:   # choppy regime -> demand a stronger drift
                    eff_drift = max(eff_drift, CFG.er_chop_drift)
                    regime = f" chop(ER={er:.2f})"
            if abs(dist) < eff_drift / 10000.0:        # need a clear early drift (adaptive quality bar)
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
        shares = max(CFG.shares, int(round(self._size_shares(maker) * self.engine_mult.get("early", 1.0))))
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
        # SESSION FILTER (v1.48): the late edge is ZERO in the Lima night block (00-07: +0.9pts,
        # fee-negative; other sessions +6.6..+14.3, both-halves stable). Early engine keeps 24h.
        if CFG.late_night_off:
            _lima_h = (time.gmtime().tm_hour - 5) % 24
            if CFG.late_night_start <= _lima_h < CFG.late_night_end:
                return
        info = get_market_info(coin)
        if not info:
            return
        ws = info.window_start
        key = (coin, ws)
        if key in self.traded:                       # one entry per window (shared with early)
            return
        # v1.63.0 UNIFIED HOUR GATE — the one filter that passed out-of-sample validation.
        # Keyed on the WINDOW's UTC hour (not "now") so it matches the research exactly.
        if CFG.trade_hours_set:
            _wh = time.gmtime(ws).tm_hour
            if _wh not in CFG.trade_hours_set:
                if (coin, ws, "hour") not in self._nc_logged:
                    logger.info(f"[LATE SKIP] {coin} hour-gate {_wh:02d}h UTC not in traded set "
                                f"{sorted(CFG.trade_hours_set)} (OOS-validated window only)")
                    self._nc_logged.add((coin, ws, "hour"))
                return
        now = time.time()
        t_rem = ws + 900 - now
        if not (CFG.late_t_min <= t_rem <= CFG.late_t_max):
            return
        # v1.60.1: prune per-window bookkeeping sets (they grew unbounded — slow leak)
        if len(self._late_evaled) > 300:
            _cut = now - 7200
            self._late_evaled = {k for k in self._late_evaled if k[1] >= _cut}
            self._nc_logged = {k for k in self._nc_logged if k[1] >= _cut}
        # v1.59 fixed-time evaluation: ONE unconditional decision per window at ~eval_trem,
        # mirroring how the shadow edge was measured. Kills first-crossing adverse selection.
        if CFG.late_eval_once:
            if t_rem > CFG.late_eval_trem:
                return                                   # not decision time yet
            if key in self._late_evaled:
                return                                   # already decided this window
            if t_rem < CFG.late_eval_floor:
                # v1.60.3: never silent — a missed slot means the scan loop stalled
                # (API hang / restart); count these, they should be ~zero.
                if (coin, ws, "evalmiss") not in self._nc_logged:
                    logger.warning(f"[LATE EVAL MISSED] {coin} slot passed unevaluated "
                                   f"(t_rem={t_rem:.0f}s < floor {CFG.late_eval_floor:.0f}) "
                                   f"— scan stall or restart; no chase by design")
                    self._nc_logged.add((coin, ws, "evalmiss"))
                return
            self._late_evaled.add(key)                   # decide NOW, whatever the outcome
        strike = float(info.threshold_price or 0)
        strike_src = str(info.strike_source or "")
        if not strike_src.startswith("chainlink"):
            return                                   # never trade Binance-strike windows
        # v1.55: settlement-feed spot ONLY for late direction (no Binance basis)
        px = float(info.current_crypto_price or 0)
        spot_src = str(getattr(info, "spot_source", "") or "")
        if not spot_src.startswith("chainlink") or px <= 0:
            cl = chainlink_ws.get_price(coin)
            if cl and cl > 0:
                px = float(cl); spot_src = "chainlink_rtds"
            else:
                try:
                    import chainlink_onchain as _cl_oc
                    cl = _cl_oc.get_price(coin)
                    if cl and cl > 0:
                        px = float(cl); spot_src = "chainlink_onchain"
                except Exception:
                    pass
        if CFG.late_require_cl_spot and (px <= 0 or not spot_src.startswith("chainlink")):
            if (coin, ws, "nospot") not in self._nc_logged:
                logger.info(f"[LATE SKIP] {coin} no Chainlink spot (src={spot_src or 'none'}) "
                            f"— refusing Binance fallback for direction")
                self._nc_logged.add((coin, ws, "nospot"))
            return
        if strike <= 0 or px <= 0:
            return
        dist = (px - strike) / strike
        if abs(dist) < CFG.late_drift_bps / 10000.0:  # late edge is drift-independent; 55-70c band does the selecting
            return
        is_up = dist > 0
        direction = "UP" if is_up else "DOWN"
        lead_state = self._late_lead_state(coin, ws, dist, is_up)
        # v1.53: REQUIRE early research snapshot (toxic bucket without it: edge −7.5 EV −0.075).
        # Fail-closed: if the bot restarted mid-window and missed early capture, skip this late.
        if CFG.late_require_early and lead_state == "none":
            if (coin, ws, "noearly") not in self._nc_logged:
                logger.info(f"[LATE SKIP] {coin} {direction} no early snapshot — "
                            f"join quality unmeasured (research: no-early EV/−0.075)")
                self._nc_logged.add((coin, ws, "noearly"))
            return
        # SKIP FADING LEADERS (v1.39): same-dir shrink early→late.
        # v1.60.9 LEAD-STATE WHITELIST — applies to BOTH engines (late core + hiband),
        # because the same ordering replicated independently in each price band on real
        # fill prices (n=126): grow is the edge, flip is negative, fade is toxic.
        #   late  60-75c:  grow +0.053 (n=40) | flip -0.020 (n=35) | fade -0.295 (n=19)
        #   hiband 80-89c: grow +0.100 (n=21, 95.2% WR) | flip -0.157 (n=11)
        # Cross-engine replication is why this is a structural finding, not a mined cell.
        if CFG.late_lead_allow and lead_state not in CFG.late_lead_allow:
            if (coin, ws, "leadwl") not in self._nc_logged:
                logger.info(f"[LATE SKIP] {coin} {direction} lead={lead_state} not in "
                            f"allowed {','.join(CFG.late_lead_allow)} (grow carries the edge)")
                self._nc_logged.add((coin, ws, "leadwl"))
            return
        if CFG.late_skip_fading and lead_state == "fade":
            if (coin, ws, "fade") not in self._nc_logged:
                er = self._early_snapshot(coin, ws) or {}
                try:
                    e_drift = float(er.get("drift_pct", 0))
                except Exception:
                    e_drift = 0.0
                logger.info(f"[LATE SKIP] {coin} {direction} fading leader "
                            f"early={e_drift:+.3f}% now={dist*100:+.3f}%")
                self._nc_logged.add((coin, ws, "fade"))
            return
        # v1.54: thin FLIP joins (dir changed early→late but lead still tiny) — wire-flip bait.
        # Live: ETH UP +3.0bps lead=flip @62c → LOSS. Established flips still allowed.
        if lead_state == "flip" and CFG.late_flip_min_bps > 0:
            if abs(dist) * 1e4 < CFG.late_flip_min_bps:
                if (coin, ws, "thinflip") not in self._nc_logged:
                    logger.info(f"[LATE SKIP] {coin} {direction} thin flip "
                                f"drift={dist*1e4:+.1f}bps < {CFG.late_flip_min_bps:.0f}bps "
                                f"(need established lead after dir change)")
                    self._nc_logged.add((coin, ws, "thinflip"))
                return
        # ROC on densified CL path (v1.57/v1.58 lookback fallback).
        roc_bps, n_ticks = self._late_roc_bps(coin)
        thr = float(CFG.late_roc_oppose_bps)
        fighting = False
        if roc_bps is not None and CFG.late_roc_oppose and thr > 0:
            fighting = (is_up and roc_bps <= -thr) or ((not is_up) and roc_bps >= thr)
        elif roc_bps is None and CFG.late_roc_cl_only:
            if (coin, ws, "noroc") not in self._nc_logged:
                logger.info(f"[LATE] {coin} {direction} CL roc sparse "
                            f"n_ticks={n_ticks} — fail-open (densify may still warm up)")
                self._nc_logged.add((coin, ws, "noroc"))

        # v1.58.1 DIRECTION:
        #  - multi-signal vote ONLY on lead=flip (fixes wrong-side flips; no grow spam)
        #  - reverse-underway on non-flip: optional roc re-point; else keep late / legacy skip
        late_up = is_up
        dir_note = ""
        if CFG.late_dir_vote and lead_state == "flip":
            want_up, vdetail = self._late_vote_direction(
                coin, ws, late_up, lead_state, roc_bps, force_roc=fighting)
            if want_up != late_up:
                is_up = want_up
                direction = "UP" if is_up else "DOWN"
                dir_note = f"dirfix late={'UP' if late_up else 'DOWN'}→{direction}"
                if (coin, ws, "dirvote") not in self._nc_logged:
                    logger.info(f"[LATE DIR] {coin} {dir_note} lead=flip "
                                f"fight={int(fighting)} n_ticks={n_ticks} | {vdetail}")
                    self._nc_logged.add((coin, ws, "dirvote"))
        elif fighting:
            if CFG.late_rev_as_dir and roc_bps is not None:
                # Re-point toward roc; book step falls back to late if ask unusable.
                want_up = roc_bps > 0
                if want_up != late_up:
                    is_up = want_up
                    direction = "UP" if is_up else "DOWN"
                    dir_note = f"revRoc late={'UP' if late_up else 'DOWN'}→{direction}"
                    if (coin, ws, "dirrev") not in self._nc_logged:
                        logger.info(f"[LATE DIR] {coin} {dir_note} lead={lead_state} "
                                    f"roc={roc_bps:+.1f} n_ticks={n_ticks}")
                        self._nc_logged.add((coin, ws, "dirrev"))
            else:
                if (coin, ws, "rev") not in self._nc_logged:
                    logger.info(f"[LATE SKIP] {coin} {direction} reverse-underway "
                                f"roc={roc_bps:+.1f}bps n_ticks={n_ticks}")
                    self._nc_logged.add((coin, ws, "rev"))
                return

        def _book_for(up_side: bool):
            tok = info.up_token_id if up_side else info.down_token_id
            try:
                bk = self.om.get_clob_book(tok) or {}
            except Exception:
                bk = {}
            return tok, bk

        token, book = _book_for(is_up)
        ask = book.get("ask")
        corrected = is_up != late_up
        min_ask_use = (min(float(CFG.late_min_ask), float(CFG.late_dir_min_ask))
                       if corrected else float(CFG.late_min_ask))
        max_ask_use = float(CFG.late_max_ask)
        # If we re-pointed direction but ask is untradeable even with dir floor, fall back
        # to late side (no overblock — still take the original setup when it is in-band).
        if corrected and (
                not ask or not (min_ask_use <= float(ask) <= max_ask_use)):
            if (coin, ws, "dirfb") not in self._nc_logged:
                logger.info(f"[LATE DIR] {coin} corrected {direction} ask out of band "
                            f"({ask}, need {min_ask_use:.2f}-{max_ask_use:.2f}) — "
                            f"fall back to late={'UP' if late_up else 'DOWN'}")
                self._nc_logged.add((coin, ws, "dirfb"))
            is_up = late_up
            direction = "UP" if is_up else "DOWN"
            token, book = _book_for(is_up)
            ask = book.get("ask")
            min_ask_use = float(CFG.late_min_ask)
            dir_note = (dir_note + " fallback_late").strip()
            corrected = False
        hiband = False
        if not ask or not (min_ask_use <= float(ask) <= max_ask_use):
            # v1.60 HIBAND: 80-90c favorite WITH agreeing >=3bps roc — the only measured
            # +EV subset above the core band. Separate engine tag/verdict; min size.
            _hb_ok = (CFG.late_hiband and not self.engine_off.get("hiband")
                      and not corrected and ask is not None
                      and CFG.hiband_min_ask <= float(ask) <= CFG.hiband_max_ask
                      and roc_bps is not None and abs(roc_bps) >= CFG.hiband_roc_bps
                      and ((roc_bps > 0) == is_up))
            if _hb_ok:
                hiband = True
            else:
                # v1.58.1: never silent — was the main "are we missing windows?" confusion
                if (coin, ws, "askband") not in self._nc_logged:
                    after_fb = " after dir-fallback" if "fallback" in dir_note else ""
                    logger.info(
                        f"[LATE SKIP] {coin} {direction} ask_out_of_band{after_fb} "
                        f"ask={ask} need={min_ask_use:.2f}-{max_ask_use:.2f} "
                        f"lead={lead_state} fight={int(fighting)}"
                    )
                    self._nc_logged.add((coin, ws, "askband"))
                return
        # EXECUTION (v1.46 + v1.50): TAKER by default — FOK at ask.
        if CFG.late_taker:
            px_ord = round(float(ask), 2)
            otype = OrderType.FAK if CFG.late_fak else OrderType.FOK
            is_taker = True
        else:
            offset = CFG.maker_offset
            if CFG.late_shade:
                _s = chainlink_ws.get_realized_vol(coin, 180) or binance_ws.get_realized_vol(coin, 180)
                if _s and _s > 0:
                    expo_bps = _s * math.sqrt(max(1.0, t_rem)) * 1e4
                    offset += min(0.03, 0.01 * int(expo_bps / CFG.late_shade_bps))
            px_ord = round(max(0.02, float(ask) - offset), 2)
            otype = OrderType.GTC
            is_taker = False
        if not hiband and self.engine_off.get("late"):   # retired by its own 40-trade verdict (v1.47)
            # Owner must clear engine_off.late in clean_bot_state.json (and ideally reset
            # late recent_ev) — otherwise every late setup is a silent no-op.
            # (hiband has its own engine_off gate at eligibility time.)
            if (coin, ws, "retired") not in self._nc_logged:
                logger.info(f"[LATE SKIP] {coin} late ENGINE RETIRED (self-gov n>=40 EV/$<=-0.03) "
                            f"— owner reset engine_off.late + clear late recent_ev to re-audition")
                self._nc_logged.add((coin, ws, "retired"))
            return
        # hiband: flat exchange-min shares × its OWN verdict multiplier (v1.60.5 — before
        # this, the n>=40 SCALE-UP verdict would have been a silent no-op on hiband).
        # Deliberately NOT Kelly: the hiband edge is thin (+0.02-0.05/$) and its meter is
        # the only sizing authority it has earned. Book-aware trim below still applies.
        if hiband:
            shares = max(CFG.shares, int(CFG.shares * float(self.engine_mult.get("hiband", 1.0))))
        else:
            shares = self._late_size_shares(coin, px_ord, lead_state=lead_state)
        # v1.60.8: risk cap + ruin guard on EVERY taker path (hiband's flat×mult branch
        # had no cap either). Belt-and-suspenders after the 2026-07-23 ruin spiral.
        _max_sh = max(CFG.shares, int((self.bankroll * CFG.max_bet_pct) / px_ord))
        if shares > _max_sh:
            logger.info(f"[RISK CAP] {coin} {direction} x{shares} -> x{_max_sh} "
                        f"({CFG.max_bet_pct*100:.0f}% of ${self.bankroll:.2f})")
            shares = _max_sh
        if CFG.shares * px_ord > self.bankroll * CFG.max_bet_hard_pct:
            if (coin, ws, "ruin") not in self._nc_logged:
                logger.warning(f"[RUIN GUARD] {coin} {direction} STAND DOWN — exchange-min "
                               f"{CFG.shares}sh@{px_ord*100:.0f}c = ${CFG.shares*px_ord:.2f} "
                               f"> {CFG.max_bet_hard_pct*100:.0f}% of ${self.bankroll:.2f}. "
                               f"Bankroll too small to size any bet responsibly.")
                self._nc_logged.add((coin, ws, "ruin"))
            return
        # v1.60.4 BOOK-AWARE FOK SIZING: post-cap-raise (9-10sh) FOKs went 0/2 — top-of-book
        # at the decision moment typically holds ~5-8 shares, and FOK demands the FULL size
        # at <= limit. Take what the book displays (x0.9 safety), floor at exchange-min;
        # if even the min isn't there, skip (an unfillable FOK collects no edge anyway).
        if is_taker:
            try:
                _d = self.om.get_full_depth(token) or {}
                _avail = sum(s for _p, s in _d.get("asks", []) if _p <= px_ord + 1e-9)
                if _avail < CFG.shares:
                    if (coin, ws, "depth") not in self._nc_logged:
                        logger.info(f"[LATE SKIP] {coin} {direction} book too thin for min "
                                    f"({_avail:.0f} < {CFG.shares} @ <= {px_ord*100:.0f}c)")
                        self._nc_logged.add((coin, ws, "depth"))
                    return
                _fit = max(CFG.shares, int(_avail * 0.9))
                if shares > _fit:
                    logger.info(f"[SIZE->BOOK] {coin} {direction} trimmed x{shares} -> x{_fit} "
                                f"(book shows {_avail:.0f} @ <= {px_ord*100:.0f}c)")
                    shares = _fit
            except Exception as e:
                logger.debug(f"depth check failed ({e}) — proceeding at planned size")
        # PER-WINDOW LEG LIMIT (v1.62.0 — replaces the blanket corr-sibling/corr-opposite
        # ban, which allowed exactly ONE coin per window and was blocking about as many
        # bets as it let through). At max_legs=1 this is byte-for-byte the old behaviour.
        # The guards were calibrated on a $25-45 book where 3 legs = 25-45% of bankroll;
        # at $115 the same 3 legs are ~10%, and max_bet_pct + max_open_pct already bound it.
        _legs = self._window_legs(ws)
        # v1.63.1: NEVER a second leg in the SAME direction. v1.62.0 collapsed two
        # different guards into one count, which is wrong: same-direction legs share a
        # fate 80.7% of the time (measured, n=259 windows) so a 2nd is pure LEVERAGE,
        # while an opposite-direction leg DIVERSIFIES (measured WR 75.9%, EV +0.047).
        # Cost of the conflation: 2026-07-27 19:26 BTC DOWN 88c + ETH DOWN 60c in ONE
        # window = a single 2x correlated bet; both reversed, -$7.40 in one print.
        _same = self._same_dir_legs(ws, direction)
        if _same:
            if (coin, ws, "legsame") not in self._nc_logged:
                logger.info(f"[LATE SKIP] {coin} {direction} same-dir leg — {', '.join(_same)} "
                            f"already holds {direction} this window (~81% shared fate = "
                            f"leverage, not diversification)")
                self._nc_logged.add((coin, ws, "legsame"))
            return
        if len(_legs) >= CFG.max_legs_per_window:
            if (coin, ws, "legcap") not in self._nc_logged:
                _held = ", ".join(f"{c} {d}" for c, d in _legs)
                logger.info(f"[LATE SKIP] {coin} {direction} window-leg-cap "
                            f"{len(_legs)}/{CFG.max_legs_per_window} — already holding "
                            f"[{_held}] this window")
                self._nc_logged.add((coin, ws, "legcap"))
            return
        _exp = self._open_exposure()
        if _exp + px_ord * shares > self.bankroll * CFG.max_open_pct:
            if (coin, ws, "expo") not in self._nc_logged:
                logger.info(f"[LATE SKIP] {coin} {direction} max-open-exposure — open ${_exp:.2f} "
                            f"+ ${px_ord*shares:.2f} > {CFG.max_open_pct*100:.0f}% of "
                            f"${self.bankroll:.2f} (${self.bankroll*CFG.max_open_pct:.2f})")
                self._nc_logged.add((coin, ws, "expo"))
            return
        _how = (("TAKER/FAK" if CFG.late_fak else "TAKER/FOK") if is_taker else "maker/GTC")
        _fee = _taker_buy_fee(px_ord, shares) if is_taker else 0.0
        _cm = CFG.late_coin_mult.get(coin.upper(), 1.0)
        _ev = self._late_engine_ev()
        _evs = f"{_ev:+.3f}" if _ev is not None else "n/a"
        _cmp = "CMPD" if self._late_compound_ok() else "MIN"
        _roc = f"{roc_bps:+.1f}" if roc_bps is not None else "n/a"
        # Geometry truth for operator: break-even WR = price; wins needed to cover 1 loss
        _be = px_ord
        _cover = (px_ord / max(1e-6, 1 - px_ord))
        logger.info(f"[LATE ENTER] {coin} {direction} drift={dist*100:+.3f}% ask={float(ask)*100:.0f}c "
                    f"-> {_how} {px_ord*100:.0f}c x{shares} (${px_ord*shares:.2f}"
                    f"{f'+fee${_fee:.2f}' if _fee else ''}, bankroll ${self.bankroll:.0f}, "
                    f"cmult={_cm:.2f}, lead={lead_state}, size={_cmp}, lateEV={_evs}, "
                    f"roc60={_roc}, feed={spot_src}/{strike_src}, "
                    f"BEwr={_be*100:.0f}% need={_cover:.2f}W/L) T={t_rem:.0f}s [AUDITION]"
                    + (" [HIBAND]" if hiband else "")
                    + (f" [{dir_note}]" if dir_note else "")
                    + (" [DRY]" if CFG.dry else ""))
        if CFG.dry:
            # paper: assume immediate fill at the signal price (taker or maker path)
            self.traded.add(key)
            self.positions[f"{coin}:{ws}"] = {"coin": coin, "ws": ws, "dir": direction,
                                              "entry": px_ord, "shares": shares, "token": token,
                                              "status": "filled", "sim": True, "late": True,
                                              "hiband": hiband, "taker": is_taker,
                                              "buy_fee": round(_fee, 4)}
            logger.info(f"[SIM FILL] {coin} {direction} @ {px_ord*100:.0f}c x{shares} (paper, late, {_how})")
            self._save()
            return
        if is_taker:
            # v1.56: FOK at ask; on unfilled/400, optional ONE retry at refreshed ask (no GTC rest).
            # v1.60.7: FAK never retries — it already took what the book had in one shot; a
            # retry after a partial fill is exactly the double-fill hazard from v1.60.2.
            attempts = 1 if CFG.late_fak else (2 if CFG.late_fok_retry else 1)
            filled_ok = False
            for attempt in range(attempts):
                if attempt > 0:
                    time.sleep(max(0.05, CFG.late_fok_retry_sleep))
                    try:
                        book2 = self.om.get_clob_book(token) or {}
                    except Exception:
                        book2 = {}
                    ask2 = book2.get("ask")
                    if not ask2 or not (min_ask_use <= float(ask2) <= max_ask_use):
                        logger.info(f"[LATE MISS] {coin} {direction} retry aborted — "
                                    f"ask out of band ({ask2})")
                        break
                    px_ord = round(float(ask2), 2)
                    shares = self._late_size_shares(coin, px_ord, lead_state=lead_state)
                    if self._open_exposure() + px_ord * shares > self.bankroll * CFG.max_open_pct:
                        logger.info(f"[LATE MISS] {coin} {direction} retry aborted — exposure")
                        break
                    logger.info(f"[LATE FOK RETRY] {coin} {direction} fresh ask "
                                f"{px_ord*100:.0f}c x{shares} (attempt {attempt+1}/{attempts})")
                try:
                    res = self.client.create_and_post_order(
                        OrderArgs(price=px_ord, size=shares, side=BUY, token_id=token),
                        PartialCreateOrderOptions(tick_size="0.01"), otype)
                    oid = (res or {}).get("orderID") or (res or {}).get("orderId")
                    matched = float((res or {}).get("size_matched") or
                                    (res or {}).get("sizeMatched") or 0)
                    if matched <= 0 and oid:
                        # v1.60.2 CRITICAL: matching is async (Jul 24 pipeline, live early)
                        # — a filled FOK can report matched=0 in the immediate response AND
                        # in an instant get_order. Real double-fills observed on-chain
                        # 2026-07-19 00:56 & 01:26 (both "no fill" prints filled ~2s later,
                        # retry doubled the position, ledger tracked neither). POLL before
                        # believing a miss; an accepted FOK is never "missed" instantly.
                        for _poll in range(3):
                            time.sleep(1.2)
                            try:
                                od = self.client.get_order(oid) or {}
                                matched = float(od.get("size_matched") or od.get("sizeMatched") or 0)
                            except Exception:
                                matched = 0.0
                            if matched > 0:
                                break
                    if matched > 0:
                        sh = int(matched)
                        fee = _taker_buy_fee(px_ord, sh)
                        self.traded.add(key)
                        self.positions[f"{coin}:{ws}"] = {
                            "coin": coin, "ws": ws, "dir": direction, "token": token,
                            "entry": px_ord, "shares": sh, "status": "filled",
                            "late": True, "hiband": hiband, "taker": True,
                            "buy_fee": round(fee, 4)}
                        # v1.60.7: FAK may fill PARTIALLY (sh < requested) — that's a success,
                        # a book-limited position at our price, not a miss.
                        partial = sh < shares
                        tag = ("FILLED TAKER PARTIAL" if partial
                               else "FILLED TAKER" if attempt == 0 else "FILLED TAKER RETRY")
                        extra = f" ({sh}/{shares} book-limited)" if partial else ""
                        logger.info(f"[{tag}] LATE {coin} {direction} @ {px_ord*100:.0f}c "
                                    f"x{sh}{extra} fee=${fee:.3f}")
                        tg._send(f"🤖 <b>{tag}</b> LATE {coin} {direction} @ {px_ord*100:.0f}c "
                                 f"x{sh}{extra}", dedup_key=f"fill-late-{coin}-{ws}")
                        filled_ok = True
                        break
                    err_txt = str((res or {}).get("errorMsg") or (res or {}).get("error") or "")
                    _ot = "FAK" if CFG.late_fak else "FOK"
                    logger.info(f"[LATE MISS] {coin} {direction} @ {px_ord*100:.0f}c x{shares} "
                                f"{_ot} no fill attempt {attempt+1}/{attempts} (book empty at price)"
                                + (f" ({err_txt[:80]})" if err_txt else ""))
                except Exception as e:
                    emsg = str(e)
                    fok_unfilled = ("fully filled or killed" in emsg.lower()
                                    or "couldn't be fully filled" in emsg.lower()
                                    or "could not be fully filled" in emsg.lower())
                    if fok_unfilled:
                        # FAK shouldn't raise this (it's FOK's all-or-nothing error) — but keep
                        # the net so a stray kill is logged as a clean $0 miss, never a crash.
                        logger.info(f"[LATE MISS] {coin} {direction} @ {px_ord*100:.0f}c "
                                    f"killed attempt {attempt+1}/{attempts}: {emsg[:120]}")
                    else:
                        logger.warning(f"[LATE ORDER FAIL] {coin} {direction}: {e}")
                        break  # non-FOK error: don't hammer
            if not filled_ok:
                # leave key out of traded so next scan can try if still in band
                pass
        else:
            # maker GTC path: rest and let check_orders track fills (incl. partials)
            try:
                res = self.client.create_and_post_order(
                    OrderArgs(price=px_ord, size=shares, side=BUY, token_id=token),
                    PartialCreateOrderOptions(tick_size="0.01"), OrderType.GTC)
                oid = (res or {}).get("orderID") or (res or {}).get("orderId")
                self.traded.add(key)
                if oid:
                    self.open_orders[oid] = {"coin": coin, "ws": ws, "dir": direction,
                                             "token": token, "price": px_ord,
                                             "shares": shares, "ts": now, "late": True,
                                             "hiband": hiband, "taker": False}
                    logger.info(f"[GTC] resting LATE {coin} {direction} @ {px_ord*100:.0f}c "
                                f"x{shares} oid={str(oid)[:10]}")
                else:
                    logger.warning(f"[LATE ORDER] no oid in result: {res}")
            except Exception as e:
                logger.warning(f"[LATE ORDER FAIL] {coin} {direction}: {e}")
        self._save()

    def _fav_entry(self, coin: str):
        """v1.64.0 FAV ENGINE — buy the favourite at ~9 minutes remaining, hold to
        settlement. The entire strategy: no direction model, no momentum, no hour
        gate. Evidence: 2M-trade census (see Cfg.fav_live comment). One fixed-time
        decision per window inside t_rem [fav_t_min, fav_t_max]; taker FAK at the
        favourite's ask; tagged 'fav' -> own meter, own n>=40 verdict, own
        emergency brake. Min-size x its OWN earned multiplier, under every guard."""
        if not CFG.fav_live or self.engine_off.get("fav"):
            return
        info = get_market_info(coin)
        if not info:
            return
        ws = info.window_start
        key = (coin, ws)
        if key in self.traded or key in self._fav_evaled:
            return
        t_rem = ws + 900 - time.time()
        if not (CFG.fav_t_min <= t_rem <= CFG.fav_t_max):
            return
        self._fav_evaled.add(key)                 # one decision, taken or not — no chase
        if len(self._fav_evaled) > 600:
            self._fav_evaled = set(list(self._fav_evaled)[-300:])
        try:
            up_bk = self.om.get_clob_book(info.up_token_id) or {}
            dn_bk = self.om.get_clob_book(info.down_token_id) or {}
        except Exception as e:
            logger.debug(f"fav book fetch failed {coin}: {e}")
            return
        pick = _pick_favorite(up_bk.get("ask"), dn_bk.get("ask"),
                              CFG.fav_min_ask, CFG.fav_max_ask)
        if not pick:
            if (coin, ws, "favband") not in self._nc_logged:
                logger.info(f"[FAV SKIP] {coin} no favourite in band "
                            f"{CFG.fav_min_ask:.2f}-{CFG.fav_max_ask:.2f} "
                            f"(up={up_bk.get('ask')} down={dn_bk.get('ask')}) T={t_rem:.0f}s")
                self._nc_logged.add((coin, ws, "favband"))
            return
        direction, px_ord = pick
        token = info.up_token_id if direction == "UP" else info.down_token_id
        # correlation: a 2nd SAME-direction leg is leverage, not a 2nd bet (v1.63.1)
        _same = self._same_dir_legs(ws, direction)
        if _same:
            if (coin, ws, "favsame") not in self._nc_logged:
                logger.info(f"[FAV SKIP] {coin} {direction} same-dir leg — {', '.join(_same)} "
                            f"already holds {direction} this window")
                self._nc_logged.add((coin, ws, "favsame"))
            return
        _legs = self._window_legs(ws)
        if len(_legs) >= CFG.max_legs_per_window:
            return
        # sizing: exchange-min x the engine's own EARNED multiplier, bankroll-capped
        shares = max(CFG.shares, int(CFG.shares * float(self.engine_mult.get("fav", 1.0))))
        max_sh = max(CFG.shares, int((self.bankroll * CFG.max_bet_pct) / px_ord))
        shares = min(shares, max_sh)
        if CFG.shares * px_ord > self.bankroll * CFG.max_bet_hard_pct:
            if (coin, ws, "favruin") not in self._nc_logged:
                logger.warning(f"[RUIN GUARD] FAV {coin} STAND DOWN — exchange-min "
                               f"${CFG.shares*px_ord:.2f} > {CFG.max_bet_hard_pct*100:.0f}% "
                               f"of ${self.bankroll:.2f}")
                self._nc_logged.add((coin, ws, "favruin"))
            return
        if self._open_exposure() + px_ord * shares > self.bankroll * CFG.max_open_pct:
            if (coin, ws, "favexpo") not in self._nc_logged:
                logger.info(f"[FAV SKIP] {coin} {direction} max-open-exposure")
                self._nc_logged.add((coin, ws, "favexpo"))
            return
        fee = _taker_buy_fee(px_ord, shares)
        logger.info(f"[FAV ENTER] {coin} {direction} fav@{px_ord*100:.0f}c x{shares} "
                    f"(${px_ord*shares:.2f}+fee${fee:.2f}, bankroll ${self.bankroll:.0f}, "
                    f"BEwr={px_ord*100:.0f}%) T={t_rem:.0f}s [AUDITION]"
                    + (" [DRY]" if CFG.dry else ""))
        if CFG.dry:
            self.traded.add(key)
            self.positions[f"{coin}:{ws}"] = {"coin": coin, "ws": ws, "dir": direction,
                                              "entry": px_ord, "shares": shares, "token": token,
                                              "status": "filled", "sim": True, "fav": True,
                                              "taker": True, "buy_fee": round(fee, 4)}
            self._save()
            return
        try:
            res = self.client.create_and_post_order(
                OrderArgs(price=px_ord, size=shares, side=BUY, token_id=token),
                PartialCreateOrderOptions(tick_size="0.01"), OrderType.FAK)
            oid = (res or {}).get("orderID") or (res or {}).get("orderId")
            matched = float((res or {}).get("size_matched") or
                            (res or {}).get("sizeMatched") or 0)
            if matched <= 0 and oid:
                # v1.60.2 policy: async matching lies for seconds — poll before miss
                for _poll in range(3):
                    time.sleep(1.2)
                    try:
                        od = self.client.get_order(oid) or {}
                        matched = float(od.get("size_matched") or od.get("sizeMatched") or 0)
                    except Exception:
                        matched = 0.0
                    if matched > 0:
                        break
            if matched > 0:
                sh = int(matched)
                fee = _taker_buy_fee(px_ord, sh)
                self.traded.add(key)
                self.positions[f"{coin}:{ws}"] = {
                    "coin": coin, "ws": ws, "dir": direction, "token": token,
                    "entry": px_ord, "shares": sh, "status": "filled",
                    "fav": True, "taker": True, "buy_fee": round(fee, 4)}
                partial = sh < shares
                tag = "FILLED TAKER PARTIAL" if partial else "FILLED TAKER"
                logger.info(f"[{tag}] FAV {coin} {direction} @ {px_ord*100:.0f}c x{sh} "
                            f"fee=${fee:.3f}")
                tg._send(f"🤖 <b>{tag}</b> FAV {coin} {direction} @ {px_ord*100:.0f}c x{sh}",
                         dedup_key=f"fill-fav-{coin}-{ws}")
            else:
                err_txt = str((res or {}).get("errorMsg") or (res or {}).get("error") or "")
                logger.info(f"[FAV MISS] {coin} {direction} @ {px_ord*100:.0f}c x{shares} "
                            f"FAK no fill" + (f" ({err_txt[:80]})" if err_txt else ""))
        except Exception as e:
            logger.warning(f"[FAV ORDER FAIL] {coin} {direction}: {e}")
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
        # fair prob the lead holds, with the verified momentum-persistence term (v1.43): project
        # the recent 5-min drift rate onto the lead direction, weighted kappa=0.25 (tested best;
        # full extrapolation is worse — momentum only partially persists).
        mu = 0.0
        if CFG.vol_div_kappa > 0:
            try:
                mu = _roc(binance_ws.get_tick_history(coin, 340), 300) / 300.0   # per-second drift rate
            except Exception:
                mu = 0.0
        mu_lead = mu if lead_up else -mu
        num = abs(dist) + CFG.vol_div_kappa * mu_lead * t_rem
        p_lead = 0.5 * (1.0 + math.erf(num / (sigma * math.sqrt(t_rem)) / math.sqrt(2)))
        up_b = down_b = {}
        try: up_b = self.om.get_clob_book(info.up_token_id) or {}
        except Exception: pass
        try: down_b = self.om.get_clob_book(info.down_token_id) or {}
        except Exception: pass
        lead_ask = (up_b if lead_up else down_b).get("ask")
        dog_ask = (down_b if lead_up else up_b).get("ask")
        # BOOK-SANITY (v1.43.2): a wide/stale book fakes huge "edges" (live: overnight asks like
        # 80c/40c summing 1.20 produced phantom +26% divergences that lost). Real two-sided books
        # sum to ~1.00-1.04; require both asks present and sum <= 1.06 or the quote isn't real.
        if not (lead_ask and dog_ask) or (float(lead_ask) + float(dog_ask)) > 1.06:
            return
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
        if self.engine_off.get("voldiv"):             # retired by its own verdict (v1.47)
            return
        shares = max(CFG.shares, int(round(CFG.shares * self.engine_mult.get("voldiv", 1.0))))
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
    def _record_fill(self, o, matched, *, race=False, partial=False):
        """Track a (possibly partial) fill, preserving engine tags + taker fee metadata."""
        sh = int(matched)
        if sh <= 0:
            return
        is_taker = bool(o.get("taker", False))
        fee = _taker_buy_fee(o["price"], sh) if is_taker else 0.0
        self.positions[f"{o['coin']}:{o['ws']}"] = {
            "coin": o["coin"], "ws": o["ws"], "dir": o["dir"], "token": o["token"],
            "entry": o["price"], "shares": sh, "status": "filled",
            # v1.45.1: carry the ENGINE TAG through the fill (was dropped → every live
            # late/voldiv fill got scored as 'early' on the per-engine boards)
            "late": o.get("late", False), "voldiv": o.get("voldiv", False),
            "hiband": o.get("hiband", False),
            "taker": is_taker, "buy_fee": round(fee, 4)}
        tag = "FILLED-RACE" if race else ("FILLED-PARTIAL" if partial else "FILLED")
        logger.info(f"[{tag}] {o['coin']} {o['dir']} @ {o['price']*100:.0f}c x{sh}"
                    + (f" fee=${fee:.3f}" if fee else ""))
        tg._send(f"{'⚠️ ' if race else ''}🤖 <b>{tag}</b> {o['coin']} {o['dir']} @ "
                 f"{o['price']*100:.0f}c x{sh}", dedup_key=f"fill-{o['coin']}-{o['ws']}-{sh}")

    def check_orders(self):
        now = time.time()
        for oid, o in list(self.open_orders.items()):
            try:
                od = self.client.get_order(oid) or {}
            except Exception:
                continue
            matched = float(od.get("size_matched") or od.get("sizeMatched") or 0)
            status = str(od.get("status", "")).upper()
            intended = float(o.get("shares") or 0)
            fully_done = status in ("MATCHED", "FILLED", "CLOSED") or (
                intended > 0 and matched + 1e-9 >= intended)
            # v1.50: keep watching PARTIAL fills — popping early left residual size untracked
            # (phantom-fill class). Update the position as matched grows; only drop the oid
            # when fully filled/cancelled/expired.
            if matched > 0 and fully_done:
                self._record_fill(o, matched, race=False, partial=False)
                self.open_orders.pop(oid, None); self._save()
                continue
            if matched > 0 and not fully_done:
                prev = float(o.get("_last_matched") or 0)
                if matched > prev + 1e-9:
                    o["_last_matched"] = matched
                    self._record_fill(o, matched, race=False, partial=True)
                    self._save()
                # still open — only cancel residual when aged out / near close
                if not (status in ("CANCELED", "EXPIRED") or now - o["ts"] > CFG.gtc_max_age
                        or o["ws"] + 900 - now < 90):
                    continue
            if status in ("CANCELED", "EXPIRED") or now - o["ts"] > CFG.gtc_max_age \
                    or o["ws"] + 900 - now < 90:
                try:
                    self.client.cancel(oid)
                except Exception:
                    pass
                # A cancel can LOSE a race with a fill: the order fills on-chain right as we
                # cancel, leaving a phantom position that silently drains the wallet when it
                # loses (e.g. 2026-06-28 06:36 "canceled" SOL DOWN @69c actually filled → −$3.45
                # untracked). ALWAYS re-verify before assuming unfilled, and track any real fill.
                # v1.61.1: POLL the re-verify (3x1.2s), same policy as the v1.60.2 taker
                # fix — async matching returns matched=0 for seconds after a real fill.
                # First maker casualty: 2026-07-24 01:58 BTC DOWN 65c x5 filled on-chain,
                # instant read said 0 → "[CANCEL] unfilled" → untracked winning position.
                matched_after = 0.0
                for _poll in range(3):
                    time.sleep(1.2)
                    try:
                        od2 = self.client.get_order(oid) or {}
                        matched_after = float(od2.get("size_matched") or od2.get("sizeMatched") or 0)
                    except Exception:
                        matched_after = 0.0
                    if matched_after > 0:
                        break
                if matched_after > 0:
                    self._record_fill(o, matched_after, race=True, partial=False)
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
                _oid = (res or {}).get("orderID") or (res or {}).get("orderId")
                if matched <= 0 and _oid:
                    # v1.60.3: async matching (same class as the v1.60.2 buy-side double-fill)
                    # — a filled SELL can read matched=0 instantly; believing it would leave the
                    # ledger holding shares the wallet already sold. Poll before trusting a miss.
                    for _poll in range(3):
                        time.sleep(1.2)
                        try:
                            od = self.client.get_order(_oid) or {}
                            matched = float(od.get("size_matched") or od.get("sizeMatched") or 0)
                        except Exception:
                            matched = 0.0
                        if matched > 0:
                            break
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
            # v1.50: debit taker buy fee so ledger/EV match capital at risk (makers fee=0).
            buy_fee = float(p.get("buy_fee") or 0.0)
            if buy_fee <= 0 and p.get("taker"):
                buy_fee = _taker_buy_fee(entry, sh)
            gross = (1 - entry) * sh if won else -entry * sh
            pnl = gross - buy_fee
            if pnl >= 0:
                self.wins += pnl
            else:
                self.losses += -pnl
            p["status"] = "resolved"; p["pnl"] = round(pnl, 2); p["buy_fee"] = round(buy_fee, 4)
            prev_br = self.bankroll
            self.bankroll += pnl                       # ← compound
            # Recovery milestone (v1.51): one-shot notice when we cross the target (default $100).
            if (CFG.target_bankroll > 0 and prev_br < CFG.target_bankroll
                    <= self.bankroll):
                logger.info(f"[TARGET HIT] bankroll ${self.bankroll:.2f} >= "
                            f"target ${CFG.target_bankroll:.0f}")
                tg._send(f"🎯 <b>TARGET HIT</b> 💰 ${self.bankroll:.2f} "
                         f"(goal ${CFG.target_bankroll:.0f})",
                         dedup_key=f"target-{int(CFG.target_bankroll)}")
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
            # LIVE ACCURACY + EV METER (v1.44: PER-ENGINE): WR alone lies at favorite prices;
            # EV/$ staked is the real compounding rate. Each engine gets its own scoreboard so a
            # good engine can't be hidden by a bad one — and each faces its own pre-registered
            # verdict at 40 trades: EV>=+0.03 scale up | -0.03..+0.03 keep min-size | <=-0.03 OFF.
            _tag = ("fav" if p.get("fav") else
                    "hiband" if p.get("hiband") else
                    "late" if p.get("late") else ("voldiv" if p.get("voldiv") else "early"))
            # stake = entry notional + buy fee (true dollars at risk for EV/$)
            _stake = entry * p.get("shares", CFG.shares) + float(p.get("buy_fee") or 0.0)
            _rec = (1 if won else 0, pnl, _stake, _tag)
            self.recent_ev.append(_rec)
            self.recent_ev = self.recent_ev[-150:]
            self.day_results.append(_rec)
            eng = [x for x in self.recent_ev if len(x) > 3 and x[3] == _tag]
            if len(eng) >= 5:
                _w = sum(x[0] for x in eng); _n = len(eng)
                _net = sum(x[1] for x in eng); _stk = sum(x[2] for x in eng)
                _ev = _net / _stk if _stk else 0.0
                verdict = ("SCALE-UP✓" if _ev >= 0.03 else "OFF✗" if _ev <= -0.03 else "min-size")
                logger.info(f"[TRACK:{_tag}] last {_n}: {_w}/{_n}={100*_w/_n:.0f}%WR | net {_net:+.2f} | "
                            f"EV/$ {_ev:+.3f} | at n>=40 -> {verdict}")
                # SELF-GOVERNANCE (v1.47): at n>=40 the engine EXECUTES its own verdict — the
                # pre-registered rules stop being advisory. OFF is permanent until owner reset
                # ("engine_off" in state); scaling steps 1x->2x->3x only while EV holds >= +0.03.
                # v1.60.8 EMERGENCY BRAKE — the n>=40 verdict is BLIND right after a SCALE-UP,
                # because scaling clears recent_ev. On 2026-07-23 the late engine bled 13
                # trades at EV/$ -0.58 (-$40, bankroll $58->$21) while its own circuit breaker
                # could not fire: the sample had been reset to zero by the ×2 verdict it had
                # just earned. Catastrophic EV must retire an engine at a far smaller n.
                if (_n >= CFG.emergency_n and _ev <= CFG.emergency_ev
                        and not self.engine_off.get(_tag)):
                    self.engine_off[_tag] = True
                    self.engine_mult[_tag] = 1.0          # also undo any scale-up
                    logger.warning(f"[EMERGENCY:{_tag}] n={_n} EV/$ {_ev:+.3f} <= "
                                   f"{CFG.emergency_ev} — ENGINE RETIRED EARLY (catastrophic "
                                   f"EV; the n>=40 rule was blind after a scale-up reset)")
                    tg._send(f"🚨 <b>EMERGENCY STOP: {_tag}</b> — n={_n}, EV/$ {_ev:+.3f}. "
                             f"Engine retired early and de-scaled to x1. Owner reset required.")
                    self._save()
                if _n >= 40 and not self.engine_off.get(_tag):
                    if _ev <= -0.03:
                        self.engine_off[_tag] = True
                        logger.info(f"[VERDICT:{_tag}] n={_n} EV/$ {_ev:+.3f} <= -0.03 — ENGINE RETIRED "
                                    f"(pre-registered rule; owner reset required)")
                        tg._send(f"⚖️ <b>VERDICT: {_tag} RETIRED</b> — n={_n}, EV/$ {_ev:+.3f}. "
                                 f"The pre-registered rule fired; engine off until owner reset.")
                    elif _ev >= 0.03 and self.engine_mult.get(_tag, 1.0) < 3.0:
                        self.engine_mult[_tag] = self.engine_mult.get(_tag, 1.0) + 1.0
                        logger.info(f"[VERDICT:{_tag}] n={_n} EV/$ {_ev:+.3f} >= +0.03 — SCALING to "
                                    f"x{self.engine_mult[_tag]:.0f} (pre-registered rule)")
                        tg._send(f"⚖️ <b>VERDICT: {_tag} SCALES</b> to x{self.engine_mult[_tag]:.0f} — "
                                 f"n={_n}, EV/$ {_ev:+.3f} earned it.")
                        # restart this engine's measurement window so the next step is judged
                        # on the new size, not stale min-size trades
                        self.recent_ev = [x for x in self.recent_ev if len(x) > 3 and x[3] != _tag]
                    self._save()
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
        _lcm = ",".join(f"{k}={v:g}" for k, v in sorted(CFG.late_coin_mult.items())) or "flat"
        _late = (f"late={'ON' if CFG.late_live else 'off'}[{','.join(CFG.late_coins)}] "
                 f"{('FAK' if CFG.late_fak else 'FOK') if CFG.late_taker else 'GTC'} cmult={_lcm} "
                 f"earlyReq={'on' if CFG.late_require_early else 'off'} "
                 f"grow×{CFG.late_grow_mult:g} cmpEV>{CFG.compound_min_ev:g}@n{CFG.compound_min_ev_n} "
                 f"dirVote={'on' if CFG.late_dir_vote else 'off'}/revAsDir={'on' if CFG.late_rev_as_dir else 'off'}")
        logger.info(f"=== CleanBot v{VERSION} start | {'DRY' if CFG.dry else 'LIVE'} | "
                    f"{'/'.join(CFG.coins)} | drift>={CFG.drift_bps}bps T>={CFG.min_t}s "
                    f"ask {CFG.min_ask}-{CFG.max_ask} | {_cf} | breaker {CFG.loss_breaker}L/"
                    f"{CFG.breaker_cooldown // 60}m | {_sz} | {_late} | "
                    f"research={'on' if CFG.research else 'off'} "
                    f"| model={('gate@'+str(CFG.model_min_prob)) if (self.model and CFG.model_gate) else ('shadow' if self.model else 'off')} "
                    f"| bankroll ${self.bankroll:.2f} target ${CFG.target_bankroll:.0f} "
                    f"stop ${self._stop_amount():.2f} ===")
        tg._send(f"🤖 <b>CleanBot v{VERSION}</b> started {'LIVE' if not CFG.dry else 'DRY'} | "
                 f"late {('FAK' if CFG.late_fak else 'FOK') if CFG.late_taker else 'GTC'} · join-quality · {_sz} · "
                 f"💰 ${self.bankroll:.2f}→${CFG.target_bankroll:.0f}")
        binance_ws.start()
        try:
            chainlink_ws.start()                 # settlement-feed strike+spot (Polymarket = Chainlink)
            _clob_route = "proxied" if os.getenv("HTTPS_PROXY") else "direct (native IP)"
            logger.info("[CHAINLINK] RTDS started — strike/spot settlement family "
                        f"(RTDS is Polymarket WS; CLOB {_clob_route})")
        except Exception as e:
            logger.warning(f"[CHAINLINK] RTDS start failed ({e})")
        try:
            # v1.57: densify CL path for roc60 — Polygon aggregator polls (NOT via Tor).
            # Does not replace Tor for order routing; only fills sparse RTDS tick history.
            import chainlink_onchain as _cl_oc
            _cl_oc.start()
            _clob_route = "proxied" if os.getenv("HTTPS_PROXY") else "direct (native IP)"
            logger.info("[CHAINLINK-ONCHAIN] densify poller started (1s RPC, no proxy) "
                        f"— improves late reverse-underway; CLOB {_clob_route}")
        except Exception as e:
            logger.warning(f"[CHAINLINK-ONCHAIN] start failed ({e}) — roc may stay sparse")
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
                if CFG.kill_floor > 0 and self.bankroll <= CFG.kill_floor and not self.killed:
                    # HARD kill-switch (v1.39; LATCHED v1.43.1 — the v1.39 check silently re-armed
                    # when the ledger bounced back above the floor, letting trading resume after a
                    # fire; observed live 2026-07-08 21:39→21:48). Once fired it now stays fired
                    # (persisted in state) until the owner resets: set "killed": false in
                    # clean_bot_state.json (or change CLEAN_KILL_FLOOR) and restart.
                    self.killed = True
                    self._save()
                if self.killed:
                    if n % 40 == 1:
                        logger.info(f"[KILL-SWITCH] LATCHED (fired at <= ${CFG.kill_floor:.2f}; "
                                    f"bankroll now ${self.bankroll:.2f}) — all trading stopped "
                                    f"(owner reset required)")
                    if not self._stop_notified:
                        tg._send(f"🛑 <b>KILL-SWITCH HIT</b> — bankroll ${self.bankroll:.2f} reached the "
                                 f"${CFG.kill_floor:.0f} floor. All trading stopped. The test is over; "
                                 f"owner reset required to resume.")
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
                    # v1.64.0 FAV — the census rebuild: favourite @ ~9min, hold.
                    # Shares the same daily-stop / breaker / kill-floor guards.
                    if CFG.fav_live:
                        for c in CFG.late_coins:
                            self._fav_entry(c)
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
                    self._research_scan(c, "mid")    # v1.49: the never-measured 3.5-9min zone
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
                try:  # v1.57: CL path density (RTDS+on-chain merge) — target >>8 ticks/120s
                    logger.info(f"[CL-TICKS] {chainlink_ws.tick_density_report(seconds=120)}")
                except Exception:
                    pass
                self._sync_bankroll()       # keep bankroll honest vs the chain (~every 40 scans)
            time.sleep(5)


if __name__ == "__main__":
    CleanBot().run()
