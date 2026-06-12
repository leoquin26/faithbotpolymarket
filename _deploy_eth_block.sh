#!/bin/bash
set -e
cd /home/ubuntu/v3-bot

echo "=== Current PM_BLOCKED_COINS in .env ==="
grep -E "^PM_BLOCKED_COINS" .env || echo "(not set — using config default 'XRP')"

echo ""
echo "=== Update .env: add ETH to PM_BLOCKED_COINS ==="
if grep -qE "^PM_BLOCKED_COINS=" .env; then
    sed -i 's/^PM_BLOCKED_COINS=.*/PM_BLOCKED_COINS=XRP,ETH/' .env
    echo "[OK] Updated existing PM_BLOCKED_COINS line"
else
    echo "" >> .env
    echo "# Apr 28: PM ETH is 36% WR / -\$15 net in backfill — same pattern as XRP" >> .env
    echo "PM_BLOCKED_COINS=XRP,ETH" >> .env
    echo "[OK] Appended PM_BLOCKED_COINS=XRP,ETH"
fi

echo ""
echo "=== Verify .env ==="
grep -E "^PM_BLOCKED_COINS" .env

echo ""
echo "=== Verify config.py reads it correctly ==="
python3 -c "
from dotenv import load_dotenv
load_dotenv()
import config
print(f'PM_BLOCKED_COINS = {config.PM_BLOCKED_COINS}')
assert 'ETH' in config.PM_BLOCKED_COINS, 'ETH not in set!'
assert 'XRP' in config.PM_BLOCKED_COINS, 'XRP not in set!'
print('[OK] both XRP and ETH blocked in PM')
"

echo ""
echo "=== Git commit + tag ==="
git add .env
git commit -m "PM: also block ETH (counterfactual: 36% WR, -\$15 net over 8 days)

Counterfactual analysis on the 8-day backfill (44 PM trades total)
revealed PM ETH is bleeding the same way PM XRP was:

  PM ETH: 11 trades, 36.4% WR, R:R 0.65, net -\$15.33
  PM XRP: 9 trades,  33.3% WR, R:R 0.81, net -\$17.37  (already blocked)
  PM BTC: 13 trades, 69.2% WR, R:R 0.67, net +\$6.76  (kept)
  PM SOL: 11 trades, 72.7% WR, R:R 0.80, net +\$13.84 (kept)

Adding ETH to PM_BLOCKED_COINS via .env (no code change needed since
PM_BLOCKED_COINS is already env-driven from Option A apr28). After
this change PM is BTC+SOL only — projected ~+\$3.43/day vs current
~+\$3.19/day with just Option A.

Reversible: just remove ETH from PM_BLOCKED_COINS in .env and restart.
ETH still trades fully in morning hours (where its WR profile differs)."

git tag -a "pm-eth-block-apr28" -m "Block ETH in PM (data-driven)"
echo "[OK] Tagged: pm-eth-block-apr28"

git push origin demo-analytics-v1-apr23 2>&1 | tail -3 || echo "(push failed — ok)"
git push origin pm-eth-block-apr28 2>&1 | tail -3 || echo "(tag push failed — ok)"

echo ""
echo "=== Restart 15m bot (5m bot already excludes ETH) ==="
OLD_PID=$(pgrep -f "python3 -u run_bot.py" | head -1)
if [ -n "$OLD_PID" ]; then
    echo "Killing 15m bot PID $OLD_PID"
    kill -TERM "$OLD_PID"
    sleep 3
    if kill -0 "$OLD_PID" 2>/dev/null; then
        kill -9 "$OLD_PID"
        sleep 1
    fi
fi

setsid nohup python3 -u run_bot.py >> v3_bot.log 2>&1 < /dev/null &
disown
sleep 4

NEW_PID=$(pgrep -f "python3 -u run_bot.py" | head -1)
if [ -z "$NEW_PID" ]; then
    echo "[FAIL] Bot did not start"
    tail -20 v3_bot.log
    exit 1
fi

echo "[OK] New 15m bot PID: $NEW_PID"

echo ""
echo "=== 5m bot still alive (no restart needed) ==="
ps -o pid,etime,pcpu,pmem,cmd -p $(pgrep -f "python3 -u run_brain_5m.py") 2>/dev/null | head -3 || echo "WARNING: 5m bot not running"

echo ""
echo "=== Both bots ==="
pgrep -af "python3 -u run_b"
