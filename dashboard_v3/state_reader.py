"""Read v3-bot JSON state files + .env settings."""
from __future__ import annotations

import os
import json
import subprocess
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("dash_v3.state")

BOT_DIR = Path("/home/ubuntu/v3-bot")
LOG_FILE = BOT_DIR / "v3_bot.log"
ENV_FILE = BOT_DIR / ".env"

# Settings you can expose on the dashboard. Others are kept private.
SAFE_SETTINGS = [
    # Sizing / bankroll
    "BANKROLL_BALANCE", "KELLY_FRACTION", "KELLY_MAX_PCT",
    "KELLY_MIN_BET", "KELLY_MAX_BET", "MAX_SINGLE_TRADE",
    # Risk / breakers
    "DAILY_LOSS_LIMIT", "USE_DAILY_STOP_LOSS",
    "MAX_EXPOSURE_PER_MARKET", "MAX_WINDOW_EXPOSURE",
    # Thresholds
    "MIN_EDGE_THRESHOLD", "MIN_CONFIDENCE_TRADE",
    "MAX_ENTRY_PRICE", "MIN_WIN_PROB",
    "MIN_RISK_REWARD", "MIN_THRESHOLD_DISTANCE",
    # Scan behaviour
    "SCAN_INTERVAL", "WARMUP_SEC",
    # Modes
    "DRY_RUN", "COMPOUND_MODE", "WEEKEND_MODE",
    "SKIP_NIGHT_HOURS", "NIGHT_START_HOUR", "NIGHT_END_HOUR",
    # Logging
    "LOG_LEVEL",
]

# Values to always redact if ever exposed.
REDACT_KEYS = {
    "POLYMARKET_PRIVATE_KEY",
    "POLYMARKET_API_KEY",
    "POLYMARKET_API_SECRET",
    "POLYMARKET_PASSPHRASE",
    "POLYMARKET_FUNDER_ADDRESS",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "ALCHEMY_POLYGON_RPC",
    "MONGODB_URI",
}


def read_env() -> dict[str, str]:
    """Read the .env file into a dict. Never returns private keys."""
    env: dict[str, str] = {}
    if not ENV_FILE.exists():
        return env
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip()
        if k in REDACT_KEYS:
            continue
        env[k] = v
    return env


def get_settings() -> dict[str, Any]:
    env = read_env()
    settings = {}
    for k in SAFE_SETTINGS:
        if k in env:
            settings[k] = env[k]
    # Also surface a handful of computed/resolved values.
    return {
        "settings": settings,
        "all_keys": list(env.keys()),
    }


def get_outcomes() -> dict:
    p = BOT_DIR / "outcomes_state.json"
    if not p.exists():
        return {"outcomes": []}
    try:
        data = json.loads(p.read_text())
        o = data.get("outcomes", [])
        wins = sum(1 for x in o if x)
        total = len(o)
        return {
            "outcomes": o,  # booleans in order
            "wins": wins,
            "losses": total - wins,
            "total": total,
            "winrate": round(wins / total * 100, 1) if total else 0,
        }
    except Exception:
        return {"outcomes": []}


def get_chop_state() -> dict:
    p = BOT_DIR / "chop_state.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def get_morning_dir_state() -> dict:
    p = BOT_DIR / "morning_dir_state.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def get_traded_windows() -> dict:
    p = BOT_DIR / "data" / "traded_windows.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────────
# Bot process status + controls
# ─────────────────────────────────────────────────────────────────
def bot_status() -> dict:
    """Check if run_bot.py is running, return PID + uptime."""
    try:
        out = subprocess.check_output(
            ["pgrep", "-af", "python3 run_bot.py"],
            text=True,
        ).strip()
        if not out:
            return {"running": False, "pid": None, "uptime_sec": 0}
        # pgrep -a returns "PID command..."
        first = out.splitlines()[0].split(maxsplit=1)
        pid = int(first[0])
        # Get process start time
        try:
            etimes = subprocess.check_output(
                ["ps", "-o", "etimes=", "-p", str(pid)],
                text=True,
            ).strip()
            uptime = int(etimes)
        except Exception:
            uptime = 0
        return {"running": True, "pid": pid, "uptime_sec": uptime}
    except subprocess.CalledProcessError:
        return {"running": False, "pid": None, "uptime_sec": 0}


def bot_start() -> dict:
    if bot_status()["running"]:
        return {"ok": False, "msg": "already running"}
    cmd = (
        "cd /home/ubuntu/v3-bot && "
        "nohup python3 run_bot.py > v3_stdout.log 2>&1 < /dev/null & disown"
    )
    subprocess.Popen(cmd, shell=True)
    time.sleep(3)
    return {"ok": True, "msg": "start issued", "status": bot_status()}


def bot_stop() -> dict:
    s = bot_status()
    if not s["running"]:
        return {"ok": False, "msg": "not running"}
    pid = s["pid"]
    subprocess.run(["kill", str(pid)])
    time.sleep(3)
    return {"ok": True, "msg": f"sent SIGTERM to {pid}", "status": bot_status()}


def bot_restart() -> dict:
    bot_stop()
    time.sleep(2)
    return bot_start()


def bot_clear_locks() -> dict:
    p = BOT_DIR / "data" / "traded_windows.json"
    try:
        p.write_text("{}")
        return {"ok": True, "msg": "traded_windows cleared"}
    except Exception as e:
        return {"ok": False, "msg": str(e)}


# ─────────────────────────────────────────────────────────────────
# Log tail (for raw viewer)
# ─────────────────────────────────────────────────────────────────
def tail_log(n: int = 200) -> list[str]:
    if not LOG_FILE.exists():
        return []
    try:
        # Efficient tail via seek
        with open(LOG_FILE, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            chunk = min(size, 200_000)
            f.seek(size - chunk)
            data = f.read().decode("utf-8", errors="ignore")
        lines = data.splitlines()[-n:]
        return lines
    except Exception:
        return []
