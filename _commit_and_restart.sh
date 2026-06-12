#!/bin/bash
set -e
cd /home/ubuntu/v3-bot

echo "=== git branch ==="
git branch --show-current

echo "=== staging changes ==="
git add run_bot.py config.py

git commit -m "fix(pm,weekend): hard PM entry cap 64c + unified weekend mode (Fri17->Mon09)

- PM_ENTRY_MAX=0.64 in config (env-overridable)
- afternoon fire path rejects clob_ask > PM_ENTRY_MAX with [PM ENTRY CAP]
  reasoning: backfill shows R:R collapses to 0.49 at 66-69c and 0.35 above 69c
- is_good_trading_hour unified: [WEEKEND MODE] blocks Fri 17:00 -> Sat/Sun all
  day -> Mon <09:00. Message: 'blocked until Monday 09:00 Lima'
- morning path unaffected (still allows up to ENTRY_MAX=0.78)"

git tag -a pm-cap-weekend-apr24 -m "PM entry cap 64c + weekend mode hardened" || true

git push origin HEAD
git push origin pm-cap-weekend-apr24 || true

echo "=== restart bot ==="
# Identify current run_bot.py PID
OLD_PID=$(pgrep -f "python3.*run_bot.py" | head -1 || echo "")
echo "old pid: ${OLD_PID:-none}"
if [ -n "$OLD_PID" ]; then
    kill "$OLD_PID" || true
    sleep 3
    # Force kill if still running
    if ps -p "$OLD_PID" >/dev/null 2>&1; then
        kill -9 "$OLD_PID"
        sleep 1
    fi
fi

# Start fresh
cd /home/ubuntu/v3-bot
setsid nohup python3 -u run_bot.py >> v3_bot.log 2>&1 < /dev/null &
NEW_PID=$!
disown "$NEW_PID" 2>/dev/null || true
sleep 4

echo "new pid: $(pgrep -f 'python3.*run_bot.py' | head -1)"
echo ""
echo "=== last 25 log lines ==="
tail -n 25 v3_bot.log
