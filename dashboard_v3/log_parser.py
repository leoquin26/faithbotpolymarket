"""Tail and parse the v3-bot log for dashboard consumption.

Runs a background thread that tails `/home/ubuntu/v3-bot/v3_bot.log`
and emits structured events into:
  - events_ring (deque, most recent first) for the log panel
  - signals_ring for the scanner panel
  - stats (today counters)

All data is in-memory; restart the dashboard to reset state.
"""
from __future__ import annotations

import os
import re
import time
import threading
import logging
from collections import deque, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger("dash_v3.parser")

# ─────────────────────────────────────────────────────────────────
# Ring buffers + counters
# ─────────────────────────────────────────────────────────────────
LOG_FILE = Path("/home/ubuntu/v3-bot/v3_bot.log")

# Every parsed line (bounded). Most recent at right; we iterate in reverse
# for "latest first" consumption.
events_ring: deque = deque(maxlen=2000)

# Signals + detector decisions, structured for the scanner panel.
signals_ring: deque = deque(maxlen=400)

# Counters, by YYYY-MM-DD -> category -> int
_today_counters: dict[str, dict[str, int]] = defaultdict(
    lambda: defaultdict(int)
)

# Per-coin block counters for today
_today_block_by_coin: dict[str, dict[str, int]] = defaultdict(
    lambda: defaultdict(int)
)

# Trades (ORDER / FILLED / WIN / LOSS) for today
_today_trades: list[dict] = []

# Remember file position for incremental tailing
_file_pos: int = 0
_file_inode: int = -1
_started: bool = False
_lock = threading.Lock()


# ─────────────────────────────────────────────────────────────────
# Regexes
# ─────────────────────────────────────────────────────────────────
RE_LINE = re.compile(
    r"^(?P<time>\d{2}:\d{2}:\d{2})\s*\|\s*(?P<level>\w+)\s*\|\s*(?P<msg>.*)$"
)

RE_SIGNAL = re.compile(
    r"\[SIGNAL\]\s+(?P<coin>\w+)\s+(?P<dir>UP|DOWN)\s+\|\s+"
    r"Prob=(?P<prob>[\d\.]+)%\s+\|\s+Ask=(?P<ask>\d+)c\s+\|\s+Edge=(?P<edge>-?[\d\.]+)%"
    r"(?:\s+\|\s+Trend=(?P<trend>[+-]?[\d\.]+))?"
)

RE_EXHAUST = re.compile(
    r"\[EXHAUST(?:-SHADOW)?\]\s+(?P<coin>\w+)\s+(?P<dir>UP|DOWN)\s+@\s+(?P<ask>\d+)c\s+\|\s+"
    r"score=(?P<score>[\d\.]+)\s+raw=(?P<raw>\w+)(?P<gated>\s+\(GATED->CLEAN\))?\s+action=(?P<action>\w+)"
)

RE_EXHAUST_BLOCK = re.compile(
    r"\[EXHAUST BLOCK\]\s+(?P<coin>\w+)\s+(?P<dir>UP|DOWN)\s+skipped\s+\(score=(?P<score>[\d\.]+)\)"
)

RE_EXHAUST_DAMPEN = re.compile(
    r"\[EXHAUST DAMPEN\]\s+(?P<coin>\w+)\s+(?P<dir>UP|DOWN)"
)

RE_EXHAUST_FLIP = re.compile(
    r"\[EXHAUST FLIP\]\s+(?P<coin>\w+)"
)

RE_ORDER = re.compile(
    r"\[ORDER\]\s+(?P<coin>\w+)\s+(?P<dir>UP|DOWN)\s+\|\s+FOK\s+@\s+(?P<ask>\d+)c\s+\|\s+"
    r"(?P<shares>\d+)\s+shares\s+\((?:cost=\$(?P<cost>[\d\.]+),\s+sized=\$(?P<sized>[\d\.]+)|\$(?P<sized_only>[\d\.]+))\)"
)

RE_FILLED = re.compile(
    r"\[FILLED\]\s+(?P<coin>\w+)\s+(?P<dir>UP|DOWN)\s+\|\s+(?P<shares>\d+)\s+shares\s+@\s+(?P<price>\d+)c\s+=\s+\$(?P<cost>[\d\.]+)"
)

RE_MISS = re.compile(r"\[MISS\]\s+(?P<coin>\w+)\s+(?P<dir>UP|DOWN)")

