#!/usr/bin/env python3
"""Fix CHEAP gate killing 35-39c Pattern A entries during early window."""
from pathlib import Path
import shutil
from datetime import datetime

PRED = Path("/home/ubuntu/v3-bot/predictor.py")
ENV = Path("/home/ubuntu/v3-bot/.env")
ts = datetime.now().strftime("%Y%m%d_%H%M%S")

OLD = """        if ask < entry_min:
            self._diag_log(
                f"cheap-{coin}-{direction}",
                f"[CHEAP] {coin} {direction}: ask={ask*100:.0f}c < {entry_min*100:.0f}c", 30.0)
            return None"""

NEW = """        _cheap_floor = float(os.getenv("EARLY_CHEAP_FLOOR", "0.35"))
        if ask < entry_min:
            _cheap_bypass = _compound_cheap_ok or (
                window_age < _early_entry_sec and ask >= _cheap_floor
            )
            if _cheap_bypass:
                logger.info(
                    f"[CHEAP BYPASS] {coin} {direction}: ask={ask*100:.0f}c "
                    f"< entry_min={entry_min*100:.0f}c — allowed "
                    f"(early={window_age:.0f}s floor={_cheap_floor*100:.0f}c)"
                )
            else:
                self._diag_log(
                    f"cheap-{coin}-{direction}",
                    f"[CHEAP] {coin} {direction}: ask={ask*100:.0f}c < {entry_min*100:.0f}c",
                    30.0,
                )
                return None"""


def main() -> None:
    text = PRED.read_text(encoding="utf-8")
    if OLD not in text:
        if "[CHEAP BYPASS]" in text:
            print("predictor: already patched")
        else:
            raise SystemExit("anchor not found")
    else:
        shutil.copy2(PRED, PRED.with_suffix(f".py.bak_{ts}"))
        PRED.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")
        print("predictor: OK")

    env = ENV.read_text(encoding="utf-8")
    updates = {
        "EARLY_ENTRY_MIN": "0.35",
        "EARLY_CHEAP_FLOOR": "0.35",
    }
    for k, v in updates.items():
        needle = f"{k}="
        if needle in env:
            lines = []
            for line in env.splitlines():
                if line.startswith(needle):
                    lines.append(f"{k}={v}")
                else:
                    lines.append(line)
            env = "\n".join(lines) + ("\n" if env.endswith("\n") else "")
        else:
            env = env.rstrip() + f"\n{k}={v}\n"
    ENV.write_text(env, encoding="utf-8")
    print(".env: updated EARLY_ENTRY_MIN / EARLY_CHEAP_FLOOR")


if __name__ == "__main__":
    main()
