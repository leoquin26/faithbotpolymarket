"""Polymarket Command Center v3.

A self-contained Flask dashboard that reads from:
  - Polymarket CLOB API (ground truth for trades/positions/P&L)
  - The v3-bot log file (real-time signals/exhaust/trades stream)
  - v3-bot JSON state files (outcomes, direction memory, traded windows)
  - .env settings (safe read + controlled write in Phase 2)

Run: python3 -m dashboard_v3.app
Listens on 0.0.0.0:8080. Cloudflared tunnel proxies the public URL
to this port.
"""
from __future__ import annotations

import os
import sys
import json
import time
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Load the v3-bot .env so CLOB credentials resolve for the adapter.
from dotenv import load_dotenv
BOT_DIR = Path("/home/ubuntu/v3-bot")
load_dotenv(BOT_DIR / ".env")

from flask import Flask, jsonify, render_template, request, send_from_directory

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from log_parser import (  # noqa: E402
    start as parser_start,
    get_events,
    get_signals,
    get_today_stats,
    get_today_trades,
    get_last_log_ts,
    get_last_scan_ts,
    get_last_file_mtime,
)
import log_parser_5m as parser_5m  # noqa: E402
import clob_adapter as clob  # noqa: E402
import state_reader as state  # noqa: E402
import subprocess  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("dash_v3")

app = Flask(
    __name__,
    template_folder=str(HERE / "templates"),
    static_folder=str(HERE / "static"),
)

# Start both log tailers as soon as the app imports.
parser_start()
parser_5m.start()

# ── [AUDIT MAY27] register the audit telemetry panels ──
try:
    import audit_panels as _audit_panels
    _audit_panels.register(app)
    logger.info("[audit_panels] registered at /audit + /api/v3/audit/*")
except Exception as _e_ap:
    logger.warning(f"[audit_panels] register failed: {_e_ap}")

try:
    import control_api as _control_api
    _control_api.register(app)
    logger.info("[control_api] registered at /control + /api/v3/{config,control,perf,sniper}")
except Exception as _e_ca:
    logger.warning(f"[control_api] register failed: {_e_ca}")

COINS = ["BTC", "ETH", "SOL", "XRP"]


def _bot_5m_status() -> dict:
    """Check if run_brain_5m.py is running, return PID + uptime.

    Uses an anchored regex so transient bash -c lines that mention
    `run_brain_5m.py` (e.g. during a restart) don't get matched first.
    """
    try:
        out = subprocess.check_output(
            ["pgrep", "-af", r"^python3.*run_brain_5m\.py$"],
            text=True,
        ).strip()
        if not out:
            return {"running": False, "pid": None, "uptime_sec": 0}
        first = out.splitlines()[0].split(maxsplit=1)
        pid = int(first[0])
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


# ─────────────────────────────────────────────────────────────────
# Page
# ─────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/charts")
def charts():
    return render_template("charts.html")


@app.route("/favicon.ico")
def favicon():
    return ("", 204)


# ─────────────────────────────────────────────────────────────────
# Status
# ─────────────────────────────────────────────────────────────────
@app.route("/api/v3/status")
def api_status():
    s = state.bot_status()
    now = datetime.now()
    # Lima is UTC-5 all year; server runs in Lima tz per our ssh date check.
    # We just return local time since the server is in Lima.
    hour = now.hour
    session = "off"
    if 9 <= hour < 12:
        session = "morning"
    elif 12 <= hour < 17:
        session = "afternoon"
    elif 17 <= hour < 24 or hour < 9:
        session = "off-hours"
    return jsonify({
        "bot": s,
        "server_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "session": session,
    })


