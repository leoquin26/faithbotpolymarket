#!/usr/bin/env python3
"""Loosen stacked gates — 0 SIGNALs since 9am due to THIN DIST + WEAK TREND + EXPENSIVE DOWN."""
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path("/home/ubuntu/v3-bot")
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")


def backup(p: Path):
    shutil.copy2(p, p.with_suffix(p.suffix + f".bak_{STAMP}"))


def patch_env():
    p = ROOT / ".env"
    backup(p)
    updates = {
        "MIN_TREND_SCORE": "0.30",
        "MIN_TREND_ABS": "0.30",
        "CHOPPY_MIN_TREND_ABS": "0.42",
        "MIN_DIST_UP_PCT": "0.0010",
        "MIN_DIST_DOWN_PCT": "0.0010",
        "EXPENSIVE_DOWN_MIN_DIST": "0.0018",
        "EXPENSIVE_DOWN_MAX_ASK": "0.62",
        "FLIP_TREND_MIN_15M": "0.85",
        "ACCURACY_DIST_PENALTY": "0.0006",
        "DIST_PENALTY_SKIP_ABOVE": "0.0008",
        "ENTRY_MAX_DOWN": "0.75",
    }
    lines = p.read_text(encoding="utf-8").splitlines()
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


def patch_predictor_defaults():
    p = ROOT / "predictor.py"
    backup(p)
    text = p.read_text(encoding="utf-8")
    reps = [
        ('float(os.getenv("MIN_DIST_UP_PCT", "0.0015"))', 'float(os.getenv("MIN_DIST_UP_PCT", "0.0010"))'),
        ('float(os.getenv("MIN_DIST_DOWN_PCT", "0.0015"))', 'float(os.getenv("MIN_DIST_DOWN_PCT", "0.0010"))'),
        ('float(os.getenv("EXPENSIVE_DOWN_MIN_DIST", "0.0025"))', 'float(os.getenv("EXPENSIVE_DOWN_MIN_DIST", "0.0018"))'),
        ('float(os.getenv("EXPENSIVE_DOWN_MAX_ASK", "0.55"))', 'float(os.getenv("EXPENSIVE_DOWN_MAX_ASK", "0.62"))'),
        ('float(os.getenv("FLIP_TREND_MIN_15M", "1.0"))', 'float(os.getenv("FLIP_TREND_MIN_15M", "0.85"))'),
    ]
    for old, new in reps:
        text = text.replace(old, new)
    p.write_text(text, encoding="utf-8")
    print("patched predictor defaults")


def reset_daily_if_stale():
    """Reset daily PNL if date is stale (bot uptime > 1 day without restart)."""
    import json
    from datetime import datetime
    f = ROOT / "data" / "daily_pnl.json"
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        data = json.loads(f.read_text())
        if data.get("date") != today:
            data = {"date": today, "losses": 0.0, "wins": 0.0, "trades": 0}
            f.write_text(json.dumps(data, indent=2) + "\n")
            print(f"reset daily_pnl.json to {today}")
        else:
            print(f"daily_pnl ok for {today}: losses={data.get('losses')} trades={data.get('trades')}")
    except Exception as e:
        print(f"daily_pnl check: {e}")


def main():
    patch_env()
    patch_predictor_defaults()
    reset_daily_if_stale()
    subprocess.run([sys.executable, "-m", "py_compile", str(ROOT / "predictor.py")], check=True)
    print("OK")


if __name__ == "__main__":
    main()
