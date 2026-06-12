#!/bin/bash
set -e
cd /home/ubuntu/v3-bot

echo "=== Git status ==="
git status --short

echo ""
echo "=== Diff summary ==="
git diff --stat run_bot.py order_manager.py

echo ""
echo "=== Adding + committing ==="
git add run_bot.py order_manager.py
git commit -m "Fix apr27: skip DAMPEN size halving on EXHAUST OVERRIDE bets

When EXHAUST OVERRIDE fires (A-tier signal: prob>=82% AND edge>=18%),
the override path was double-penalizing size:
  - Tier C (50%) from daily-loss cap
  - x DAMPEN (50%) from override flag
  = 25% of natural Kelly

A-tier signals already self-select the strongest evidence in the system.
Override IS the safety filter; stacking DAMPEN on top makes the bet too
small ($1.95 today on a sized-up $8.15 natural Kelly).

Fix: in run_bot.py, set _override_full_size=True when override fires.
In order_manager.py, skip the *0.5 size cut when that flag is present.
The Tier-C daily-loss cap still applies (still conservative on bad days)."

git tag -a "override-no-double-penalty-apr27" -m "Drop DAMPEN halving on override A-tier bets"
echo "[OK] Tagged: override-no-double-penalty-apr27"

echo ""
echo "=== Pushing to demo branch ==="
git push origin demo-analytics-v1-apr23 || echo "(push failed, that's ok if no upstream)"
git push origin override-no-double-penalty-apr27 || echo "(tag push failed, ok)"

echo ""
echo "=== Restarting bot ==="
echo "Old PID:"
pgrep -f "python3 -u run_bot.py" || true

OLD_PID=$(pgrep -f "python3 -u run_bot.py" | head -1)
if [ -n "$OLD_PID" ]; then
    echo "Killing old PID $OLD_PID"
    kill -TERM "$OLD_PID"
    sleep 3
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "Force kill"
        kill -9 "$OLD_PID"
        sleep 1
    fi
fi

echo "Starting new bot..."
cd /home/ubuntu/v3-bot
setsid nohup python3 -u run_bot.py >> v3_bot.log 2>&1 < /dev/null &
disown
sleep 4

NEW_PID=$(pgrep -f "python3 -u run_bot.py" | head -1)
echo "New PID: $NEW_PID"
ps -p "$NEW_PID" -o pid,etime,cmd

echo ""
echo "=== Last 15 log lines ==="
tail -15 v3_bot.log