# ─────────────────────────────────────────────────────────────────
# P&L
# ─────────────────────────────────────────────────────────────────
@app.route("/api/v3/pnl")
def api_pnl():
    """P&L derived from bot log WIN/LOSS events (which match Polymarket
    redemption outcomes). Fix H prevents phantom wins going forward."""
    today_stats = get_today_stats()
    # 7-day history — scan recent trades list for day bucketing.
    trades = get_today_trades()
    daily = {today_stats["today"]: today_stats["pnl_usd"]}

    # Also pull real on-chain buys/sells for risk exposure numbers.
    now = time.time()
    day_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    on_chain = clob.pnl_for_period(day_start, now)

    return jsonify({
        "today": {
            "pnl_usd": today_stats["pnl_usd"],
            "wins": today_stats["wins"],
            "losses": today_stats["losses"],
            "winrate": today_stats["winrate"],
            "trades": len(trades),
        },
        "on_chain_today": on_chain,
        "daily": daily,
    })


# ─────────────────────────────────────────────────────────────────
# Trades (real on-chain)
# ─────────────────────────────────────────────────────────────────
@app.route("/api/v3/trades")
def api_trades():
    limit = int(request.args.get("limit", 50))
    raw = clob.get_all_trades(limit=limit)
    # Normalize for the UI.
    out = []
    for t in raw:
        ts = int(t.get("match_time") or t.get("matchTime") or 0)
        out.append({
            "ts": ts,
            "time": datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else "",
            "side": str(t.get("side", "")).upper(),
            "outcome": t.get("outcome", ""),
            "size": float(t.get("size", 0) or 0),
            "price": float(t.get("price", 0) or 0),
            "notional": round(
                float(t.get("size", 0) or 0) * float(t.get("price", 0) or 0),
                2,
            ),
            "status": t.get("status", ""),
            "market": (
                t.get("market") or t.get("title") or t.get("question") or ""
            ),
            "asset": t.get("asset") or t.get("tokenId") or t.get("asset_id"),
        })
    return jsonify({"trades": out, "count": len(out)})


# ─────────────────────────────────────────────────────────────────
# Positions (open)
# ─────────────────────────────────────────────────────────────────
@app.route("/api/v3/positions")
def api_positions():
    positions = clob.get_active_positions()
    out = []
    for p in positions:
        size = float(p.get("size", 0) or 0)
        if size <= 0.01:
            continue
        avg_price = float(p.get("avg_price") or p.get("avgPrice") or 0)
        cost = float(p.get("cost", 0) or (size * avg_price))
        last_ts = int(p.get("last_ts") or p.get("lastTradeTime") or 0)
        out.append({
            "asset": p.get("asset"),
            "size": round(size, 4),
            "avg_price": round(avg_price, 4),
            "cost": round(cost, 2),
            "market": p.get("market") or "",
            "outcome": p.get("outcome") or "",
            "last_time": (
                datetime.fromtimestamp(last_ts).strftime("%H:%M:%S")
                if last_ts else ""
            ),
        })
    return jsonify({"positions": out, "count": len(out)})


# ─────────────────────────────────────────────────────────────────
# Market grid (coins + last signals)
# ─────────────────────────────────────────────────────────────────
@app.route("/api/v3/market")
def api_market():
    """Synthesize the latest per-coin market snapshot from the log.

    We scan the recent signal stream for the most recent ask/prob/trend
    per coin+direction. Because ticks are ~every few seconds, values
    stay fresh. On top we read morning_dir_state.json for committed
    direction history.
    """
    signals = get_signals(limit=300)
    morn_dir = state.get_morning_dir_state()
    chop = state.get_chop_state()

    coin_rows = {}
    for c in COINS:
        coin_rows[c] = {
            "coin": c,
            "up": None,
            "down": None,
            "last_signal": None,
            "last_action": None,
        }

    for s in signals:  # oldest first (get_signals returns newest first; reverse)
        pass  # we loop below in reverse

    # get_signals returns newest-first; iterate and take first match per (coin, dir).
    for s in signals:
        coin = s.get("coin")
        if coin not in coin_rows:
            continue
        d = s.get("dir")
        kind = s.get("kind", "")
        if kind == "SIGNAL" and d in ("UP", "DOWN"):
            slot = coin_rows[coin][d.lower()]
            if slot is None:
                coin_rows[coin][d.lower()] = {
                    "ask": s.get("ask"),
                    "prob": s.get("prob"),
                    "edge": s.get("edge"),
                    "trend": s.get("trend"),
                    "time": s.get("t"),
                }
            if coin_rows[coin]["last_signal"] is None:
                coin_rows[coin]["last_signal"] = {
                    "t": s.get("t"), "dir": d, "ask": s.get("ask"),
                    "prob": s.get("prob"), "edge": s.get("edge"),
                }
        elif kind.startswith("EXHAUST") or kind == "BLOCK":
            if coin_rows[coin]["last_action"] is None:
                coin_rows[coin]["last_action"] = {
                    "t": s.get("t"),
                    "kind": kind,
                    "dir": d,
                    "score": s.get("score"),
                    "action": s.get("action") or (
                        "BLOCK" if kind == "BLOCK" else kind
                    ),
                }

    return jsonify({
        "coins": list(coin_rows.values()),
        "morning_dir_history": morn_dir,
        "chop_state": chop,
    })


