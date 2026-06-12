#!/usr/bin/env python3
"""Relax far-strike settlement (dist leads) + lower MIDDAY min_trend."""
from pathlib import Path
import re
import sys

FAR_OLD = '''        else:
            bs_dir = "UP" if base_up_prob >= 0.5 else "DOWN"
            dist_dir = "UP" if dist_pct > 0 else "DOWN"
            if bs_dir != dist_dir:
                self._diag_log(
                    f"settle-far-{coin}",
                    f"[SETTLEMENT] {coin}: BS→{bs_dir} dist→{dist_dir} "
                    f"(dist={dist_pct*100:+.3f}% N(d2)={base_up_prob:.1%}) — abstain",
                    12.0,
                )
                return None
            settlement_dir = bs_dir
            combined_prob = 0.55 * base_up_prob + 0.25 * raw_prob + 0.20 * book_up'''

FAR_NEW = '''        else:
            # Far from strike: level (dist) leads; BS only vetoes strong disagreement
            dist_dir = "UP" if dist_pct > 0 else "DOWN"
            _bs_veto = float(os.getenv("SETTLEMENT_FAR_BS_VETO", "0.05"))
            if dist_dir == "UP" and base_up_prob < (0.50 - _bs_veto):
                self._diag_log(
                    f"settle-far-{coin}",
                    f"[SETTLEMENT] {coin}: dist→UP vetoed by BS={base_up_prob:.1%} "
                    f"(dist={dist_pct*100:+.3f}%) — abstain",
                    12.0,
                )
                return None
            if dist_dir == "DOWN" and base_up_prob > (0.50 + _bs_veto):
                self._diag_log(
                    f"settle-far-{coin}",
                    f"[SETTLEMENT] {coin}: dist→DOWN vetoed by BS={base_up_prob:.1%} "
                    f"(dist={dist_pct*100:+.3f}%) — abstain",
                    12.0,
                )
                return None
            settlement_dir = dist_dir
            combined_prob = 0.45 * base_up_prob + 0.25 * raw_prob + 0.20 * book_up + (
                0.10 * (base_up_prob if dist_dir == "UP" else (1.0 - base_up_prob))
            )'''


def patch_predictor(root: Path) -> None:
    path = root / "predictor.py"
    text = path.read_text(encoding="utf-8")
    if FAR_OLD not in text:
        raise SystemExit(f"predictor far-settlement block not found in {path}")
    path.write_text(text.replace(FAR_OLD, FAR_NEW, 1), encoding="utf-8")
    print(f"OK {path}")


def patch_env(env_path: Path) -> None:
    text = env_path.read_text(encoding="utf-8")
    if "SESSION_P3_MIN_TREND=0.15" in text:
        print(f"OK {env_path} (already 0.15)")
        return
    if "SESSION_P3_MIN_TREND=0.22" in text:
        text = text.replace("SESSION_P3_MIN_TREND=0.22", "SESSION_P3_MIN_TREND=0.15", 1)
    else:
        text = re.sub(
            r"^SESSION_P3_MIN_TREND=.*$",
            "SESSION_P3_MIN_TREND=0.15",
            text,
            count=1,
            flags=re.M,
        )
        if "SESSION_P3_MIN_TREND=" not in text:
            text += "\nSESSION_P3_MIN_TREND=0.15\nSETTLEMENT_FAR_BS_VETO=0.05\n"
    if "SETTLEMENT_FAR_BS_VETO=" not in text:
        text += "SETTLEMENT_FAR_BS_VETO=0.05\n"
    env_path.write_text(text, encoding="utf-8")
    print(f"OK {env_path}")


def main():
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    patch_predictor(root)
    patch_env(root / ".env")
    print("PATCH OK")


if __name__ == "__main__":
    main()
