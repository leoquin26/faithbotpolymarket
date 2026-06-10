#!/usr/bin/env python3
"""Session-calibrate engine: ET-aligned phases, live calibrator, unified gates."""
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path("/home/ubuntu/v3-bot")
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
CAL_SRC = ROOT / "regime_aware.disabled_20260603_142746" / "confidence_calibrator.py"


def backup(p: Path):
    if p.exists():
        shutil.copy2(p, p.with_suffix(p.suffix + f".bak_{STAMP}"))


SESSION_CALIBRATION = '''"""
Session calibration — ET-aligned trading phases + per-session gate thresholds.

Phases (America/New_York, weekdays):
  P1  08:30-09:30  Pre-open early trend (BTC/ETH/SOL)
  P2  09:30-11:00  US cash open chop — NO TRADING
  P1b 11:00-12:30  Post-open trend (BTC/ETH/SOL)
  P3  12:30-15:00  Midday trend (all coins)
  PM  15:00-18:00  Afternoon main engine
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except (TypeError, ValueError):
        return default


def _parse_hm(s: str, default: Tuple[int, int]) -> Tuple[int, int]:
    try:
        h, m = s.strip().split(":")
        return int(h), int(m)
    except Exception:
        return default


def _hm_now() -> Tuple[int, int]:
    return datetime.now(NY).hour, datetime.now(NY).minute


def _hm_ge(hm: Tuple[int, int], ref: Tuple[int, int]) -> bool:
    return hm[0] > ref[0] or (hm[0] == ref[0] and hm[1] >= ref[1])


def _hm_lt(hm: Tuple[int, int], ref: Tuple[int, int]) -> bool:
    return hm[0] < ref[0] or (hm[0] == ref[0] and hm[1] < ref[1])


# ET phase boundaries (env-overridable)
P_PRE_START = _parse_hm(os.getenv("SESSION_PRE_OPEN_START", "8:30"), (8, 30))
P_OPEN_START = _parse_hm(os.getenv("SESSION_US_OPEN_START", "9:30"), (9, 30))
P_OPEN_END = _parse_hm(os.getenv("SESSION_US_OPEN_END", "11:00"), (11, 0))
P_POST_END = _parse_hm(os.getenv("SESSION_POST_OPEN_END", "12:30"), (12, 30))
P_MIDDAY_END = _parse_hm(os.getenv("SESSION_MIDDAY_END", "15:00"), (15, 0))
P_DAY_END = _parse_hm(os.getenv("SESSION_DAY_END", "18:00"), (18, 0))


@dataclass
class SessionGates:
    name: str
    phase: Optional[int]
    min_trend: float
    min_dist: float
    min_prob: float
    min_edge: float
    choppy_min_trend: float
    allow_trade: bool
    allowed_coins: Optional[set]


def _allowed(env_key: str, default: str) -> set:
    raw = os.getenv(env_key, default)
    return {c.strip().upper() for c in raw.split(",") if c.strip()}


def get_session() -> SessionGates:
    """Return current session gates (ET clock)."""
    now = datetime.now(NY)
    hm = (now.hour, now.minute)
    wd = now.weekday()

    base_trend = _env_float("MIN_TREND_SCORE", _env_float("MIN_TREND_ABS", "0.30"))
    base_dist = _env_float("MIN_DIST_UP_PCT", "0.0010")
    base_prob = _env_float("MIN_WIN_PROB", "0.62")
    base_edge = _env_float("MIN_EDGE_THRESHOLD", "0.08")
    chop_trend = _env_float("CHOPPY_MIN_TREND_ABS", "0.38")

    if wd >= 5:
        return SessionGates("WEEKEND", None, base_trend, base_dist, base_prob, base_edge, chop_trend, False, None)

    if _hm_lt(hm, P_PRE_START) or _hm_ge(hm, P_DAY_END):
        return SessionGates("OFF", None, base_trend, base_dist, base_prob, base_edge, chop_trend, False, None)

    # P2 — US open chop blackout
    if _hm_ge(hm, P_OPEN_START) and _hm_lt(hm, P_OPEN_END):
        return SessionGates(
            "US_OPEN_CHOP", 2,
            base_trend, base_dist, base_prob, base_edge, chop_trend,
            False, None,
        )

    # P1 pre-open
    if _hm_ge(hm, P_PRE_START) and _hm_lt(hm, P_OPEN_START):
        return SessionGates(
            "PRE_OPEN", 1,
            _env_float("SESSION_P1_MIN_TREND", str(base_trend)),
            _env_float("SESSION_P1_MIN_DIST", str(base_dist)),
            _env_float("SESSION_P1_MIN_PROB", "0.60"),
            _env_float("SESSION_P1_MIN_EDGE", "0.07"),
            _env_float("SESSION_P1_CHOPPY_TREND", str(chop_trend)),
            True, _allowed("MORNING_P1_ALLOWED", "BTC,ETH,SOL"),
        )

    # P1 post-open
    if _hm_ge(hm, P_OPEN_END) and _hm_lt(hm, P_POST_END):
        return SessionGates(
            "POST_OPEN", 1,
            _env_float("SESSION_P1_MIN_TREND", str(base_trend)),
            _env_float("SESSION_P1_MIN_DIST", str(base_dist * 0.9)),
            _env_float("SESSION_P1_MIN_PROB", "0.60"),
            _env_float("SESSION_P1_MIN_EDGE", "0.07"),
            _env_float("SESSION_P1_CHOPPY_TREND", str(chop_trend)),
            True, _allowed("MORNING_P1_ALLOWED", "BTC,ETH,SOL"),
        )

    # P3 midday
    if _hm_ge(hm, P_POST_END) and _hm_lt(hm, P_MIDDAY_END):
        return SessionGates(
            "MIDDAY", 3,
            _env_float("SESSION_P3_MIN_TREND", str(base_trend * 0.95)),
            _env_float("SESSION_P3_MIN_DIST", str(base_dist * 0.9)),
            _env_float("SESSION_P3_MIN_PROB", "0.58"),
            _env_float("SESSION_P3_MIN_EDGE", "0.07"),
            _env_float("SESSION_P3_CHOPPY_TREND", str(chop_trend * 0.95)),
            True, _allowed("MORNING_P3_ALLOWED", "BTC,ETH,SOL"),
        )

    # Afternoon
    return SessionGates(
        "AFTERNOON", None,
        base_trend, base_dist, base_prob, base_edge, chop_trend,
        True, None,
    )


def is_morning_session() -> bool:
    s = get_session()
    return s.name in ("PRE_OPEN", "POST_OPEN", "MIDDAY")


def is_afternoon_session() -> bool:
    return get_session().name == "AFTERNOON"


def can_trade_now() -> Tuple[bool, str]:
    s = get_session()
    if s.name == "WEEKEND":
        return False, "[WEEKEND] no trading Sat/Sun ET"
    if s.name == "OFF":
        hm = _hm_now()
        return False, f"[OFF HOURS] {hm[0]:02d}:{hm[1]:02d} ET — window 8:30am-6pm ET"
    if s.name == "US_OPEN_CHOP":
        return False, f"[US OPEN] 9:30-11:00 ET chop blackout (scanning active)"
    return True, ""


def get_regime_label(is_choppy: bool) -> str:
    return "CHOPPY" if is_choppy else "TRENDING"


def book_agrees(direction: str, book_up: float, gap: float = 0.04) -> bool:
    if direction == "UP":
        return book_up >= 0.50 + gap
    return book_up <= 0.50 - gap


def session_expensive_down_max_ask(book_agrees_dir: bool) -> float:
    base = _env_float("EXPENSIVE_DOWN_MAX_ASK", "0.62")
    if book_agrees_dir:
        return _env_float("EXPENSIVE_DOWN_BOOK_AGREE_MAX", "0.78")
    return base


def session_expensive_down_min_dist(book_agrees_dir: bool) -> float:
    base = _env_float("EXPENSIVE_DOWN_MIN_DIST", "0.0018")
    if book_agrees_dir:
        return _env_float("EXPENSIVE_DOWN_BOOK_AGREE_DIST", "0.0012")
    return base
'''


