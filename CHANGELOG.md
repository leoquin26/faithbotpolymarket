# CleanBot Changelog

Every change to CleanBot gets: (1) a `VERSION` bump in `clean_bot.py`, (2) an
entry here, (3) a git tag `cleanbot-vX.Y.Z`, (4) a push to GitHub. The running
version is logged on startup and shown in Telegram + the dashboard, so you always
know exactly what's live. Roll back with `git checkout cleanbot-vX.Y.Z`.

Versioning: **MAJOR.MINOR.PATCH** — MAJOR = strategy change, MINOR = new
feature/knob, PATCH = fix/tuning.

---

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
