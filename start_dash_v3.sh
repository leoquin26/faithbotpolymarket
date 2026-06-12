#!/bin/bash
cd /home/ubuntu/v3-bot
pkill -f 'dashboard_v3.app' 2>/dev/null
sleep 1
setsid nohup python3 -m dashboard_v3.app > /tmp/dashboard_v3.log 2>&1 </dev/null &
disown
