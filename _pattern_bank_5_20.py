"""Build a pattern bank from 5/20 trades.

For each WIN and LOSS on 5/20, pull:
  - The [SIGNAL] line(s) that led to it
  - The [EXHAUST] line
  - The [KELLY] line (size, conditions)
  - The [FILLED] line
  - The [WIN/LOSS] resolution

Output: side-by-side comparison of W vs L on similar setups.
"""
import re
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timedelta

LOG = Path("/home/ubuntu/v3-bot/logs/bot_2026-05-20.log")

TS_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2})")

def parse_ts(line):
    m = TS_RE.match(line)
    if not m:
        return None
    h, mi, s = map(int, m.groups())
    return h * 3600 + mi * 60 + s

def fmt_ts(s):
    h = s // 3600
    m = (s % 3600) // 60
    se = s % 60
    return f"{h:02d}:{m:02d}:{se:02d}"

# Patterns we care about
RES_RE = re.compile(r"\[(WIN|LOSS)\s+(MORNING|PM)\]\s+(\w+)\s+(UP|DOWN)\s+\|\s+([+\-\$\d.]+)\s+\|\s+Entry:\s+(\d+)c\s+x(\d+)")
FIL_RE = re.compile(r"\[FILLED\]\s+(\w+)\s+(UP|DOWN)\s+\|\s+(\d+)\s+shares\s+@\s+(\d+)c\s+=\s+\$([\d.]+)")
ORD_RE = re.compile(r"\[ORDER\]\s+(\w+)\s+(UP|DOWN).*?@\s+(\d+)c\s+\|\s+(\d+)\s+shares.*?cost=\$([\d.]+).*?Edge\s+([\d.]+)%")
KEL_RE = re.compile(r"\[KELLY\]\s+(\w+):\s+f\*=([\d.]+).*?tier=(\w+)\([\d%]+\).*?size=\$([\d.]+).*?\(p=(\d+)%\s+b=([\d.]+)\s+edge=([\d.]+)%\s+entry=([\d.]+)")
SIG_RE = re.compile(r"\[SIGNAL\]\s+(\w+)\s+(UP|DOWN)\s+\|\s+Prob=(\d+)%\s+\|\s+Ask=(\d+)c\s+\|\s+Edge=([\d.]+)%\s+\|\s+Trend=([+\-][\d.]+)\s+Dist=([+\-][\d.]+)%\s+ROC60=([+\-][\d.]+)bps.*?σ=([\d.e\-]+)\s+T=(\d+)s")
EXH_RE = re.compile(r"\[EXHAUST\]\s+(\w+)\s+(UP|DOWN)\s+@\s+(\d+)c\s+\|\s+score=([\d.]+)")
COM_RE = re.compile(r"\[COMMIT\]\s+(\w+)\s+(UP|DOWN)\s+\|\s+(TRENDING|CHOPPY)\s+\|\s+history=([^|]+)\|")

# Parse the whole log into time-indexed lines
print(f"Reading {LOG} ...")
lines = []
with LOG.open(encoding="utf-8", errors="ignore") as fh:
    for line in fh:
        if "[5M]" in line:
            continue
        ts = parse_ts(line)
        if ts is not None:
            lines.append((ts, line.rstrip()))

print(f"Loaded {len(lines)} non-5M lines.")

# Find all resolutions (wins/losses)
trades = []
for ts, line in lines:
    m = RES_RE.search(line)
    if m:
        result, phase, coin, direction, amt_str, entry_c, shares = m.groups()
        amt = float(amt_str.replace("+", "").replace("-", "").replace("$", ""))
        signed_amt = amt if result == "WIN" else -amt
        trades.append({
            "ts": ts,
            "result": result,
            "phase": phase,
            "coin": coin,
            "direction": direction,
            "pnl": signed_amt,
            "entry_c": int(entry_c),
            "shares": int(shares),
        })

print(f"Found {len(trades)} trades.\n")

