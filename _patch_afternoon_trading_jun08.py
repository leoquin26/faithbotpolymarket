#!/usr/bin/env python3
"""Fix dead zone after 1-2pm: midday position cap + afternoon chop overblock."""
import os
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


def patch_session_calibration():
    p = ROOT / "session_calibration.py"
    backup(p)
    text = p.read_text(encoding="utf-8")
    # Afternoon block - lower gates
    old = '''    # Afternoon
    return SessionGates(
        "AFTERNOON", None,
        base_trend, base_dist, base_prob, base_edge, chop_trend,
        True, None,
    )'''
    new = '''    # Afternoon — looser gates (chop afternoon still trades on clear trend)
    return SessionGates(
        "AFTERNOON", None,
        _env_float("SESSION_AFTERNOON_MIN_TREND", str(max(0.22, base_trend * 0.85))),
        _env_float("SESSION_AFTERNOON_MIN_DIST", str(base_dist * 0.85)),
        _env_float("SESSION_AFTERNOON_MIN_PROB", "0.56"),
        _env_float("SESSION_AFTERNOON_MIN_EDGE", "0.06"),
        _env_float("SESSION_AFTERNOON_CHOPPY_TREND", "0.26"),
        True, None,
    )'''
    if old not in text:
        raise SystemExit("session_calibration afternoon block not found")
    p.write_text(text.replace(old, new), encoding="utf-8")
    print("patched session_calibration.py")


def patch_predictor():
    p = ROOT / "predictor.py"
    backup(p)
    text = p.read_text(encoding="utf-8")
    old = '''        if is_chop:
            _min_tr = _session.choppy_min_trend
            if abs(trend_score) < _min_tr:
                self._diag_log(
                    f"chopstrict-{coin}",
                    f"[CHOPPY STRICT] {coin}: |trend|={abs(trend_score):.3f} < {_min_tr} "
                    f"session={_session.name} — skip",
                    15.0,
                )
                return None'''
    new = '''        if is_chop:
            _min_tr = _session.choppy_min_trend
            _dist_clear = abs(dist_pct) >= float(os.getenv("CHOPPY_DIST_BYPASS", "0.0012"))
            _trend_strong = abs(trend_score) >= float(os.getenv("CHOPPY_TREND_BYPASS", "0.32"))
            if abs(trend_score) < _min_tr and not (_dist_clear and _trend_strong):
                self._diag_log(
                    f"chopstrict-{coin}",
                    f"[CHOPPY STRICT] {coin}: |trend|={abs(trend_score):.3f} < {_min_tr} "
                    f"session={_session.name} — skip",
                    15.0,
                )
                return None'''
    if old not in text:
        raise SystemExit("predictor choppy strict block not found")
    p.write_text(text.replace(old, new), encoding="utf-8")
    print("patched predictor.py")


def patch_run_bot():
    p = ROOT / "run_bot.py"
    backup(p)
    text = p.read_text(encoding="utf-8")

    # Track session transitions + reset chop history entering afternoon
    if "_last_trade_session" not in text:
        text = text.replace(
            "_CONFIRM_SCANS = int(os.getenv(\"ACCURACY_CONFIRM_SCANS\", \"3\"))",
            "_CONFIRM_SCANS = int(os.getenv(\"ACCURACY_CONFIRM_SCANS\", \"3\"))\n"
            "_last_trade_session: str | None = None",
        )

    old_phase = '''            # ── Time phase detection (ET session calendar) ──
            import session_calibration as _sess
            _is_morning = _sess.is_morning_session()
            _is_afternoon = _sess.is_afternoon_session()'''

    new_phase = '''            # ── Time phase detection (ET session calendar) ──
            import session_calibration as _sess
            global _last_trade_session
            _sg = _sess.get_session()
            _is_morning = _sess.is_morning_session()
            _is_afternoon = _sess.is_afternoon_session()
            if _sg.name == "AFTERNOON" and _last_trade_session != "AFTERNOON":
                try:
                    predictor._chop_detector._history.clear()
                    predictor._chop_detector._save()
                    logger.info("[SESSION] Afternoon start — reset chop detector history")
                except Exception as _e_ch:
                    logger.debug(f"[SESSION] chop reset failed: {_e_ch}")
            _last_trade_session = _sg.name'''

    if old_phase in text and "_last_trade_session" not in text.split(old_phase)[1][:200]:
        text = text.replace(old_phase, new_phase)
    elif "_last_trade_session" in text:
        print("run_bot session transition already patched")
    else:
        raise SystemExit("run_bot phase block not found")

    old_max = "                    if active_count < 1:  # max 1 position in morning (conservative)"
    new_max = """                    _max_morning = 2 if _sg.name == "MIDDAY" else int(os.getenv("MORNING_MAX_POSITIONS", "1"))
                    if active_count < _max_morning:"""
    if old_max in text:
        text = text.replace(old_max, new_max)
    else:
        print("run_bot morning max pos skip")

    p.write_text(text, encoding="utf-8")
    print("patched run_bot.py")


def patch_env():
    p = ROOT / ".env"
    backup(p)
    updates = {
        "CHOPPY_MIN_TREND_ABS": "0.28",
        "SESSION_AFTERNOON_MIN_TREND": "0.22",
        "SESSION_AFTERNOON_CHOPPY_TREND": "0.26",
        "SESSION_AFTERNOON_MIN_EDGE": "0.06",
        "CHOPPY_DIST_BYPASS": "0.0010",
        "CHOPPY_TREND_BYPASS": "0.30",
        "MORNING_MAX_POSITIONS": "1",
        "ACCURACY_CONFIRM_SCANS": "2",
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


def restart_bot():
    subprocess.run(["bash", "-lc", "pkill -f 'python3 -u run_bot.py' || true"], check=False)
    subprocess.run(["sleep", "2"], check=True)
    subprocess.Popen(
        ["nohup", "python3", "-u", "run_bot.py"],
        stdout=open(ROOT / "logs" / f"bot_afternoon_fix_{STAMP}.log", "a"),
        stderr=subprocess.STDOUT,
        cwd=str(ROOT),
    )
    subprocess.run(["sleep", "3"], check=True)
    r = subprocess.run(
        ["bash", "-lc", "ps aux | grep 'python3 -u run_bot' | grep -v grep"],
        capture_output=True, text=True,
    )
    print("bot:", r.stdout.strip() or "NOT RUNNING")


def main():
    patch_session_calibration()
    patch_predictor()
    patch_run_bot()
    patch_env()
    for f in ["session_calibration.py", "predictor.py", "run_bot.py"]:
        subprocess.run([sys.executable, "-m", "py_compile", str(ROOT / f)], check=True)
    restart_bot()
    print("OK")


if __name__ == "__main__":
    main()