def install_regime_aware():
    ra = ROOT / "regime_aware"
    ra.mkdir(exist_ok=True)
    init = ra / "__init__.py"
    if not init.exists():
        init.write_text('"""Regime-aware helpers (calibrator only)."""\n', encoding="utf-8")
    if CAL_SRC.exists():
        shutil.copy2(CAL_SRC, ra / "confidence_calibrator.py")
        print("installed regime_aware/confidence_calibrator.py")
    else:
        print("WARN: calibrator source missing")


def write_session_calibration():
    p = ROOT / "session_calibration.py"
    backup(p)
    p.write_text(SESSION_CALIBRATION, encoding="utf-8")
    print("wrote session_calibration.py")


def patch_morning_strategy():
    p = ROOT / "morning_strategy.py"
    backup(p)
    text = '''"""
Morning Strategy — session-calibrated filters on top of main Predictor.

Phases use America/New_York (ET) via session_calibration.py:
  P1  08:30-09:30 + 11:00-12:30  early/post-open trend
  P2  09:30-11:00                 US cash open — NO TRADING
  P3  12:30-15:00                 midday trend
"""
import os
from typing import Optional
from loguru import logger

from predictor import Prediction
import session_calibration as sess


def get_morning_phase() -> Optional[int]:
    s = sess.get_session()
    return s.phase if s.name in ("PRE_OPEN", "POST_OPEN", "MIDDAY", "US_OPEN_CHOP") else None


def is_morning_hour() -> bool:
    return sess.is_morning_session()


def filter_morning_signal(pred: Prediction, trend_score: float) -> Optional[Prediction]:
    s = sess.get_session()
    if not s.allow_trade or s.phase is None:
        if s.name == "US_OPEN_CHOP":
            logger.debug(
                f"[MORNING P2] {pred.coin}: 9:30-11:00 ET US open chop — no trading"
            )
        return None

    coin = pred.coin
    if s.allowed_coins and coin not in s.allowed_coins:
        logger.debug(f"[MORNING P{s.phase}] {coin}: only {s.allowed_coins}")
        return None

    if pred.probability < s.min_prob:
        logger.debug(
            f"[MORNING P{s.phase}] {coin}: prob={pred.probability:.0%} < {s.min_prob:.0%}"
        )
        return None
    if pred.edge < s.min_edge:
        logger.debug(
            f"[MORNING P{s.phase}] {coin}: edge={pred.edge*100:.1f}% < {s.min_edge*100:.0f}%"
        )
        return None
    if abs(trend_score) < s.min_trend:
        logger.debug(
            f"[MORNING P{s.phase}] {coin}: |trend|={abs(trend_score):.2f} < {s.min_trend}"
        )
        return None

    logger.info(
        f"[MORNING P{s.phase}] {coin} {pred.direction} APPROVED | "
        f"session={s.name} Prob={pred.probability:.0%} Edge={pred.edge*100:.1f}% "
        f"|Trend|={abs(trend_score):.2f}"
    )
    return pred
'''
    p.write_text(text, encoding="utf-8")
    print("patched morning_strategy.py")


