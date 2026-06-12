#!/bin/bash
# Audit gate-counts for any 15m or 5m log file.
# Usage: ./audit_overblock.sh logs/bot_2026-05-08.log [...more files]
for F in "$@"; do
  echo "=== $F ==="
  if [ ! -f "$F" ]; then echo "  (missing)"; continue; fi
  total=$(wc -l <"$F")
  echo "  total_lines=$total"
  echo "  --- gate counts ---"
  for tag in \
    "WEAK TREND" "FLAT PRICE" "FEW TICKS" "FLIP GUARD" "RECENT FLIP" \
    "EXPENSIVE" "CHEAP" "NO ASK" "TRAP BAND" "TRAP BAND OVERRIDE" \
    "EXHAUST] " "EXHAUST OVERRIDE" "EXHAUST BLOCK" "EXHAUST DAMPEN" \
    "CONSENSUS" "DIR LOCK" "LATE WHIPSAW" "COLD START" "TOO LATE" \
    "WARMUP" "MORNING P1\\]" "MORNING P3\\]" "MORNING P1 TRADE" \
    "MORNING P3 TRADE" "PM COIN BLOCK" "PM ENTRY CAP" "POST-LOSS COOLDOWN" \
    "DAILY LOSS" "CORR DOUBLE-UP" "CORR DIVERGE" "ASK MOVED" \
    "SIGNAL\\]" "COMMIT\\]" "\\[ORDER\\]" "\\[FILLED\\]" \
    "\\[KELLY\\]" "B-TIER CAP" \
    "\\[WIN " "\\[LOSS " "RESOLVE POLY" \
  ; do
    n=$(grep -cE "\[$tag" "$F" 2>/dev/null || echo 0)
    n=${n:-0}
    if [ "$n" -gt 0 ]; then
      printf "  %-22s  %s\n" "$tag" "$n"
    fi
  done
done
