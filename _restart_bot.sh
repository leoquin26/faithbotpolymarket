#!/bin/bash
# Canonical CleanBot restart dance (v1.60.1). ALWAYS use this — never ad-hoc kills.
# Encodes the hard-earned rules:
#  - anchored pkill (unanchored patterns match the ssh shell itself → double-bot incident Jul 16)
#  - waits out the late fixed-time eval slot (t_rem 195→150s ≈ sec 690-760 of each 900s window)
#    so a restart never re-evaluates a window the old process already decided
#  - watchdog paused during the swap; verifies exactly 1 process after
set -e
cd ~/v3-bot

S=$(( $(date +%s) % 900 ))
if [ "$S" -ge 690 ] && [ "$S" -le 760 ]; then
    W=$((765 - S))
    echo "[restart] inside late-eval slot — waiting ${W}s"
    sleep "$W"
fi

touch .watchdog_pause
pkill -f "^python3 -u clean_bot.py$" || true
sleep 2
REMAIN=$(pgrep -fc "^python3 -u clean_bot.py$" || true)
if [ -n "$REMAIN" ] && [ "$REMAIN" != "0" ]; then
    echo "[restart] ERROR: $REMAIN old process(es) survived — aborting (watchdog still paused!)"
    exit 1
fi

setsid nohup python3 -u clean_bot.py </dev/null >>clean_bot.log 2>&1 &
sleep 5
COUNT=$(pgrep -fc "^python3 -u clean_bot.py$" || echo 0)
rm -f .watchdog_pause
echo "[restart] procs=$COUNT (must be 1) | watchdog unpaused"
tail -n 2 clean_bot.log
[ "$COUNT" = "1" ]