RE_WIN = re.compile(
    r"\[WIN\s+(?P<session>\w+)\]\s+(?P<coin>\w+)\s+(?P<dir>UP|DOWN)\s+\|\s+"
    r"\+\$(?P<amount>[\d\.]+)\s+\|\s+Entry:\s+(?P<entry>\d+)c\s+x(?P<shares>\d+)"
)

RE_LOSS = re.compile(
    r"\[LOSS\s+(?P<session>\w+)\]\s+(?P<coin>\w+)\s+(?P<dir>UP|DOWN)\s+\|\s+"
    r"-\$(?P<amount>[\d\.]+)\s+\|\s+Entry:\s+(?P<entry>\d+)c\s+x(?P<shares>\d+)"
)

RE_KELLY = re.compile(
    r"\[KELLY\]\s+(?P<coin>\w+):\s+f\*=(?P<f>[\d\.]+)\s+frac=(?P<frac>[\d\.]+)"
    r".*?size=\$(?P<size>[\d\.]+).*?bankroll=\$(?P<bankroll>[\d\.]+)"
)

RE_BREAKER = re.compile(
    r"\[LOSS BREAKER\]\s+(?P<n>\d+)\s+consecutive losses"
)

RE_APPROVED = re.compile(
    r"\[(?P<session>MORNING|PM)\s+P\d+\]\s+(?P<coin>\w+)\s+(?P<dir>UP|DOWN)\s+APPROVED"
)


# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────
def _today_key() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _reset_if_new_day(current_date: str):
    """Clear today's counters when we cross midnight."""
    today = _today_key()
    if current_date != today:
        with _lock:
            _today_counters.pop(current_date, None)
            _today_block_by_coin.pop(current_date, None)
            _today_trades.clear()


def _classify(level: str, msg: str) -> str:
    """Classify a log line into a filter bucket for the UI."""
    if "[SIGNAL]" in msg:
        return "signal"
    if "[EXHAUST BLOCK]" in msg or "[EXHAUST DAMPEN]" in msg or "[EXHAUST FLIP]" in msg:
        return "exhaust"
    if "[EXHAUST]" in msg or "[EXHAUST-SHADOW]" in msg:
        return "exhaust"
    if "[ORDER]" in msg or "[FILLED]" in msg or "[MISS]" in msg:
        return "trade"
    if "[WIN " in msg or "[LOSS " in msg:
        return "trade"
    if "[KELLY]" in msg:
        return "trade"
    if "[LOSS BREAKER]" in msg or "BREAKER" in msg:
        return "risk"
    if "[MORNING P" in msg or "[PM P" in msg:
        return "approve"
    # Debug-but-meaningful filter decisions that we want visible as the
    # "live heartbeat" in the dashboard (bot is scanning but nothing to
    # trade yet).
    if (
        "[EXPENSIVE]" in msg
        or "[WEAK TREND]" in msg
        or "[COLD START]" in msg
        or "[WARMUP]" in msg
        or "[NO DATA]" in msg
        or "[CHOP]" in msg
        or "[WINDOW LOCKED]" in msg
    ):
        return "filter"
    if level in ("ERROR", "CRITICAL"):
        return "error"
    if level == "WARNING":
        return "warn"
    if level == "DEBUG":
        return "debug"
    return "info"


def _log_hms_to_epoch(hms: str) -> float:
    """Convert HH:MM:SS (server-local) on today's date to a UTC epoch.

    The server is in Lima (UTC-5). `time.mktime()` treats the tuple as
    local time, so it correctly produces UTC epoch seconds regardless
    of system tz, as long as the system clock matches Lima.
    """
    try:
        h, m, s = [int(x) for x in hms.split(":")]
    except Exception:
        return time.time()
    now = datetime.now()
    dt = now.replace(hour=h, minute=m, second=s, microsecond=0)
    # If the log's HH:MM:SS is in the future by >10 min, it must be
    # yesterday's log (unlikely but handles midnight rotation).
    if (dt - now).total_seconds() > 600:
        dt = dt.replace(day=dt.day - 1)
    return time.mktime(dt.timetuple())


