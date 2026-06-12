# FaithBot — Agent Onboarding & Improvement Guide

You are a coding/analysis agent tasked with **reviewing and improving** the FaithBot
Polymarket trading bot. This guide tells you exactly how the system works, where the
data lives, how to review logs, how to run analyses, and how to ship safe improvements.

> Read order: `README.md` -> `FAITHBOT_DOCUMENTATION.md` (architecture/module ref)
> -> `V10_CONTEXT.md` (latest audit findings & rejected ideas) -> `MODEL_AUDIT.md`
> -> this file (operations).

---

## 0. TL;DR mission
The bot predicts BTC/ETH/SOL/XRP 15-minute Up/Down outcomes on Polymarket and bets when
it sees an edge vs market price. Your job: **make its directional predictions more
accurate and its sizing smarter** — using evidence from logs and trade history, NOT by
just blocking more trades. The owner explicitly prefers *better understanding of the
market* over risk-blocking.

---

## 1. Environment & access
- Host: AWS EC2 (Ubuntu), bot dir: `~/v3-bot`.
- Two live processes:
  - `python3 -u run_bot.py`            -> the 15m live engine
  - `python3 -m dashboard_v3.app`      -> Flask dashboard on port `8080` (env `DASH_PORT`)
- A separate experimental track: `run_brain_5m.py` (5m markets, do not touch unless asked).
- Outbound feeds use a Tor/SOCKS proxy in places (see `force_tor.py`); RPC calls bypass it.

### Restarting safely
```bash
# find + stop the 15m bot
pkill -f 'run_bot.py'
# start (logs go to logs/bot_<date>.log via the runner; nohup to detach)
cd ~/v3-bot && nohup python3 -u run_bot.py >> v3_bot.log 2>&1 &
```
Always confirm only ONE bot runs: `ps aux | grep run_bot | grep -v grep`.
There is a helper `_ensure_single_bot.sh`.

### Cron jobs
- `*/7 * * * *` -> `_rtds_healthcheck.py` (Chainlink RTDS feed watchdog) -> `logs/rtds_health_cron.log`
- `30 22 * * *` -> `cleanup_logs.sh`

---

## 2. Where the data lives (your evidence)
### Logs (`~/v3-bot/logs/` and `~/v3-bot/`)
- `logs/bot_YYYY-MM-DD.log`  -> per-day engine log (PRIMARY source for review)
- `v3_bot.log`               -> current rolling stdout
- `logs/rtds_health*.log`    -> feed health
- Log line format: `HH:MM:SS | LEVEL | [TAG] message`

### Structured trade events (best for analysis)
- `data/trade_events.jsonl`  -> one JSON per event. Example SIGNAL event:
```json
{"ts":"...","event":"SIGNAL","coin":"ETH","side":"DOWN","entry":0.65,
 "prob":0.788,"edge":0.138,"trend_score":-0.745,"window_start":...,"confidence":"HIGH","poly_price":0.65}
```
- `data/trade_ledger.db`     -> SQLite ledger of trades/outcomes
- `data/daily_pnl.json`, `data/open_positions.json`, `data/traded_windows.json` -> live state
- `data/poly_reconciled.csv`, `data/poly_raw_trades.csv` -> reconciled real Polymarket fills (ground truth outcomes)
- `data/strike_cache.json`   -> settled strikes per window

