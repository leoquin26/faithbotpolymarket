"""PHASE A + PHASE B restoration (May 13, 2026 PM)
=================================================

Phase A — restore peak-engine quality gates removed since May 4:
  A1. Per-coin DIR LOCK (predictor.py)
  A2. CHOPPY_MIN_TREND_ABS=0.48 gate (predictor.py)
  A3. FLIP_TREND_MIN_5M=2.0 env-driven (predictor.py)
  A4. Trap-band memory taint (order_manager.py)

Phase B — new microstructure feature:
  B1. OBI (Order Book Imbalance) directional gate (predictor.py)

All changes env-controllable so they can be rolled back from .env.
"""
import re
import shutil
from pathlib import Path
from datetime import datetime

V3 = Path("/home/ubuntu/v3-bot")
PRED = V3 / "predictor.py"
ORDER = V3 / "order_manager.py"
CONFIG = V3 / "config.py"

STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")


def backup(p: Path):
    bak = p.with_suffix(p.suffix + f".bak_phaseAB_{STAMP}")
    shutil.copy2(p, bak)
    print(f"  backup: {bak.name}")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"  MARKER NOT FOUND: {label}")
    occurrences = text.count(old)
    if occurrences > 1:
        raise SystemExit(f"  MARKER NOT UNIQUE ({occurrences}x): {label}")
    return text.replace(old, new, 1)


# ─────────────────────────────────────────────────────────
# config.py — add CHOPPY_MIN_TREND_ABS + FLIP_TREND env vars
# ─────────────────────────────────────────────────────────
print("=== config.py ===")
backup(CONFIG)
ctxt = CONFIG.read_text()

if "CHOPPY_MIN_TREND_ABS" not in ctxt:
    # Insert just after MIN_DIRECTIONAL_EDGE/MIN_CONVICTION block
    marker = "MIN_WINDOW_AGE = int(os.getenv(\"MIN_WINDOW_AGE\", \"60\"))"
    addition = """MIN_WINDOW_AGE = int(os.getenv("MIN_WINDOW_AGE", "60"))

# ── Phase A restoration (May 13, 2026): re-add peak quality gates ──
# Choppy-strict gate: if chop detector is on AND |trend| below this,
# abstain. Peak engine (Apr 21 → May 5 $90→$199 run) had this; it
# was lost during May 12 V6/V8/Phase-2 deployments.
CHOPPY_MIN_TREND_ABS = float(os.getenv("CHOPPY_MIN_TREND_ABS", "0.48"))

# Per-timeframe flip-guard threshold. 5m needs stricter (peak: 2.0) to
# stop fast micro-reversal self-hedging. V7 Phase-1 had lowered to 1.2.
FLIP_TREND_MIN_5M = float(os.getenv("FLIP_TREND_MIN_5M", "2.0"))
FLIP_TREND_MIN_15M = float(os.getenv("FLIP_TREND_MIN_15M", "1.5"))

# Trap-band memory: once ask touched 60-64c this window for a
# (coin, dir), don't chase a lower retry unless prob >= TRAP_BAND_OVERRIDE_PROB
# AND edge >= TRAP_BAND_OVERRIDE_EDGE (A-tier override).
TRAP_BAND_OVERRIDE_PROB = float(os.getenv("TRAP_BAND_OVERRIDE_PROB", "0.85"))
TRAP_BAND_OVERRIDE_EDGE = float(os.getenv("TRAP_BAND_OVERRIDE_EDGE", "0.18"))

# ── Phase B (May 13, 2026): Order Book Imbalance directional gate ──
# depth_ratio = bid_total / ask_total of the token we're buying.
# < OBI_HARD_MIN  → abstain (very weak demand for our direction)
# < OBI_SOFT_MIN  → require edge >= MIN_EDGE + OBI_SOFT_EDGE_BOOST
# Kill switch: OBI_GATE=off in .env disables both layers.
OBI_HARD_MIN = float(os.getenv("OBI_HARD_MIN", "0.30"))
OBI_SOFT_MIN = float(os.getenv("OBI_SOFT_MIN", "0.50"))
OBI_SOFT_EDGE_BOOST = float(os.getenv("OBI_SOFT_EDGE_BOOST", "0.05"))"""
    ctxt = replace_once(ctxt, marker, addition, "config.py block")
    CONFIG.write_text(ctxt)
    print("  config additions injected")
else:
    print("  config already patched — skipping")


# ─────────────────────────────────────────────────────────
# predictor.py — Phase A1-A3 + Phase B1
# ─────────────────────────────────────────────────────────
print("=== predictor.py ===")
backup(PRED)
ptxt = PRED.read_text()