def _parse_line(raw: str):
    """Parse a single raw log line. Mutates global state under _lock."""
    m = RE_LINE.match(raw.strip())
    if not m:
        return
    t, level, msg = m["time"], m["level"], m["msg"].strip()
    category = _classify(level, msg)

    # True DEBUG noise (no [TAG]) gets dropped entirely to keep the
    # ring focused. Classified DEBUG-with-tag lines (filter decisions)
    # are kept.
    if level == "DEBUG" and category == "debug":
        return

    # Proper timestamp derived from the log line itself (not parse time).
    log_ts = _log_hms_to_epoch(t)

    today = _today_key()

    with _lock:
        events_ring.append({
            "t": t,
            "level": level,
            "msg": msg,
            "cat": category,
            "ts": log_ts,
        })

        counters = _today_counters[today]
        counters["total"] += 1

        # ───── structured parse ─────
        sm = RE_SIGNAL.search(msg)
        if sm:
            counters["signals"] += 1
            signals_ring.append({
                "t": t,
                "kind": "SIGNAL",
                "coin": sm["coin"],
                "dir": sm["dir"],
                "ask": int(sm["ask"]),
                "prob": float(sm["prob"]),
                "edge": float(sm["edge"]),
                "trend": float(sm["trend"]) if sm["trend"] else None,
                "ts": log_ts,
            })
            return

        em = RE_EXHAUST.search(msg)
        if em:
            action = em["action"]
            gated = bool(em["gated"])
            signals_ring.append({
                "t": t,
                "kind": f"EXHAUST_{action}",
                "coin": em["coin"],
                "dir": em["dir"],
                "ask": int(em["ask"]),
                "score": float(em["score"]),
                "raw": em["raw"],
                "gated": gated,
                "action": action,
                "ts": log_ts,
            })
            return

        bm = RE_EXHAUST_BLOCK.search(msg)
        if bm:
            coin = bm["coin"]
            counters["blocks"] += 1
            _today_block_by_coin[today][coin] += 1
            signals_ring.append({
                "t": t,
                "kind": "BLOCK",
                "coin": coin,
                "dir": bm["dir"],
                "score": float(bm["score"]),
                "ts": log_ts,
            })
            return

        dm = RE_EXHAUST_DAMPEN.search(msg)
        if dm:
            counters["dampens"] += 1
            signals_ring.append({
                "t": t,
                "kind": "DAMPEN",
                "coin": dm["coin"],
                "dir": dm["dir"],
                "ts": log_ts,
            })
            return

        fm = RE_EXHAUST_FLIP.search(msg)
        if fm:
            counters["flips"] += 1
            signals_ring.append({
                "t": t,
                "kind": "FLIP",
                "coin": fm["coin"],
                "ts": log_ts,
            })
            return

        om = RE_ORDER.search(msg)
        if om:
            counters["orders"] += 1
            size_usd = float(om["sized"] or om["sized_only"] or 0)
            cost = float(om["cost"] or size_usd)
            _today_trades.append({
                "t": t,
                "type": "ORDER",
                "coin": om["coin"],
                "dir": om["dir"],
                "ask": int(om["ask"]),
                "shares": int(om["shares"]),
                "size_usd": size_usd,
                "cost": cost,
                "ts": log_ts,
            })
            return

        flm = RE_FILLED.search(msg)
        if flm:
            counters["fills"] += 1
            _today_trades.append({
                "t": t,
                "type": "FILLED",
                "coin": flm["coin"],
                "dir": flm["dir"],
                "shares": int(flm["shares"]),
                "price": int(flm["price"]),
                "cost": float(flm["cost"]),
                "ts": log_ts,
            })
            return

        wm = RE_WIN.search(msg)
        if wm:
            counters["wins"] += 1
            counters["pnl_cents"] += int(float(wm["amount"]) * 100)
            _today_trades.append({
                "t": t,
                "type": "WIN",
                "coin": wm["coin"],
                "dir": wm["dir"],
                "amount": float(wm["amount"]),
                "entry": int(wm["entry"]),
                "shares": int(wm["shares"]),
                "session": wm["session"],
                "ts": log_ts,
            })
            return

        lm = RE_LOSS.search(msg)
        if lm:
            counters["losses"] += 1
            counters["pnl_cents"] -= int(float(lm["amount"]) * 100)
            _today_trades.append({
                "t": t,
                "type": "LOSS",
                "coin": lm["coin"],
                "dir": lm["dir"],
                "amount": float(lm["amount"]),
                "entry": int(lm["entry"]),
                "shares": int(lm["shares"]),
                "session": lm["session"],
                "ts": log_ts,
            })
            return

        km = RE_KELLY.search(msg)
        if km:
            signals_ring.append({
                "t": t,
                "kind": "KELLY",
                "coin": km["coin"],
                "size_usd": float(km["size"]),
                "bankroll": float(km["bankroll"]),
                "ts": log_ts,
            })
            return

        brm = RE_BREAKER.search(msg)
        if brm:
            counters["breakers"] += 1
            return