# ─────────────────────────────────────────────────────────────────
# Signal scanner (live stream)
# ─────────────────────────────────────────────────────────────────
@app.route("/api/v3/scanner")
def api_scanner():
    limit = int(request.args.get("limit", 80))
    return jsonify({"signals": get_signals(limit=limit)})


# ─────────────────────────────────────────────────────────────────
# Risk + bot-today
# ─────────────────────────────────────────────────────────────────
@app.route("/api/v3/risk")
def api_risk():
    env = state.read_env()
    stats = get_today_stats()

    bankroll = float(env.get("BANKROLL_BALANCE", 0) or 0)
    kelly_pct = float(env.get("KELLY_MAX_PCT", 0) or 0)
    kelly_frac = float(env.get("KELLY_FRACTION", 0) or 0)
    daily_loss_limit = float(env.get("DAILY_LOSS_LIMIT", 0) or 0)
    use_dsl = str(env.get("USE_DAILY_STOP_LOSS", "")).lower() in ("1", "true", "yes")

    pnl = stats["pnl_usd"]
    loss_today = -pnl if pnl < 0 else 0
    dsl_remaining = max(0.0, daily_loss_limit - loss_today) if use_dsl else None

    # Breaker status = consecutive losses ending the log stream.
    recent_trades = get_today_trades()
    streak = 0
    streak_kind = None
    for tr in reversed(recent_trades):
        if tr["type"] == "WIN":
            if streak_kind == "WIN":
                streak += 1
            else:
                break
            streak_kind = "WIN"
        elif tr["type"] == "LOSS":
            if streak_kind in (None, "LOSS"):
                streak += 1
                streak_kind = "LOSS"
            else:
                break

    return jsonify({
        "bankroll": bankroll,
        "kelly_fraction": kelly_frac,
        "kelly_max_pct": kelly_pct,
        "daily_loss_limit": daily_loss_limit,
        "daily_loss_limit_enabled": use_dsl,
        "loss_today": round(loss_today, 2),
        "dsl_remaining": dsl_remaining,
        "pnl_today": pnl,
        "streak": streak,
        "streak_kind": streak_kind,
        "breakers_today": stats["breakers"],
    })


# ─────────────────────────────────────────────────────────────────
# Calibration
# ─────────────────────────────────────────────────────────────────
@app.route("/api/v3/calibration")
def api_calibration():
    out = state.get_outcomes()
    return jsonify(out)


# ─────────────────────────────────────────────────────────────────
# Exhaustion stats
# ─────────────────────────────────────────────────────────────────
@app.route("/api/v3/exhaust_stats")
def api_exhaust_stats():
    stats = get_today_stats()
    return jsonify({
        "blocks": stats["blocks"],
        "dampens": stats["dampens"],
        "flips": stats["flips"],
        "by_coin": stats["blocks_by_coin"],
        "signals": stats["signals"],
        "orders": stats["orders"],
        "fills": stats["fills"],
    })


