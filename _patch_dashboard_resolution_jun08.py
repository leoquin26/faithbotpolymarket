#!/usr/bin/env python3
"""Dashboard: reconcile WIN/LOSS with Polymarket Gamma (official resolution)."""
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path("/home/ubuntu/v3-bot")
DASH = ROOT / "dashboard_v3"
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")


def backup(p: Path):
    if p.exists():
        shutil.copy2(p, p.with_suffix(p.suffix + f".bak_{STAMP}"))


TRADE_RECONCILER = '''"""
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
    r"^(?P<time>\\d{2}:\\d{2}:\\d{2})\\s*\\|\\s*(?:\\w+\\s*\\|\\s*)?(?P<msg>.*)$"
)
RE_STRIKE = re.compile(
    r"\\[STRIKE\\]\\s+(?P<coin>\\w+)\\s+(?P<slug>\\w+-updown-15m-(?P<ws>\\d+))"
)
RE_FILLED = re.compile(
    r"\\[FILLED\\]\\s+(?P<coin>\\w+)\\s+(?P<dir>UP|DOWN)\\s+\\|\\s+"
    r"(?P<shares>\\d+)\\s+shares\\s+@\\s+(?P<price>\\d+)c\\s+=\\s+\\$(?P<cost>[\\d\\.]+)"
)
RE_GTC = re.compile(
    r"\\[GTC FILLED\\]\\s+(?P<coin>\\w+)\\s+(?P<dir>UP|DOWN)\\s+@\\s+(?P<price>\\d+)c\\s+"
    r"\\((?P<shares>[\\d\\.]+)\\s+shares,\\s+\\$(?P<cost>[\\d\\.]+)\\)"
)
RE_RESOLVE_GAMMA = re.compile(
    r"\\[RESOLVE\\]\\s+(?P<coin>\\w+)\\s+(?P<dir>UP|DOWN)\\s+\\|\\s+gamma winner=(?P<winner>UP|DOWN)"
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
'''


def patch_log_parser():
    p = DASH / "log_parser.py"
    backup(p)
    text = p.read_text(encoding="utf-8")

    if "from dashboard_v3.trade_reconciler import reconcile_today" not in text:
        insert = '''
# Gamma-truth reconciliation (Polymarket official outcomes)
try:
    from dashboard_v3.trade_reconciler import reconcile_today as _reconcile_today
except Exception:
    try:
        from trade_reconciler import reconcile_today as _reconcile_today
    except Exception:
        _reconcile_today = None

'''
        text = text.replace(
            'logger = logging.getLogger("dash_v3.parser")',
            'logger = logging.getLogger("dash_v3.parser")' + insert,
        )

    old_stats = '''def get_today_stats() -> dict:
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
        }'''

    new_stats = '''def get_today_stats() -> dict:
    today = _today_key()
    with _lock:
        c = dict(_today_counters.get(today, {}))
        by_coin = {k: dict(v) for k, v in _today_block_by_coin.items()}
        pnl = c.get("pnl_cents", 0) / 100.0
        wins = c.get("wins", 0)
        losses = c.get("losses", 0)
        n_resolved = wins + losses
        winrate = (wins / n_resolved * 100) if n_resolved else 0
        base = {
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
    # Override W/L/PnL with Polymarket Gamma truth when available
    if _reconcile_today:
        try:
            rec = _reconcile_today()
            if rec.get("resolved", 0) > 0:
                base["wins"] = rec["wins"]
                base["losses"] = rec["losses"]
                base["winrate"] = rec["winrate"]
                base["pnl_usd"] = rec["pnl_usd"]
                base["resolution_source"] = rec.get("source", "gamma")
                base["pending_trades"] = rec.get("pending", 0)
        except Exception as _re:
            logger.debug(f"gamma reconcile failed: {_re}")
    return base'''

    old_trades = '''def get_today_trades() -> list[dict]:
    today = _today_key()
    with _lock:
        return [t for t in _today_trades if t.get("day") == today]'''

    new_trades = '''def get_today_trades() -> list[dict]:
    today = _today_key()
    if _reconcile_today:
        try:
            rec = _reconcile_today()
            out = []
            for t in rec.get("trades", []):
                out.append({**t, "day": today})
            if out:
                return out
        except Exception as _re:
            logger.debug(f"gamma trades reconcile failed: {_re}")
    with _lock:
        return [t for t in _today_trades if t.get("day") == today]'''

    if old_stats not in text:
        if "resolution_source" in text:
            print("log_parser stats already patched")
        else:
            raise SystemExit("get_today_stats block not found")
    else:
        text = text.replace(old_stats, new_stats)

    if old_trades not in text:
        if "gamma trades reconcile" in text:
            print("log_parser trades already patched")
        else:
            raise SystemExit("get_today_trades block not found")
    else:
        text = text.replace(old_trades, new_trades)

    # GTC FILLED realtime parse
    if "RE_GTC_FILLED" not in text:
        text = text.replace(
            "RE_FILLED = re.compile(",
            'RE_GTC_FILLED = re.compile(\n'
            '    r"\\[GTC FILLED\\]\\s+(?P<coin>\\w+)\\s+(?P<dir>UP|DOWN)\\s+@\\s+(?P<ask>\\d+)c\\s+"\n'
            '    r"\\((?P<shares>[\\d\\.]+)\\s+shares,\\s+\\$(?P<cost>[\\d\\.]+)\\)"\n'
            ')\n\nRE_FILLED = re.compile(',
        )
        gtc_parse = '''
        gtc = RE_GTC_FILLED.search(msg)
        if gtc:
            counters["fills"] += 1
            _today_trades.append({
                "day": stats_day,
                "t": t,
                "type": "FILLED",
                "coin": gtc["coin"],
                "dir": gtc["dir"],
                "shares": int(float(gtc["shares"])),
                "price": int(gtc["ask"]),
                "cost": float(gtc["cost"]),
                "ts": log_ts,
            })
            return

'''
        text = text.replace("        flm = RE_FILLED.search(msg)", gtc_parse + "        flm = RE_FILLED.search(msg)")

    p.write_text(text, encoding="utf-8")
    print("patched log_parser.py")


