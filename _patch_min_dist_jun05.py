#!/usr/bin/env python3
"""Require real distance from strike + block bounce entries on expensive side."""
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path("/home/ubuntu/v3-bot")
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")


def backup(p: Path):
    shutil.copy2(p, p.with_suffix(p.suffix + f".bak_{STAMP}"))


def patch_predictor():
    p = ROOT / "predictor.py"
    backup(p)
    text = p.read_text(encoding="utf-8")

    anchor = "        # Spot vs strike: never buy DOWN above strike / UP below strike"
    block = '''        # ── Minimum distance: thin dist = coin flip, not a real edge ──
        _min_dist_up = float(os.getenv("MIN_DIST_UP_PCT", "0.0015"))
        _min_dist_dn = float(os.getenv("MIN_DIST_DOWN_PCT", "0.0015"))
        if direction == "UP" and dist_pct < _min_dist_up:
            self._diag_log(
                f"thin-dist-{coin}",
                f"[THIN DIST] {coin} UP: dist={dist_pct*100:+.3f}% < {_min_dist_up*100:.2f}% above strike — skip",
                12.0,
            )
            return None
        if direction == "DOWN" and dist_pct > -_min_dist_dn:
            self._diag_log(
                f"thin-dist-{coin}",
                f"[THIN DIST] {coin} DOWN: dist={dist_pct*100:+.3f}% > -{_min_dist_dn*100:.2f}% below strike — skip",
                12.0,
            )
            return None

        # ── Bounce guard: roc60 positive + thin dist below strike = dead cat, not DOWN ──
        if (direction == "DOWN" and roc_60 > float(os.getenv("BOUNCE_ROC60_MIN", "0.00005"))
                and dist_pct > -float(os.getenv("BOUNCE_DIST_MAX", "0.0025"))):
            self._diag_log(
                f"bounce-{coin}",
                f"[BOUNCE] {coin} DOWN: roc60={roc_60*10000:+.1f}bps dist={dist_pct*100:+.3f}% — bounce, skip",
                12.0,
            )
            return None

        # ── Expensive DOWN needs deep dist (56-64c DOWN on -0.2% dist = today's loss) ──
        _exp_dn_ask = float(os.getenv("EXPENSIVE_DOWN_MAX_ASK", "0.55"))
        _exp_dn_dist = float(os.getenv("EXPENSIVE_DOWN_MIN_DIST", "0.0025"))
        if direction == "DOWN" and ask >= _exp_dn_ask and abs(dist_pct) < _exp_dn_dist:
            self._diag_log(
                f"exp-dn-{coin}",
                f"[EXPENSIVE DOWN] {coin}: ask={ask*100:.0f}c dist={dist_pct*100:+.3f}% "
                f"need {_exp_dn_dist*100:.2f}%+ cushion — skip",
                12.0,
            )
            return None

'''
    if "[THIN DIST]" not in text:
        if anchor not in text:
            raise SystemExit("anchor not found")
        text = text.replace(anchor, block + anchor, 1)

    p.write_text(text, encoding="utf-8")
    print("patched predictor.py")


def patch_env():
    p = ROOT / ".env"
    backup(p)
    updates = {
        "MIN_DIST_UP_PCT": "0.0015",
        "MIN_DIST_DOWN_PCT": "0.0015",
        "EXPENSIVE_DOWN_MAX_ASK": "0.55",
        "EXPENSIVE_DOWN_MIN_DIST": "0.0025",
        "BOUNCE_ROC60_MIN": "0.00005",
        "BOUNCE_DIST_MAX": "0.0025",
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


def main():
    patch_predictor()
    patch_env()
    subprocess.run([sys.executable, "-m", "py_compile", str(ROOT / "predictor.py")], check=True)
    print("OK")


if __name__ == "__main__":
    main()
