#!/bin/bash
# CleanBot watchdog — cron every 5 min. Restarts crashed processes (bot, dash, scout).
# NEVER starts a second instance (strict count==0 check). Respects a maintenance pause:
#   touch ~/v3-bot/.watchdog_pause   -> watchdog does nothing (remove to re-arm)
cd /home/ubuntu/v3-bot || exit 0
LOG=/home/ubuntu/v3-bot/logs/watchdog.log
[ -f .watchdog_pause ] && exit 0

restart_if_dead () {
    local script="$1" start_cmd="$2"
    local n
    # anchored: match ONLY the real python process, not launcher wrappers whose
    # bash -c command line contains the same string
    n=$(pgrep -fc "^python3 -u $script\$")
    if [ "$n" -eq 0 ]; then
        echo "$(date '+%F %T') [WATCHDOG] $script DOWN -> restarting" >> "$LOG"
        setsid nohup bash -c "$start_cmd" >/dev/null 2>&1 < /dev/null &
        sleep 3
        n=$(pgrep -fc "^python3 -u $script\$")
        echo "$(date '+%F %T') [WATCHDOG] $script post-restart count=$n" >> "$LOG"
    fi
}

restart_if_dead "clean_bot.py"     "cd /home/ubuntu/v3-bot && exec python3 -u clean_bot.py >> clean_bot.log 2>&1"
restart_if_dead "cleanbot_dash.py" "cd /home/ubuntu/v3-bot && exec python3 -u cleanbot_dash.py >> dash.log 2>&1"
restart_if_dead "daily_scout.py"   "cd /home/ubuntu/v3-bot && exec python3 -u daily_scout.py >> logs/daily_scout_stdout.log 2>&1"
restart_if_dead "hourly_capture.py" "cd /home/ubuntu/v3-bot && exec python3 -u hourly_capture.py >> hourly_capture.log 2>&1"
restart_if_dead "quantum_dash.py" "cd /home/ubuntu/v3-bot && exec python3 -u quantum_dash.py >> quantum_dash.log 2>&1"
