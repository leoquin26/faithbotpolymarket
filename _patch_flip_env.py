#!/usr/bin/env python3
"""
Surgical patch: make TH_FLIP env-driven.

Changes:
  exhaustion_detector.py:
    - Add `import os` if not present
    - Replace `TH_FLIP = 0.70` with env-driven version

Safe to run multiple times (idempotent).
"""
import re
from pathlib import Path

TARGET = Path("/home/ubuntu/v3-bot/exhaustion_detector.py")
src = TARGET.read_text()

if "import os" not in src.split("from loguru")[0]:
    src = src.replace("import time\n", "import time\nimport os\n", 1)
    print("[ok] added `import os`")
else:
    print("[skip] `import os` already present")

old = "TH_FLIP = 0.70"
new = ("TH_FLIP = float(os.getenv(\"EXHAUST_TH_FLIP\", \"0.70\"))"
       "  # was 0.70; lowered via env after Jun-1 backtest (311 trades, 71% flip-WR in 0.50-0.70 zone)")

if old in src and new not in src:
    src = src.replace(old, new, 1)
    print(f"[ok] replaced TH_FLIP definition")
elif new in src:
    print("[skip] env-driven TH_FLIP already in place")
else:
    print(f"[ERROR] expected line not found: '{old}'")
    raise SystemExit(1)

TARGET.write_text(src)

import subprocess
r = subprocess.run(["python3", "-m", "py_compile", str(TARGET)], capture_output=True, text=True)
if r.returncode != 0:
    print(f"[ERROR] syntax check failed:\n{r.stderr}")
    raise SystemExit(2)
print("[ok] syntax check passed")
print(f"[done] {TARGET}")
