"""
_apply_audit_may27_v6.py — fix the four predictor-formula bugs identified
in the forensic analysis after today's losses.

Fixes (each independently env-toggleable so we can roll any one back):

  F1 (CRITICAL) — Enforce MIN_DISTANCE_PCT
       Config has MIN_DISTANCE_PCT=0.0008 (0.08%) but no code reads it.
       Today's BTC #2 (dist=0.058%), BTC #3 (dist=0.013%), and ETH #1
       (dist=0.042%) all should have abstained but didn't.
       Knob: MIN_DISTANCE_ENFORCE=on (default on)

  F2 (FORMULA) — Sigmoid steepness 3.0 → 1.5; trend/BS blend 70/30 → 50/50
       The 79% probabilities the bot logs today are really ~50.7% BS-prob
       overlaid with optimistic momentum. Lower steepness + balanced blend
       produces honest probabilities (~64%) that yield smaller Kelly bets
       and proportionally smaller losses.
       Knobs:
         TREND_SIGMOID_STEEPNESS (default 1.5, was hardcoded 3.0)
         TREND_BS_BLEND (default 0.50 = trend weight, rest is BS)

  F3 (DEAD ZONE) — Run dead-zone abstention on bs_prob, not combined_prob
       DEAD_ZONE = 0.04 already exists but checks the trend-amplified
       combined_prob. By the time momentum overlay pushes 50.7% to 79%,
       the dead zone is bypassed. Run it on the *honest* BS prob instead.
       Knob: DEAD_ZONE_ON_BS=on (default on)

  F4 (ACCURACY) — Re-enable MIN_ACCURACY gate in afternoon
       The comment says "afternoon has proven 80%+ WR" — but today's
       afternoon WR is 33% (1/3). The gate exists in code but is currently
       a no-op. Re-enable so the bot recognizes "today is bad, slow down".
       Knob: ACCURACY_GATE_ON=on (default on, threshold 0.45)

Idempotent + anchor-verified. Run on EC2:
    cd /home/ubuntu/v3-bot && python3 _apply_audit_may27_v6.py
"""
from __future__ import annotations

import os
import sys
from typing import List, Tuple

REPO = "/home/ubuntu/v3-bot"


def patch_file(path: str, edits: List[Tuple[str, str, str]]) -> int:
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    applied = 0
    for label, anchor, replacement in edits:
        if replacement in src:
            print(f"  [skip] {label}: replacement already present")
            continue
        if anchor not in src:
            raise RuntimeError(
                f"{path}: anchor for {label!r} not found and replacement "
                "not present — manual intervention needed"
            )
        if src.count(anchor) > 1:
            raise RuntimeError(
                f"{path}: anchor for {label!r} matches multiple times "
                f"({src.count(anchor)}) — anchor too generic"
            )
        src = src.replace(anchor, replacement, 1)
        applied += 1
        print(f"  [done] {label}")
    if applied:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(src)
        os.replace(tmp, path)
    return applied