# A1a. Add _window_directions per-coin dict in __init__
old = "        self._window_direction: Optional[str] = None\n        self._window_start_ts: int = 0"
new = ("        self._window_direction: Optional[str] = None\n"
       "        self._window_directions: Dict[str, str] = {}  # Phase A restoration: per-coin dir lock\n"
       "        self._window_start_ts: int = 0")
if "self._window_directions:" not in ptxt:
    ptxt = replace_once(ptxt, old, new, "A1a __init__ window_directions")
    print("  A1a per-coin dict added to __init__")
else:
    print("  A1a already present")

# A1b. Replace single-var DIR LOCK with per-coin DIR LOCK and clear on new window
old = ("        # Cross-asset direction consistency\n"
       "        if window_start != self._window_start_ts:\n"
       "            self._window_direction = None\n"
       "            self._window_start_ts = window_start\n"
       "            self._window_trends.clear()\n"
       "        \n"
       "        # Record this coin's trend for consensus\n"
       "        self._window_trends[coin] = direction\n"
       "        \n"
       "        # If we already committed to a direction, block contradictions\n"
       "        if self._window_direction is not None and direction != self._window_direction:\n"
       "            self._diag_log(\n"
       "                f\"dirlock-{coin}\",\n"
       "                f\"[DIR LOCK] {coin} {direction}: committed to {self._window_direction} — skipping\",\n"
       "                15.0,\n"
       "            )\n"
       "            return None")
new = ("        # Cross-asset direction consistency (Phase A: per-coin lock + cross-asset consensus)\n"
       "        if window_start != self._window_start_ts:\n"
       "            self._window_direction = None\n"
       "            self._window_directions.clear()  # Phase A: per-coin reset each window\n"
       "            self._window_start_ts = window_start\n"
       "            self._window_trends.clear()\n"
       "        \n"
       "        # Record this coin's trend for consensus\n"
       "        self._window_trends[coin] = direction\n"
       "        \n"
       "        # Phase A1: per-coin DIR LOCK — only block if THIS coin already committed\n"
       "        # to a different direction this window. Restored from peak engine\n"
       "        # (apr30/may01). Cross-asset is handled by CONSENSUS below.\n"
       "        prior = self._window_directions.get(coin)\n"
       "        if prior is not None and direction != prior:\n"
       "            self._diag_log(\n"
       "                f\"dirlock-{coin}\",\n"
       "                f\"[DIR LOCK] {coin} {direction}: this coin committed to {prior} this window — skipping\",\n"
       "                15.0,\n"
       "            )\n"
       "            return None")
if "Phase A1: per-coin DIR LOCK" not in ptxt:
    ptxt = replace_once(ptxt, old, new, "A1b DIR LOCK replacement")
    print("  A1b per-coin DIR LOCK installed")
else:
    print("  A1b already installed")

# A1c. Commit per-coin direction at the END (after _window_direction commit)
old = ("        self._window_direction = direction\n"
       "        self._chop_detector.record_direction(direction)")
new = ("        self._window_direction = direction  # legacy global (kept for safety)\n"
       "        self._window_directions[coin] = direction  # Phase A: per-coin commit\n"
       "        self._chop_detector.record_direction(direction)")
if "Phase A: per-coin commit" not in ptxt:
    ptxt = replace_once(ptxt, old, new, "A1c per-coin commit at end")
    print("  A1c per-coin commit added")
else:
    print("  A1c already added")

# A2. CHOPPY_MIN_TREND_ABS gate — inject after is_chop branch
old = ("        # Regime detection: choppy vs trending\n"
       "        chop = self._chop_detector\n"
       "        is_chop = chop.is_choppy()\n"
       "\n"
       "        if is_chop:")
new = ("        # Regime detection: choppy vs trending\n"
       "        chop = self._chop_detector\n"
       "        is_chop = chop.is_choppy()\n"
       "\n"
       "        # Phase A2 restoration: chop-strict |trend| gate (peak engine had this)\n"
       "        if is_chop:\n"
       "            _min_tr = float(getattr(config, \"CHOPPY_MIN_TREND_ABS\", 0.48))\n"
       "            if abs(trend_score) < _min_tr:\n"
       "                self._diag_log(\n"
       "                    f\"chopstrict-{coin}\",\n"
       "                    f\"[CHOPPY STRICT] {coin}: |trend|={abs(trend_score):.3f} < {_min_tr} \"\n"
       "                    f\"(restored peak gate)\",\n"
       "                    15.0,\n"
       "                )\n"
       "                return None\n"
       "\n"
       "        if is_chop:")
if "Phase A2 restoration" not in ptxt:
    ptxt = replace_once(ptxt, old, new, "A2 CHOPPY_MIN_TREND_ABS")
    print("  A2 CHOPPY_MIN_TREND_ABS gate restored")
