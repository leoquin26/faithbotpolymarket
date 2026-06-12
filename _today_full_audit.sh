#!/bin/bash
# Full audit of today's bot activity. Goal: explain why trades are scarce + losing.
cd /home/ubuntu/v3-bot

echo "================================================================"
echo "1. ALL FILLED orders today (since 09:00 Lima)"
echo "================================================================"
# Find the most recent midnight rollover and use only entries after it
LAST_MIDNIGHT_LINE=$(grep -n "00:00:0[0-9]" v3_bot.log | tail -1 | cut -d: -f1)
if [ -z "$LAST_MIDNIGHT_LINE" ]; then
    LAST_MIDNIGHT_LINE=1
fi
# Grab everything from there
tail -n +"$LAST_MIDNIGHT_LINE" v3_bot.log > /tmp/today.log
wc -l /tmp/today.log
echo ""
grep -E "FILLED|FIRED" /tmp/today.log | grep -vE "^\\[" | head -30

echo ""
echo "================================================================"
echo "2. ALL block reasons today (count by category)"
echo "================================================================"
grep -oE '\[[A-Z][A-Z _-]+\]' /tmp/today.log | sort | uniq -c | sort -rn | head -25

echo ""
echo "================================================================"
echo "3. EXHAUST OVERRIDE attempts today"
echo "================================================================"
grep -E "EXHAUST OVERRIDE|EXHAUST DAMPEN|EXHAUST BLOCK|EXHAUST.*ABSTAIN" /tmp/today.log | tail -30

echo ""
echo "================================================================"
echo "4. KELLY sizing entries (every fill attempt)"
echo "================================================================"
grep -E "\[KELLY\]" /tmp/today.log | tail -15

echo ""
echo "================================================================"
echo "5. LOW PROB / WEAK TREND blocks (signals that almost made it)"
echo "================================================================"
grep -E "\[LOW PROB\]|\[WEAK TREND\]|\[NO ASK\]" /tmp/today.log | wc -l
echo "  (total noise entries)"
echo ""
echo "Top signals filtered for being TOO CLOSE to threshold:"
grep -E "\[LOW PROB\] (BTC|ETH|SOL|XRP) (UP|DOWN): prob=7[3-9]" /tmp/today.log | tail -15

echo ""
echo "================================================================"
echo "6. RESOLVED trades today (wins/losses)"
echo "================================================================"
grep -E "WIN|LOSS|RESOLVED" /tmp/today.log | grep -E "(BTC|ETH|SOL|XRP)" | tail -20

echo ""
echo "================================================================"
echo "7. Bot restart history today (each restart = new REST poller line)"
echo "================================================================"
grep -E "REST.*price poller started" /tmp/today.log

echo ""
echo "================================================================"
echo "8. Phase windows (MORNING / PM)"
echo "================================================================"
grep -E "MORNING|PM SESSION|Phase" /tmp/today.log | tail -10

echo ""
echo "================================================================"
echo "9. TODAY'S P&L summary"
echo "================================================================"
TOTAL_FILLS=$(grep -E "^\\[OK\\] FILLED" /tmp/today.log | wc -l)
echo "Total fills today: $TOTAL_FILLS"
grep -E "^\\[OK\\] FILLED" /tmp/today.log | tail -10
