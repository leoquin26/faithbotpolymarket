#!/usr/bin/env python3
"""
TREND_INVERT test (Jun-1 evening):

Hypothesis: in current crypto regime, the market is mean-reverting, not
trending. Backtest of 311 trades shows 71% WR if we flip every direction.

Implementation: when TREND_INVERT=on (env), negate the FINAL trend_score
right before the sigmoid. This makes the bot bet AGAINST momentum.

Safety: env-driven, easily reversible. Logs every inversion so we can
verify behavior.
"""
import re
from pathlib import Path

TARGET = Path("/home/ubuntu/v3-bot/predictor.py")
src = TARGET.read_text()

# Anchor: the line right before raw_prob = _sigmoid(trend_score * _steep)
old = """        _steep = float(os.getenv("SIGMOID_STEEPNESS", "2.0"))
        _bs_w = float(os.getenv("BS_WEIGHT", "0.50"))
        raw_prob = _sigmoid(trend_score * _steep)"""

new = """        _steep = float(os.getenv("SIGMOID_STEEPNESS", "2.0"))
        _bs_w = float(os.getenv("BS_WEIGHT", "0.50"))
        # Jun-1 evening: TREND_INVERT test. Hypothesis: market is mean-reverting,
        # bot's trend-following is inverted. Backtest shows 71% WR if flipped.
        # When TREND_INVERT=on, negate trend_score so bot bets against momentum.
        if os.getenv("TREND_INVERT", "off").lower() == "on":
            _orig_trend = trend_score
            trend_score = -trend_score
            logger.info(
                f"[TREND INVERT] {coin}: trend {_orig_trend:+.2f} -> {trend_score:+.2f} "
                f"(mean-revert mode active)"
            )
        raw_prob = _sigmoid(trend_score * _steep)"""

if old not in src:
    print("[ERROR] anchor not found verbatim. Aborting.")
    raise SystemExit(1)
if new in src:
    print("[skip] TREND_INVERT already in place.")
else:
    src = src.replace(old, new, 1)
    TARGET.write_text(src)
    print("[ok] TREND_INVERT logic added before sigmoid")

import subprocess
r = subprocess.run(["python3", "-m", "py_compile", str(TARGET)], capture_output=True, text=True)
if r.returncode != 0:
    print(f"[ERROR] syntax check failed:\n{r.stderr}")
    raise SystemExit(2)
print("[ok] syntax OK")
