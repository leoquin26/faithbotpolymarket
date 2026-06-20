# CleanBot Changelog

Every change to CleanBot gets: (1) a `VERSION` bump in `clean_bot.py`, (2) an
entry here, (3) a git tag `cleanbot-vX.Y.Z`, (4) a push to GitHub. The running
version is logged on startup and shown in Telegram + the dashboard, so you always
know exactly what's live. Roll back with `git checkout cleanbot-vX.Y.Z`.

Versioning: **MAJOR.MINOR.PATCH** — MAJOR = strategy change, MINOR = new
feature/knob, PATCH = fix/tuning.

---

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
