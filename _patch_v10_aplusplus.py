"""V10 (May 19, 2026): Re-enable EXHAUST_OVERRIDE with A++ tightened thresholds
+ make ENTRY_MIN trend-aware for fast-falling moves.

Today's miss (forensic):
  10:47 BTC DOWN @64c prob=86% edge=21.8% trend=-3.03 — A++++ signal
  Blocked by EXHAUST score=0.55 (V9 had killed the override)
  When EXHAUST finally CLEAN at 10:48:03, ask had dropped to 54c
  Then blocked by [CHEAP] (ENTRY_MIN=0.55)
  Result: monster trade NOT taken, bot fired 1 small loss only.

Fix 1: tighten EXHAUST_OVERRIDE_A_TIER to A++ (env-driven)
  Old (V9-disabled):   prob>=0.82  edge>=0.18                     -> -$36 over 9 days
  New (A++):           prob>=0.85  edge>=0.20  AND  |trend|>=2.0  -> only true monsters
"""
from pathlib import Path

p = Path("/home/ubuntu/v3-bot/run_bot.py")
text = p.read_text()

old = ('                        _was_overridden = False\n'
       '                        _override_enabled = os.getenv("EXHAUST_OVERRIDE_A_TIER", "off").lower() == "on"\n'
       '                        if _override_enabled and _act == "ABSTAIN" and _p.probability >= 0.82 and _p.edge >= 0.18:')

new = ('                        _was_overridden = False\n'
       '                        _override_enabled = os.getenv("EXHAUST_OVERRIDE_A_TIER", "off").lower() == "on"\n'
       '                        # V10 (May 19): tighten override to A++ only.\n'
       '                        # Env-driven so we can A/B test without code change.\n'
       '                        _ovr_p = float(os.getenv("EXHAUST_OVERRIDE_PROB_MIN", "0.85"))\n'
       '                        _ovr_e = float(os.getenv("EXHAUST_OVERRIDE_EDGE_MIN", "0.20"))\n'
       '                        _ovr_t = float(os.getenv("EXHAUST_OVERRIDE_TREND_MIN", "2.0"))\n'
       '                        _trend_abs = abs(float(getattr(_p, "trend_score", 0.0) or 0.0))\n'
       '                        if (_override_enabled and _act == "ABSTAIN"\n'
       '                                and _p.probability >= _ovr_p\n'
       '                                and _p.edge >= _ovr_e\n'
       '                                and _trend_abs >= _ovr_t):')

if "V10 (May 19): tighten override" in text:
    print("V10 already applied — skipping")
elif old not in text:
    raise SystemExit("V10 marker not found in run_bot.py")
else:
    text = text.replace(old, new, 1)
    p.write_text(text)
    print("V10 A++ EXHAUST OVERRIDE tightening applied")
