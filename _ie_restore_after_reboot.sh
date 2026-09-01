#!/usr/bin/env bash
# Post-reboot restore for the Ireland box (setsid-launched processes do not
# survive a reboot; crons do). Idempotent: launches only what is not running.
# Usage: ssh ... 'bash ~/v3-bot/_ie_restore_after_reboot.sh'
cd /home/ubuntu/v3-bot || exit 1
rm -f .watchdog_pause
launch() {  # name.py [logfile]
  local script="$1" log="${2:-${1%.py}_run.log}"
  if pgrep -f "python3 -u $script" >/dev/null; then echo "ok    $script (running)"; return; fi
  (setsid nohup python3 -u "$script" >> "$log" 2>&1 < /dev/null &)
  sleep 2
  pgrep -f "python3 -u $script" >/dev/null && echo "START $script" || echo "FAIL  $script (see $log)"
}
launch hourly_capture.py        # 1H collector -> hourly_research.csv
launch late_book_capture.py     # 15m 1Hz book -> late_book.jsonl
launch clean_bot.py             # 15m scan-only (engines OFF, bankroll mirror)
launch late_shadow.py           # the clock experiment ($0)
launch quantum_dash.py          # :8096
launch data_control.py          # :8097
launch cleanbot_dash.py         # :8095
launch cf_notify.py
launch daily_scout.py
launch arb_monitor.py
# cloudflared quick tunnels (URLs rotate on restart)
tunnel() {  # port logfile
  if pgrep -f "tunnel --no-autoupdate --url http://localhost:$1" >/dev/null; then echo "ok    tunnel :$1"; return; fi
  (setsid nohup ./cloudflared tunnel --no-autoupdate --url "http://localhost:$1" > "$2" 2>&1 < /dev/null &)
  echo "START tunnel :$1 -> $2"
}
tunnel 8096 cf_dash.log
tunnel 8097 cf_data.log
tunnel 8095 cf_clean.log
sleep 8
echo "--- tunnel URLs ---"
for f in cf_dash.log cf_data.log cf_clean.log; do echo -n "$f: "; grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$f" 2>/dev/null | head -1 || echo "(pending)"; done
echo "--- mem ---"; free -m | head -2
echo "--- crons ---"; crontab -l | grep -cE 'watchdog|gate|clock|balance|nightly|edge_watch|seat_scan'
