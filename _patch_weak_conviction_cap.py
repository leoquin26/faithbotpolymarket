"""Option A: Half-Kelly for weak-conviction trades (May 21, 2026).

The pattern eating our gains:
  Prob 75-77% + |trend| 0.6-0.9 + entry 36-50c → Kelly A-tier (100%)
  $7-8 position when right wins big, when wrong loses -$7
  Same setup wins AND loses, but variance kills the equity curve

Fix: when prob<80% AND |trend|<1.0, halve the Kelly position.
Same EV, half the variance. Equity curve smooths out.

Env-controlled:
  WEAK_CONVICTION_CAP=on    (default)
  WEAK_CONVICTION_PROB_MAX=0.80
  WEAK_CONVICTION_TREND_MAX=1.0
  WEAK_CONVICTION_MULT=0.50
"""
from pathlib import Path

p = Path("/home/ubuntu/v3-bot/order_manager.py")
text = p.read_text()

if "WEAK CONVICTION CAP" in text:
    print("weak-conviction cap already present — skipping")
    raise SystemExit(0)

# Inject AFTER the existing dampen block, BEFORE the final logger.info
old = ("            dampen_tag = \"\"\n"
       "            if getattr(pred, \"_dampened\", False):\n"
       "                # Fix apr27 (no double penalty): if EXHAUST OVERRIDE fired,\n"
       "                # skip the 50% size cut. Override already self-selected A-tier.\n"
       "                if getattr(pred, \"_override_full_size\", False):\n"
       "                    dampen_tag = \" dampen=skipped(override)\"\n"
       "                else:\n"
       "                    pre_dampen = size\n"
       "                    size = max(kelly_min_bet, size * 0.5)\n"
       "                    dampen_tag = f\" dampen=50%(pre=${pre_dampen:.2f})\"")

new = ("            dampen_tag = \"\"\n"
       "            if getattr(pred, \"_dampened\", False):\n"
       "                # Fix apr27 (no double penalty): if EXHAUST OVERRIDE fired,\n"
       "                # skip the 50% size cut. Override already self-selected A-tier.\n"
       "                if getattr(pred, \"_override_full_size\", False):\n"
       "                    dampen_tag = \" dampen=skipped(override)\"\n"
       "                else:\n"
       "                    pre_dampen = size\n"
       "                    size = max(kelly_min_bet, size * 0.5)\n"
       "                    dampen_tag = f\" dampen=50%(pre=${pre_dampen:.2f})\"\n"
       "\n"
       "            # WEAK CONVICTION CAP (May 21): half-Kelly when prob<80% AND |trend|<1.0\n"
       "            # Pattern that ate our gains 5/19-5/20: -$7 losses on prob 75-77% +\n"
       "            # |trend| 0.6-0.9 + cheap entries 36-50c. Same EV, half variance.\n"
       "            # Kill switch: WEAK_CONVICTION_CAP=off in .env.\n"
       "            weak_conv_tag = \"\"\n"
       "            if os.getenv(\"WEAK_CONVICTION_CAP\", \"on\").lower() != \"off\":\n"
       "                _wc_prob_max = float(os.getenv(\"WEAK_CONVICTION_PROB_MAX\", \"0.80\"))\n"
       "                _wc_trend_max = float(os.getenv(\"WEAK_CONVICTION_TREND_MAX\", \"1.0\"))\n"
       "                _wc_mult = float(os.getenv(\"WEAK_CONVICTION_MULT\", \"0.50\"))\n"
       "                _wc_trend_abs = abs(float(getattr(pred, \"trend_score\", 0.0) or 0.0))\n"
       "                if p < _wc_prob_max and _wc_trend_abs < _wc_trend_max and not getattr(pred, \"_override_full_size\", False):\n"
       "                    pre_weak = size\n"
       "                    size = max(kelly_min_bet, size * _wc_mult)\n"
       "                    weak_conv_tag = f\" weak_conv={_wc_mult:.0%}(pre=${pre_weak:.2f})\"")

if old not in text:
    raise SystemExit("dampen marker not found")
text = text.replace(old, new, 1)

# Also extend the log line to show weak_conv_tag
old_log = ("            logger.info(\n"
           "                f\"[KELLY] {pred.coin}: f*={full_kelly:.3f} frac={fractional:.3f} \"\n"
           "                f\"pct_cap={kelly_max_pct:.2%} tier={tier_name}({tier_mult:.0%}){dampen_tag} \"")

new_log = ("            logger.info(\n"
           "                f\"[KELLY] {pred.coin}: f*={full_kelly:.3f} frac={fractional:.3f} \"\n"
           "                f\"pct_cap={kelly_max_pct:.2%} tier={tier_name}({tier_mult:.0%}){dampen_tag}{weak_conv_tag} \"")

if old_log in text:
    text = text.replace(old_log, new_log, 1)

p.write_text(text)
print("Weak-conviction half-Kelly cap installed")
