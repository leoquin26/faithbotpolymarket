# Copy-paste prompt — start a full FaithBot review

Give the text below to a Claude Code agent that has this repo and the
`polymarket-key.pem` key available. It tells the agent which branch to use, how to
connect to the EC2, and how to review everything before changing code.

---

```
You are reviewing and improving FaithBot, an automated Polymarket trading bot that
predicts BTC/ETH/SOL/XRP 15-minute "Up/Down" outcomes and bets when it sees an edge.

=== REPO / BRANCH ===
- GitHub repo: github.com/leoquin26/faithbotpolymarket
- Use the branch: producition_bot_v10   <-- check this out, do all work here
  git fetch origin
  git checkout producition_bot_v10
  git pull origin producition_bot_v10
- This branch contains the code + a `faithbot-review` Claude Code skill + AGENT_GUIDE.md
  (full operations playbook). Use them.

=== CONNECT TO THE EC2 (the bot + all logs/data live here) ===
- Host (public IP): 54.162.216.46
- SSH user: ubuntu
- Private key: polymarket-key.pem   (NOT in git — it must already be on your machine;
  if it's elsewhere, use its absolute path. Note: the public IP can change if the
  instance was stopped/started without an Elastic IP — confirm if connection fails.)
- Bot directory on the server: ~/v3-bot  (= /home/ubuntu/v3-bot)

Connect:
  chmod 600 polymarket-key.pem            # SSH refuses world/group-readable keys
  ssh -i polymarket-key.pem ubuntu@54.162.216.46
  cd ~/v3-bot

Drive it non-interactively (preferred for an agent):
  ssh -o ConnectTimeout=20 -i polymarket-key.pem ubuntu@54.162.216.46 "cd ~/v3-bot && <command>"

View the dashboard from your laptop (binds to port 8080):
  ssh -i polymarket-key.pem -L 8080:localhost:8080 ubuntu@54.162.216.46   # then open http://localhost:8080

If "Permission denied (publickey)": wrong key, wrong user (must be ubuntu), or key perms
not 600. If "Connection timed out": instance off or security group not allowing TCP 22.
See AGENT_GUIDE.md "Connecting to the EC2" for more troubleshooting.

=== START HERE (in order) ===
1. On the branch producition_bot_v10, read .claude/skills/faithbot-review/SKILL.md and follow it.
2. Connect to the EC2 (above) and cd ~/v3-bot.
3. Read, in this order: README.md, FAITHBOT_DOCUMENTATION.md, V10_CONTEXT.md,
   MODEL_AUDIT.md, then the rest of AGENT_GUIDE.md.

=== THEN DO A FULL REVIEW (read-only first — do NOT change code yet) ===
A. Health: confirm exactly one run_bot.py and the dashboard are running; check
   logs/rtds_health*.log for feed problems.
B. Logs: for the last ~5 trading days (logs/bot_YYYY-MM-DD.log), produce the gate/skip
   tag distribution and list every [SIGNAL]/[COMMIT]. Identify the top reasons trades
   are skipped and whether they're leaving edge on the table.
C. Outcomes: join signals in data/trade_events.jsonl with ground-truth results in
   data/poly_reconciled.csv (and/or data/trade_ledger.db). Compute, with sample sizes:
   - overall win rate and PnL,
   - win rate by coin, by direction (UP/DOWN), by ET session, by distance-from-strike
     tier, and by confidence level,
   - calibration: do higher model probabilities actually win more often? (Brier / bucketed
     reliability).
D. Reuse the shipped audit scripts where useful (_volaudit.py, _size_vs_wr.py,
   _tier_analysis.py, _counterfactual_*.py, _audit_blockers*.py,
   _grade_calibration_shadow.py, _late_window_backtest.py).

=== DELIVERABLE (written report, no code changes yet) ===
1. Current performance summary (numbers + sample sizes).
2. The 3-5 biggest, evidence-backed opportunities to improve DIRECTIONAL ACCURACY and
   SIZING — ranked by expected impact and confidence.
3. For each: the hypothesis, the data that supports it, a proposed change, and a
   counterfactual/backtest plan to validate it BEFORE deploying.

=== RULES ===
- Prefer improving accuracy (pick the correct direction + size well) over hard-blocking
  trades. The owner does NOT want more blunt blocks.
- Always report sample sizes; be skeptical of tiny samples.
- Do NOT re-deploy ideas already tested and rejected (morning directional fade, z-score
  gate, late-window deceleration-reversal predictor) unless you have NEW statistically
  sound evidence — see V10_CONTEXT.md.
- NEVER print or commit secrets (.env*, *.pem, private keys, API tokens). NEVER run two
  run_bot.py instances. NEVER delete data/ history.
- Make changes only after I approve the plan. When you do: back up the engine file first
  (cp predictor.py predictor.py.bak_$(date +%s)), keep changes env-tunable when possible,
  validate with a counterfactual, restart one bot, watch today's log, then commit to the
  producition_bot_v10 branch (verify git diff --cached --name-only has no secrets/logs/data).

Begin with steps 1-3, then give me the full review report (A-D) before proposing any edits.
```
