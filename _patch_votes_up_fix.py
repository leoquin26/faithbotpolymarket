#!/usr/bin/env python3
from pathlib import Path
from datetime import datetime
import shutil

p = Path("/home/ubuntu/v3-bot/predictor.py")
text = p.read_text(encoding="utf-8")
old = """        else:
            is_up = combined_prob >= 0.5
            direction = "UP" if is_up else "DOWN"
            win_prob = combined_prob if is_up else (1.0 - combined_prob)
        ask = up_ask if is_up else down_ask"""
new = """        else:
            is_up = combined_prob >= 0.5
            direction = "UP" if is_up else "DOWN"
            win_prob = combined_prob if is_up else (1.0 - combined_prob)
            votes_up = votes_down = 0
            vote_dir = None
        ask = up_ask if is_up else down_ask"""
if old not in text:
    raise SystemExit("block not found")
shutil.copy2(p, p.with_suffix(f".bak_votes_{datetime.now().strftime('%H%M%S')}"))
p.write_text(text.replace(old, new, 1), encoding="utf-8")
print("OK")
