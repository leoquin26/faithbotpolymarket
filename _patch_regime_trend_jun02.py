#!/usr/bin/env python3
"""Fix regime weak-trend SKIP killing Pattern A (trend 0.93 < alpha_low 1.0)."""
from pathlib import Path
import shutil
from datetime import datetime

ROOT = Path("/home/ubuntu/v3-bot")
RS = ROOT / "regime_aware" / "regime_strategy.py"
PRED = ROOT / "predictor.py"
ENV = ROOT / ".env"
ts = datetime.now().strftime("%Y%m%d_%H%M%S")

OLD_INIT = """    def __init__(self,
                 trend_alpha_low: float = 1.0,
                 trend_alpha_high: float = 1.5,
                 trend_extreme: float = 2.0,
                 anti_signal_wr: float = 0.40,
                 alpha_signal_wr: float = 0.65):
        self.trend_alpha_low = trend_alpha_low
        self.trend_alpha_high = trend_alpha_high
        self.trend_extreme = trend_extreme"""

NEW_INIT = """    def __init__(self,
                 trend_alpha_low: float = None,
                 trend_alpha_high: float = None,
                 trend_extreme: float = None,
                 anti_signal_wr: float = 0.40,
                 alpha_signal_wr: float = 0.65):
        import os as _os_rs
        self.trend_alpha_low = float(trend_alpha_low if trend_alpha_low is not None
            else _os_rs.getenv("REGIME_TREND_ALPHA_LOW", "0.85"))
        self.trend_alpha_high = float(trend_alpha_high if trend_alpha_high is not None
            else _os_rs.getenv("REGIME_TREND_ALPHA_HIGH", "1.5"))
        self.trend_extreme = float(trend_extreme if trend_extreme is not None
            else _os_rs.getenv("REGIME_TREND_EXTREME", "2.0"))"""

OLD_WEAK = """            else:
                # Weak trend in reverting regime: skip
                return Action("SKIP", direction, 0, "reverting+weak-trend")"""

NEW_WEAK = """            else:
                # Jun-2 PM: Pattern A — high-edge cheap entry despite sub-1.0 trend scale
                import os as _os_pa
                _pa_edge = float(_os_pa.getenv("REGIME_PATTERN_A_MIN_EDGE", "0.15"))
                _pa_ask = float(_os_pa.getenv("REGIME_PATTERN_A_MAX_ASK", "0.55"))
                _pa_prob = float(_os_pa.getenv("REGIME_PATTERN_A_MIN_PROB", "0.62"))
                _pa_trend = float(_os_pa.getenv("REGIME_PATTERN_A_MIN_TREND", "0.50"))
                if (signal.edge >= _pa_edge and signal.ask <= _pa_ask
                        and signal.prob >= _pa_prob and trend_abs >= _pa_trend):
                    return Action(
                        "TRADE_HALF", direction, 0.5,
                        f"reverting+pattern-A(e={signal.edge*100:.0f}%@{ask*100:.0f}c)",
                    )
                return Action("SKIP", direction, 0, "reverting+weak-trend")"""

OLD_CHEAP_BYPASS = """            _cheap_bypass = _compound_cheap_ok or (
                window_age < _early_entry_sec and ask >= _cheap_floor
            )"""

NEW_CHEAP_BYPASS = """            _cheap_bypass = ask >= _cheap_floor and (
                _compound_cheap_ok
                or (window_age < _early_entry_sec and ask >= _cheap_floor)
            )"""


def patch_file(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        if new.strip()[:40] in text:
            print(f"  {label}: already patched")
            return
        raise SystemExit(f"  {label}: anchor not found")
    shutil.copy2(path, path.with_suffix(path.suffix + f".bak_{ts}"))
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print(f"  {label}: OK")


def patch_env() -> None:
    env = ENV.read_text(encoding="utf-8")
    adds = {
        "REGIME_TREND_ALPHA_LOW": "0.85",
        "REGIME_PATTERN_A_MIN_EDGE": "0.15",
        "REGIME_PATTERN_A_MAX_ASK": "0.55",
        "REGIME_PATTERN_A_MIN_PROB": "0.62",
        "REGIME_PATTERN_A_MIN_TREND": "0.50",
    }
    for k, v in adds.items():
        if f"{k}=" not in env:
            env = env.rstrip() + f"\n{k}={v}\n"
        else:
            lines = []
            for line in env.splitlines():
                if line.startswith(f"{k}="):
                    lines.append(f"{k}={v}")
                else:
                    lines.append(line)
            env = "\n".join(lines) + ("\n" if env.endswith("\n") else "")
    ENV.write_text(env, encoding="utf-8")
    print("  .env: OK")


def main() -> None:
    print("Patching regime trend + cheap floor...")
    patch_file(RS, OLD_INIT, NEW_INIT, "regime_strategy init")
    patch_file(RS, OLD_WEAK, NEW_WEAK, "regime pattern-A")
    patch_file(PRED, OLD_CHEAP_BYPASS, NEW_CHEAP_BYPASS, "predictor cheap floor")
    patch_env()
    print("Done.")


if __name__ == "__main__":
    main()