def patch_run_bot():
    p = ROOT / "run_bot.py"
    backup(p)
    text = p.read_text(encoding="utf-8")

    old_hours = '''def is_good_trading_hour() -> tuple:
    """Returns (can_trade, message). Uses Lima time (UTC-5) directly."""
    if not config.SKIP_NIGHT_HOURS:
        return True, ""
    from zoneinfo import ZoneInfo
    lima = ZoneInfo("America/Lima")
    now_lima = datetime.now(lima)
    lima_hour = now_lima.hour
    weekday = now_lima.weekday()
    if weekday >= 5:
        day_name = "Saturday" if weekday == 5 else "Sunday"
        return False, f"[WEEKEND] {day_name} {lima_hour}:00 Lima — no trading on weekends"
    if lima_hour < 9 or lima_hour >= 17:
        return False, f"[OFF HOURS] {lima_hour}:{now_lima.minute:02d} Lima — trade window 9am-5pm Lima (scanning active)"
    return True, ""'''

    new_hours = '''def is_good_trading_hour() -> tuple:
    """Returns (can_trade, message). ET session calendar via session_calibration."""
    if not config.SKIP_NIGHT_HOURS:
        return True, ""
    import session_calibration as _sess
    return _sess.can_trade_now()'''

    if old_hours not in text:
        if "session_calibration" in text and "can_trade_now" in text:
            print("run_bot is_good_trading_hour already patched")
        else:
            raise SystemExit("run_bot is_good_trading_hour block not found")
    else:
        text = text.replace(old_hours, new_hours)

    old_phase = '''            # ── Time phase detection ──
            from zoneinfo import ZoneInfo as _ZI
            _lima_now = datetime.now(_ZI("America/Lima"))
            _is_morning = 9 <= _lima_now.hour < 14
            _is_afternoon = 14 <= _lima_now.hour < 17'''

    new_phase = '''            # ── Time phase detection (ET session calendar) ──
            import session_calibration as _sess
            _is_morning = _sess.is_morning_session()
            _is_afternoon = _sess.is_afternoon_session()'''

    if old_phase in text:
        text = text.replace(old_phase, new_phase)
    elif "_sess.is_morning_session()" in text:
        print("run_bot phase detection already patched")
    else:
        raise SystemExit("run_bot phase block not found")

    p.write_text(text, encoding="utf-8")
    print("patched run_bot.py")


