# CleanBot Changelog

Every change to CleanBot gets: (1) a `VERSION` bump in `clean_bot.py`, (2) an
entry here, (3) a git tag `cleanbot-vX.Y.Z`, (4) a push to GitHub. The running
version is logged on startup and shown in Telegram + the dashboard, so you always
know exactly what's live. Roll back with `git checkout cleanbot-vX.Y.Z`.

Versioning: **MAJOR.MINOR.PATCH** — MAJOR = strategy change, MINOR = new
feature/knob, PATCH = fix/tuning.

---

## v1.8.1 — 2026-06-22 — drift 12→10 (Chainlink feed runs smaller drifts than Binance)
**Tag:** `cleanbot-v1.8.1` · **Status:** ✅ live

After the v1.8.0 Chainlink switch, the bot went quiet — Chainlink is a smoother feed,
so its drifts run smaller than Binance, and almost nothing cleared the 12bps bar
(observed drifts 3–12bps in a calm window; one 12.7bps blocked by ETH confirm).
The 12bps bar was tuned on *Binance* data and is too high for the (correct) Chainlink
feed. Lowered `CLEAN_DRIFT_BPS` 12→10 to restore volume — now safe because the feed
is correct (smaller drift = real signal, not the old cross-feed noise). Note: all
backtest thresholds are Binance-derived; the research logger is now capturing
Chainlink-era drifts+outcomes to re-tune properly. The one trade before the fix
(10:33, SOL UP +19bps Binance → settled DOWN, −$2.85) was the cross-feed flip itself.

## v1.8.0 — 2026-06-22 — ROOT-CAUSE FIX: strike/spot on Chainlink (settlement feed), not Binance
**Tag:** `cleanbot-v1.8.0` · **Status:** ✅ live · **the real bug**

