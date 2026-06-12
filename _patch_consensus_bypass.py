"""Apply CONSENSUS bypass for high prob (A1) to predictor.py on EC2."""
from pathlib import Path

PATH = Path("/home/ubuntu/v3-bot/predictor.py")
src = PATH.read_text()

OLD = """            if majority and direction != majority:
                self._diag_log(
                    f"consensus-{coin}",
                    f"[CONSENSUS] {coin} {direction}: market consensus is {majority} "
                    f"({up_count}UP/{down_count}DOWN) — blocking minority bet",
                    15.0,
                )
                return None"""

NEW = """            if majority and direction != majority:
                _consensus_bypass = float(os.getenv("CONSENSUS_BYPASS_MIN_PROB", "0.78"))
                if win_prob >= _consensus_bypass:
                    self._diag_log(
                        f"consensus-bypass-{coin}",
                        f"[CONSENSUS BYPASS] {coin} {direction}: minority vs {majority} "
                        f"({up_count}UP/{down_count}DOWN) but prob={win_prob*100:.0f}% "
                        f">= {_consensus_bypass*100:.0f}% — allowing",
                        30.0,
                    )
                else:
                    self._diag_log(
                        f"consensus-{coin}",
                        f"[CONSENSUS] {coin} {direction}: market consensus is {majority} "
                        f"({up_count}UP/{down_count}DOWN) — blocking minority bet",
                        15.0,
                    )
                    return None"""

if src.count(OLD) != 1:
    raise SystemExit(f"Expected 1 consensus block, got {src.count(OLD)}")

src = src.replace(OLD, NEW, 1)

IMPORT_NEEDLE = "import math\nimport time"
IMPORT_NEW = "import math\nimport os\nimport time"
if IMPORT_NEEDLE not in src:
    raise SystemExit("import math block not found")
if "\nimport os\n" in src or src.startswith("import os"):
    pass  # already has os
else:
    src = src.replace(IMPORT_NEEDLE, IMPORT_NEW, 1)

PATH.write_text(src)
print("[OK] predictor.py patched: CONSENSUS bypass + import os")