# ─────────────────────────────────────────────────────────────────
# Tailer thread
# ─────────────────────────────────────────────────────────────────
def _tail_loop(bootstrap_lines: int = 2000, poll_interval: float = 0.5):
    """Run forever. Handles log rotation by detecting inode change."""
    global _file_pos, _file_inode
    logger.info(f"tailer starting on {LOG_FILE} (bootstrap={bootstrap_lines})")
    bootstrapped = False

    while True:
        try:
            if not LOG_FILE.exists():
                time.sleep(2)
                continue

            st = LOG_FILE.stat()
            if st.st_ino != _file_inode:
                # New file (first run or rotated) — reset.
                _file_inode = st.st_ino
                _file_pos = 0

            with open(LOG_FILE, "r", encoding="utf-8", errors="ignore") as fh:
                if not bootstrapped and bootstrap_lines > 0:
                    # Read the last N lines to seed the dashboard.
                    fh.seek(0, os.SEEK_END)
                    size = fh.tell()
                    chunk = min(size, 200_000)  # ~200 KB of tail
                    fh.seek(size - chunk)
                    tail = fh.read()
                    lines = tail.splitlines()[-bootstrap_lines:]
                    for ln in lines:
                        _parse_line(ln)
                    _file_pos = size
                    bootstrapped = True
                    logger.info(f"bootstrap complete ({len(lines)} lines parsed)")
                else:
                    fh.seek(_file_pos)
                    new = fh.read()
                    if new:
                        for ln in new.splitlines():
                            _parse_line(ln)
                        _file_pos = fh.tell()

            time.sleep(poll_interval)
        except Exception as e:
            logger.exception(f"tailer error: {e}")
            time.sleep(2)


def start():
    global _started
    with _lock:
        if _started:
            return
        _started = True
    th = threading.Thread(target=_tail_loop, daemon=True, name="log-tailer")
    th.start()


# ─────────────────────────────────────────────────────────────────
# Public getters
# ─────────────────────────────────────────────────────────────────
def get_events(limit: int = 200, category: str | None = None) -> list[dict]:
    with _lock:
        if category:
            buf = [e for e in events_ring if e["cat"] == category]
        else:
            buf = list(events_ring)
    return list(reversed(buf[-limit:]))


def get_last_log_ts() -> float:
    """Epoch of the most recent parsed event (from the log line itself).

    Used by the frontend as a liveness heartbeat.
    """
    with _lock:
        if not events_ring:
            return 0.0
        return float(events_ring[-1].get("ts", 0.0))


def get_last_file_mtime() -> float:
    """Unix mtime of v3_bot.log — proves the bot is actively writing
    even if no events matched our regexes recently (e.g. pure DEBUG)."""
    try:
        return LOG_FILE.stat().st_mtime
    except Exception:
        return 0.0


def get_signals(limit: int = 100) -> list[dict]:
    with _lock:
        buf = list(signals_ring)
    return list(reversed(buf[-limit:]))


def get_today_stats() -> dict:
    today = _today_key()
    with _lock:
        c = dict(_today_counters.get(today, {}))
        by_coin = {k: dict(v) for k, v in _today_block_by_coin.items()}
        pnl = c.get("pnl_cents", 0) / 100.0
        wins = c.get("wins", 0)
        losses = c.get("losses", 0)
        n_resolved = wins + losses
        winrate = (wins / n_resolved * 100) if n_resolved else 0
        return {
            "today": today,
            "total_events": c.get("total", 0),
            "signals": c.get("signals", 0),
            "orders": c.get("orders", 0),
            "fills": c.get("fills", 0),
            "blocks": c.get("blocks", 0),
            "dampens": c.get("dampens", 0),
            "flips": c.get("flips", 0),
            "wins": wins,
            "losses": losses,
            "winrate": round(winrate, 1),
            "pnl_usd": round(pnl, 2),
            "breakers": c.get("breakers", 0),
            "blocks_by_coin": by_coin.get(today, {}),
        }


def get_today_trades() -> list[dict]:
    with _lock:
        return list(_today_trades)
