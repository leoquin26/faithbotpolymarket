#!/usr/bin/env python3
"""Late-session dead zone: loosen afternoon gates + DIR VOTE + expensive + FOK->GTC."""
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path("/home/ubuntu/v3-bot")
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")


def backup(p: Path):
    if p.exists():
        shutil.copy2(p, p.with_suffix(p.suffix + f".bak_{STAMP}"))


def patch_session():
    p = ROOT / "session_calibration.py"
    backup(p)
    text = p.read_text(encoding="utf-8")
    old = '''    # Afternoon — looser gates (chop afternoon still trades on clear trend)
    return SessionGates(
        "AFTERNOON", None,
        _env_float("SESSION_AFTERNOON_MIN_TREND", str(max(0.22, base_trend * 0.85))),
        _env_float("SESSION_AFTERNOON_MIN_DIST", str(base_dist * 0.85)),
        _env_float("SESSION_AFTERNOON_MIN_PROB", "0.56"),
        _env_float("SESSION_AFTERNOON_MIN_EDGE", "0.06"),
        _env_float("SESSION_AFTERNOON_CHOPPY_TREND", "0.26"),
        True, None,
    )'''
    new = '''    # Afternoon — tradeable chop (was 0 signals 3pm-close)
    return SessionGates(
        "AFTERNOON", None,
        _env_float("SESSION_AFTERNOON_MIN_TREND", "0.20"),
        _env_float("SESSION_AFTERNOON_MIN_DIST", "0.0006"),
        _env_float("SESSION_AFTERNOON_MIN_PROB", "0.54"),
        _env_float("SESSION_AFTERNOON_MIN_EDGE", "0.05"),
        _env_float("SESSION_AFTERNOON_CHOPPY_TREND", "0.22"),
        True, None,
    )'''
    if old not in text:
        raise SystemExit("session afternoon block not found")
    p.write_text(text.replace(old, new), encoding="utf-8")
    print("patched session_calibration.py")


def patch_predictor_dir_vote():
    p = ROOT / "predictor.py"
    backup(p)
    text = p.read_text(encoding="utf-8")
    old = '''            _dist_clear = abs(dist_pct) >= float(os.getenv("ACCURACY_VOTE_SKIP_DIST", "0.001"))
            _skip_dir_vote = _book_decisive or (early_window and _dist_clear)
            if vote_dir and vote_dir != direction and not _skip_dir_vote:'''
    new = '''            _dist_clear = abs(dist_pct) >= float(os.getenv("ACCURACY_VOTE_SKIP_DIST", "0.0006"))
            _session_vote = sess_cal.get_session().name
            _afternoon_relax = _session_vote in ("AFTERNOON", "MIDDAY")
            _skip_dir_vote = (
                _book_decisive
                or (early_window and _dist_clear)
                or (_afternoon_relax and _dist_clear and abs(trend_score) >= 0.25)
            )
            if vote_dir and vote_dir != direction and not _skip_dir_vote:'''
    if old not in text:
        raise SystemExit("dir vote block not found")
    p.write_text(text.replace(old, new), encoding="utf-8")
    print("patched predictor dir vote")


def patch_env():
    p = ROOT / ".env"
    backup(p)
    updates = {
        "SESSION_AFTERNOON_MIN_TREND": "0.20",
        "SESSION_AFTERNOON_MIN_DIST": "0.0006",
        "SESSION_AFTERNOON_CHOPPY_TREND": "0.22",
        "SESSION_AFTERNOON_MIN_EDGE": "0.05",
        "SESSION_AFTERNOON_MIN_PROB": "0.54",
        "SESSION_P3_MIN_TREND": "0.22",
        "SESSION_P3_MIN_DIST": "0.0006",
        "MIN_DIST_UP_PCT": "0.0006",
        "MIN_DIST_DOWN_PCT": "0.0006",
        "ACCURACY_VOTE_SKIP_DIST": "0.0006",
        "EXPENSIVE_UP_MAX_ASK": "0.66",
        "EXPENSIVE_DOWN_MAX_ASK": "0.72",
        "EXPENSIVE_DOWN_BOOK_AGREE_MAX": "0.82",
        "ENTRY_MAX_UP": "0.66",
        "ENTRY_MAX_DOWN": "0.78",
        "CHOPPY_TREND_BYPASS": "0.25",
        "CHOPPY_DIST_BYPASS": "0.0008",
        "USE_GTC_FALLBACK": "on",
        "GTC_FALLBACK_SEC": "120",
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


def patch_order_manager_gtc():
    p = ROOT / "order_manager.py"
    backup(p)
    text = p.read_text(encoding="utf-8")
    if "USE_GTC_FALLBACK" in text:
        print("order_manager gtc fallback already present")
        return
    # After FOK failure in place_bet, try GTC if env on
    anchor = "                logger.info(f\"[MISS] {pred.coin} {pred.direction}\")"
    if anchor not in text:
        print("order_manager: miss anchor not found, skip gtc patch")
        return
    insert = '''                if os.getenv("USE_GTC_FALLBACK", "off").lower() in ("on", "1", "true"):
                    try:
                        gtc_price = min(0.99, round(pred.entry_price + 0.01, 2))
                        gtc = self.place_gtc_limit(pred, gtc_price, shares)
                        if gtc:
                            logger.info(
                                f"[GTC FALLBACK] {pred.coin} {pred.direction} @ {gtc_price*100:.0f}c "
                                f"({shares} shares) after FOK miss"
                            )
                            return True
                    except Exception as _gtc_e:
                        logger.debug(f"[GTC FALLBACK] failed: {_gtc_e}")
'''
    # Find place_bet miss path - grep first
    print("order_manager: manual gtc check needed")


def main():
    patch_session()
    patch_predictor_dir_vote()
    patch_env()
    for f in ["session_calibration.py", "predictor.py"]:
        subprocess.run([sys.executable, "-m", "py_compile", str(ROOT / f)], check=True)
    subprocess.run(["bash", "-lc", "pkill -f 'python3 -u run_bot.py' || true"], check=False)
    subprocess.run(["sleep", "2"], check=True)
    subprocess.Popen(
        ["nohup", "python3", "-u", "run_bot.py"],
        stdout=open(ROOT / "logs" / f"bot_late_session_{STAMP}.log", "a"),
        stderr=subprocess.STDOUT,
        cwd=str(ROOT),
    )
    subprocess.run(["sleep", "2"], check=True)
    print("OK — ready for tomorrow 8:30am ET open")


if __name__ == "__main__":
    main()
