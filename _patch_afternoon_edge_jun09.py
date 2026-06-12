#!/usr/bin/env python3
"""Afternoon unblock: session thin-dist, 2% edge, run_bot session edge."""
from pathlib import Path
import re
import sys

THIN_OLD = '''        # ── Minimum distance: thin dist = coin flip, not a real edge ──
        _min_dist_up = float(os.getenv("MIN_DIST_UP_PCT", "0.0010"))
        _min_dist_dn = float(os.getenv("MIN_DIST_DOWN_PCT", "0.0010"))'''

THIN_NEW = '''        # ── Minimum distance: thin dist = coin flip, not a real edge ──
        _sg_dist = sess_cal.get_session()
        _min_dist_up = _sg_dist.min_dist
        _min_dist_dn = _sg_dist.min_dist'''

RUNBOT_ACTIONABLE_OLD = '''            actionable = [
                p for p in predictions
                if p.confidence in ("HIGH", "MEDIUM")
                and p.edge >= config.MIN_EDGE
            ]'''

RUNBOT_ACTIONABLE_NEW = '''            _pm_sess = sess_cal.get_session()
            _pm_min_edge = max(config.MIN_EDGE, _pm_sess.min_edge)
            actionable = [
                p for p in predictions
                if p.confidence in ("HIGH", "MEDIUM")
                and p.edge >= _pm_min_edge
            ]'''

RUNBOT_CLOB_OLD = '''                                if real_edge < config.MIN_EDGE:
                                    logger.info(
                                        f"[CLOB REJECT] {best.coin} {best.direction}: "
                                        f"CLOB ask={clob_ask*100:.0f}c prob={best.probability:.0%} "
                                        f"real_edge={real_edge*100:.1f}% < {config.MIN_EDGE*100:.0f}%"
                                    )'''

RUNBOT_CLOB_NEW = '''                                if real_edge < _pm_min_edge:
                                    logger.info(
                                        f"[CLOB REJECT] {best.coin} {best.direction}: "
                                        f"CLOB ask={clob_ask*100:.0f}c prob={best.probability:.0%} "
                                        f"real_edge={real_edge*100:.1f}% < {_pm_min_edge*100:.0f}%"
                                    )'''


def patch_file(path: Path, pairs: list) -> None:
    text = path.read_text(encoding="utf-8")
    for old, new, label in pairs:
        if old not in text:
            raise SystemExit(f"MISSING [{label}] in {path}")
        text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8")
    print(f"OK {path}")


def ensure_import_run_bot(text: str) -> str:
    if "import session_calibration as sess_cal" in text:
        return text
    anchor = "import morning_strategy as morn"
    if anchor not in text:
        raise SystemExit("run_bot import anchor missing")
    return text.replace(anchor, anchor + "\nimport session_calibration as sess_cal", 1)


def patch_env(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    updates = {
        "SESSION_AFTERNOON_MIN_EDGE": "0.02",
        "SESSION_AFTERNOON_MIN_DIST": "0.0005",
        "MIN_EDGE_THRESHOLD": "0.02",
        "BOUNCE_ROC60_MIN": "0.00010",
        "ACCURACY_CONFIRM_SCANS": "1",
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
    patch_file(root / "predictor.py", [(THIN_OLD, THIN_NEW, "thin dist session")])
    rb = root / "run_bot.py"
    rb_text = rb.read_text(encoding="utf-8")
    rb_text = ensure_import_run_bot(rb_text)
    rb.write_text(rb_text, encoding="utf-8")
    patch_file(rb, [
        (RUNBOT_ACTIONABLE_OLD, RUNBOT_ACTIONABLE_NEW, "actionable edge"),
        (RUNBOT_CLOB_OLD, RUNBOT_CLOB_NEW, "clob edge"),
    ])
    patch_env(root / ".env")
    print("PATCH OK")


if __name__ == "__main__":
    main()
