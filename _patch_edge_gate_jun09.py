#!/usr/bin/env python3
"""Fix order_manager hardcoded 2% edge gate + lower afternoon min_trend."""
from pathlib import Path
import re
import sys

OM_OLD = '''        real_edge = pred.probability - actual_entry
        if real_edge < 0.02:
            logger.info(
                f"[EDGE GATE] {coin}: real_edge={real_edge*100:.1f}% "
                f"(post={pred.probability*100:.0f}% - ask={actual_entry*100:.0f}c) < 2%"
            )
            return False'''

OM_NEW = '''        real_edge = pred.probability - actual_entry
        _min_edge = float(getattr(config, "MIN_EDGE", 0.02))
        if real_edge < _min_edge:
            logger.info(
                f"[EDGE GATE] {coin}: real_edge={real_edge*100:.1f}% "
                f"(post={pred.probability*100:.0f}% - ask={actual_entry*100:.0f}c) < {_min_edge*100:.0f}%"
            )
            return False'''


def main():
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    om = root / "order_manager.py"
    text = om.read_text(encoding="utf-8")
    if OM_OLD not in text:
        raise SystemExit("order_manager edge gate block not found")
    om.write_text(text.replace(OM_OLD, OM_NEW, 1), encoding="utf-8")
    print(f"OK {om}")

    env = root / ".env"
    et = env.read_text(encoding="utf-8")
    updates = {
        "MIN_EDGE_THRESHOLD": "0.01",
        "SESSION_AFTERNOON_MIN_EDGE": "0.01",
        "SESSION_AFTERNOON_MIN_TREND": "0.12",
    }
    for key, val in updates.items():
        pat = rf"^{re.escape(key)}=.*$"
        if re.search(pat, et, re.M):
            et = re.sub(pat, f"{key}={val}", et, count=1, flags=re.M)
        else:
            et += f"\n{key}={val}\n"
    env.write_text(et, encoding="utf-8")
    print(f"OK {env}")
    print("PATCH OK")


if __name__ == "__main__":
    main()