> NOTE: logs, .env, *.db, data/*.jsonl, and runtime state are gitignored and live ONLY on
> the EC2. To analyze real outcomes you must read them on the box.

---

## 3. Log vocabulary (gate/skip reasons)
The engine narrates every decision with a `[TAG]`. Most common (counts vary by day):
- `[SIGNAL]` -> a tradeable signal was produced; `[COMMIT]` -> order sent
- `[TF DISAGREE]` -> ROC60 vs ROC300 timeframes disagree -> trend dampened
- `[WEAK TREND]` -> trend score below session threshold -> skip
- `[OVERSHOOT]` -> too far from strike (mean-reversion trap) -> skip
- `[THIN DIST]/[THIN EDGE]/[LOW EDGE]/[LOW PROB]` -> edge/probability too small
- `[EXPENSIVE]/[EXPENSIVE UP]/[EXPENSIVE DOWN]/[NEAR FLOOR]` -> price gates
- `[TOO LATE]/[WARMUP]/[COLD START]` -> timing gates within the window
- `[CHOPPY]/[CHOPPY STRICT]` -> chop detector; `[FLIP GUARD]` -> direction flip blocked
- `[CONSENSUS]/[CONSENSUS BYPASS]` -> multi-signal agreement gate
- `[BOUNCE]/[MOM CONFLICT]` -> momentum sanity checks
- `[SETTLEMENT]/[STRIKE]/[RESOLVE PENDING]/[GAMMA]` -> settlement/resolution logic
- `[CALIBRATION LIVE]` -> probability calibration applied
- `[MORNING P1]` -> morning-regime path

### Quick log review recipes
```bash
# Win/loss + skip distribution for a day
grep -oE '\[[A-Z][A-Z _0-9]+\]' logs/bot_2026-06-12.log | sort | uniq -c | sort -rn
# All committed trades today
grep -E '\[SIGNAL\]|\[COMMIT\]' logs/bot_2026-06-12.log
# Why a coin kept getting skipped
grep 'BTC' logs/bot_2026-06-12.log | grep -E 'WEAK TREND|OVERSHOOT|THIN'
```

---

## 4. How a prediction is made (mental model)
1. Pull settled strike (Chainlink on-chain / RTDS; Binance/Bybit fallback).
2. Compute EWMA tick volatility (predictor.py — note v10 dt-floor fix).
3. Compute momentum ROC60/ROC300 from Chainlink ticks.
4. Black-Scholes binary prob that price ends Up vs strike.
5. Apply gates (section 3). If a signal survives -> size it.
6. Sizing (order_manager.py): distance-tiered shares x session-weight multiplier,
   correlation cap (no BTC+ETH same-direction double-up), env share floor.
7. Place Fill-or-Kill order.

Key files: `predictor.py`, `order_manager.py`, `run_bot.py`, `session_calibration.py`,
`market_data.py` + feed modules, `poly_resolution.py`.

---

## 5. v10 state — what's live and what was REJECTED
See `V10_CONTEXT.md` for full detail. Summary:
- LIVE: EWMA volatility fix (was ~25x inflated), Chainlink-only momentum, overshoot cap,
  widened near-strike gate + dead-momentum block, distance-tiered + session-weighted
  sizing, correlation cap, share-floor 5->3, feed-resilience.
- REJECTED (don't redo without NEW data): morning directional fade (strike conflict +
  tiny samples), z-score gate (no lift), late-window deceleration-reversal predictor
  (variance, not deceleration, drives reversals).

### Known weak spots to investigate (good starting points)
- Probability calibration is weak (low Brier / low corr with outcomes) -> distance is
  currently a better sizing signal than raw prob. Improving calibration is high-value.
- Morning regime underperforms (esp. UP bets historically ~33% WR) but samples are thin.
- `[TF DISAGREE]` and `[WEAK TREND]` dominate skips -> study whether thresholds are
  optimal or leaving edge on the table.

---

## 6. Running analyses (existing tooling)
The repo ships the audit scripts used during v10 (prefix `_`). They read the logs/JSONL.
Examples (run from `~/v3-bot`):
- `_volaudit.py`            -> volatility sanity / inflation check
- `_size_vs_wr.py`         -> bet size vs realized win rate
- `_tier_analysis.py`      -> distance-tier performance
- `_counterfactual_*.py`   -> replay "what if" gate/sizing changes against history
- `_audit_blockers*.py`    -> what gates are blocking trades + would-be outcomes
- `_grade_calibration_shadow.py` -> shadow-grade probability calibration
- `_late_window_backtest.py`, `_falling_knife_counterfactual.py` -> regime studies

Pattern for a NEW study: parse `data/trade_events.jsonl` (signals) + join outcomes from
`data/poly_reconciled.csv` / `trade_ledger.db`, then measure win rate / PnL by the
variable you're testing. Prefer counterfactual replay over live experiments.

---

## 7. How to make improvements SAFELY
1. **Diagnose first** with logs + a counterfactual/backtest. State the hypothesis and the
   sample size. Beware tiny samples (the morning-fade trap).
2. **Prefer accuracy over blocking.** The owner does not want more hard blocks; aim to
   pick the *correct direction* and size appropriately.
3. **Make changes env-tunable** where possible (read `config.py` + `.env.example`), so
   behavior can be tuned without code edits.
4. **Backup before patching** an engine file: `cp predictor.py predictor.py.bak_$(date +%s)`
   (these .bak files are gitignored).
5. **Validate**: re-run the relevant counterfactual; `python3 -c 'import ast; ast.parse(open("run_bot.py").read())'` for a quick syntax check; ideally a dry import.
6. **Restart** the bot (section 1), then watch `logs/bot_<today>.log` for `[SIGNAL]`/`[COMMIT]`
   and confirm no tracebacks.
7. **Commit to git** (branch `producition_bot_v10` or a new feature branch). NEVER commit
   `.env*`, `*.pem`, `*.bak*`, logs, or `data/` state — `.gitignore` already blocks these,
   but double-check `git diff --cached --name-only` before pushing.

---

## 8. Guardrails (do NOT do)
- Do NOT commit or print secrets: `.env`, `.env.bak*`, `*.pem`, private keys, API tokens.
- Do NOT run two `run_bot.py` instances at once.
- Do NOT delete `data/` history (it's the ground-truth outcome record).
- Do NOT re-deploy the rejected ideas in section 5 without fresh statistically-sound data.
- Do NOT widen risk blindly; size with evidence.

---

## 9. Fast start checklist
1. `ssh` to the EC2, `cd ~/v3-bot`.
2. `ps aux | grep -E 'run_bot|dashboard' | grep -v grep` (confirm health).
3. Read today's log tail + tag distribution (section 3 recipes).
4. Read `V10_CONTEXT.md` + `MODEL_AUDIT.md`.
5. Pick ONE weak spot (section 5), run a counterfactual (section 6).
6. Propose change -> backup -> patch -> validate -> restart -> commit (section 7).
