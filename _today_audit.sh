#!/bin/bash
cd /home/ubuntu/v3-bot

echo "=== bot status ==="
PID=$(pgrep -f "python3.*run_bot.py" | head -1)
if [ -n "$PID" ]; then
    ps -p "$PID" -o pid,etime,%cpu,%mem,cmd
else
    echo "BOT NOT RUNNING"
fi

echo ""
echo "=== today's log (from latest midnight rollover) ==="
ANCHOR=$(grep -nE "^00:00:0[0-5]" v3_bot.log | tail -1 | cut -d: -f1)
echo "anchor line: $ANCHOR"
TODAY_LINES=$(tail -n +"$ANCHOR" v3_bot.log)
echo "today total lines: $(echo "$TODAY_LINES" | wc -l)"

echo ""
echo "=== APPROVED / FILLED / WIN / LOSS today ==="
tail -n +"$ANCHOR" v3_bot.log | grep -E "APPROVED|FILLED|WIN |LOSS |LOSS BREAKER|MORNING CAP|MORNING STICKY|EXHAUST BLOCK|PM ENTRY CAP|WEEKEND MODE" | head -60

echo ""
echo "=== Block tally for today ==="
tail -n +"$ANCHOR" v3_bot.log | grep -oE "WARMUP|COLD START|CHEAP|WEAK TREND|LATE|FLIP GUARD|EXHAUST|CONSENSUS|MAX_POS|LOSS BREAKER|MORNING CAP|MORNING STICKY|EXHAUST BLOCK|PM ENTRY CAP|WEEKEND MODE|CLOB RANGE" | sort | uniq -c | sort -rn

echo ""
echo "=== signal-level look in window after the 2 losses ==="
tail -n +"$ANCHOR" v3_bot.log | awk '/LOSS MORNING|LOSS PM/{c++; print NR": "$0} c>=2 && /^[0-9]/{ if(NR-l<3000){print NR": "$0}; l=NR }' | head -40
