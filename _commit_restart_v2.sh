#!/bin/bash
set -e
cd /home/ubuntu/v3-bot

echo "=== Diff summary ==="
git diff --stat order_manager.py

echo ""
echo "=== Adding + committing ==="
git add order_manager.py
git commit -m "CLOB V2 migration apr28: switch order_manager.py to py-clob-client-v2

Polymarket migrated to CLOB V2 at 2026-04-28 11:00 UTC. V1 SDK orders
now return 400 'order_version_mismatch' (verified in v3_bot.log).

Pre-flight checks passed:
- V2 SDK init works with existing kwargs (host, key, chain_id,
  signature_type, funder)
- OrderArgs(price, size, side, token_id) is V2-compatible (default
  expiration=0, builder_code/metadata default to BYTES32_ZERO)
- Wallet auto-wrapped to pUSD: balance \$155.75
- V2 Exchange (0xE111...996B) allowance already set
- EIP-712 signing test produces SignedOrderV2 with timestamp,
  metadata, builder fields populated correctly
- get_clob_market_info() returns mts=0.01, mos=5, fee=7.2% taker-only

This patch is import-only; no logic changes."

git tag -a "clob-v2-migration-apr28" -m "Migrated to py-clob-client-v2 1.0.0"
echo "[OK] Tagged: clob-v2-migration-apr28"

echo ""
echo "=== Pushing to demo branch ==="
git push origin demo-analytics-v1-apr23 || echo "(push failed, ok)"
git push origin clob-v2-migration-apr28 || echo "(tag push failed, ok)"

echo ""
echo "=== Confirming no bot is running ==="
pgrep -af 'python3 -u run_bot.py' || echo "(no bot running — good)"

echo ""
echo "=== Starting fresh bot on V2 ==="
setsid nohup python3 -u run_bot.py >> v3_bot.log 2>&1 < /dev/null &
disown
sleep 4

NEW_PID=$(pgrep -f 'python3 -u run_bot.py' | head -1)
if [ -z "$NEW_PID" ]; then
    echo "[FAIL] Bot did not start — check v3_bot.log"
    tail -30 v3_bot.log
    exit 1
fi
echo "[OK] New PID: $NEW_PID"
ps -p "$NEW_PID" -o pid,etime,pcpu,pmem,cmd

echo ""
echo "=== Last 20 log lines ==="
tail -20 v3_bot.log
