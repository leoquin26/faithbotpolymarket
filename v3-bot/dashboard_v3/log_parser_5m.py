"""5M-bot log tailer. Mirrors `log_parser.py` but reads `v3_bot_5m.log`.

The 5M bot prefixes each user-visible log line with `[5M]`, but the
underlying tags ([SIGNAL], [WIN 5M], [LOSS 5M], [EXHAUST BLOCK], etc.)
match the same regex set. We re-use the existing patterns and store
results in a separate set of ring buffers so the dashboard can render
the two bots side-by-side.
"""
from __future__ import annotations

import os
import re
import time
import threading
import logging
from collections import deque, defaultdict
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("dash_v3.parser_5m")

# [DASH-PATH-FIX 2026-05-08] 5m bot writes logs/bot_5m_YYYY-MM-DD.log via
# loguru midnight rotation. Resolve today's path each tail iteration.
_LEGACY_LOG_FILE = Path("/home/ubuntu/v3-bot/v3_bot_5m.log")
_LOG_DIR = Path("/home/ubuntu/v3-bot/logs")

RE_STATS_DAY = re.compile(r"^bot_5m_(\d{4}-\d{2}-\d{2})\.log$")
_tail_stats_date: str | None = None


def _active_log_path() -> Path:
    # Never fall back to legacy at midnight — stale file poisons WIN/LOSS.
    return _LOG_DIR / f"bot_5m_{datetime.now().strftime('%Y-%m-%d')}.log"


LOG_FILE = _LEGACY_LOG_FILE

events_ring: deque = deque(maxlen=2000)
signals_ring: deque = deque(maxlen=400)
_today_counters: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
_today_block_by_coin: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
_today_trades: list[dict] = []
_file_pos: int = 0
_file_inode: int = -1
_started: bool = False
_lock = threading.Lock()


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
RE_EXHAUST_DAMPEN = re.compile(r"\[EXHAUST DAMPEN\]\s+(?P<coin>\w+)\s+(?P<dir>UP|DOWN)")
RE_EXHAUST_FLIP = re.compile(r"\[EXHAUST FLIP\]\s+(?P<coin>\w+)")
RE_EXHAUST_OVERRIDE = re.compile(
    r"\[5M EXHAUST OVERRIDE-HIGH-ENTRY\]\s+(?P<coin>\w+)\s+(?P<dir>UP|DOWN)"
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
    r"\[WIN\s+(?P<session>[\w]+)\]\s+(?P<coin>\w+)\s+(?P<dir>UP|DOWN)\s+\|\s+"
    r"\+\$(?P<amount>[\d\.]+)\s+\|\s+Entry:\s+(?P<entry>\d+)c\s+x(?P<shares>\d+)"
)
RE_LOSS = re.compile(
    r"\[LOSS\s+(?P<session>[\w]+)\]\s+(?P<coin>\w+)\s+(?P<dir>UP|DOWN)\s+\|\s+"
    r"-\$(?P<amount>[\d\.]+)\s+\|\s+Entry:\s+(?P<entry>\d+)c\s+x(?P<shares>\d+)"
)
RE_RESOLVE_DEFER = re.compile(
    r"\[RESOLVE DEFERRED\]\s+(?P<coin>\w+)\s+(?P<dir>UP|DOWN)"
)