def patch_app_session():
    """Use ET session labels in snapshot (optional small fix)."""
    p = DASH / "app.py"
    backup(p)
    text = p.read_text(encoding="utf-8")
    old = '''        now = datetime.now()
        hour = now.hour
        if 9 <= hour < 12:
            session = "morning"
        elif 12 <= hour < 17:
            session = "afternoon"
        else:
            session = "off-hours"'''
    new = '''        try:
            import session_calibration as _sess
            sg = _sess.get_session()
            session = sg.name.lower().replace("_", "-")
        except Exception:
            now = datetime.now()
            hour = now.hour
            if 9 <= hour < 12:
                session = "morning"
            elif 12 <= hour < 17:
                session = "afternoon"
            else:
                session = "off-hours"'''
    if old in text:
        text = text.replace(old, new)
        p.write_text(text, encoding="utf-8")
        print("patched app.py session label")
    else:
        print("app.py session skip")


def main():
    (DASH / "trade_reconciler.py").write_text(TRADE_RECONCILER, encoding="utf-8")
    print("wrote trade_reconciler.py")
    patch_log_parser()
    patch_app_session()
    for f in ["trade_reconciler.py", "log_parser.py", "app.py"]:
        subprocess.run(
            [sys.executable, "-m", "py_compile", str(DASH / f)],
            check=True,
            cwd=str(ROOT),
        )
    # Restart dashboard
    subprocess.run(["bash", "-lc", "pkill -f 'python3 -m dashboard_v3.app' || true"], check=False)
    subprocess.run(["sleep", "2"], check=True)
    subprocess.Popen(
        ["nohup", "python3", "-m", "dashboard_v3.app"],
        stdout=open(ROOT / "logs" / "dashboard_restart.log", "a"),
        stderr=subprocess.STDOUT,
        cwd=str(ROOT),
    )
    subprocess.run(["sleep", "3"], check=True)
    r = subprocess.run(
        ["bash", "-lc", "ps aux | grep 'dashboard_v3.app' | grep -v grep"],
        capture_output=True, text=True,
    )
    print("dashboard:", r.stdout.strip() or "NOT RUNNING")
    # Verify reconcile output
    r2 = subprocess.run(
        [sys.executable, "-c", "from dashboard_v3.trade_reconciler import reconcile_today; import json; print(json.dumps(reconcile_today(), indent=2)[:800])"],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    print(r2.stdout or r2.stderr)
    print("OK")


if __name__ == "__main__":
    main()