# For each trade, find the FILLED that matches (most recent before res)
# and the SIGNAL/KELLY/EXHAUST/COMMIT before that FILLED
def find_back(trades, lines):
    for t in trades:
        target_entry = t["entry_c"]
        target_coin = t["coin"]
        target_dir = t["direction"]
        target_shares = t["shares"]
        # FILLED must precede resolution
        fil_idx = None
        for i in range(len(lines) - 1, -1, -1):
            ts, line = lines[i]
            if ts > t["ts"]:
                continue
            mf = FIL_RE.search(line)
            if mf and mf.group(1) == target_coin and mf.group(2) == target_dir and \
               int(mf.group(3)) == target_shares and int(mf.group(4)) == target_entry:
                fil_idx = i
                break
        if fil_idx is None:
            t["context"] = "FILLED not found"
            continue
        # Look back up to ~30 seconds for SIGNAL/KELLY/EXHAUST/COMMIT/ORDER
        fil_ts = lines[fil_idx][0]
        ctx = {
            "filled": lines[fil_idx][1],
            "signal": None, "kelly": None, "exhaust": None, "commit": None, "order": None,
        }
        for j in range(fil_idx - 1, max(0, fil_idx - 80), -1):
            line = lines[j][1]
            if ctx["order"] is None:
                mo = ORD_RE.search(line)
                if mo and mo.group(1) == target_coin and mo.group(2) == target_dir:
                    ctx["order"] = line
                    continue
            if ctx["kelly"] is None:
                mk = KEL_RE.search(line)
                if mk and mk.group(1) == target_coin:
                    ctx["kelly"] = line
                    continue
            if ctx["exhaust"] is None:
                me = EXH_RE.search(line)
                if me and me.group(1) == target_coin and me.group(2) == target_dir:
                    ctx["exhaust"] = line
                    continue
            if ctx["signal"] is None:
                ms = SIG_RE.search(line)
                if ms and ms.group(1) == target_coin and ms.group(2) == target_dir:
                    ctx["signal"] = line
            if ctx["commit"] is None:
                mc = COM_RE.search(line)
                if mc and mc.group(1) == target_coin and mc.group(2) == target_dir:
                    ctx["commit"] = line
            if all(ctx.values()):
                break
        t["context"] = ctx

find_back(trades, lines)

# Pretty print
print("=" * 110)
print(f"{'#':<3} {'time':<10} {'result':<5} {'coin':<4} {'dir':<5} {'phase':<8} {'PnL':>8} {'entry':>6} {'shares':>7}")
print("=" * 110)

wins = [t for t in trades if t["result"] == "WIN"]
losses = [t for t in trades if t["result"] == "LOSS"]

for i, t in enumerate(trades, 1):
    print(f"{i:<3} {fmt_ts(t['ts']):<10} {t['result']:<5} {t['coin']:<4} {t['direction']:<5} {t['phase']:<8} {t['pnl']:>+7.2f} {t['entry_c']:>5}c {t['shares']:>7}")

print()
print(f"Total: {len(wins)} W / {len(losses)} L / Net: ${sum(t['pnl'] for t in trades):+.2f}")
print()

# Now per-trade signal context
print("=" * 110)
print("DETAILED SIGNAL CONTEXT PER TRADE")
print("=" * 110)
for i, t in enumerate(trades, 1):
    print(f"\n--- Trade #{i} {fmt_ts(t['ts'])} {t['result']} {t['coin']} {t['direction']} @{t['entry_c']}c x{t['shares']} ({t['pnl']:+.2f}) ---")
    ctx = t.get("context")
    if isinstance(ctx, str):
        print(f"  {ctx}")
        continue
    for k in ("signal", "commit", "exhaust", "kelly", "order", "filled"):
        v = ctx.get(k)
        if v:
            # truncate timestamp prefix
            short = v.split("INFO     | ")[-1] if "INFO     | " in v else v
            print(f"  [{k:7s}] {short[:140]}")
        else:
            print(f"  [{k:7s}] -- not found --")