def _today_key() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _classify(level: str, msg: str) -> str:
    if "[SIGNAL]" in msg:
        return "signal"
    if ("[EXHAUST BLOCK]" in msg or "[EXHAUST DAMPEN]" in msg
            or "[EXHAUST FLIP]" in msg or "EXHAUST OVERRIDE" in msg):
        return "exhaust"
    if "[EXHAUST]" in msg or "[EXHAUST-SHADOW]" in msg:
        return "exhaust"
    if "[ORDER]" in msg or "[FILLED]" in msg or "[MISS]" in msg:
        return "trade"
    if "[WIN " in msg or "[LOSS " in msg:
        return "trade"
    if "[KELLY]" in msg or "FIXED SIZE" in msg:
        return "trade"
    if "[LOSS BREAKER]" in msg or "DAILY LOSS CAP" in msg or "BREAKER" in msg:
        return "risk"
    if (
        "[EXPENSIVE]" in msg or "[WEAK TREND]" in msg
        or "[COLD START]" in msg or "[WARMUP]" in msg
        or "[NO DATA]" in msg or "[CHOP]" in msg
        or "[WINDOW LOCKED]" in msg or "[5M]" in msg
        or "[RESOLVE DEFERRED]" in msg or "[TRAP BAND]" in msg
        or "[PM COIN BLOCK]" in msg or "[RESOLVE POLY]" in msg
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
    try:
        h, m, s = [int(x) for x in hms.split(":")]
    except Exception:
        return time.time()
    now = datetime.now()
    dt = now.replace(hour=h, minute=m, second=s, microsecond=0)
    if (dt - now).total_seconds() > 600:
        dt = dt.replace(day=dt.day - 1)
    return time.mktime(dt.timetuple())


def _parse_line(raw: str):
    m = RE_LINE.match(raw.strip())
    if not m:
        return
    t, level, msg = m["time"], m["level"], m["msg"].strip()
    category = _classify(level, msg)

    if level == "DEBUG" and category == "debug":
        return

    log_ts = _log_hms_to_epoch(t)
    stats_day = _tail_stats_date or _today_key()

    with _lock:
        events_ring.append({
            "t": t, "level": level, "msg": msg, "cat": category,
            "ts": log_ts, "bot": "5m",
        })
        counters = _today_counters[stats_day]
        counters["total"] += 1

        sm = RE_SIGNAL.search(msg)
        if sm:
            counters["signals"] += 1
            signals_ring.append({
                "t": t, "kind": "SIGNAL", "coin": sm["coin"],
                "dir": sm["dir"], "ask": int(sm["ask"]),
                "prob": float(sm["prob"]), "edge": float(sm["edge"]),
                "trend": float(sm["trend"]) if sm["trend"] else None,
                "ts": log_ts, "bot": "5m",
            })
            return

        em = RE_EXHAUST.search(msg)
        if em:
            signals_ring.append({
                "t": t, "kind": f"EXHAUST_{em['action']}",
                "coin": em["coin"], "dir": em["dir"], "ask": int(em["ask"]),
                "score": float(em["score"]), "raw": em["raw"],
                "gated": bool(em["gated"]), "action": em["action"],
                "ts": log_ts, "bot": "5m",
            })
            return

        bm = RE_EXHAUST_BLOCK.search(msg)
        if bm:
            counters["blocks"] += 1
            _today_block_by_coin[stats_day][bm["coin"]] += 1
            signals_ring.append({
                "t": t, "kind": "BLOCK", "coin": bm["coin"],
                "dir": bm["dir"], "score": float(bm["score"]),
                "ts": log_ts, "bot": "5m",
            })
            return

        dm = RE_EXHAUST_DAMPEN.search(msg)
        if dm:
            counters["dampens"] += 1
            signals_ring.append({
                "t": t, "kind": "DAMPEN", "coin": dm["coin"],
                "dir": dm["dir"], "ts": log_ts, "bot": "5m",
            })
            return

        fm = RE_EXHAUST_FLIP.search(msg)
        if fm:
            counters["flips"] += 1
            signals_ring.append({
                "t": t, "kind": "FLIP", "coin": fm["coin"],
                "ts": log_ts, "bot": "5m",
            })
            return

        ovm = RE_EXHAUST_OVERRIDE.search(msg)
        if ovm:
            counters["overrides"] += 1
            signals_ring.append({
                "t": t, "kind": "OVERRIDE", "coin": ovm["coin"],
                "dir": ovm["dir"], "ts": log_ts, "bot": "5m",
            })
            return

        om = RE_ORDER.search(msg)
        if om:
            counters["orders"] += 1
            size_usd = float(om["sized"] or om["sized_only"] or 0)
            cost = float(om["cost"] or size_usd)
            _today_trades.append({
                "day": stats_day, "t": t, "type": "ORDER", "coin": om["coin"],
                "dir": om["dir"], "ask": int(om["ask"]),
                "shares": int(om["shares"]), "size_usd": size_usd,
                "cost": cost, "ts": log_ts, "bot": "5m",
            })
            return

        flm = RE_FILLED.search(msg)
        if flm:
            counters["fills"] += 1
            _today_trades.append({
                "day": stats_day, "t": t, "type": "FILLED", "coin": flm["coin"],
                "dir": flm["dir"], "shares": int(flm["shares"]),
                "price": int(flm["price"]), "cost": float(flm["cost"]),
                "ts": log_ts, "bot": "5m",
            })
            return

        wm = RE_WIN.search(msg)
        if wm:
            counters["wins"] += 1
            counters["pnl_cents"] += int(float(wm["amount"]) * 100)
            _today_trades.append({
                "day": stats_day, "t": t, "type": "WIN", "coin": wm["coin"],
                "dir": wm["dir"], "amount": float(wm["amount"]),
                "entry": int(wm["entry"]), "shares": int(wm["shares"]),
                "session": wm["session"], "ts": log_ts, "bot": "5m",
            })
            return

        lm = RE_LOSS.search(msg)
        if lm:
            counters["losses"] += 1
            counters["pnl_cents"] -= int(float(lm["amount"]) * 100)
            _today_trades.append({
                "day": stats_day, "t": t, "type": "LOSS", "coin": lm["coin"],
                "dir": lm["dir"], "amount": float(lm["amount"]),
                "entry": int(lm["entry"]), "shares": int(lm["shares"]),
                "session": lm["session"], "ts": log_ts, "bot": "5m",
            })
            return

        rm = RE_RESOLVE_DEFER.search(msg)
        if rm:
            counters["resolve_deferred"] += 1
            return


def _tail_loop(bootstrap_lines: int = 1500, poll_interval: float = 0.5):
    global _file_pos, _file_inode
    current_path = _active_log_path()
    logger.info(f"5m tailer starting on {current_path} (bootstrap={bootstrap_lines})")
    bootstrapped = False

    while True:
        try:
            new_path = _active_log_path()
            if new_path != current_path:
                logger.info(
                    f"5m active log path changed: {current_path} -> {new_path}"
                )
                current_path = new_path
                bootstrapped = False
                _file_pos = 0
                _file_inode = -1

            if not current_path.exists():
                time.sleep(2)
                continue

            global _tail_stats_date
            msd = RE_STATS_DAY.match(current_path.name)
            _tail_stats_date = msd.group(1) if msd else _today_key()

            st = current_path.stat()
            if st.st_ino != _file_inode:
                _file_inode = st.st_ino
                _file_pos = 0

            with open(current_path, "r", encoding="utf-8", errors="ignore") as fh:
                if not bootstrapped and bootstrap_lines > 0:
                    fh.seek(0, os.SEEK_END)
                    size = fh.tell()
                    chunk = min(size, 200_000)
                    fh.seek(max(0, size - chunk))
                    tail = fh.read()
                    lines = tail.splitlines()[-bootstrap_lines:]
                    for ln in lines:
                        _parse_line(ln)
                    _file_pos = size
                    bootstrapped = True
                    logger.info(
                        f"5m bootstrap complete ({len(lines)} lines from "
                        f"{current_path.name})"
                    )
                else:
                    fh.seek(_file_pos)
                    new = fh.read()
                    if new:
                        for ln in new.splitlines():
                            _parse_line(ln)
                        _file_pos = fh.tell()

            time.sleep(poll_interval)
        except Exception as e:
            logger.exception(f"5m tailer error: {e}")
            time.sleep(2)


def start():
    global _started
    with _lock:
        if _started:
            return
        _started = True
    th = threading.Thread(target=_tail_loop, daemon=True, name="log-tailer-5m")
    th.start()


def get_events(limit: int = 200, category: str | None = None) -> list[dict]:
    with _lock:
        if category:
            buf = [e for e in events_ring if e["cat"] == category]
        else:
            buf = list(events_ring)
    return list(reversed(buf[-limit:]))


def get_last_log_ts() -> float:
    with _lock:
        if not events_ring:
            return 0.0
        return float(events_ring[-1].get("ts", 0.0))


def get_last_file_mtime() -> float:
    try:
        return _active_log_path().stat().st_mtime
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
            "overrides": c.get("overrides", 0),
            "wins": wins,
            "losses": losses,
            "winrate": round(winrate, 1),
            "pnl_usd": round(pnl, 2),
            "blocks_by_coin": by_coin.get(today, {}),
            "resolve_deferred": c.get("resolve_deferred", 0),
        }


def get_today_trades() -> list[dict]:
    today = _today_key()
    with _lock:
        return [t for t in _today_trades if t.get("day") == today]
