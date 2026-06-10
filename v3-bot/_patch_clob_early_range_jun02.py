#!/usr/bin/env python3
"""Fix afternoon CLOB RANGE using ENTRY_MIN=55c while predictor allows 35-54c."""
from pathlib import Path
import shutil
from datetime import datetime

RUN = Path("/home/ubuntu/v3-bot/run_bot.py")
ENV = Path("/home/ubuntu/v3-bot/.env")
ts = datetime.now().strftime("%Y%m%d_%H%M%S")

OLD = """                        if clob_ask < config.ENTRY_MIN or clob_ask > config.ENTRY_MAX:
                            logger.info(
                                f"[CLOB RANGE] {best.coin} {best.direction}: "
                                f"CLOB ask={clob_ask*100:.0f}c outside "
                                f"{config.ENTRY_MIN*100:.0f}-{config.ENTRY_MAX*100:.0f}c"
                            )
                            unlock_window(best.coin, best.market_info.window_start)
                            continue"""

NEW = """                        _ws = int(best.market_info.window_start or 0)
                        _w_age = int(time.time()) - _ws if _ws else 9999
                        _early_sec = int(os.getenv("EARLY_ENTRY_SEC", "120"))
                        _clob_min = float(config.ENTRY_MIN)
                        _clob_max = float(config.ENTRY_MAX)
                        if _w_age < _early_sec:
                            _clob_min = min(_clob_min, float(os.getenv("EARLY_ENTRY_MIN", "0.35")))
                        _abs_floor = float(os.getenv("PM_CLOB_MIN", "0.35"))
                        _clob_min = max(_abs_floor, _clob_min)
                        if clob_ask < _clob_min or clob_ask > _clob_max:
                            logger.info(
                                f"[CLOB RANGE] {best.coin} {best.direction}: "
                                f"CLOB ask={clob_ask*100:.0f}c outside "
                                f"{_clob_min*100:.0f}-{_clob_max*100:.0f}c"
                                f"{' (early)' if _w_age < _early_sec else ''}"
                            )
                            unlock_window(best.coin, best.market_info.window_start)
                            continue"""


def main() -> None:
    text = RUN.read_text(encoding="utf-8")
    if OLD not in text:
        if "_clob_min = max(_abs_floor" in text:
            print("run_bot: already patched")
        else:
            raise SystemExit("anchor not found")
    else:
        shutil.copy2(RUN, RUN.with_suffix(f".bak_{ts}"))
        RUN.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")
        print("run_bot CLOB early range: OK")

    env = ENV.read_text(encoding="utf-8")
    for line in ["PM_CLOB_MIN=0.35", "REGIME_PATTERN_A_MIN_PROB=0.60"]:
        k = line.split("=")[0]
        if f"{k}=" not in env:
            env = env.rstrip() + "\n" + line + "\n"
        else:
            lines = []
            for ln in env.splitlines():
                lines.append(line if ln.startswith(f"{k}=") else ln)
            env = "\n".join(lines) + ("\n" if env.endswith("\n") else "")
    ENV.write_text(env, encoding="utf-8")
    print(".env: OK")


if __name__ == "__main__":
    main()
