#!/bin/bash
cd /home/ubuntu/v3-bot
echo "=== direct call is_good_trading_hour ==="
python3 <<'PYEOF'
import run_bot
ok, msg = run_bot.is_good_trading_hour()
print("can_trade:", ok)
print("reason  :", msg)
PYEOF

echo ""
echo "=== waiting 90s for periodic log print ==="
sleep 90

echo ""
echo "=== WEEKEND MODE / OFF HOURS lines from today ==="
tail -n 300 v3_bot.log | grep -E "WEEKEND MODE|OFF HOURS" | tail -5

echo ""
echo "=== bot pid ==="
pgrep -f "python3.*run_bot.py" | head -3

echo ""
echo "=== PM entry cap present? ==="
grep -n "PM ENTRY CAP" run_bot.py
grep -n "PM_ENTRY_MAX" config.py
