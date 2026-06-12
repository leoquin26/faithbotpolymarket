#!/usr/bin/env python3
"""
Bug fixes (Jun 1):
  - P2 EARLY-CONV: add HIGH-EDGE bypass clause. Was blocking 200+ high-edge
    signals per month because bypass list didn't include "any signal with
    huge edge". Math says high edge = strong opportunity regardless of trend.
  - V8 WHIPSAW: raise swing threshold from 12c to 20c via env. 12c was
    calibrated for low-vol markets; current crypto regime has 15-30c swings
    as baseline. Half of V8 blocks (130/265 last 30d) are at the 10-19c
    swing tier which is normal market noise.

Both env-driven for safe rollback.
"""
import re
from pathlib import Path

TARGET = Path("/home/ubuntu/v3-bot/predictor.py")
src = TARGET.read_text()

# ── Fix P2: add high-edge bypass ──
old_p2 = """                _p2_keep = (
                    (abs(trend_score) >= 1.5 and edge >= 0.10)
                    or (abs(trend_score) >= 2.0)
                    or (ask >= 0.65)
                    # May 13: allow ultra-high-probability early entries even when
                    # |trend| is 1.1-1.4 (was blocking e.g. BTC DOWN 83% / 23% edge).
                    or (win_prob >= 0.82 and edge >= 0.20)
                )"""

new_p2 = """                _p2_keep = (
                    (abs(trend_score) >= 1.5 and edge >= 0.10)
                    or (abs(trend_score) >= 2.0)
                    or (ask >= 0.65)
                    # May 13: allow ultra-high-probability early entries even when
                    # |trend| is 1.1-1.4 (was blocking e.g. BTC DOWN 83% / 23% edge).
                    or (win_prob >= 0.82 and edge >= 0.20)
                    # Jun 1 BUG FIX: high-edge bypass. Was blocking 200+ signals/mo
                    # with edge 15-26% just because |trend| < 1.5. Math says huge
                    # edge = great opportunity regardless of trend strength.
                    or (edge >= float(os.getenv("P2_HIGH_EDGE_BYPASS", "0.15")))
                )"""

if old_p2 not in src:
    print("[ERROR] P2 bypass block not found verbatim. Aborting.")
    raise SystemExit(1)
if new_p2 in src:
    print("[skip] P2 high-edge bypass already present.")
else:
    src = src.replace(old_p2, new_p2, 1)
    print("[ok] P2 EARLY-CONV: added HIGH-EDGE bypass")

# ── Fix V8: raise swing threshold via env ──
old_v8 = "                if _v8_swing >= 0.12:"
new_v8 = "                if _v8_swing >= float(os.getenv(\"V8_SWING_MIN\", \"0.12\")):"

if old_v8 not in src:
    print("[ERROR] V8 swing check not found verbatim. Aborting.")
    raise SystemExit(2)
if new_v8 in src:
    print("[skip] V8 env-driven swing threshold already present.")
else:
    src = src.replace(old_v8, new_v8, 1)
    print("[ok] V8 WHIPSAW: swing threshold env-driven")

TARGET.write_text(src)

import subprocess
r = subprocess.run(["python3", "-m", "py_compile", str(TARGET)], capture_output=True, text=True)
if r.returncode != 0:
    print(f"[ERROR] syntax check failed:\n{r.stderr}")
    raise SystemExit(3)
print("[ok] syntax OK")