def patch_predictor():
    p = ROOT / "predictor.py"
    backup(p)
    text = p.read_text(encoding="utf-8")

    # Import session_calibration after config import
    if "import session_calibration" not in text:
        text = text.replace(
            "import config",
            "import config\nimport session_calibration as sess_cal",
        )

    # Session-aware trend gates (replace choppy/weak trend block)
    old_trend = '''        # Peak: stricter |trend| when chop detector says choppy
        if is_chop:
            _min_tr = float(getattr(config, "CHOPPY_MIN_TREND_ABS", 0.48))
            if abs(trend_score) < _min_tr:
                self._diag_log(
                    f"chopstrict-{coin}",
                    f"[CHOPPY STRICT] {coin}: |trend|={abs(trend_score):.3f} < {_min_tr} — skip",
                    15.0,
                )
                return None

        else:
            _min_trend = float(os.getenv("MIN_TREND_SCORE", os.getenv("MIN_TREND_ABS", "0.40")))
            if abs(trend_score) < _min_trend:
                self._diag_log(
                    f"dead-{coin}",
                    f"[WEAK TREND] {coin}: trend={trend_score:+.3f} dist={dist_pct*100:+.4f}% "
                    f"roc60={roc_60*10000:+.1f}bps roc120={roc_120*10000:+.1f}bps — need {_min_trend:.2f}+",
                    15.0,
                )
                return None'''

    new_trend = '''        # Session-calibrated trend gates
        _session = sess_cal.get_session()
        if is_chop:
            _min_tr = _session.choppy_min_trend
            if abs(trend_score) < _min_tr:
                self._diag_log(
                    f"chopstrict-{coin}",
                    f"[CHOPPY STRICT] {coin}: |trend|={abs(trend_score):.3f} < {_min_tr} "
                    f"session={_session.name} — skip",
                    15.0,
                )
                return None
        else:
            _min_trend = _session.min_trend
            if abs(trend_score) < _min_trend:
                self._diag_log(
                    f"dead-{coin}",
                    f"[WEAK TREND] {coin}: trend={trend_score:+.3f} dist={dist_pct*100:+.4f}% "
                    f"session={_session.name} need {_min_trend:.2f}+ — skip",
                    15.0,
                )
                return None'''

    if old_trend in text:
        text = text.replace(old_trend, new_trend)
    elif "_session = sess_cal.get_session()" in text:
        print("predictor trend gates already patched")
    else:
        raise SystemExit("predictor trend block not found")

    # Replace expensive down block for book-agree relaxation
    old_exp_dn = '''        _exp_dn_ask = float(os.getenv("EXPENSIVE_DOWN_MAX_ASK", "0.62"))
        _exp_dn_dist = float(os.getenv("EXPENSIVE_DOWN_MIN_DIST", "0.0018"))
        if direction == "DOWN" and ask >= _exp_dn_ask and abs(dist_pct) < _exp_dn_dist:'''

    new_exp_dn = '''        _bk_agree = sess_cal.book_agrees(direction, book_up)
        _exp_dn_ask = sess_cal.session_expensive_down_max_ask(_bk_agree and direction == "DOWN")
        _exp_dn_dist = sess_cal.session_expensive_down_min_dist(_bk_agree and direction == "DOWN")
        if direction == "DOWN" and ask >= _exp_dn_ask and abs(dist_pct) < _exp_dn_dist:'''

    if old_exp_dn in text:
        text = text.replace(old_exp_dn, new_exp_dn)
    elif "session_expensive_down_max_ask" in text:
        print("predictor expensive down already patched")
    else:
        # try alternate defaults
        alt = '''        _exp_dn_ask = float(os.getenv("EXPENSIVE_DOWN_MAX_ASK", "0.62"))
        _exp_dn_dist = float(os.getenv("EXPENSIVE_DOWN_MIN_DIST", "0.0018"))
        if direction == "DOWN" and ask >= _exp_dn_ask and abs(dist_pct) < _exp_dn_dist:'''
        if alt in text:
            text = text.replace(alt, new_exp_dn)

    old_final_gates = '''        edge = win_prob - ask
        min_edge = getattr(config, "MIN_EDGE", 0.05)

        min_prob = getattr(config, "MIN_WIN_PROB", 0.65)
        if win_prob < min_prob:
            self._diag_log(f"lowprob-{coin}", f"[LOW PROB] {coin} {direction}: prob={win_prob*100:.0f}% < {min_prob*100:.0f}%", 15.0)
            return None

        if edge < min_edge:'''

    new_final_gates = '''        edge = win_prob - ask

        # Live probability calibration (regime + chop + late window)
        _cal_live = os.getenv("CALIBRATION_LIVE", "off").lower() in ("on", "1", "true")
        _cal_shadow = os.getenv("CALIBRATION_SHADOW", "off").lower() in ("on", "1", "true")
        if _cal_live or _cal_shadow:
            try:
                from regime_aware.confidence_calibrator import calibrate as _calibrate
                from regime_aware.confidence_calibrator import format_log_line as _cal_fmt
                _regime = sess_cal.get_regime_label(is_chop)
                _cal_res = _calibrate(
                    raw_prob=win_prob,
                    regime=_regime,
                    trend_abs=abs(trend_score),
                    bucket_stats=None,
                    microstructure_features=None,
                    reversion_risk=0.0,
                    T_sec=float(time_remaining),
                    xasset_features=None,
                    direction=direction,
                )
                _mode = "LIVE" if _cal_live else "SHADOW"
                logger.debug(_cal_fmt(coin, direction, _cal_res, mode=_mode))
                if _cal_live:
                    win_prob = float(_cal_res["calibrated_prob"])
                    edge = win_prob - ask
            except Exception as _cal_e:
                logger.debug(f"[CALIBRATION] skip {coin}: {_cal_e}")

        _sg = sess_cal.get_session()
        min_edge = max(getattr(config, "MIN_EDGE", 0.05), _sg.min_edge)
        min_prob = max(getattr(config, "MIN_WIN_PROB", 0.65), _sg.min_prob)
        if win_prob < min_prob:
            self._diag_log(f"lowprob-{coin}", f"[LOW PROB] {coin} {direction}: prob={win_prob*100:.0f}% < {min_prob*100:.0f}% session={_sg.name}", 15.0)
            return None

        if edge < min_edge:'''

    if old_final_gates in text:
        text = text.replace(old_final_gates, new_final_gates)
    elif "_calibrate" in text:
        print("predictor calibrator already wired")
    else:
        raise SystemExit("predictor final gates block not found")

    p.write_text(text, encoding="utf-8")
    print("patched predictor.py")


