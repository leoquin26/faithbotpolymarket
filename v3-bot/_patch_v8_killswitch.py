"""Add a V8_WHIPSAW_BLOCK env kill switch to predictor.py on EC2."""
from pathlib import Path

p = Path("/home/ubuntu/v3-bot/predictor.py")
text = p.read_text()

old = (
    "        # ── V8 Late-Window Whipsaw Block (added 2026-05-12) ──\n"
    "        # 15m only. Blocks trades entered at the top of a recent ask\n"
    "        # whipsaw with little time left in the window. Counterfactual\n"
    "        # May 4-12: +$12.63 net (5 losses saved, 3 wins killed).\n"
    "        if _tf == \"15m\":"
)

new = (
    "        # ── V8 Late-Window Whipsaw Block (added 2026-05-12) ──\n"
    "        # 15m only. Blocks trades entered at the top of a recent ask\n"
    "        # whipsaw with little time left in the window. Counterfactual\n"
    "        # May 4-12: +$12.63 net (5 losses saved, 3 wins killed).\n"
    "        # Kill switch (May 13 PM): V8_WHIPSAW_BLOCK=off disables entirely.\n"
    "        _v8_enabled = os.getenv(\"V8_WHIPSAW_BLOCK\", \"on\").lower() != \"off\"\n"
    "        if _tf == \"15m\" and _v8_enabled:"
)

if old not in text:
    raise SystemExit("V8 marker not found")
if "V8_WHIPSAW_BLOCK" in text:
    print("V8 kill switch already present")
else:
    p.write_text(text.replace(old, new, 1))
    print("V8_WHIPSAW_BLOCK kill switch added")
