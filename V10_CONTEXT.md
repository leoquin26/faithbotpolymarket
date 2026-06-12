# v10 Audit Context — for an analysis agent

This branch (`producition_bot_v10`) is the deployed state after a multi-session
accuracy & sizing audit of the FaithBot 15-minute Up/Down predictor. This file
captures *what was investigated, what changed, and what was rejected and why*, so
a downstream agent can analyze the bot without re-deriving the history.

Read `FAITHBOT_DOCUMENTATION.md` first for architecture/module reference, then this.

## System at a glance
- Trades Polymarket 15m crypto Up/Down (BTC, ETH, SOL, XRP).
- Settlement-first: directional probability vs on-chain settled strike (Chainlink),
  with Binance/Bybit as fallback feeds.
- Probability: Black-Scholes binary + EWMA tick volatility + multi-timeframe momentum (ROC60/ROC300).
- Entry gated by: near-strike accuracy gate, chop/consensus gates, direction lock,
  momentum alignment, overshoot cap. Sizing: distance-tiered + session-weighted.

## Entry points / key modules
- `run_bot.py`            — 15m main loop (live engine)
- `run_brain_5m.py`       — 5m experimental loop (separate test track)
- `predictor.py`          — probability engine, volatility, momentum, gates
- `order_manager.py`      — sizing (distance tiers + session weights), order placement
- `market_data.py`, `binance_ws.py`, `bybit_ws.py`, `chainlink_ws.py`,
  `chainlink_onchain.py`, `polymarket_ws.py` — feeds
- `poly_resolution.py`    — settlement/resolution
- `session_calibration.py`— ET session definitions used for sizing
- `morning_predictor.py`, `morning_strategy.py` — morning regime logic
- `regime_aware/`         — confidence calibration helpers
- `dashboard_v3/`         — Flask monitoring dashboard
- `analytics/`            — event logging / ledger

## v10 changes that ARE live (and why)
1. EWMA volatility fix (predictor.py): sub-second tick bursts divided by tiny dt
   inflated per-second sigma ~25x. Fix: dt floor = 1.0s, lambda = 0.97, sigma cap.
   This was a major source of mispriced probabilities.
2. Hybrid momentum unified: ROC60/ROC300 now computed from Chainlink ticks only.
   Previously volatility came from Binance while direction came from Chainlink ->
   feed mismatch caused over-abstentions and noisy momentum.
3. Overshoot cap: skip trades far from strike (|dist| >= ~0.30%) — empirically these
   are mean-reversion traps with poor win rate.
4. Near-strike accuracy gate widened + dead-momentum blocking (block coin-flip entries
   with no momentum near the strike).
5. Sizing overhaul (order_manager.py):
   - Replaced Kelly (model probs had ~0 correlation with outcomes, so Kelly mis-sized)
     with DISTANCE-TIERED sizing (e.g. 9/6/4 shares by |dist| band), env-tunable.
   - SESSION-WEIGHTED multiplier by ET session (morning down-weighted, afternoon up).
   - Lowered hard share floor 5 -> env default 3 (the 5-floor was negating de-sizing
     in low-confidence/low-volume sessions).
   - Correlation cap to avoid simultaneous same-direction BTC+ETH double exposure.
6. Feed resilience: Chainlink RTDS 429/ban handling (backoff, on-chain fallback,
   healthcheck cron), RPC proxy bypass fix.

## Hypotheses TESTED and REJECTED (do not re-deploy without new data)
- Morning directional FADE (invert signal): morning looks mean-reverting and morning
  UP bets historically ~33% win. BUT inverting is physically unsound far from strike
  (strike conflict) and near-strike samples were too small to trust. Reverted.
- Z-score gate: backtested, did not improve outcomes. Rejected.
- Late-window momentum-deceleration reversal predictor: variance, not deceleration,
  explained most late reversals. No forecasting lift. Rejected.

## Known open questions for the analysis agent
- Morning regime still underperforms; needs more samples before any directional logic
  is statistically safe. Session sizing currently down-weights morning instead.
- Model probability calibration is weak (Brier/correlation low) — distance is currently
  a better sizing signal than raw probability. Worth revisiting calibration.
- Many env knobs exist; see `.env.example` and `config.py`. Live `.env` is NOT in git.

## Notes / gotchas
- `.env`, `*.pem`, `*.key`, logs, and runtime state (`data/*.jsonl`, traded_windows)
  are gitignored — secrets and live state are NOT on this branch by design.
- One-off `_patch_*.py`/`_audit_*.py`/`_test_*.py` scripts and `*_v4/v5/v8` backups
  on the server are scratch artifacts and intentionally NOT committed.