else:
    print("  A2 already restored")

# A3. FLIP_TREND_MIN — env-driven, default 2.0 for 5m (revert V7 Phase-1)
old = ("            # V7 Phase-1 (2026-05-12): lower 5m flip-guard from 1.5 to 1.2\n"
       "            # to capture A-tier setups the bot was locked out of by a\n"
       "            # stale DOWN/UP commit streak. 15m unchanged (V6 stays at 1.5).\n"
       "            FLIP_TREND_MIN = 1.2 if _tf == \"5m\" else 1.5")
new = ("            # Phase A3 restoration: peak 5m flip-guard was 2.0 (V7 Phase-1\n"
       "            # had lowered to 1.2). Peak comment: \"5m needs stricter threshold\n"
       "            # because fast micro-reversals produce self-hedging trades\".\n"
       "            # Env-driven so we can A/B test without code change.\n"
       "            if _tf == \"5m\":\n"
       "                FLIP_TREND_MIN = float(getattr(config, \"FLIP_TREND_MIN_5M\", 2.0))\n"
       "            else:\n"
       "                FLIP_TREND_MIN = float(getattr(config, \"FLIP_TREND_MIN_15M\", 1.5))")
if "Phase A3 restoration" not in ptxt:
    ptxt = replace_once(ptxt, old, new, "A3 FLIP_TREND_MIN")
    print("  A3 FLIP_TREND_MIN env-driven (default 5m=2.0)")
else:
    print("  A3 already env-driven")

# B1. OBI gate — inject right before V8 block (after V6 redirect)
# The V6 block ends with logic that may flip direction/ask/token_id/depth.
# We insert AFTER V6 ABORT branch but BEFORE V8.
old = "        # ── V8 Late-Window Whipsaw Block (added 2026-05-12) ──"
new = ("        # ── Phase B (May 13, 2026): Order Book Imbalance directional gate ──\n"
       "        # depth_ratio = bid_total / ask_total for the token we'd buy.\n"
       "        # Strong supply vs weak demand on our side → require stronger setup.\n"
       "        # Kill switch: OBI_GATE=off disables entirely.\n"
       "        _obi_enabled = os.getenv(\"OBI_GATE\", \"on\").lower() != \"off\"\n"
       "        if _obi_enabled and depth > 0:\n"
       "            _obi_hard = float(getattr(config, \"OBI_HARD_MIN\", 0.30))\n"
       "            _obi_soft = float(getattr(config, \"OBI_SOFT_MIN\", 0.50))\n"
       "            _obi_boost = float(getattr(config, \"OBI_SOFT_EDGE_BOOST\", 0.05))\n"
       "            _min_edge_cfg = float(getattr(config, \"MIN_EDGE\", 0.05))\n"
       "            if depth < _obi_hard:\n"
       "                logger.info(\n"
       "                    f\"[OBI HARD BLOCK] {coin} {direction}@{ask*100:.0f}c | \"\n"
       "                    f\"OBI={depth:.2f} < {_obi_hard:.2f} (book heavily favors contra) — abstaining\"\n"
       "                )\n"
       "                return None\n"
       "            if depth < _obi_soft and edge < (_min_edge_cfg + _obi_boost):\n"
       "                logger.info(\n"
       "                    f\"[OBI SOFT BLOCK] {coin} {direction}@{ask*100:.0f}c | \"\n"
       "                    f\"OBI={depth:.2f} edge={edge*100:.1f}% < required \"\n"
       "                    f\"{(_min_edge_cfg + _obi_boost)*100:.1f}% — abstaining\"\n"
       "                )\n"
       "                return None\n"
       "\n"
       "        # ── V8 Late-Window Whipsaw Block (added 2026-05-12) ──")
if "Phase B (May 13, 2026): Order Book Imbalance" not in ptxt:
    ptxt = replace_once(ptxt, old, new, "B1 OBI gate")
    print("  B1 OBI gate injected")
else:
    print("  B1 already injected")

PRED.write_text(ptxt)
print("  predictor.py written")


# ─────────────────────────────────────────────────────────
# order_manager.py — Phase A4 trap-band memory
# ─────────────────────────────────────────────────────────
print("=== order_manager.py ===")
backup(ORDER)
otxt = ORDER.read_text()

# A4a. Add _trap_band_tainted to __init__
old = "        self._fok_throttle: Dict[str, float] = {}"
new = ("        self._fok_throttle: Dict[str, float] = {}\n"
       "        # Phase A4 restoration: trap-band memory. (coin:window:dir) keys\n"
       "        # that touched 60-64c in this window. Subsequent retries blocked\n"
       "        # unless A-tier override (TRAP_BAND_OVERRIDE_PROB / _EDGE).\n"
       "        self._trap_band_tainted: set = set()")
