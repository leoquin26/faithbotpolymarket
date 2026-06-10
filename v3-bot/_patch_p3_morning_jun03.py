#!/usr/bin/env python3
"""Fix P3 morning gate: env var names + Pattern-A like P1."""
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path("/home/ubuntu/v3-bot")
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")


def backup(p: Path):
    shutil.copy2(p, p.with_suffix(p.suffix + f".bak_{STAMP}"))


def patch_morning():
    p = ROOT / "morning_strategy.py"
    backup(p)
    text = p.read_text(encoding="utf-8")
    old = """    if phase == 3:
        if coin not in P3_ALLOWED:
            return None
        if prob < P3_MIN_PROB:
            logger.debug(f"[MORNING P3] {coin} prob {prob:.0%} < {P3_MIN_PROB:.0%}")
            return None
        if edge < P3_MIN_EDGE:
            logger.debug(f"[MORNING P3] {coin} edge {edge*100:.1f}% < {P3_MIN_EDGE*100:.0f}%")
            return None
        if abs_trend < P3_MIN_TREND:
            logger.debug(f"[MORNING P3] {coin} |trend| {abs_trend:.2f} < {P3_MIN_TREND}")
            return None
        logger.info(
            f"[MORNING P3] {coin} {pred.direction} APPROVED | "
            f"Prob={prob:.0%} Edge={edge*100:.1f}% |Trend|={abs_trend:.2f}"
        )
        return pred"""
    new = """    if phase == 3:
        if coin not in P3_ALLOWED:
            return None
        _entry = getattr(pred, "entry_price", 0) or getattr(pred, "poly_price", 0)
        _pa_prob = float(_os_ms.getenv("MORNING_P3_PATTERN_A_PROB", "0.58"))
        _pa_edge = float(_os_ms.getenv("MORNING_P3_PATTERN_A_EDGE", "0.08"))
        _pa_ask = float(_os_ms.getenv("MORNING_P3_PATTERN_A_MAX_ASK", "0.55"))
        _pattern_a = (
            prob >= _pa_prob and edge >= _pa_edge
            and _entry > 0.05 and _entry <= _pa_ask
        )
        _eff_prob = min(P3_MIN_PROB, _pa_prob) if _pattern_a else P3_MIN_PROB
        if prob < _eff_prob:
            logger.debug(
                f"[MORNING P3] {coin} prob {prob:.0%} < {_eff_prob:.0%}"
                f"{' (need pattern-A)' if not _pattern_a else ''}"
            )
            return None
        if edge < P3_MIN_EDGE:
            logger.debug(f"[MORNING P3] {coin} edge {edge*100:.1f}% < {P3_MIN_EDGE*100:.0f}%")
            return None
        if abs_trend < P3_MIN_TREND and not _pattern_a:
            logger.debug(f"[MORNING P3] {coin} |trend| {abs_trend:.2f} < {P3_MIN_TREND}")
            return None
        if _pattern_a:
            logger.info(
                f"[MORNING P3 PATTERN-A] {coin} {pred.direction}: "
                f"prob={prob:.0%} edge={edge*100:.1f}% ask={_entry*100:.0f}c"
            )
        logger.info(
            f"[MORNING P3] {coin} {pred.direction} APPROVED | "
            f"Prob={prob:.0%} Edge={edge*100:.1f}% |Trend|={abs_trend:.2f}"
        )
        return pred"""
    if old not in text:
        raise SystemExit("P3 block not found")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")
    print("patched morning_strategy.py")


def patch_env():
    p = ROOT / ".env"
    backup(p)
    lines = p.read_text(encoding="utf-8").splitlines()
    updates = {
        "MORNING_P3_MIN_PROB": "0.58",
        "MORNING_P3_MIN_EDGE": "0.08",
        "MORNING_P3_MIN_TREND": "0.50",
        "MORNING_P3_PATTERN_A_PROB": "0.58",
        "MORNING_P3_PATTERN_A_EDGE": "0.08",
        "MORNING_P3_PATTERN_A_MAX_ASK": "0.55",
    }
    seen = set()
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.strip().startswith("#") else None
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    for k, v in updates.items():
        if k not in seen:
            out.append(f"{k}={v}")
    p.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("patched .env")


def main():
    patch_morning()
    patch_env()
    print("OK")


if __name__ == "__main__":
    main()
