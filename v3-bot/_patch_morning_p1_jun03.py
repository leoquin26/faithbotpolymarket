#!/usr/bin/env python3
"""Morning P1 prob 65% blocks 58-64% Pattern A signals with 14-21% edge."""
from pathlib import Path
import shutil
from datetime import datetime

MS = Path("/home/ubuntu/v3-bot/morning_strategy.py")
ENV = Path("/home/ubuntu/v3-bot/.env")
ts = datetime.now().strftime("%Y%m%d_%H%M%S")

OLD_P1 = """    if phase == 1:
        if coin not in P1_ALLOWED:
            logger.debug(f"[MORNING P1] {coin} not in allowed ({P1_ALLOWED})")
            return None
        if prob < P1_MIN_PROB:
            logger.debug(f"[MORNING P1] {coin} prob {prob:.0%} < {P1_MIN_PROB:.0%}")
            return None
        if edge < P1_MIN_EDGE:
            logger.debug(f"[MORNING P1] {coin} edge {edge*100:.1f}% < {P1_MIN_EDGE*100:.0f}%")
            return None
        if abs_trend < P1_MIN_TREND:
            logger.debug(f"[MORNING P1] {coin} |trend| {abs_trend:.2f} < {P1_MIN_TREND}")
            return None"""

NEW_P1 = """    if phase == 1:
        if coin not in P1_ALLOWED:
            logger.debug(f"[MORNING P1] {coin} not in allowed ({P1_ALLOWED})")
            return None
        _entry = getattr(pred, "entry_price", 0) or getattr(pred, "poly_price", 0)
        _pa_prob = float(_os_ms.getenv("MORNING_P1_PATTERN_A_PROB", "0.58"))
        _pa_edge = float(_os_ms.getenv("MORNING_P1_PATTERN_A_EDGE", "0.10"))
        _pa_ask = float(_os_ms.getenv("MORNING_P1_PATTERN_A_MAX_ASK", "0.55"))
        _pattern_a = (
            prob >= _pa_prob and edge >= _pa_edge
            and _entry > 0.05 and _entry <= _pa_ask
        )
        _eff_prob = min(P1_MIN_PROB, _pa_prob) if _pattern_a else P1_MIN_PROB
        if prob < _eff_prob:
            logger.debug(
                f"[MORNING P1] {coin} prob {prob:.0%} < {_eff_prob:.0%}"
                f"{' (need pattern-A edge>=' + str(int(_pa_edge*100)) + '%)' if not _pattern_a else ''}"
            )
            return None
        if edge < P1_MIN_EDGE:
            logger.debug(f"[MORNING P1] {coin} edge {edge*100:.1f}% < {P1_MIN_EDGE*100:.0f}%")
            return None
        if abs_trend < P1_MIN_TREND and not _pattern_a:
            logger.debug(f"[MORNING P1] {coin} |trend| {abs_trend:.2f} < {P1_MIN_TREND}")
            return None
        if _pattern_a and abs_trend < P1_MIN_TREND:
            logger.info(
                f"[MORNING P1 PATTERN-A] {coin} {pred.direction}: "
                f"prob={prob:.0%} edge={edge*100:.1f}% ask={_entry*100:.0f}c "
                f"|trend|={abs_trend:.2f} — trend gate waived"
            )"""


def patch_env() -> None:
    env = ENV.read_text(encoding="utf-8")
    updates = {
        "MORNING_P1_MIN_PROB": "0.58",
        "MORNING_P1_PATTERN_A_PROB": "0.58",
        "MORNING_P1_PATTERN_A_EDGE": "0.10",
        "MORNING_P1_PATTERN_A_MAX_ASK": "0.55",
    }
    for k, v in updates.items():
        if f"{k}=" in env:
            lines = [f"{k}={v}" if ln.startswith(f"{k}=") else ln for ln in env.splitlines()]
            env = "\n".join(lines) + ("\n" if env.endswith("\n") else "")
        else:
            env = env.rstrip() + f"\n{k}={v}\n"
    ENV.write_text(env, encoding="utf-8")


def main() -> None:
    text = MS.read_text(encoding="utf-8")
    if OLD_P1 not in text:
        if "MORNING P1 PATTERN-A" in text:
            print("morning_strategy: already patched")
        else:
            raise SystemExit("anchor not found")
    else:
        shutil.copy2(MS, MS.with_suffix(f".bak_{ts}"))
        MS.write_text(text.replace(OLD_P1, NEW_P1, 1), encoding="utf-8")
        print("morning_strategy: OK")
    patch_env()
    print(".env: OK")


if __name__ == "__main__":
    main()