if "_trap_band_tainted" not in otxt:
    ptr = ORDER  # placeholder
    if "from typing import" in otxt and "Set" not in otxt.split("from typing import")[1].split("\n")[0]:
        # Add Set import for typing if absent
        otxt = otxt.replace("from typing import", "from typing import Set,", 1)
    otxt = replace_once(otxt, old, new, "A4a __init__ trap_band_tainted")
    print("  A4a trap_band_tainted added to __init__")
else:
    print("  A4a already present")

# A4b. Insert trap-band check INSIDE place_bet — right after FOK throttle block
old = ("        if self.is_window_traded(coin, window_start):\n"
       "            logger.warning(f\"[SKIP] Already traded {coin} in this window\")\n"
       "            return False")
new = ("        if self.is_window_traded(coin, window_start):\n"
       "            logger.warning(f\"[SKIP] Already traded {coin} in this window\")\n"
       "            return False\n"
       "\n"
       "        # Phase A4: trap-band memory check\n"
       "        _trap_tk = f\"{coin}:{window_start}:{direction}\"\n"
       "        if _trap_tk in self._trap_band_tainted:\n"
       "            import os as _ostb\n"
       "            _ovr_p = float(os.getenv(\"TRAP_BAND_OVERRIDE_PROB\",\n"
       "                getattr(config, \"TRAP_BAND_OVERRIDE_PROB\", 0.85)))\n"
       "            _ovr_e = float(os.getenv(\"TRAP_BAND_OVERRIDE_EDGE\",\n"
       "                getattr(config, \"TRAP_BAND_OVERRIDE_EDGE\", 0.18)))\n"
       "            if pred.probability >= _ovr_p and pred.edge >= _ovr_e:\n"
       "                logger.info(\n"
       "                    f\"[TRAP BAND MEMORY OVERRIDE] {coin} {direction}: \"\n"
       "                    f\"prob={pred.probability*100:.0f}% edge={pred.edge*100:.1f}% — A-tier allowed\"\n"
       "                )\n"
       "            else:\n"
       "                logger.info(\n"
       "                    f\"[TRAP BAND MEMORY] {coin} {direction}: blocked — \"\n"
       "                    f\"trap band touched earlier this window\"\n"
       "                )\n"
       "                return False")
if "Phase A4: trap-band memory check" not in otxt:
    otxt = replace_once(otxt, old, new, "A4b place_bet trap_band check")
    print("  A4b trap-band memory check inserted in place_bet")
else:
    print("  A4b already inserted")

# A4c. Add taint logic — when CLOB ask is in trap band during place_bet, mark
# Insert AFTER the CLOB ask validation block we read earlier.
old = ("        # CLOB ask validation: real entry price\n"
       "        if real_ask and real_ask > config.ENTRY_MAX:\n"
       "            logger.info(f\"[CLOB GATE] {coin}: ask={real_ask*100:.0f}c > {config.ENTRY_MAX*100:.0f}c — too expensive\")\n"
       "            return False")
new = ("        # CLOB ask validation: real entry price\n"
       "        if real_ask and real_ask > config.ENTRY_MAX:\n"
       "            logger.info(f\"[CLOB GATE] {coin}: ask={real_ask*100:.0f}c > {config.ENTRY_MAX*100:.0f}c — too expensive\")\n"
       "            return False\n"
       "\n"
       "        # Phase A4: taint window if CLOB ask sits in 60-64c trap band\n"
       "        try:\n"
       "            _tb_min = float(getattr(config, \"TRAP_BAND_MIN\", 0.60))\n"
       "            _tb_max = float(getattr(config, \"TRAP_BAND_MAX\", 0.64))\n"
       "            if real_ask and _tb_min <= real_ask <= _tb_max:\n"
       "                self._trap_band_tainted.add(_trap_tk)\n"
       "                logger.debug(\n"
       "                    f\"[TRAP BAND TAINT] {coin} {direction}: ask={real_ask*100:.0f}c \"\n"
       "                    f\"in band {_tb_min*100:.0f}-{_tb_max*100:.0f}c — window tainted\"\n"
       "                )\n"
       "        except Exception:\n"
       "            pass")
if "Phase A4: taint window" not in otxt:
    otxt = replace_once(otxt, old, new, "A4c CLOB ask taint logic")
    print("  A4c taint logic inserted after CLOB gate")
else:
    print("  A4c already inserted")

ORDER.write_text(otxt)
print("  order_manager.py written")

print("\n=== ALL PATCHES APPLIED ===")
