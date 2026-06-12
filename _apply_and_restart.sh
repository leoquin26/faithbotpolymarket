#!/bin/bash
set -e
cd /home/ubuntu/v3-bot

echo "=== apply patch ==="
python3 _patch_exhaust_override.py

echo ""
echo "=== syntax check ==="
python3 -m py_compile run_bot.py

echo ""
echo "=== verify patch text in place ==="
grep -n "EXHAUST OVERRIDE" run_bot.py

echo ""
echo "=== git commit ==="
git add run_bot.py
git commit -m "feat(exhaust): edge-priority override — A-tier signals (prob>=82% edge>=18%) downgrade ABSTAIN to DAMPEN

Today's MORNING P3 (12:00-12:32 Lima) showed EXHAUST blocking signals
like BTC UP @ 62c Prob=84% Edge=21.6% Trend=+1.30. These are the
strongest historical winners (>80% win rate by backfill). The fix:
when prob>=82% AND edge>=18%, treat EXHAUST ABSTAIN as DAMPEN
instead — fire at half size. Loss is bounded; we recover the
upside on the rare top-tier signals."

git tag -a exhaust-override-apr27 -m "Edge-priority EXHAUST override" || true
git push origin HEAD
git push origin exhaust-override-apr27 || true

echo ""
echo "=== restart bot ==="
OLD_PID=$(pgrep -f "python3.*run_bot.py" | head -1 || echo "")
echo "old pid: ${OLD_PID:-none}"
if [ -n "$OLD_PID" ]; then
    kill "$OLD_PID" || true
    sleep 3
    if ps -p "$OLD_PID" >/dev/null 2>&1; then
        kill -9 "$OLD_PID"
        sleep 1
    fi
fi
setsid nohup python3 -u run_bot.py >> v3_bot.log 2>&1 < /dev/null &
NEW_PID=$!
disown "$NEW_PID" 2>/dev/null || true
sleep 5
echo "new pid: $(pgrep -f 'python3.*run_bot.py' | head -1)"

echo ""
echo "=== last 12 log lines ==="
tail -n 12 v3_bot.log

echo ""
echo "=== ready — watch for [EXHAUST OVERRIDE] in subsequent windows ==="