CleanBot was computing drift entirely on **Binance** (strike = binance_kline_open,
spot = binance_ws) — but **Polymarket settles on Chainlink.** Measured live
cross-feed basis: **BTC +9.0 / ETH +9.9 / SOL +11.3 bps** — i.e. ~10bps, the SAME
size as the 12bps signal. So a "+12bps up" on Binance could be flat-or-DOWN on the
Chainlink feed that actually pays out → near-the-money bets (where the bot lives)
get their direction flipped. This is the documented audit-C1 (Jun 10) failure, and
it's why the rebuild stopped working while the old (Chainlink-aligned) bot won.
Owner called this out repeatedly ("something wrong in the calculations / the
threshold / we won more before") — and was right; I was wrong to blame the regime.

- **Start `chainlink_ws`** on boot (was never started → `get_strike`/`get_market_info`
  always fell back to Binance: 724/724 reads today).
- **Use the Chainlink strike + spot** that `get_market_info` already computes
  (`info.threshold_price` + `info.current_crypto_price`) instead of overriding spot
  with `binance_ws`. Fixed in scan(), `_research_scan`, `_market_confirms`.
- Binance remains the graceful fallback if Chainlink drops. No other logic changed.
- Expected: near-money bets stop getting flipped by the basis → the real edge.

## v1.7.1 — 2026-06-22 — Restore volume (drift 20→12); close-prob engine tested & rejected
**Tag:** `cleanbot-v1.7.1` · **Status:** ✅ live

Owner: don't wait for rare bets — bet a lot on a real close-vs-threshold prediction.
Built + backtested the close-probability engine (`close_prob_test.py`):
`P(close>strike)=Φ(dist/(σ·√min_left))`, enter when confident.

- It gives the volume (43k+ trades) and uses time-left — but **over-predicts**
  (~80% claimed vs ~70% actual; Gaussian misses fat-tailed reversals) and is
  **slightly worse than the plain drift rule** (75% vs 78%). The drift the bot
  already uses *is* the close-vs-threshold prediction; the Φ-formula just adds
  lower-quality volume. **Rejected** as redundant over-refinement.
- **`CLEAN_DRIFT_BPS` 20 → 12** — restore volume (bet a lot, ~80% directional),
  the drift IS the prediction. drift=20 was too restrictive (rare bets).
- Honest: directional edge caps ~70-78% (reversals random); +EV at volume; the
  real constraint is variance on a tiny account ($11.60). Tiny bets + $6 stop.

## v1.7.0 — 2026-06-22 — Clearer-signal entry (drift 10→20bps); strip the noise
**Tag:** `cleanbot-v1.7.0` · **Status:** ✅ live

Back to basics, owner-directed: the hair-trigger 10bps entry was the problem.
Tested on 46k windows (`timing_test.py`): entering on a *clearer* drift sharply
raises win rate — **10bps 78% → 20bps 85% → 25bps 87%** — and cuts the 3-loss
wipeout streaks ~3–4×. The momentum "confirmation" overlay added only **+0.5pt**
(noise), confirming we over-refined and buried the signal.

- **`CLEAN_DRIFT_BPS` 10 → 20.** Enter only on a clear, high-conviction drift.
  Higher WR = lower variance = a small account survives.
- Bonus: drift≥20 *and still cheap* (ask ≤62¢) self-selects the inefficient/overnight
  windows where the market is lagging — the regime the edge actually works in.
- Simpler, not more complex — one clear trigger. Maker, ask-cap, $6 stop unchanged;
  ML model stays benched (shadow) since it added noise live, not signal.
- Note: backtest WRs are binance-directional; live runs lower, but the *relative*
  lift (fewer losses on bigger drifts) transfers.

## v1.6.1 — 2026-06-21 — Entry-timed model retrain + gate→shadow (DRY validation)
**Tag:** `cleanbot-v1.6.1` · **Status:** ✅ live (DRY shadow)

The v1.6.0 DRY run caught the model **miscalibrated live**: it stamped ~0.84 on
every window but delivered 44% (4W/5L) and hit the sim stop. Root cause: trained at
a fixed **minute 5**, but the bot enters at **minute 2–3** → out-of-distribution →
saturated. The DRY shadow did its job — $0 real lost.

- **Retrained on the bot's REAL entry timing** (`build_v3.py`): features at the FIRST
  minute (2–5) the drift crosses 10bps. Entry-minute dist {2:14.8k, 3:5.5k, 4:3.9k,
  5:3.2k} — matches live. Now **calibrated** (0.8→83%, 0.9→91%) but honestly
  **modest discrimination** (AUC 0.57, prob std 0.05) — 2 minutes of data is thin
  signal. Redeployed `drift_model_band.joblib`.
- **Gate → shadow** (`CLEAN_MODEL_GATE=off`): logs `model_prob` vs outcomes without
  gating/stopping, so we validate live calibration cheaply before it touches money.
- Code unchanged except VERSION; model artifact + env only. Banner `model=shadow`.

## v1.6.0 — 2026-06-21 — ML model gate (calibrated P(drift wins)), DRY shadow
**Tag:** `cleanbot-v1.6.0` · **Status:** ✅ live (DRY validation)

Adds a calibrated gradient-boosting model that scores each candidate's probability
of the early-drift winning, trained offline on **46k windows from 1m klines** (see
`_ml_train/`). Honest scope: within the tradeable band (drift≥10bps) the model
lifts WR ~84.8%→90.8% on the top ~47% and flags the weak ~75% windows (modest but
real, calibrated 0.8→86% / 0.9→93%, walk-forward OOS).

- **Shared feature module** `model_features.py` used by BOTH training and the bot →
  parity guaranteed (this fixes a parity break where the bot computed `sigma`
  differently than training).
- `_model_prob(coin, ws)`: fetches binance 1m klines, builds the 8 features, scores
  `drift_model_band.joblib` (sklearn 1.8, isotonic-calibrated). Cached per window.
- Logs `model_prob` on every ENTER + in the research CSV (new column) for live
  calibration validation. **Gate** (`CLEAN_MODEL_GATE`) blocks entries below
  `CLEAN_MODEL_MIN_PROB` (0.80) — running DRY first to validate before live.
- New env: `CLEAN_MODEL_PATH, CLEAN_MODEL_GATE, CLEAN_MODEL_MIN_PROB`. Model scoring
  is wrapped in try/except — never breaks trading. Banner shows `model=gate@0.8`.
- Real-data backtest (419 Polymarket trades): filtering by model prob turned the old
  bot's −$97 into +$136 OOS — the market prices the favored side ~59¢ regardless of
  the model's confidence, so high-prob windows carry real +EV.

## v1.5.3 — 2026-06-20 — Max-ask 0.62 (cut thin-margin 63–66¢) + fav_ask logging fix
**Tag:** `cleanbot-v1.5.3` · **Status:** ✅ live

Owner flagged live losses entering at 60¢+ that immediately reversed. Confirmed:
a high ask means the move is already priced — you buy near exhaustion, and the
breakeven (= entry price) eats the edge.

- **`CLEAN_MAX_ASK` 0.66 → 0.62.** WR-by-entry (n=89): 56–59¢ **77%** / 60–62¢
  **68%** (sweet spot, comfortable margin) vs 63–66¢ **69%** with a **66%
  breakeven** = razor-thin (+3) → first to flip negative in chop. Cut it.
  (≤55¢ is also negative — 44% — but n=18; holding `MIN_ASK` for more data, not
  over-narrowing.)
- **Fix `fav_ask`/`up_ask`/`down_ask` logging:** stored `int(0.64)=0` (truncated
  the fraction) → research ask column was useless. Now `int(round(ask*100))` = cents.
- Confirmation/sizing/breaker/research logic unchanged.

## v1.5.2 — 2026-06-20 — Fix: research CSV header + ENTER mislabeling
**Tag:** `cleanbot-v1.5.2` · **Status:** ✅ live (DRY)

Two research-logger bugs found while reviewing the weekend DRY run:
- **Missing header:** the CSV was pre-created empty, so `new = not exists` was
  False → header never written → the dashboard `DictReader` mis-read every row
  (Research tab stayed blank). Fix: `new = not exists OR getsize == 0`; existing
  CSV back-filled with the header.
- **ENTER mislabeled as SKIP:** `_research_scan` captures a window the first time
  drift ≥ 3bps, which can predate the actual entry → traded windows were logged
  `SKIP` (6 ENTER vs 17 real sim trades). Fix: re-label `decision=ENTER` at
  resolve time from the final `traded` state.
- `drift_correct`, features, and outcomes were always correct — only the header
  and the decision label were wrong.

## v1.5.1 — 2026-06-20 — Fix: research writer crashed (missing `import csv`)
**Tag:** `cleanbot-v1.5.1` · **Status:** ✅ live (DRY)

- **Bug:** `_research_resolve` used `csv.DictWriter` but `csv` was never imported,
  so **every** research write failed silently (`name 'csv' is not defined`) since
  v1.4 → `clean_bot_research.csv` stayed empty → the dashboard 🔬 Research tab had
  no data to show. Trading/sim were unaffected (research is isolated).
- **Fix:** added `import csv`. The CSV now writes one row per resolved window;
  Research tab populates within ~16 min (first window resolution after restart).

## v1.5.0 — 2026-06-20 — Full DRY simulation (paper trading) + dashboard badge
**Tag:** `cleanbot-v1.5.0` · **Status:** ✅ live (DRY)

Run the weekend **risk-free**: DRY mode is now a full paper-trade simulation, not
just a no-op log. Reset to a real-balance seed of **$30** after a choppy-regime
drawdown ($48→~$30).

- **DRY = full lifecycle sim:** `[ENTER]` → `[SIM FILL]` (assume the maker fills)
  → gamma resolve → simulated P&L/bankroll, exactly like live but **no real
  orders**. Positions tagged `sim:true`; `[WIN]/[LOSS]` get a `[SIM]` suffix /
  🧪 Telegram prefix. Gathers weekend data with zero risk.
- **State exposes `mode` (DRY/LIVE) + `version` + `bankroll`** → dashboard shows a
  prominent **🧪 DRY / SIMULATION** banner + sim bankroll on the CleanBot tab
  (`_patch_dash_drysim.py`).
- Reset to **$30** (bot-tracked bankroll had drifted ~$6 above the real balance;
  now sized to reality). `CLEAN_DRY=true`, `CLEAN_START_BANKROLL=30`.
- Trading/sizing/confirmation/research logic unchanged — flip `CLEAN_DRY=false`
  to go live again.

## v1.4.0 — 2026-06-20 — Research data logger + dashboard
**Tag:** `cleanbot-v1.4.0` · **Status:** ✅ live

Capture **everything** for future edge-mining — every real-move window, traded
*or* skipped, with full features + true outcome. Read-only, fully isolated from
the trade path (its own try/except — can never place an order or break trading).

- **`clean_bot_research.csv`** — one row per window with `|drift| >=
  CLEAN_RESEARCH_MIN_BPS` (3): `ts, window_start, coin, dir, drift_pct,
  roc60_bps, roc300_bps, sigma, fav_ask, up_ask, down_ask, btc_drift_pct,
  sol_drift_pct, confirmed, decision (ENTER/SKIP), reason (weak_drift/no_confirm/
  ask_out_of_zone/exposure), t_left, winner, drift_correct`.
- **Captures the windows we SKIP** (with the reason) + the outcome → tells us if a
  gate is leaving money on the table (`drift_correct` on skipped windows).
- Resolved via gamma (Chainlink). New helpers: `_roc`, `_coin_drift`,
  `_research_scan`, `_research_resolve`. Env: `CLEAN_RESEARCH` (on),
  `CLEAN_RESEARCH_MIN_BPS` (3).
- **Dashboard 🔬 Research tab** (`_patch_dash_research.py`): summary (drift-correct
  traded vs skipped), skip-reason "are we over-filtering?" table, recent windows
  with all features + outcome, and the live log. Endpoint `/api/v3/research`.
- Trading logic / sizing / confirmation unchanged.

## v1.3.0 — 2026-06-19 — Cross-coin confirmation for ETH (the follower coin)
**Tag:** `cleanbot-v1.3.0` · **Status:** ✅ live

ETH is a high-beta *follower* of the crypto market — its **solo** drifts are noise
that reverts. The data (clean_bot.log, n=23 ETH): ETH when SOL agrees = **64%**,
ETH solo (SOL flat) = **22%**, ETH vs SOL diverging = **0%**.

- **ETH (and any `CLEAN_CONFIRM_COINS`) only trades when the broader market is
  drifting the same way.** Soft confirmation: each market proxy
  (`CLEAN_CONFIRM_MARKET=BTC,SOL`) that leans the same direction ≥
  `CLEAN_CONFIRM_BPS` (3) votes +1, opposing votes −1 → trade only if net > 0.
  `get_market_info` is used to read BTC/SOL drift (we don't trade BTC).
- Handles divergence correctly: ETH-solo and ETH-vs-market → `[NO CONFIRM]` skip
  (throttled once per window, doesn't lock the window so it can fire if the
  market aligns later). Fail-open if no proxy data (transient).
- **SOL unchanged** (it's the leader/proxy, not confirmed). Compounding (v1.1),
  quality (v1.2) unchanged. New env: `CLEAN_CONFIRM_COINS, CLEAN_CONFIRM_MARKET,
  CLEAN_CONFIRM_BPS`. Banner shows the confirm rule.
- Goal: turn ETH from a ~48% drag into a ~64% contributor by only taking
  market-confirmed ETH (not blocking blindly, not inverting noise). Deploy +
  measure; revisit if confirmed-ETH holds ≥60% over more trades.

## v1.2.0 — 2026-06-19 — Quality tightening + whipsaw breaker
**Tag:** `cleanbot-v1.2.0` · **Status:** ✅ live

Shipped after validating a 4-loss streak — every loss was a *marginal* signal in
a whipsawing (chop) regime. All four would have been skipped by these:

- **Drift floor 7 → 10bps** (`CLEAN_DRIFT_BPS=10`). The 7–10bps band wins only
  54% (coin-flips); ≥10bps wins ~74%. Skipped 3 of the 4 losses.
- **Min-ask 45¢ → 50¢** (`CLEAN_MIN_ASK=0.50`). Don't bet a side the market
  prices below 50¢ (market disagrees with our drift, and it was right). Skipped
  the other 2 losses (SOL UP @44¢, SOL DOWN @46¢).
- **Consecutive-loss breaker** (new): after `CLEAN_LOSS_BREAKER` (3) losses in a
  row, pause `CLEAN_BREAKER_COOLDOWN` (1800s/30min) — protects peak gains during
  choppy regimes the net-based daily stop misses. Counter persisted (restart-safe),
  resets on a win; Telegram 🧊 alert on trip.
- Banner now shows ask-range + breaker config. Compounding (v1.1) unchanged.
- New env: `CLEAN_LOSS_BREAKER, CLEAN_BREAKER_COOLDOWN`; changed `CLEAN_DRIFT_BPS`,
  `CLEAN_MIN_ASK`.

## v1.1.0 — 2026-06-19 — Compounding (bankroll-scaled sizing)
**Tag:** `cleanbot-v1.1.0` · **Status:** ✅ live

- **Bet size now scales with the bankroll** (was fixed 5 shares). Each bet =
  `CLEAN_KELLY_FRAC` of the live bankroll → the account compounds as it wins.
- **Half-Kelly default (6%).** Derived from 51 live trades: 65% WR, avg win
  +$1.95 / avg loss −$2.70, b=0.72 → full Kelly 16.4%; we run 6% (conservative,
  robust if true WR < 65%).
- **Bankroll tracked + persisted** in `clean_bot_state.json`, `+= pnl` each
  resolution. Seeded via `CLEAN_START_BANKROLL` (set to real balance: $48.60).
- **Risk caps:** per-bet ≤ `CLEAN_MAX_BET_PCT` (10%); total open exposure ≤
  `CLEAN_MAX_OPEN_PCT` (25%, limits correlated ETH+SOL stacking); daily stop now
  `CLEAN_STOP_PCT` (15% of bankroll, floor $6) — scales with the account.
- **`VERSION` constant** added — logged on startup, shown in Telegram.
- New env: `CLEAN_COMPOUND, CLEAN_START_BANKROLL, CLEAN_KELLY_FRAC,
  CLEAN_MAX_BET_PCT, CLEAN_MAX_OPEN_PCT, CLEAN_STOP_PCT`.
- Strategy/quality filters **unchanged** from v1.0 (ETH/SOL, early-drift ≥7bps,
  maker, 55–66¢).
- Analysis tool: `deep_analysis.py` (joins drift→fill→outcome; coin×dir, drift,
  entry, EV, Kelly).
- **Performance basis:** 51 trades, 65–67% WR, +$15–22 (account ~$28 → ~$49).
  Edge concentrated in SOL-UP (92%); ETH is marginal; cheap entries (≤62¢) win.

## v1.0.0 — 2026-06-18 — Initial clean rebuild
**Tag:** `cleanbot-v1.0` · **Status:** baseline (pre-compound rollback point)

- New `clean_bot.py` — minimal single-purpose **early-drift** trader, replacing
  the gate-paralysed `run_bot.py` (~10 stacked gates that stopped it trading).
- Edge: first ~5 min of a 15m window (T≥600s), if price drifted ≥7bps from the
  window-open strike, bet that direction. Maker-first (rest 1¢ below ask, 0 fee),
  ask ≤66¢ only, ETH/SOL, fixed 5-share size, $6 net daily stop.
- Reuses proven infra: OrderManager CLOB client, market_data, binance feed,
  Ireland proxy, gamma (Chainlink) resolution. Restart-safe state, dry-run mode.
- Telegram notifications (startup/fill/win/loss/stop). Dashboard 🤖 CleanBot tab.
- Docs: `CLEANBOT.md`. Analyzer: `clean_analysis.py`.
- **First profitable session:** 48 trades, 67% WR, +$22.50 real on-chain.
