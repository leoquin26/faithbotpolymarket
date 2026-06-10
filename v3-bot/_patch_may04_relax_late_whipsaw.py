"""
Relax LATE WHIPSAW (Fix B) — over-blocking 61% of commits.

Changes:
  1. Time threshold: 300s -> 180s (last 3 min only, not last 5 min)
  2. Use TRAP_BAND_OVERRIDE thresholds (default 0.85 / 0.18) instead of
     MORNING_OVERRIDE thresholds (0.88 / 0.22). Keeps A-tier idea but
     unblocks legit ~82-85% prob signals.
  3. Skip the gate when ask<=0 (NO ASK gate handles that case).

Idempotent: bails if already relaxed.
"""
from pathlib import Path
import sys

PRED = Path("/home/ubuntu/v3-bot/predictor.py")

src = PRED.read_text()

OLD = (
    "        # may04 fix B: late-entry tightening for whipsaw.\n"
    "        # If we already saw the OPPOSITE direction earlier this window\n"
    "        # for this coin AND we're in the last 5 minutes, demand A-tier\n"
    "        # (prob >= 88% AND edge >= 22%). Prevents chasing a flipped\n"
    "        # micro-trend after DIR LOCK released or the prior commit.\n"
    "        _seen = self._window_dir_seen.get(coin, set())\n"
    "        _opposite = \"DOWN\" if direction == \"UP\" else \"UP\"\n"
    "        if _opposite in _seen and time_remaining < 300:\n"
    "            _ovr_p = float(getattr(config, \"MORNING_OVERRIDE_PROB\", 0.88) or 0.88)\n"
    "            _ovr_e = float(getattr(config, \"MORNING_OVERRIDE_EDGE\", 0.22) or 0.22)\n"
    "            _edge_local = win_prob - ask\n"
    "            if win_prob < _ovr_p or _edge_local < _ovr_e:\n"
    "                self._diag_log(\n"
    "                    f\"latewhip-{coin}\",\n"
    "                    f\"[LATE WHIPSAW] {coin} {direction}: opposite seen this window AND \"\n"
    "                    f\"T={time_remaining:.0f}s<300 — need A-tier (prob>={_ovr_p*100:.0f}% \"\n"
    "                    f\"edge>={_ovr_e*100:.0f}%); have prob={win_prob*100:.0f}% \"\n"
    "                    f\"edge={_edge_local*100:+.1f}%\",\n"
    "                    10.0,\n"
    "                )\n"
    "                return None\n"
)

NEW = (
    "        # may04 fix B: late-entry tightening for whipsaw.\n"
    "        # If we already saw the OPPOSITE direction earlier this window\n"
    "        # for this coin AND we're in the last 3 minutes, demand A-tier\n"
    "        # (TRAP_BAND_OVERRIDE thresholds: prob >= 85% AND edge >= 18%).\n"
    "        # Prevents chasing a flipped micro-trend right at window close,\n"
    "        # but keeps legit mid-quality signals flowing for 12 of 15 min.\n"
    "        # Skip when ask<=0 — NO ASK gate handles that case.\n"
    "        _seen = self._window_dir_seen.get(coin, set())\n"
    "        _opposite = \"DOWN\" if direction == \"UP\" else \"UP\"\n"
    "        if _opposite in _seen and time_remaining < 180 and ask > 0:\n"
    "            _ovr_p = float(getattr(config, \"TRAP_BAND_OVERRIDE_PROB\", 0.85) or 0.85)\n"
    "            _ovr_e = float(getattr(config, \"TRAP_BAND_OVERRIDE_EDGE\", 0.18) or 0.18)\n"
    "            _edge_local = win_prob - ask\n"
    "            if win_prob < _ovr_p or _edge_local < _ovr_e:\n"
    "                self._diag_log(\n"
    "                    f\"latewhip-{coin}\",\n"
    "                    f\"[LATE WHIPSAW] {coin} {direction}: opposite seen this window AND \"\n"
    "                    f\"T={time_remaining:.0f}s<180 — need A-tier (prob>={_ovr_p*100:.0f}% \"\n"
    "                    f\"edge>={_ovr_e*100:.0f}%); have prob={win_prob*100:.0f}% \"\n"
    "                    f\"edge={_edge_local*100:+.1f}%\",\n"
    "                    10.0,\n"
    "                )\n"
    "                return None\n"
)

if NEW in src:
    print("[skip] already relaxed")
    sys.exit(0)
if OLD not in src:
    print("[fail] could not find original LATE WHIPSAW block")
    sys.exit(2)

new_src = src.replace(OLD, NEW, 1)
PRED.write_text(new_src)
print("[ok] LATE WHIPSAW relaxed: T<180s, A-tier=85%/18%, skip if ask<=0")