# ── F1: enforce MIN_DISTANCE_PCT (right after dist_pct is computed) ──────────
# F4: re-enable MIN_ACCURACY check in the same block (was a `pass`)
#
# Anchor on the existing "Distance from strike" comment + dist_pct line.
PREDICTOR_EDITS: List[Tuple[str, str, str]] = [
    (
        "F1+F4: MIN_DISTANCE_PCT and MIN_ACCURACY gates (after dist_pct)",
        '''        # Distance from strike as percentage
        dist_pct = (current_price - strike) / strike if strike > 0 else 0.0

        # Trend score: combines short-term momentum with position relative to strike
        # Positive = price moving UP / above strike, Negative = DOWN / below strike
        trend_score = 0.0''',
        '''        # Distance from strike as percentage
        dist_pct = (current_price - strike) / strike if strike > 0 else 0.0

        # ── [AUDIT MAY27 F1] enforce MIN_DISTANCE_PCT ──
        # Today's losses (BTC#2 dist=0.058%, BTC#3 dist=0.013%, ETH#1 dist=0.042%)
        # all happened with the price right at the strike. The config defined
        # MIN_DISTANCE_PCT=0.0008 (0.08%) but no code enforced it. Now it does.
        try:
            if os.getenv("MIN_DISTANCE_ENFORCE", "on").lower() == "on":
                _min_dist_pct = float(getattr(config, "MIN_DISTANCE_PCT", 0.0008))
                if abs(dist_pct) < _min_dist_pct:
                    self._diag_log(
                        f"near-strike-{coin}",
                        f"[NEAR STRIKE] {coin}: dist={dist_pct*100:.3f}% "
                        f"< {_min_dist_pct*100:.2f}% — abstaining (price too close to strike)",
                        15.0,
                    )
                    return None
        except Exception as _e_md:
            logger.debug(f"[NEAR STRIKE] check failed: {_e_md}")

        # ── [AUDIT MAY27 F4] MIN_ACCURACY gate (re-enabled) ──
        # The comment said "afternoon has proven 80%+ WR" — that was historical.
        # Today's afternoon is 33% WR. Slow the bot down on bad days.
        try:
            if os.getenv("ACCURACY_GATE_ON", "on").lower() == "on":
                _acc = self._recent_accuracy()
                _min_acc = float(getattr(self, "MIN_ACCURACY", 0.45))
                if len(self._outcomes) >= max(4, self.ACCURACY_WINDOW // 2) and _acc < _min_acc:
                    self._diag_log(
                        f"acc-{coin}",
                        f"[ACCURACY GATE] {coin}: recent={_acc*100:.0f}% "
                        f"< {_min_acc*100:.0f}% over {len(self._outcomes)} trades — abstaining",
                        30.0,
                    )
                    return None
        except Exception as _e_ag:
            logger.debug(f"[ACCURACY GATE] check failed: {_e_ag}")

        # Trend score: combines short-term momentum with position relative to strike
        # Positive = price moving UP / above strike, Negative = DOWN / below strike
        trend_score = 0.0''',
    ),
    (
        "F2: env-knob sigmoid steepness + 50/50 blend (was hardcoded 3.0 + 70/30)",
        '''        # ── Step 2: Convert trend to probability using sigmoid ──
        # Steepness controls how quickly trend translates to confidence
        base_up_prob = _bs_binary_prob(current_price, strike, sigma, time_remaining)
        raw_prob = _sigmoid(trend_score * 3.0)

        # Blend: 70% trend-based, 30% BS mathematical
        combined_prob = 0.70 * raw_prob + 0.30 * base_up_prob
        combined_prob = max(0.01, min(0.99, combined_prob))''',
        '''        # ── Step 2: Convert trend to probability using sigmoid ──
        # [AUDIT MAY27 F2] steepness 3.0→1.5 + blend 70/30→50/50.
        # Old formula gave 79% confidence on what BS said was 50.7% (coinflip).
        # New formula at trend=0.80: sigmoid(1.2)=77% trend × 0.50 + BS(50.7%) × 0.50 = 64%.
        # That's a more honest probability, leading to smaller Kelly sizes on
        # near-coinflip setups and proportionally smaller losses.
        base_up_prob = _bs_binary_prob(current_price, strike, sigma, time_remaining)
        _trend_steepness = float(os.getenv("TREND_SIGMOID_STEEPNESS", "1.5"))
        _trend_weight = float(os.getenv("TREND_BS_BLEND", "0.50"))
        _trend_weight = max(0.0, min(1.0, _trend_weight))
        raw_prob = _sigmoid(trend_score * _trend_steepness)

        # ── [AUDIT MAY27 F3] dead-zone check on bs_prob (the honest one) ──
        # By the time the trend overlay pushes a 50.7% BS to 79% combined,
        # DEAD_ZONE is bypassed. Run it on bs_prob directly.
        try:
            if os.getenv("DEAD_ZONE_ON_BS", "on").lower() == "on":
                if abs(base_up_prob - 0.5) < self.DEAD_ZONE:
                    self._diag_log(
                        f"dead-bs-{coin}",
                        f"[DEAD ZONE BS] {coin}: BS prob={base_up_prob*100:.1f}% "
                        f"(within ±{self.DEAD_ZONE*100:.0f}pp of 50%) — coinflip; abstaining",
                        15.0,
                    )
                    return None
        except Exception as _e_dz:
            logger.debug(f"[DEAD ZONE BS] check failed: {_e_dz}")

        combined_prob = _trend_weight * raw_prob + (1.0 - _trend_weight) * base_up_prob
        combined_prob = max(0.01, min(0.99, combined_prob))''',
    ),
]


def main() -> int:
    print("=" * 64)
    print("  Applying AUDIT_MAY27 v6: predictor formula fixes")
    print("=" * 64)
    print()
    print("→ predictor.py")
    p_path = os.path.join(REPO, "predictor.py")
    n = patch_file(p_path, PREDICTOR_EDITS)
    print(f"  applied {n}/{len(PREDICTOR_EDITS)} edits")
    print()
    print("→ Verifying syntax")
    import py_compile
    try:
        py_compile.compile(p_path, doraise=True)
        print(f"  [OK] predictor.py")
    except py_compile.PyCompileError as e:
        print(f"  [FAIL] {e}")
        return 2
    print()
    print("Done. Restart the bot.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