def patch_env():
    p = ROOT / ".env"
    backup(p)
    updates = {
        "MIN_TREND_SCORE": "0.28",
        "MIN_TREND_ABS": "0.28",
        "CHOPPY_MIN_TREND_ABS": "0.36",
        "MIN_WIN_PROB": "0.60",
        "MIN_EDGE_THRESHOLD": "0.07",
        "MIN_DIST_UP_PCT": "0.0008",
        "MIN_DIST_DOWN_PCT": "0.0008",
        "EXPENSIVE_DOWN_MAX_ASK": "0.65",
        "EXPENSIVE_DOWN_MIN_DIST": "0.0015",
        "EXPENSIVE_DOWN_BOOK_AGREE_MAX": "0.78",
        "EXPENSIVE_DOWN_BOOK_AGREE_DIST": "0.0010",
        "SESSION_P1_MIN_TREND": "0.28",
        "SESSION_P1_MIN_DIST": "0.0008",
        "SESSION_P1_MIN_PROB": "0.58",
        "SESSION_P1_MIN_EDGE": "0.06",
        "SESSION_P3_MIN_TREND": "0.26",
        "SESSION_P3_MIN_DIST": "0.0008",
        "SESSION_P3_MIN_PROB": "0.56",
        "SESSION_P3_MIN_EDGE": "0.06",
        "MORNING_P1_ALLOWED": "BTC,ETH,SOL",
        "MORNING_P3_ALLOWED": "BTC,ETH,SOL",
        "MORNING_P1_MIN_TREND": "0.28",
        "MORNING_P3_MIN_TREND": "0.26",
        "CALIBRATION_LIVE": "on",
        "CALIBRATION_SHADOW": "on",
        "ACCURACY_DIST_PENALTY": "0.0005",
        "DIST_PENALTY_SKIP_ABOVE": "0.0006",
        "ENTRY_MAX_DOWN": "0.76",
        "ENTRY_MAX_UP": "0.64",
        "FLIP_TREND_MIN_15M": "0.80",
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
    r = subprocess.run(
        ["bash", "-lc", "ps aux | grep 'python3 -u run_bot' | grep -v grep | awk '{print $2}'"],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    for pid in r.stdout.strip().split():
        if pid.isdigit():
            subprocess.run(["kill", pid], check=False)
    subprocess.run(["sleep", "2"], check=True)
    subprocess.Popen(
        ["nohup", "python3", "-u", "run_bot.py"],
        stdout=open(ROOT / "logs" / f"bot_session_cal_{STAMP}.log", "a"),
        stderr=subprocess.STDOUT,
        cwd=str(ROOT),
    )
    subprocess.run(["sleep", "3"], check=True)
    r2 = subprocess.run(
        ["bash", "-lc", "ps aux | grep 'python3 -u run_bot' | grep -v grep"],
        capture_output=True, text=True,
    )
    print("bot:", r2.stdout.strip() or "NOT RUNNING")


def main():
    install_regime_aware()
    write_session_calibration()
    patch_morning_strategy()
    patch_run_bot()
    patch_predictor()
    patch_env()
    for f in ["session_calibration.py", "morning_strategy.py", "run_bot.py", "predictor.py"]:
        subprocess.run([sys.executable, "-m", "py_compile", str(ROOT / f)], check=True)
    subprocess.run(
        [sys.executable, "-m", "py_compile", str(ROOT / "regime_aware" / "confidence_calibrator.py")],
        check=True,
    )
    restart_bot()
    print("OK session calibration deployed")


if __name__ == "__main__":
    main()