# ─────────────────────────────────────────────────────────────────
# Logs
# ─────────────────────────────────────────────────────────────────
@app.route("/api/v3/logs")
def api_logs():
    limit = int(request.args.get("limit", 200))
    category = request.args.get("category")  # signal|trade|exhaust|risk|error|warn|info|approve
    bot = (request.args.get("bot") or "15m").lower()  # 15m | 5m | both
    if category == "all":
        category = None

    if bot == "15m":
        events = [{**e, "bot": "15m"} for e in get_events(limit=limit, category=category)]
    elif bot == "5m":
        events = parser_5m.get_events(limit=limit, category=category)
    else:  # both — interleave by ts (newest first)
        a = [{**e, "bot": "15m"} for e in get_events(limit=limit, category=category)]
        b = parser_5m.get_events(limit=limit, category=category)
        merged = a + b
        merged.sort(key=lambda e: e.get("ts", 0), reverse=True)
        events = merged[:limit]

    return jsonify({"events": events, "count": len(events), "bot": bot})


@app.route("/api/v3/logs/raw")
def api_logs_raw():
    n = int(request.args.get("n", 200))
    return jsonify({"lines": state.tail_log(n=n)})


# ─────────────────────────────────────────────────────────────────
# 5m bot — dedicated endpoints
# ─────────────────────────────────────────────────────────────────
@app.route("/api/v3/5m/snapshot")
def api_5m_snapshot():
    """Lightweight snapshot for the 5m bot panel.
    Returns status, today's stats, recent trades, recent events, recent signals.
    Polled at the same cadence as the 15m snapshot.
    """
    try:
        status = _bot_5m_status()
        stats = parser_5m.get_today_stats()
        trades = parser_5m.get_today_trades()[-25:]
        events = parser_5m.get_events(limit=120)
        signals = parser_5m.get_signals(limit=40)
        heartbeat = {
            "last_event_ts": parser_5m.get_last_log_ts(),
            "last_scan_ts": parser_5m.get_last_scan_ts(),
            "log_mtime": parser_5m.get_last_file_mtime(),
            "now": time.time(),
        }
        env = state.read_env()
        cfg_5m = {
            "enabled": str(env.get("M5_ENABLED", "0")).lower() in ("1", "true", "yes"),
            "coins": env.get("M5_COINS", ""),
            "test_size_usd": env.get("M5_TEST_SIZE_USD", ""),
            "daily_loss_cap": env.get("M5_DAILY_LOSS_CAP", ""),
            "trade_hours": f"{env.get('M5_TRADE_HOURS_START','?')}-{env.get('M5_TRADE_HOURS_END','?')}",
            "min_edge": env.get("M5_MIN_EDGE", ""),
            "min_trend": env.get("M5_MIN_TREND", ""),
            "entry_max": env.get("M5_ENTRY_MAX", ""),
        }
        loss_today = -stats["pnl_usd"] if stats["pnl_usd"] < 0 else 0
        cap = float(cfg_5m["daily_loss_cap"] or 0)
        cap_remaining = max(0.0, cap - loss_today) if cap > 0 else None
        return jsonify({
            "bot": status,
            "stats": stats,
            "trades": trades,
            "events": events,
            "signals": signals,
            "heartbeat": heartbeat,
            "config": cfg_5m,
            "loss_today": round(loss_today, 2),
            "cap_remaining": round(cap_remaining, 2) if cap_remaining is not None else None,
        })
    except Exception as e:
        logger.exception("5m snapshot failed")
        return jsonify({"error": str(e)}), 500


@app.route("/api/v3/5m/logs")
def api_5m_logs():
    limit = int(request.args.get("limit", 200))
    category = request.args.get("category")
    if category == "all":
        category = None
    events = parser_5m.get_events(limit=limit, category=category)
    return jsonify({"events": events, "count": len(events)})


@app.route("/api/v3/5m/scanner")
def api_5m_scanner():
    limit = int(request.args.get("limit", 80))
    return jsonify({"signals": parser_5m.get_signals(limit=limit)})


# ─────────────────────────────────────────────────────────────────
# Settings
# ─────────────────────────────────────────────────────────────────
@app.route("/api/v3/settings")
def api_settings():
    return jsonify(state.get_settings())


# Phase 2: POST /api/v3/settings to edit. Left disabled for now.
@app.route("/api/v3/settings", methods=["POST"])
def api_settings_post():
    return jsonify({
        "ok": False,
        "msg": "Settings editing is disabled in phase 1. "
               "Use `ssh` + edit .env + restart bot for now.",
    }), 403


