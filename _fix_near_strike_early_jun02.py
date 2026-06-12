"""
Jun 2 PM — fix "lost it by 2 minutes" / EXPENSIVE at 80c.

ROOT CAUSE (14:15 window forensics):
  14:15:04  warmup done at 20s
  14:15:20 - 14:16:24  [NEAR STRIKE] BTC dist=0.030-0.038% < 0.05% — predictor returns
                          BEFORE reading ask price
  14:16:40  first evaluation past NEAR STRIKE → ask already 80c [EXPENSIVE]

The market priced BTC DOWN while we refused to look (near-strike gate).
Warmup was NOT the problem.

FIX: first NEAR_STRIKE_EARLY_SEC of each window, use looser EARLY_MIN_DISTANCE_PCT
(default 0.02% = 0.0002) so we evaluate asks while price is leaving the strike.
After 90s, revert to normal MIN_DISTANCE_PCT (0.05%).
"""
from pathlib import Path

PRED = Path("/home/ubuntu/v3-bot/predictor.py")
ENV = Path("/home/ubuntu/v3-bot/.env")

OLD = """        # [AUDIT MAY27 F1] enforce MIN_DISTANCE_PCT
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
                        f"< {_min_dist_pct*100:.2f}% - abstaining (price too close to strike)",
                        15.0,
                    )
                    return None
        except Exception as _e_md:
            logger.debug(f"[NEAR STRIKE] check failed: {_e_md}")"""

NEW = """        # [AUDIT MAY27 F1] enforce MIN_DISTANCE_PCT
        # Jun-2 PM: early-window looser floor — see 14:15 window (NEAR STRIKE blocked
        # 96s while ask went 55c→80c; first check at 80c was [EXPENSIVE]).
        try:
            if os.getenv("MIN_DISTANCE_ENFORCE", "on").lower() == "on":
                _min_dist_pct = float(getattr(config, "MIN_DISTANCE_PCT", 0.0008))
                _early_sec = int(os.getenv("NEAR_STRIKE_EARLY_SEC", "120"))
                _early_dist = float(os.getenv("EARLY_MIN_DISTANCE_PCT", "0.0002"))
                if window_age < _early_sec:
                    _min_dist_pct = min(_min_dist_pct, _early_dist)
                if abs(dist_pct) < _min_dist_pct:
                    self._diag_log(
                        f"near-strike-{coin}",
                        f"[NEAR STRIKE] {coin}: dist={dist_pct*100:.3f}% "
                        f"< {_min_dist_pct*100:.2f}% - abstaining (price too close to strike)",
                        15.0,
                    )
                    return None
        except Exception as _e_md:
            logger.debug(f"[NEAR STRIKE] check failed: {_e_md}")"""


def main():
    text = PRED.read_text()
    if "NEAR_STRIKE_EARLY_SEC" in text:
        print("[SKIP] near-strike early bypass already in predictor")
    elif OLD not in text:
        print("[FAIL] near-strike block not found")
        return
    else:
        PRED.write_text(text.replace(OLD, NEW, 1))
        print("[OK] predictor: early-window NEAR STRIKE bypass")

    env = ENV.read_text()
    adds = [
        ("NEAR_STRIKE_EARLY_SEC", "120"),
        ("EARLY_MIN_DISTANCE_PCT", "0.0002"),
    ]
    import re
    for key, val in adds:
        if re.search(rf"^{key}=", env, re.M):
            env = re.sub(rf"^{key}=.*$", f"{key}={val}", env, flags=re.M)
            print(f"[OK] {key}={val}")
        else:
            env = env.rstrip() + f"\n# Jun-2 PM: evaluate asks in first 2min while leaving strike\n{key}={val}\n"
            print(f"[OK] added {key}={val}")
    ENV.write_text(env)


if __name__ == "__main__":
    main()
