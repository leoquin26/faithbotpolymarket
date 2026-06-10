"""
Reconcile bot log trades with Polymarket Gamma official outcomes.

Log [WIN]/[LOSS] can be wrong when Gamma was not closed yet at resolve time.
This module re-derives outcomes from fills + gamma winner per window.
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

BOT_DIR = Path("/home/ubuntu/v3-bot")
LOG_DIR = BOT_DIR / "logs"
DATA_DIR = BOT_DIR / "data"

# poly_resolution lives in bot root
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))

RE_LINE = re.compile(
    r"^(?P<time>\d{2}:\d{2}:\d{2})\s*\|\s*(?:\w+\s*\|\s*)?(?P<msg>.*)$"
)
RE_STRIKE = re.compile(
    r"\[STRIKE\]\s+(?P<coin>\w+)\s+(?P<slug>\w+-updown-15m-(?P<ws>\d+))"
)
RE_FILLED = re.compile(
    r"\[FILLED\]\s+(?P<coin>\w+)\s+(?P<dir>UP|DOWN)\s+\|\s+"
    r"(?P<shares>\d+)\s+shares\s+@\s+(?P<price>\d+)c\s+=\s+\$(?P<cost>[\d\.]+)"
)
RE_GTC = re.compile(
    r"\[GTC FILLED\]\s+(?P<coin>\w+)\s+(?P<dir>UP|DOWN)\s+@\s+(?P<price>\d+)c\s+"
    r"\((?P<shares>[\d\.]+)\s+shares,\s+\$(?P<cost>[\d\.]+)\)"
)
RE_RESOLVE_GAMMA = re.compile(
    r"\[RESOLVE\]\s+(?P<coin>\w+)\s+(?P<dir>UP|DOWN)\s+\|\s+gamma winner=(?P<winner>UP|DOWN)"
)

_gamma_cache: dict[str, tuple[float, str | None]] = {}


def _active_log() -> Path:
    today = LOG_DIR / f"bot_{datetime.now().strftime('%Y-%m-%d')}.log"
    if today.exists() and today.stat().st_size > 0:
        return today
    cands = [p for p in LOG_DIR.glob("bot_*.log") if p.stat().st_size > 0]
    return max(cands, key=lambda x: x.stat().st_mtime) if cands else today


def _hms_epoch(hms: str) -> float:
    try:
        h, m, s = [int(x) for x in hms.split(":")]
    except Exception:
        return time.time()
    now = datetime.now()
    dt = now.replace(hour=h, minute=m, second=s, microsecond=0)
    if (dt - now).total_seconds() > 600:
        dt = dt.replace(day=dt.day - 1)
    return time.mktime(dt.timetuple())


def _gamma_winner(coin: str, window_start: int) -> str | None:
    key = f"{coin}:{window_start}"
    cached = _gamma_cache.get(key)
    if cached and time.time() - cached[0] < 300:
        return cached[1]
    try:
        import poly_resolution as pr
        slug = pr.market_slug(coin, window_start, "15m")
        m = pr.fetch_market_by_slug(slug)
        w = pr.resolved_winner(m) if m else None
        _gamma_cache[key] = (time.time(), w)
        return w
    except Exception:
        return None


def _scan_fills(log_path: Path) -> list[dict]:
    """Parse fills from log; attach window_start from latest STRIKE per coin."""
    if not log_path.exists():
        return []
    ws_by_coin: dict[str, int] = {}
    fills: list[dict] = []
    for raw in log_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = RE_LINE.match(raw.strip())
        if not m:
            continue
        t, msg = m["time"], m["msg"]
        ts = _hms_epoch(t)
        sm = RE_STRIKE.search(msg)
        if sm:
            ws_by_coin[sm["coin"]] = int(sm["ws"])
            continue
        fm = RE_FILLED.search(msg)
        if fm:
            coin = fm["coin"]
            fills.append({
                "t": t, "ts": ts, "coin": coin, "dir": fm["dir"],
                "shares": int(fm["shares"]),
                "entry": int(fm["price"]),
                "cost": float(fm["cost"]),
                "window_start": ws_by_coin.get(coin, 0),
                "source": "FILLED",
            })
            continue
        gm = RE_GTC.search(msg)
        if gm:
            coin = gm["coin"]
            fills.append({
                "t": t, "ts": ts, "coin": coin, "dir": gm["dir"],
                "shares": int(float(gm["shares"])),
                "entry": int(gm["price"]),
                "cost": float(gm["cost"]),
                "window_start": ws_by_coin.get(coin, 0),
                "source": "GTC",
            })
    return fills


def _load_open_positions() -> dict:
    p = DATA_DIR / "open_positions.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def reconcile_today() -> dict[str, Any]:
    """Return gamma-truth trades + stats for today."""
    log_path = _active_log()
    fills = _scan_fills(log_path)
    open_pos = _load_open_positions()

    # Dedupe: same coin+window = one trade (latest fill wins)
    by_key: dict[tuple, dict] = {}
    for f in fills:
        ws = f.get("window_start") or 0
        if ws <= 0:
            # fallback: open_positions
            op = open_pos.get(f["coin"], {})
            if op and op.get("side") == f["dir"]:
                ws = int(op.get("window_start") or 0)
                f["window_start"] = ws
        key = (f["coin"], f["dir"], ws)
        by_key[key] = f

    trades: list[dict] = []
    wins = losses = 0
    pnl = 0.0

    for (coin, side, ws), f in sorted(by_key.items(), key=lambda x: x[1]["ts"]):
        if ws <= 0:
            trades.append({
                **f, "type": "OPEN", "status": "pending",
                "gamma_winner": None, "pnl": None, "reconciled": True,
            })
            continue
        winner = _gamma_winner(coin, ws)
        if not winner:
            # still open on Polymarket
            trades.append({
                **f, "type": "OPEN", "status": "pending",
                "gamma_winner": None, "pnl": None, "reconciled": True,
                "window_start": ws,
            })
            continue
        won = side == winner
        shares = f["shares"]
        cost = f["cost"]
        amount = round(shares - cost, 2) if won else round(cost, 2)
        pnl += (shares - cost) if won else -cost
        if won:
            wins += 1
            trades.append({
                **f, "type": "WIN", "status": "resolved",
                "gamma_winner": winner, "amount": amount,
                "pnl": round(shares - cost, 2), "reconciled": True,
                "session": "15M", "source": "gamma",
            })
        else:
            losses += 1
            trades.append({
                **f, "type": "LOSS", "status": "resolved",
                "gamma_winner": winner, "amount": amount,
                "pnl": round(-cost, 2), "reconciled": True,
                "session": "15M", "source": "gamma",
            })

    n = wins + losses
    # Prefer daily_pnl.json totals if reconciled matches count
    daily = {}
    try:
        daily = json.loads((DATA_DIR / "daily_pnl.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    if daily.get("date") == datetime.now().strftime("%Y-%m-%d"):
        pnl_file = round(float(daily.get("wins", 0)) - float(daily.get("losses", 0)), 2)
        if abs(pnl_file - round(pnl, 2)) < 0.5:
            pnl = pnl_file

    return {
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "pnl_usd": round(pnl, 2),
        "winrate": round(wins / n * 100, 1) if n else 0.0,
        "resolved": n,
        "pending": sum(1 for t in trades if t.get("type") == "OPEN"),
        "source": "gamma",
    }
