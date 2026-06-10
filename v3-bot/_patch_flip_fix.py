#!/usr/bin/env python3
"""
Fix the EXHAUST FLIP logic in run_bot.py.

Before:
    FLIP inverted probability (76% -> 24%) but did NOT update entry_price.
    Result: signal becomes "UP @ 67c with 24% prob, edge=-43%" -> blocked
            by MIN_WIN_PROB, MORNING gates, etc. -> never trades.

After:
    Keep original probability (the flip means "trust the OPPOSITE outcome
    with the same conviction"). Update entry_price to contra ask (1 - ask),
    which is the actual price for the flipped direction in a binary market.
    Mark as _was_overridden so downstream DAMPEN doesn't shave the prob.
"""
from pathlib import Path

TARGET = Path("/home/ubuntu/v3-bot/run_bot.py")
src = TARGET.read_text()

old = '''                        if _act == "FLIP":
                            _orig = _p.direction
                            _p.direction = "DOWN" if _p.direction == "UP" else "UP"
                            _p.probability = 1.0 - _p.probability
                            _entry = _p.entry_price if _p.entry_price > 0.05 else _p.poly_price
                            _p.edge = _p.probability - _entry
                            logger.info(f"[EXHAUST FLIP] {_p.coin} {_orig}->{_p.direction}")'''

new = '''                        if _act == "FLIP":
                            # Jun-1 FIX: previous code inverted probability (76%->24%) but kept
                            # entry_price unchanged -> downstream gates killed every flipped signal.
                            # New behavior: keep original probability (flip = trust opposite outcome
                            # with same conviction), and update entry to contra ask (1 - original).
                            _orig = _p.direction
                            _orig_entry = _p.entry_price if _p.entry_price > 0.05 else _p.poly_price
                            _contra_entry = max(0.01, min(0.99, 1.0 - _orig_entry))
                            _p.direction = "DOWN" if _p.direction == "UP" else "UP"
                            _p.entry_price = _contra_entry
                            _p.poly_price = _contra_entry
                            _p.edge = _p.probability - _contra_entry
                            _was_overridden = True  # protect prob from DAMPEN downstream
                            logger.info(
                                f"[EXHAUST FLIP] {_p.coin} {_orig}@{_orig_entry*100:.0f}c -> "
                                f"{_p.direction}@{_contra_entry*100:.0f}c | "
                                f"prob={_p.probability*100:.0f}% edge={_p.edge*100:.0f}%"
                            )'''

if old not in src:
    print("[ERROR] expected FLIP block not found verbatim. Aborting.")
    raise SystemExit(1)

if new in src:
    print("[skip] new FLIP block already present.")
else:
    src = src.replace(old, new, 1)
    TARGET.write_text(src)
    print("[ok] patched FLIP block in run_bot.py")

import subprocess
r = subprocess.run(["python3", "-m", "py_compile", str(TARGET)], capture_output=True, text=True)
if r.returncode != 0:
    print(f"[ERROR] syntax check failed:\n{r.stderr}")
    raise SystemExit(2)
print("[ok] syntax OK")