# ─────────────────────────────────────────────────────────────────
# Bot controls
# ─────────────────────────────────────────────────────────────────
@app.route("/api/v3/bot/<action>", methods=["POST"])
def api_bot_action(action: str):
    if action == "start":
        return jsonify(state.bot_start())
    if action == "stop":
        return jsonify(state.bot_stop())
    if action == "restart":
        return jsonify(state.bot_restart())
    if action == "clear_locks":
        return jsonify(state.bot_clear_locks())
    return jsonify({"ok": False, "msg": f"unknown action: {action}"}), 400


# ─────────────────────────────────────────────────────────────────
# Aggregated snapshot (all-in-one for polling)
# ─────────────────────────────────────────────────────────────────
@app.route("/api/v3/snapshot")
def api_snapshot():
    """Single call returning everything needed to redraw the dashboard.

    Intended to be polled every 2s by the frontend. Heavy items (trades,
    positions) are cached in clob_adapter so repeated polls are cheap.
    """
    try:
        status = state.bot_status()
        now = datetime.now()
        try:
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
                session = "off-hours"

        stats = get_today_stats()
        env = state.read_env()
        bankroll = float(env.get("BANKROLL_BALANCE", 0) or 0)
        kelly_pct = float(env.get("KELLY_MAX_PCT", 0) or 0)
        daily_loss_limit = float(env.get("DAILY_LOSS_LIMIT", 0) or 0)
        use_dsl = str(env.get("USE_DAILY_STOP_LOSS", "")).lower() in ("1", "true", "yes")

        recent_15m = get_today_trades()[-25:]
        try:
            recent_5m = parser_5m.get_today_trades()[-25:]
        except Exception:
            recent_5m = []
        recent = sorted(
            recent_15m + recent_5m,
            key=lambda x: float(x.get("ts", 0)),
            reverse=True,
        )[:25]
        signals = get_signals(limit=40)
        events = [{**e, "bot": "15m"} for e in get_events(limit=120)]

        # Market grid
        coin_rows = {c: {
            "coin": c, "up": None, "down": None,
            "last_signal": None, "last_action": None,
        } for c in COINS}
        for s in signals:
            coin = s.get("coin")
            if coin not in coin_rows:
                continue
            d = s.get("dir")
            kind = s.get("kind", "")
            if kind == "SIGNAL" and d in ("UP", "DOWN"):
                slot = coin_rows[coin][d.lower()]
                if slot is None:
                    coin_rows[coin][d.lower()] = {
                        "ask": s.get("ask"), "prob": s.get("prob"),
                        "edge": s.get("edge"), "trend": s.get("trend"),
                        "time": s.get("t"),
                    }
                if coin_rows[coin]["last_signal"] is None:
                    coin_rows[coin]["last_signal"] = {
                        "t": s.get("t"), "dir": d, "ask": s.get("ask"),
                        "prob": s.get("prob"), "edge": s.get("edge"),
                    }
            elif kind.startswith("EXHAUST") or kind == "BLOCK":
                if coin_rows[coin]["last_action"] is None:
                    coin_rows[coin]["last_action"] = {
                        "t": s.get("t"), "kind": kind, "dir": d,
                        "score": s.get("score"),
                        "action": s.get("action") or (
                            "BLOCK" if kind == "BLOCK" else kind
                        ),
                    }

        pnl = stats["pnl_usd"]
        loss_today = -pnl if pnl < 0 else 0

        # apr28 combined P&L: dashboard hero card sums 15M + 5M
        # so the user sees one bottom line. Per-bot breakdowns
        # are still exposed for the individual bot cards.
        try:
            stats_5m = parser_5m.get_today_stats()
        except Exception as _e:
            logger.debug(f"[snapshot] 5m stats failed: {_e}")
            stats_5m = {"pnl_usd": 0, "wins": 0, "losses": 0, "winrate": 0}
        pnl_5m = float(stats_5m.get("pnl_usd", 0) or 0)
        wins_5m = int(stats_5m.get("wins", 0) or 0)
        losses_5m = int(stats_5m.get("losses", 0) or 0)
        total_pnl = round(pnl + pnl_5m, 2)
        total_wins = int(stats["wins"]) + wins_5m
        total_losses = int(stats["losses"]) + losses_5m
        total_settled = total_wins + total_losses
        total_winrate = (
            round(total_wins * 100.0 / total_settled, 1)
            if total_settled else 0.0
        )

        # Heartbeats — prove the pipeline is live even when the bot is
        # quiet (scan-only, no signals / fills).
        heartbeat = {
            "last_event_ts": get_last_log_ts(),
            "last_scan_ts": get_last_scan_ts(),
            "log_mtime": get_last_file_mtime(),
            "now": time.time(),
        }

        return jsonify({
            "server_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "session": session,
            "bot": status,
            "heartbeat": heartbeat,
            "pnl": {
                "today": pnl,
                "wins": stats["wins"],
                "losses": stats["losses"],
                "winrate": stats["winrate"],
            },
            "pnl_total": {
                "today": total_pnl,
                "wins": total_wins,
                "losses": total_losses,
                "winrate": total_winrate,
                "bot_15m": {
                    "pnl": round(pnl, 2),
                    "wins": stats["wins"],
                    "losses": stats["losses"],
                    "winrate": stats["winrate"],
                },
                "bot_5m": {
                    "pnl": round(pnl_5m, 2),
                    "wins": wins_5m,
                    "losses": losses_5m,
                    "winrate": stats_5m.get("winrate", 0),
                },
            },
            "risk": {
                "bankroll": bankroll,
                "kelly_max_pct": kelly_pct,
                "daily_loss_limit": daily_loss_limit,
                "daily_loss_limit_enabled": use_dsl,
                "loss_today": round(loss_today, 2),
                "dsl_remaining": (
                    round(max(0.0, daily_loss_limit - loss_today), 2)
                    if use_dsl else None
                ),
                "breakers_today": stats["breakers"],
            },
            "exhaust": {
                "blocks": stats["blocks"],
                "dampens": stats["dampens"],
                "flips": stats["flips"],
                "signals": stats["signals"],
                "orders": stats["orders"],
                "fills": stats["fills"],
                "by_coin": stats["blocks_by_coin"],
            },
            "calibration": state.get_outcomes(),
            "market": {
                "coins": list(coin_rows.values()),
            },
            "trades_today": recent,
            "signals": signals,
            "events": events,
            "bot_5m": {
                "status": _bot_5m_status(),
                "stats": stats_5m,
                "heartbeat": {
                    "last_event_ts": parser_5m.get_last_log_ts(),
                    "last_scan_ts": parser_5m.get_last_scan_ts(),
                    "log_mtime": parser_5m.get_last_file_mtime(),
                },
            },
        })
    except Exception as e:
        logger.exception("snapshot failed")
        return jsonify({"error": str(e)}), 500


@app.route("/api/v3/bot_marks")
def api_bot_marks():
    """Per-coin markers for chart overlays.

    Returns: {"BTC": [{ts, type, dir, price, amount, ...}, ...], ...}
    `ts` is UTC epoch seconds (Lightweight Charts `time` field).
    """
    trades = get_today_trades()
    by_coin: dict[str, list[dict]] = {c: [] for c in COINS}
    for t in trades:
        coin = t.get("coin")
        if coin not in by_coin:
            continue
        by_coin[coin].append({
            "ts": int(t.get("ts", 0)),
            "t": t.get("t"),
            "type": t.get("type"),
            "dir": t.get("dir"),
            "entry": t.get("entry"),
            "price": t.get("price"),
            "ask": t.get("ask"),
            "shares": t.get("shares"),
            "amount": t.get("amount"),
            "cost": t.get("cost"),
            "session": t.get("session"),
        })
    return jsonify({"coins": by_coin, "n": sum(len(v) for v in by_coin.values())})


@app.route("/api/v3/health")
def api_health():
    return jsonify({"ok": True, "ts": time.time()})


if __name__ == "__main__":
    port = int(os.getenv("DASH_PORT", "8080"))
    logger.info(f"Starting Dashboard v3 on 0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
