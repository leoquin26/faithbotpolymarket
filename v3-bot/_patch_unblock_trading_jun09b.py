#!/usr/bin/env python3
"""Unblock trading: FLIP GUARD dist bypass, lower MIDDAY gates."""
from pathlib import Path
import re
import sys

FLIP_OLD = '''        if len(recent_hist) >= 3:
            opposite = sum(1 for d in recent_hist if d and d != direction)
            FLIP_TREND_MIN = float(os.getenv("FLIP_TREND_MIN_15M", "0.85"))
            if opposite >= 3 and abs(trend_score) < FLIP_TREND_MIN:
                self._diag_log(
                    f"flipguard-{coin}",
                    f"[FLIP GUARD] {coin} {direction}: recent={'->'.join(recent_hist)} "
                    f"trend={trend_score:+.2f} — need |trend|>={FLIP_TREND_MIN}",
                    12.0,
                )
                return None'''

FLIP_NEW = '''        if len(recent_hist) >= 3:
            opposite = sum(1 for d in recent_hist if d and d != direction)
            FLIP_TREND_MIN = float(os.getenv("FLIP_TREND_MIN_15M", "0.55"))
            _flip_bypass_dist = float(os.getenv("FLIP_GUARD_BYPASS_DIST", "0.0012"))
            _dist_agrees = (
                (direction == "UP" and dist_pct >= _flip_bypass_dist)
                or (direction == "DOWN" and dist_pct <= -_flip_bypass_dist)
            )
            if opposite >= 3 and abs(trend_score) < FLIP_TREND_MIN and not _dist_agrees:
                self._diag_log(
                    f"flipguard-{coin}",
                    f"[FLIP GUARD] {coin} {direction}: recent={'->'.join(recent_hist)} "
                    f"trend={trend_score:+.2f} dist={dist_pct*100:+.3f}% — need |trend|>={FLIP_TREND_MIN}",
                    12.0,
                )
                return None'''

SETTLE_ROC_OLD = '''            roc_dir = _dir_from_sign(roc_300, _min_roc)
            if roc_dir and roc_dir != level_dir:
                self._diag_log(
                    f"settle-roc-{coin}",
                    f"[SETTLEMENT] {coin}: dist→{level_dir} roc300→{roc_dir} "
                    f"(dist={dist_pct*100:+.3f}% roc300={roc_300*10000:+.1f}bps) — abstain",
                    12.0,
                )
                return None'''

SETTLE_ROC_NEW = '''            _roc_veto = float(os.getenv("SETTLEMENT_ROC_VETO", "0.00008"))
            if abs(roc_300) >= _roc_veto:
                roc_dir = _dir_from_sign(roc_300, _min_roc)
                if roc_dir and roc_dir != level_dir:
                    self._diag_log(
                        f"settle-roc-{coin}",
                        f"[SETTLEMENT] {coin}: dist→{level_dir} roc300→{roc_dir} "
                        f"(dist={dist_pct*100:+.3f}% roc300={roc_300*10000:+.1f}bps) — abstain",
                        12.0,
                    )
                    return None'''


def patch_file(path: Path, pairs: list) -> None:
    text = path.read_text(encoding="utf-8")
    for old, new, label in pairs:
        if old not in text:
            raise SystemExit(f"MISSING [{label}] in {path}")
        text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8")
    print(f"OK {path}")


def patch_env(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    updates = {
        "FLIP_TREND_MIN_15M": "0.55",
        "FLIP_GUARD_BYPASS_DIST": "0.0012",
        "SESSION_P3_MIN_TREND": "0.12",
        "SESSION_P3_MIN_EDGE": "0.05",
        "SETTLEMENT_ROC_VETO": "0.00008",
        "MIN_EDGE_THRESHOLD": "0.05",
    }
    for key, val in updates.items():
        pat = rf"^{re.escape(key)}=.*$"
        if re.search(pat, text, re.M):
            text = re.sub(pat, f"{key}={val}", text, count=1, flags=re.M)
        else:
            text += f"\n{key}={val}\n"
    path.write_text(text, encoding="utf-8")
    print(f"OK {path}")


def main():
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    patch_file(root / "predictor.py", [
        (FLIP_OLD, FLIP_NEW, "flip guard bypass"),
        (SETTLE_ROC_OLD, SETTLE_ROC_NEW, "settlement roc veto"),
    ])
    patch_env(root / ".env")
    print("PATCH OK")


if __name__ == "__main__":
    main()
