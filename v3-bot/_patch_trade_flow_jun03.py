#!/usr/bin/env python3
"""Jun 3: unblock trades — exhaust T_rem units bug, extend P1, relax gates."""
import re
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path("/home/ubuntu/v3-bot")
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")


def backup(p: Path):
    b = p.with_suffix(p.suffix + f".bak_{STAMP}")
    shutil.copy2(p, b)
    print(f"backup {b}")


def patch_run_bot():
    p = ROOT / "run_bot.py"
    backup(p)
    text = p.read_text(encoding="utf-8")

    old = (
        '                        _t_rem = float(getattr(getattr(_p, "market_info", None), "time_remaining", 0) or 0)\n'
        '                        _ep = (_p.entry_price if _p.entry_price > 0.05 else _p.poly_price)\n'
        '                        if (_act == "ABSTAIN" and not _was_overridden\n'
        '                                and _t_rem >= _early_t\n'
    )
    new = (
        '                        # market_info.time_remaining is MINUTES; thresholds are SECONDS\n'
        '                        _t_rem_raw = float(getattr(getattr(_p, "market_info", None), "time_remaining", 0) or 0)\n'
        '                        _t_rem = _t_rem_raw * 60.0 if _t_rem_raw < 200 else _t_rem_raw\n'
        '                        _ep = (_p.entry_price if _p.entry_price > 0.05 else _p.poly_price)\n'
        '                        if (_act == "ABSTAIN" and not _was_overridden\n'
        '                                and _t_rem >= _early_t\n'
    )
    if old not in text:
        raise SystemExit("run_bot: early T_rem block not found")
    text = text.replace(old, new, 1)

    mid_anchor = (
        '                            _act = "DAMPEN"\n'
        '                            _was_overridden = True\n'
        '                        if _act == "ABSTAIN":\n'
        '                            # ── Fix A apr23: sticky EXHAUST ABSTAIN memory ──\n'
    )
    mid_insert = (
        '                            _act = "DAMPEN"\n'
        '                            _was_overridden = True\n'
        '                        # Jun-3: mid-window dampen — score<=0.60, edge>=7%, 40-68c, T>=10min left\n'
        '                        _mid_score = float(os.getenv("EXHAUST_MID_SCORE_MAX", "0.60"))\n'
        '                        _mid_edge = float(os.getenv("EXHAUST_MID_MIN_EDGE", "0.07"))\n'
        '                        _mid_t = float(os.getenv("EXHAUST_MID_MIN_T_SEC", "600"))\n'
        '                        if (_act == "ABSTAIN" and not _was_overridden\n'
        '                                and _t_rem >= _mid_t\n'
        '                                and 0.40 <= _ep <= 0.68\n'
        '                                and float(_p.edge or 0) >= _mid_edge\n'
        '                                and float(_res.get("score", 0) or 0) <= _mid_score):\n'
        '                            logger.info(\n'
        '                                f"[EXHAUST OVERRIDE-MID] {_p.coin} {_p.direction}: "\n'
        '                                f"entry={_ep*100:.0f}c edge={float(_p.edge)*100:.1f}% "\n'
        '                                f"T={_t_rem:.0f}s score={_res.get(\'score\', 0):.2f} -> DAMPEN"\n'
        '                            )\n'
        '                            _act = "DAMPEN"\n'
        '                            _was_overridden = True\n'
        '                        if _act == "ABSTAIN":\n'
        '                            # ── Fix A apr23: sticky EXHAUST ABSTAIN memory ──\n'
    )
    if mid_anchor not in text:
        raise SystemExit("run_bot: mid-insert anchor not found")
    text = text.replace(mid_anchor, mid_insert, 1)

    p.write_text(text, encoding="utf-8")
    print("patched run_bot.py")


def patch_morning():
    p = ROOT / "morning_strategy.py"
    backup(p)
    text = p.read_text(encoding="utf-8")
    old = (
        '    if h < 10 or (h == 10 and m < 30):\n'
        '        return 1\n'
        '    if (h == 10 and m >= 30) or h == 11:\n'
        '        return 2\n'
    )
    new = (
        '    _p1_end_h = int(_os_ms.getenv("MORNING_P1_END_HOUR", "10"))\n'
        '    _p1_end_m = int(_os_ms.getenv("MORNING_P1_END_MINUTE", "59"))\n'
        '    if h < 10 or (h == _p1_end_h and m <= _p1_end_m):\n'
        '        return 1\n'
        '    if h == 11:\n'
        '        return 2\n'
    )
    if old not in text:
        raise SystemExit("morning_strategy: phase block not found")
    text = text.replace(old, new, 1)
    p.write_text(text, encoding="utf-8")
    print("patched morning_strategy.py")


def patch_env():
    env = ROOT / ".env"
    backup(env)
    lines = env.read_text(encoding="utf-8").splitlines()
    updates = {
        "MORNING_P1_END_HOUR": "10",
        "MORNING_P1_END_MINUTE": "59",
        "EXHAUST_EARLY_MIN_EDGE": "0.07",
        "EXHAUST_MID_SCORE_MAX": "0.60",
        "EXHAUST_MID_MIN_EDGE": "0.07",
        "EXHAUST_MID_MIN_T_SEC": "600",
        "REGIME_PATTERN_A_MIN_EDGE": "0.08",
    }
    keys = set(updates)
    out = []
    seen = set()
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.strip().startswith("#") else None
        if key in keys:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    for k, v in updates.items():
        if k not in seen:
            out.append(f"{k}={v}")
    env.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("patched .env")


def main():
    patch_run_bot()
    patch_morning()
    patch_env()
    print("OK")


if __name__ == "__main__":
    main()
