#!/bin/bash
cd /home/ubuntu/v3-bot
ANCHOR=$(grep -nE "^00:00:0[0-5]" v3_bot.log | tail -1 | cut -d: -f1)

echo "=== 1) The 2 fills today (entry side) ==="
tail -n +"$ANCHOR" v3_bot.log | grep -E "FILLED|APPROVED" | grep -v WINDOW | head -10

echo ""
echo "=== 2) RESOLUTION lines for both losses ==="
tail -n +"$ANCHOR" v3_bot.log | grep -E "RESOLVE POLY|LOSS |WIN " | head -10

echo ""
echo "=== 3) Activity after 10:00 (post-2nd loss) until now ==="
tail -n +"$ANCHOR" v3_bot.log | awk '/^1[0-2]:/' | grep -E "LOSS BREAKER|MORNING|EXPENSIVE|EXHAUST BLOCK|EXHAUST DAMP|FLIP GUARD|APPROVED|FILLED|P2|P3|P1|RESET" | head -50

echo ""
echo "=== 4) Block reasons from 10:15 to NOW ==="
tail -n +"$ANCHOR" v3_bot.log | awk '/^1[0-9]:/' | grep -oE "EXPENSIVE|EXHAUST BLOCK|FLIP GUARD|WEAK TREND|COLD START|WARMUP|CONSENSUS|CHEAP|LATE|MORNING|LOSS BREAKER|LOW PROB|NO ASK|MAX_POS" | sort | uniq -c | sort -rn

echo ""
echo "=== 5) Last 25 INFO/WARNING lines ==="
tail -n +"$ANCHOR" v3_bot.log | grep -vE "DEBUG" | tail -25

echo ""
echo "=== 6) Any signals during 12:00-12:31 (MORNING P3 today) ==="
tail -n +"$ANCHOR" v3_bot.log | awk '/^12:[0-3][0-9]/' | grep -E "MORNING P3|APPROVED|EXHAUST|FLIP|WEAK TREND|signal|EXPENSIVE" | head -30

echo ""
echo "=== 7) Bankroll setting ==="
grep -E "BANKROLL_BALANCE" .env
