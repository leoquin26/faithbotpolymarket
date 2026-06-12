---
name: faithbot-review
description: Review and improve the FaithBot Polymarket 15m Up/Down trading bot. Use when asked to analyze the bot, review trade logs, audit predictions, study win rate, run counterfactuals/backtests, improve directional accuracy or sizing, or diagnose losses. Covers log paths, the trade-event schema, the gate/skip vocabulary, existing audit scripts, and the safe change/restart/commit workflow.
---

# FaithBot Review & Improvement Skill

Use this skill to analyze and improve the FaithBot crypto Up/Down trading bot.

## Step 0 — Connect to the EC2
The bot + all logs/data live on an AWS EC2 box (logs/data are gitignored, EC2-only).
```bash
chmod 600 polymarket-key.pem   # key is NOT in git; owner provides it
ssh -i polymarket-key.pem ubuntu@54.162.216.46
cd ~/v3-bot
```
Drive it non-interactively: `ssh -i polymarket-key.pem ubuntu@54.162.216.46 "cd ~/v3-bot && <cmd>"`.
Dashboard tunnel: `ssh -i polymarket-key.pem -L 8080:localhost:8080 ubuntu@54.162.216.46` -> http://localhost:8080.
See AGENT_GUIDE.md "Connecting to the EC2" for troubleshooting.

## Step 1 — Load context (read in this order)
1. `README.md`
2. `FAITHBOT_DOCUMENTATION.md` (architecture + module reference)
3. `V10_CONTEXT.md` (latest audit: what's live + what was tested-and-rejected)
4. `MODEL_AUDIT.md`
5. `AGENT_GUIDE.md` (operations: access, logs, data, recipes, safe workflow) <- the full playbook

`AGENT_GUIDE.md` is the authoritative operations reference. Read it fully before acting.

## Step 2 — Gather evidence
- Logs: `logs/bot_YYYY-MM-DD.log` (format `HH:MM:SS | LEVEL | [TAG] msg`), `v3_bot.log`.
- Structured: `data/trade_events.jsonl` (signals), `data/trade_ledger.db` (SQLite),
  `data/poly_reconciled.csv` (ground-truth fills/outcomes), `data/daily_pnl.json`.
- Tag distribution: `grep -oE '\[[A-Z][A-Z _0-9]+\]' logs/bot_<date>.log | sort | uniq -c | sort -rn`
- These live ONLY on the EC2 (gitignored). Read them on the box.

## Step 3 — Analyze
- Reuse shipped audit scripts (prefix `_`): `_volaudit.py`, `_size_vs_wr.py`,
  `_tier_analysis.py`, `_counterfactual_*.py`, `_audit_blockers*.py`,
  `_grade_calibration_shadow.py`, `_late_window_backtest.py`.
- For new studies: join signals (trade_events.jsonl) with outcomes (poly_reconciled.csv /
  trade_ledger.db); measure win rate / PnL by the variable under test. Prefer
  counterfactual replay over live experiments. Always report sample size.

## Step 4 — Improve safely
- Prefer ACCURACY (pick correct direction + size well) over hard-blocking trades.
- Make changes env-tunable (`config.py`, `.env.example`) when possible.
- Backup engine files before editing: `cp predictor.py predictor.py.bak_$(date +%s)`.
- Validate with the relevant counterfactual + `python3 -c 'import ast; ast.parse(open("run_bot.py").read())'`.
- Restart one bot only (see AGENT_GUIDE "Restarting safely" + `_ensure_single_bot.sh`),
  then watch today's log for `[SIGNAL]`/`[COMMIT]` and tracebacks.

## Step 5 — Commit
- Branch `producition_bot_v10` (or a new feature branch).
- NEVER commit `.env*`, `*.pem`, `*.bak*`, logs, or `data/` state (`.gitignore` blocks
  these — still verify `git diff --cached --name-only`).

## Guardrails
- Never print/commit secrets (private keys, API tokens, `.env*`, `*.pem`).
- Never run two `run_bot.py` instances. Never delete `data/` history.
- Don't re-deploy rejected ideas (morning fade, z-score gate, deceleration-reversal)
  without fresh statistically-sound data. Don't widen risk without evidence.
