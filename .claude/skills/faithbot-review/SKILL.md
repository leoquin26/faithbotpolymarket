---
name: faithbot-review
description: Review and improve the CleanBot (formerly FaithBot) Polymarket 15m Up/Down trading bot. Use when asked to analyze the bot, review trade logs, audit predictions, study win rate, run counterfactuals/backtests, improve accuracy or sizing, or diagnose losses. Covers EC2 access, log paths, the research-data schema, engine self-governance, existing audit scripts, git remotes, and the safe change/restart/commit workflow.
---

# CleanBot Review & Improvement Skill

**Current era (Jul 2026): CleanBot** (`clean_bot.py`, v1.49.x) — the old FaithBot
(`run_bot.py`, branch `producition_bot_v10`) is retired history. Read
`CHANGELOG.md` first: it is the authoritative decision log (every version, every
verdict, every env change, with the evidence).

## Step 0 — Connect to the EC2
Everything live runs on AWS EC2 (logs/data are gitignored, EC2-only).
```bash
chmod 600 polymarket-key.pem   # key is in the LOCAL project root, NOT in git
ssh -i polymarket-key.pem ubuntu@54.162.216.46
cd ~/v3-bot
```
Non-interactive: `ssh -i polymarket-key.pem ubuntu@54.162.216.46 "cd ~/v3-bot && <cmd>"`.
Dashboard (React SPA): `ssh -i polymarket-key.pem -L 8095:localhost:8095 ubuntu@54.162.216.46`
-> http://localhost:8095 (backend `cleanbot_dash.py`, UI in `dash_ui/`).

## What's on the EC2 (~/v3-bot)
- `clean_bot.py` — THE bot. Log: `~/v3-bot/clean_bot.log` (repo ROOT, not logs/).
- `clean_bot_research.csv` — every window snapshot + outcome (phases: early / mid / late).
  Columns include drift_pct, sigma, fav_ask, t_left, winner, drift_correct, book_imb, phase.
- `clean_bot_state.json` — bankroll, positions, `engine_off`, `engine_mult`, `recent_ev`
  (tagged per-engine results), `killed` latch.
- `.env` — secrets + all CLEAN_* tuning (gitignored; `.env.bak_*` are timestamped backups).
- Side processes: `daily_scout.py` (Strategy-2 shadow), `arb_monitor.py`, `sniper_dryrun.py`,
  `mtm_tracker.py`, `cleanbot_dash.py`.
- Analysis toolkit: `_accuracy_audit.py`, `_late_verify.py`, `_sigma_upgrade.py`,
  `_meta_label.py`, `_fetch_klines.py` (+ `data/klines_1m.csv` 1-minute OHLC cache).
- `_watchdog.sh` on cron */5min — auto-restarts crashed bot/dash/scout.

## Engine architecture (self-governing since v1.47)
Per-engine scoreboards `[TRACK:early|late|voldiv]` (WR + EV/$ staked) and a midnight
`[SCORE]` board. **Pre-registered verdicts execute AUTOMATICALLY at n>=40 per engine:**
EV/$ >= +0.03 -> self-scales size x2/x3; <= -0.03 -> `[VERDICT] ENGINE RETIRED`
(latched in state; owner reset only). Engine status lives in `clean_bot_state.json`
and on the dashboard "Engines" panel.
- `early` — RETIRED Jul 10 2026 (EV -0.177, math-locked). Do NOT resurrect.
- `voldiv` — OFF (failed live audition; model was worse-calibrated than the market).
- `late` — the live engine: taker fills at the ask, >=3bps fresh lead, 55-70c,
  SOL/ETH/BTC, one leg per direction per window, sleeps 00:00-07:00 Lima.

## Safe change workflow (MANDATORY)
1. Edit LOCAL repo (`C:\Users\leona\Projects\polymarket-no-maxi-bot`), syntax-check
   (`python -c "import ast; ast.parse(open('clean_bot.py', encoding='utf-8').read())"`).
2. `scp` to EC2, syntax-check there, verify `md5sum` matches local.
3. **Watchdog restart dance:** `touch ~/v3-bot/.watchdog_pause` -> kill
   `pgrep -f '^python3 -u clean_bot.py$'` -> `setsid nohup python3 -u clean_bot.py >>
   clean_bot.log 2>&1 &` -> verify exactly 1 process -> `rm ~/v3-bot/.watchdog_pause`.
4. VERSION bump in clean_bot.py + CHANGELOG.md entry + git tag `cleanbot-vX.Y.Z`.
5. **Push to BOTH remotes:** `git push origin cleanbot-main --tags` AND
   `git push faithbot cleanbot-main --tags`
   (origin = github.com/leoquin26/Randomforest.git — legacy default;
   faithbot = github.com/leoquin26/faithbotpolymarket.git — owner's preferred).

## Verification discipline (hard-earned; violations cost real money)
- NOTHING deploys without an out-of-sample pass on `clean_bot_research.csv`
  (chronological 70/30 split; gates n>=80, z>=1.64 vs break-even = avg entry price, EV/$>0).
- Break-even WR at a binary = the price paid (64c -> need 64%). WR alone always lies.
- The research capture floor is 3bps — anything below is UNMEASURED; never trade it.
- Execution matters as much as signal: resting maker orders fill adversely (measured
  -11pts vs taker); the late engine takes the ask on purpose.
- Owner's standing rules: no ultra-rare setups, no overblocking, constant betting;
  state the trades/day impact of any proposed change; report numbers plainly, never spin.
- Rejected forever (fresh stats required to even discuss): early drift engine, VOLDIV
  divergence trading, fade-the-drift, HMM direction prediction, order-flow veto,
  hour-of-day filters on the early universe, latency arb/sniper, XRP (tainted strikes),
  active exits/TP/SL, RS/YZ sigma swap, meta-labeling sizer, kappa momentum term.

## Guardrails
- Never print/commit secrets (`.env*`, `*.pem`, keys). Never run two bot instances
  (watchdog races — always use the pause dance). Never delete data/ or the research CSV.
- The reconciler clamps the bankroll DOWN for a few minutes after wins (redemption lag)
  — benign, verified on-chain; don't "fix" phantom discrepancies without checking chain truth.
