#!/bin/bash
set -e
cd /home/ubuntu/v3-bot

echo "=== Append M5_* operational env vars ==="
if ! grep -q "^M5_ENABLED=" .env; then
    cat >> .env <<'ENVEOF'

# 5m bot test-week config (Apr 28)
M5_ENABLED=1
M5_COINS=BTC,SOL
M5_TEST_SIZE_USD=3.0
M5_DAILY_LOSS_CAP=5.0
M5_TRADE_HOURS_START=9
M5_TRADE_HOURS_END=12
M5_ENTRY_MAX=0.59
M5_MIN_EDGE=0.15
M5_MIN_TREND=0.80
M5_MAX_CONCURRENT=1
ENVEOF
    echo "[OK] Added M5_* to .env"
else
    echo "[SKIP] M5_* already in .env"
fi

echo ""
echo "=== Clean up smoke test artifacts ==="
rm -f _smoke_5m.py /tmp/smoke_traded_5m.json
rm -f _patch_engine_for_5m.py _patch_option_a.py _commit_option_a.sh

echo ""
echo "=== Git commit + tag ==="
git add config.py predictor.py market_data.py order_manager.py run_brain_5m.py .gitignore || true

# Make sure .gitignore covers 5m artifacts
if ! grep -q "traded_windows_5m" .gitignore 2>/dev/null; then
    cat >> .gitignore <<'GIEOF'

# 5m bot state files
traded_windows_5m.json
v3_bot_5m.log
logs/bot_5m_*.log
GIEOF
    git add .gitignore
fi

git commit -m "Add 5m bot (run_brain_5m.py) for test week — \$3 fixed sizing

A separate process running alongside the 15m main bot to harvest more
trade signals. The market is huge: 4 coins × 96 windows/day = 384 5m
windows vs 64 15m windows, so 6x more shots on goal.

Engine reuse (no duplication):
- Same Predictor (now timeframe-aware: warmup 30s + late-block 60s for 5m)
- Same exhaustion_detector
- Same OrderManager (extended with traded_file + force_size_usd +
  daily_loss_cap + bot_name params)
- Same telegram_notifier with [5M] prefix
- Same TRAP_BAND filter (60-63c blocked here too)
- Same weekend mode

Test-week safety rails:
- Fixed \$3 per bet (Kelly bypassed) -- bounded loss exposure
- \$5/day hard stop, separate from 15m's stop loss
- BTC + SOL only (skip XRP/ETH until BTC+SOL prove out)
- Morning only (9-12 Lima) -- the data shows morning is our edge zone
- Entry cap 59c (V2 mos=5 + \$3 budget)
- Min edge 15%, min trend 0.80 -- tighter than 15m

Process isolation:
- Separate PID (run_brain_5m.py)
- Separate traded_windows_5m.json (15m's locks untouched)
- Separate logs (logs/bot_5m_YYYY-MM-DD.log + v3_bot_5m.log)
- Crashes do not affect 15m bot

Backed by smoke test confirming:
- 5m markets exist on Polymarket (slug: {coin}-updown-5m-{ts})
- Predictor handles 5m timeframe correctly
- CLOB ask read works for 5m token IDs
- OrderManager constructor accepts new test-mode params

Activate via M5_ENABLED=1 in .env (off by default)."

git tag -a "5m-bot-testweek-apr28" -m "5m bot v1 -- test week, \$3 fixed"
echo "[OK] Tagged: 5m-bot-testweek-apr28"

echo ""
echo "=== Push ==="
git push origin demo-analytics-v1-apr23 || echo "(branch push failed - ok)"
git push origin 5m-bot-testweek-apr28 || echo "(tag push failed - ok)"

echo ""
echo "=== Verify 15m bot still running (should be untouched) ==="
ps aux | grep -E "python3 -u run_bot.py" | grep -v grep || echo "  WARNING: 15m bot not running"

echo ""
echo "=== Start 5m bot ==="
OLD_5M_PID=$(pgrep -f "python3 -u run_brain_5m.py" | head -1 || true)
if [ -n "$OLD_5M_PID" ]; then
    echo "Killing existing 5m bot PID $OLD_5M_PID"
    kill -TERM "$OLD_5M_PID"
    sleep 3
fi

setsid nohup python3 -u run_brain_5m.py >> v3_bot_5m.log 2>&1 < /dev/null &
disown
sleep 5

NEW_5M_PID=$(pgrep -f "python3 -u run_brain_5m.py" | head -1 || true)
if [ -z "$NEW_5M_PID" ]; then
    echo "[FAIL] 5m bot did not start"
    tail -30 v3_bot_5m.log
    exit 1
fi

echo "[OK] 5m bot PID: $NEW_5M_PID"
ps -p "$NEW_5M_PID" -o pid,etime,pcpu,pmem,cmd

echo ""
echo "=== Last 30 lines of 5m bot log ==="
tail -30 v3_bot_5m.log
