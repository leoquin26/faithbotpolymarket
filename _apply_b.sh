#!/bin/bash
set -e
cd /home/ubuntu/v3-bot
python3 _patch_override_no_haircut.py
echo "=== syntax check ==="
python3 -m py_compile run_bot.py && echo "OK"
echo "=== verify ==="
grep -n "_was_overridden" run_bot.py | head -5
echo "=== git ==="
git add run_bot.py
git commit -m "fix(exhaust override): skip probability haircut when override fired

The original DAMPEN handler shaves probability by 0.85x — fine for soft
vetos. But when ABSTAIN was overridden because the signal was A-tier
(prob>=82% edge>=18%), the haircut takes it to ~70% and the next
MORNING P3 gate (prob>=78%) blocks the trade. We just sabotaged
ourselves. Override path now keeps prob/edge intact and only halves
the bet size via the _dampened flag."
git tag -a override-no-haircut-apr27 -m "Override: skip prob haircut" || true
git push origin HEAD
git push origin override-no-haircut-apr27 || true
echo "=== restart ==="
OLD=$(pgrep -f "python3.*run_bot.py" | head -1)
echo "old pid: $OLD"
[ -n "$OLD" ] && kill "$OLD" && sleep 3
ps -p "$OLD" >/dev/null 2>&1 && kill -9 "$OLD" && sleep 1
setsid nohup python3 -u run_bot.py >> v3_bot.log 2>&1 < /dev/null & disown $! 2>/dev/null
sleep 5
echo "new pid: $(pgrep -f 'python3.*run_bot.py' | head -1)"
echo "=== last 8 log lines ==="
tail -n 8 v3_bot.log
