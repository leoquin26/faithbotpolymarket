#!/bin/bash
# Dedupe 15m / 5m python bots (keep lowest PID). Run after deploys if pgrep shows extras.
set -u
cd /home/ubuntu/v3-bot || exit 1

# 15m
mapfile -t P15 < <(pgrep -f '^python3 -u run_bot\.py$' 2>/dev/null | sort -n)
if [ "${#P15[@]}" -gt 1 ]; then
  echo "[ensure-single] 15m: keeping PID ${P15[0]}, killing ${P15[@]:1}"
  for pid in "${P15[@]:1}"; do kill "$pid" 2>/dev/null || true; done
  sleep 2
  for pid in "${P15[@]:1}"; do kill -9 "$pid" 2>/dev/null || true; done
fi

# 5m (absolute path in argv)
mapfile -t P5 < <(pgrep -f '^python3 -u /home/ubuntu/v3-bot/run_brain_5m\.py$' 2>/dev/null | sort -n)
if [ "${#P5[@]}" -gt 1 ]; then
  echo "[ensure-single] 5m: keeping PID ${P5[0]}, killing ${P5[@]:1}"
  for pid in "${P5[@]:1}"; do kill "$pid" 2>/dev/null || true; done
  sleep 2
  for pid in "${P5[@]:1}"; do kill -9 "$pid" 2>/dev/null || true; done
fi

echo "[ensure-single] 15m: $(pgrep -f '^python3 -u run_bot\.py$' | tr '\n' ' ')"
echo "[ensure-single] 5m: $(pgrep -f '^python3 -u /home/ubuntu/v3-bot/run_brain_5m\.py$' | tr '\n' ' ')"
