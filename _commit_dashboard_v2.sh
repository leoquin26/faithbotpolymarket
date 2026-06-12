#!/bin/bash
set -e
cd /home/ubuntu/v3-bot

echo "=== Diff ==="
git diff --stat dashboard_v3/clob_adapter.py

echo ""
echo "=== Commit + tag ==="
git add dashboard_v3/clob_adapter.py
git commit -m "CLOB V2 migration apr28: dashboard adapter to py-clob-client-v2

Mirrors the order_manager.py V2 swap (clob-v2-migration-apr28).
Dashboard only does read-only ops (get_trades, get_positions,
get_balances) - all exist in V2 SDK with same signatures.
Defensive hasattr() guards on get_positions/get_balances make
the code tolerant of any V2 method renames.

Verified live: get_all_trades() against V2 returns the same shape
(id, market, asset_id, side, size, fee_rate_bps, price, status,
match_time)."

git tag -a "clob-v2-dashboard-apr28" -m "Dashboard adapter on V2 SDK"
echo "[OK] Tagged: clob-v2-dashboard-apr28"

echo ""
echo "=== Push ==="
git push origin demo-analytics-v1-apr23 || echo "(branch push failed - ok)"
git push origin clob-v2-dashboard-apr28 || echo "(tag push failed - ok)"

echo ""
echo "=== Restart dashboard ==="
OLD_DASH=$(pgrep -f "python3 -m dashboard_v3.app" | head -1 || true)
if [ -n "$OLD_DASH" ]; then
    echo "Killing dashboard PID $OLD_DASH"
    kill -TERM "$OLD_DASH"
    sleep 3
    if kill -0 "$OLD_DASH" 2>/dev/null; then
        echo "Force kill"
        kill -9 "$OLD_DASH"
        sleep 1
    fi
else
    echo "(no dashboard running)"
fi

echo ""
echo "Starting dashboard..."
cd /home/ubuntu/v3-bot
setsid nohup python3 -m dashboard_v3.app >> dashboard.log 2>&1 < /dev/null &
disown
sleep 5

NEW_DASH=$(pgrep -f "python3 -m dashboard_v3.app" | head -1)
if [ -z "$NEW_DASH" ]; then
    echo "[FAIL] Dashboard did not start - check dashboard.log"
    tail -20 dashboard.log
    exit 1
fi
echo "[OK] Dashboard PID: $NEW_DASH"
ps -p "$NEW_DASH" -o pid,etime,pcpu,pmem,cmd

echo ""
echo "=== Last 15 dashboard log lines ==="
tail -15 dashboard.log
