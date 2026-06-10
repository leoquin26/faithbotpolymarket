"""
audit_panels — dashboard v3 add-on that surfaces all the new AUDIT_MAY27
telemetry (reversion-risk shadow, calibration shadow, microstructure,
cross-asset, sqlite ledger, per-coin regime) without touching the existing
dashboard routes.

Wire-up (single line in app.py):
    from dashboard_v3 import audit_panels
    audit_panels.register(app)

Routes added:
    /audit                          → render audit.html
    /api/v3/audit/snapshot          → aggregated payload for the page poller
    /api/v3/audit/reversion_log     → recent [REVERSION SHADOW] lines
    /api/v3/audit/calibration_log   → recent [CALIBRATION SHADOW] lines
    /api/v3/audit/micro_log         → recent [MICRO] lines (per coin)
    /api/v3/audit/xasset_log        → recent [XASSET] lines
    /api/v3/audit/ledger            → sqlite ledger summary + recent trades
    /api/v3/audit/per_coin_regime   → regime state per coin
    /api/v3/audit/grade             → live counterfactual grading for both
                                       reversion-risk and calibrator
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from flask import Flask, jsonify, render_template, request


REPO = "/home/ubuntu/v3-bot"
LEDGER_DB = os.getenv("LEDGER_DB_PATH", f"{REPO}/data/trade_ledger.db")


# ── Log regexes (mirror what the graders parse) ──────────────────────────────
_TS = r"(\d{2}):(\d{2}):(\d{2})"
RE_REVERSION = re.compile(
    rf"^{_TS}\s+\|\s+\w+\s+\|\s+\[REVERSION (?P<mode>SHADOW|LIVE)\]\s+"
    r"(?P<coin>BTC|ETH|SOL|XRP)\s+(?P<dir>UP|DOWN)\s+"
    r"risk=(?P<risk>[0-9.]+)\s+action=(?P<action>CLEAN|DAMPEN|INVERT)\s+"
    r"vel_adv=(?P<vel>[0-9.]+)cpm\s+spike=(?P<spike>none|mild|very)\s+"
    r"T=(?P<t>\d+)s"
)
RE_CALIBRATION = re.compile(
    rf"^{_TS}\s+\|\s+\w+\s+\|\s+\[CALIBRATION (?P<mode>SHADOW|LIVE)\]\s+"
    r"(?P<coin>BTC|ETH|SOL|XRP)\s+(?P<dir>UP|DOWN)\s+"
    r"raw=(?P<raw>\d+)%\s+cal=(?P<cal>\d+)%\s+\((?P<delta>[-+0-9.]+)pp\)\s+\|\s+"
    r"reg=(?P<reg>[0-9.]+)\s+bkt=(?P<bkt>[0-9.]+)\s+"
    r"mic=(?P<mic>[0-9.]+)\s+rev=(?P<rev>[0-9.]+)\s+late=(?P<late>[0-9.]+)"
)
RE_MICRO = re.compile(
    rf"^{_TS}\s+\|\s+\w+\s+\|\s+\[MICRO\]\s+(BTC|ETH|SOL|XRP)\s+(UP|DOWN)\s+"
    r"ask_vel=(?P<ask_vel>[+-]?[0-9.]+)cpm\s+adv=(?P<adv>[0-9.]+)cpm\s+"
    r"bid_vel=(?P<bid_vel>[+-]?[0-9.]+)cpm\s+spread=(?P<spread>[\d.\-—]+)(?:bps)?\s+"
    r"depth_skew=(?P<skew>[+-]?[0-9.]+)\s+depth_side=(?P<depth>[0-9.]+)"
)
RE_XASSET = re.compile(
    rf"^{_TS}\s+\|\s+\w+\s+\|\s+\[XASSET\]\s+"
    r"breadth=(?P<breadth>[+-]?[0-9.]+)\s+dom=(?P<dom>[A-Z—]+)\s+age=(?P<age>\d+)s"
)
RE_SIGNAL = re.compile(
    rf"^{_TS}\s+\|\s+\w+\s+\|\s+\[SIGNAL\]\s+(BTC|ETH|SOL|XRP)\s+(UP|DOWN)\s+\|\s+"
    r"Prob=(?P<prob>\d+)%\s+\|\s+Ask=(?P<ask>\d+)c\s+\|\s+Edge=(?P<edge>[-+0-9.]+)%"
)


def _latest_log_path() -> Optional[str]:
    """Find today's bot loguru file; fall back to the most recent."""
    today = datetime.now().strftime("%Y-%m-%d")
    cand = f"{REPO}/logs/bot_{today}.log"
    if os.path.exists(cand):
        return cand
    # fallback: newest bot_*.log
    try:
        files = sorted(
            f for f in os.listdir(f"{REPO}/logs")
            if f.startswith("bot_") and f.endswith(".log") and "5m" not in f
        )
        if files:
            return f"{REPO}/logs/{files[-1]}"
    except Exception:
        pass
    return None


def _tail_lines(path: str, n_bytes: int = 512_000) -> List[str]:
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - n_bytes))
            return f.read().decode(errors="replace").splitlines()
    except Exception:
        return []


def _parse_recent(regex, limit: int = 40) -> List[Dict]:
    path = _latest_log_path()
    if not path:
        return []
    out: List[Dict] = []
    for line in _tail_lines(path):
        m = regex.match(line)
        if m:
            row = m.groupdict()
            row["time"] = f"{m.group(1)}:{m.group(2)}:{m.group(3)}"
            out.append(row)
    return out[-limit:]


# ── Endpoint: reversion log ──────────────────────────────────────────────────
def _reversion_log(limit: int = 40) -> Dict:
    rows = _parse_recent(RE_REVERSION, limit=limit)
    by_action: Dict[str, int] = {"CLEAN": 0, "DAMPEN": 0, "INVERT": 0}
    for r in rows:
        by_action[r["action"]] = by_action.get(r["action"], 0) + 1
    return {"rows": rows, "by_action": by_action, "n": len(rows)}


# ── Endpoint: calibration log ────────────────────────────────────────────────
def _calibration_log(limit: int = 40) -> Dict:
    rows = _parse_recent(RE_CALIBRATION, limit=limit)
    deltas: List[float] = []
    factor_sums: Dict[str, float] = {}
    factor_count = 0
    for r in rows:
        try:
            raw = int(r["raw"]) / 100.0
            cal = int(r["cal"]) / 100.0
            deltas.append((cal - raw) * 100.0)
        except Exception:
            pass
        for k in ("reg", "bkt", "mic", "rev", "late"):
            try:
                factor_sums[k] = factor_sums.get(k, 0.0) + float(r[k])
            except Exception:
                pass
        factor_count += 1
    summary = {
        "n": len(rows),
        "avg_delta_pp": round(sum(deltas) / max(1, len(deltas)), 2) if deltas else 0.0,
        "max_shrink_pp": round(min(deltas), 2) if deltas else 0.0,
        "max_lift_pp": round(max(deltas), 2) if deltas else 0.0,
        "avg_factors": {
            k: round(v / max(1, factor_count), 3) for k, v in factor_sums.items()
        },
    }
    return {"rows": rows, "summary": summary}


# ── Endpoint: microstructure log ─────────────────────────────────────────────
def _micro_log(limit: int = 60) -> Dict:
    path = _latest_log_path()
    rows_full: List[Dict] = []
    if path:
        for line in _tail_lines(path):
            m = RE_MICRO.match(line)
            if not m:
                continue
            spread_str = m.group("spread")
            try:
                spread_bps = float(spread_str)
            except ValueError:
                spread_bps = None
            rows_full.append({
                "time": f"{m.group(1)}:{m.group(2)}:{m.group(3)}",
                "coin": m.group(4),
                "direction": m.group(5),
                "ask_vel": float(m.group("ask_vel")),
                "adv": float(m.group("adv")),
                "bid_vel": float(m.group("bid_vel")),
                "spread_bps": spread_bps,
                "depth_skew": float(m.group("skew")),
                "depth_side": float(m.group("depth")),
            })
    rows_full = rows_full[-limit:]
    latest: Dict[str, Dict] = {}
    for r in rows_full:
        key = f"{r['coin']}:{r['direction']}"
        latest[key] = r
    return {"rows": rows_full, "latest_by_side": latest}


# ── Endpoint: xasset log ─────────────────────────────────────────────────────
def _xasset_log(limit: int = 40) -> Dict:
    rows = _parse_recent(RE_XASSET, limit=limit)
    return {"rows": rows, "n": len(rows)}


# ── Endpoint: ledger summary ─────────────────────────────────────────────────
def _ledger_snapshot() -> Dict:
    out: Dict[str, Any] = {
        "db_path": LEDGER_DB,
        "exists": os.path.exists(LEDGER_DB),
        "events_by_type": {},
        "n_trades": 0,
        "recent_trades": [],
        "state_snapshots": {},
    }
    if not out["exists"]:
        return out
    try:
        with sqlite3.connect(LEDGER_DB, timeout=2.0) as c:
            for ev, n in c.execute(
                "SELECT event, COUNT(*) AS n FROM events "
                "GROUP BY event ORDER BY n DESC"
            ):
                out["events_by_type"][ev] = n
            out["n_trades"] = c.execute(
                "SELECT COUNT(*) FROM trades"
            ).fetchone()[0]
            for row in c.execute(
                "SELECT trade_id, coin, side, placed_ts, resolved_ts, entry, "
                "shares, cost, prob, edge, regime, reversion_action, "
                "reversion_risk, phase, won, pnl "
                "FROM trades ORDER BY placed_ts DESC LIMIT 25"
            ):
                (tid, coin, side, placed, resolved, entry, shares, cost,
                 prob, edge, regime, rev_act, rev_risk, phase, won, pnl) = row
                out["recent_trades"].append({
                    "trade_id": tid,
                    "coin": coin, "side": side,
                    "placed_iso": datetime.utcfromtimestamp(placed or 0).strftime("%H:%M:%S")
                    if placed else None,
                    "resolved_iso": datetime.utcfromtimestamp(resolved or 0).strftime("%H:%M:%S")
                    if resolved else None,
                    "entry_c": int(round((entry or 0) * 100)),
                    "shares": shares,
                    "cost": cost,
                    "prob_pct": round((prob or 0) * 100, 1) if prob else None,
                    "edge_pct": round((edge or 0) * 100, 1) if edge else None,
                    "regime": regime,
                    "reversion": rev_act,
                    "reversion_risk": rev_risk,
                    "phase": phase,
                    "won": won,
                    "pnl": pnl,
                })
            for tbl in (
                "state_outcomes", "state_chop",
                "state_traded_windows", "state_regime_summary",
            ):
                try:
                    out["state_snapshots"][tbl] = c.execute(
                        f"SELECT COUNT(*) FROM {tbl}"
                    ).fetchone()[0]
                except Exception:
                    out["state_snapshots"][tbl] = None
    except Exception as e:
        out["error"] = str(e)
    return out


# ── Endpoint: per-coin regime ────────────────────────────────────────────────
def _per_coin_regime() -> Dict:
    """Read regime_detector_state.json and surface the per-coin regimes."""
    try:
        path = f"{REPO}/data/regime_detector_state.json"
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)
    except Exception as e:
        return {"error": str(e)}
    # Use the new persistence schema (per_coin saved if available)
    coin_recent = state.get("per_coin", {})
    # Best-effort classification mirrors RegimeDetector._classify
    per_coin: Dict[str, Dict] = {}
    for coin, recs in coin_recent.items():
        n = len(recs)
        if n == 0:
            per_coin[coin] = {"n": 0, "regime": "WARMUP"}
            continue
        wins = sum(1 for r in recs if r.get("won"))
        wr = wins / n if n else 0.0
        ht = [r for r in recs if abs(r.get("trend", 0)) >= 1.5]
        ht_wr = (sum(1 for r in ht if r.get("won")) / len(ht)) if ht else None
        regime = "WARMUP"
        if n >= 8:
            if ht_wr is None:
                regime = "NEUTRAL"
            elif ht_wr >= 0.60:
                regime = "TRENDING"
            elif ht_wr <= 0.40:
                regime = "REVERTING"
            else:
                regime = "NEUTRAL"
        per_coin[coin] = {
            "n": n, "wr": round(wr, 3),
            "ht_wr": round(ht_wr, 3) if ht_wr is not None else None,
            "regime": regime,
        }
    # Global summary
    recent_global = state.get("recent", [])
    n_g = len(recent_global)
    wr_g = (sum(1 for r in recent_global if r.get("won")) / n_g) if n_g else 0.0
    return {
        "per_coin": per_coin,
        "global": {"n": n_g, "wr": round(wr_g, 3)},
    }


# ── Endpoint: live grading ───────────────────────────────────────────────────
def _run_grader(script: str) -> Dict:
    """Run one of the on-EC2 graders, capture its stdout."""
    try:
        path = _latest_log_path()
        if not path:
            return {"ok": False, "error": "no log file"}
        out = subprocess.check_output(
            ["python3", f"{REPO}/{script}", "--logs", path, "--no-detail"],
            text=True, timeout=15, stderr=subprocess.STDOUT,
        )
        return {"ok": True, "output": out}
    except subprocess.CalledProcessError as e:
        return {"ok": False, "output": e.output}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _grade_both() -> Dict:
    return {
        "reversion": _run_grader("_grade_reversion_shadow.py"),
        "calibration": _run_grader("_grade_calibration_shadow.py"),
    }


# ── Endpoint: aggregated snapshot ────────────────────────────────────────────
def _snapshot() -> Dict:
    return {
        "ts": int(time.time()),
        "reversion": _reversion_log(limit=40),
        "calibration": _calibration_log(limit=40),
        "micro": _micro_log(limit=80),
        "xasset": _xasset_log(limit=40),
        "ledger": _ledger_snapshot(),
        "per_coin_regime": _per_coin_regime(),
    }


# ── Register routes ──────────────────────────────────────────────────────────
def register(app: Flask) -> None:
    @app.route("/audit")
    def _audit_page():
        return render_template("audit.html")

    @app.route("/api/v3/audit/snapshot")
    def _audit_snapshot_route():
        return jsonify(_snapshot())

    @app.route("/api/v3/audit/reversion_log")
    def _audit_reversion_route():
        return jsonify(_reversion_log(limit=int(request.args.get("limit", 40))))

    @app.route("/api/v3/audit/calibration_log")
    def _audit_calibration_route():
        return jsonify(_calibration_log(limit=int(request.args.get("limit", 40))))

    @app.route("/api/v3/audit/micro_log")
    def _audit_micro_route():
        return jsonify(_micro_log(limit=int(request.args.get("limit", 80))))

    @app.route("/api/v3/audit/xasset_log")
    def _audit_xasset_route():
        return jsonify(_xasset_log(limit=int(request.args.get("limit", 40))))

    @app.route("/api/v3/audit/ledger")
    def _audit_ledger_route():
        return jsonify(_ledger_snapshot())

    @app.route("/api/v3/audit/per_coin_regime")
    def _audit_pcr_route():
        return jsonify(_per_coin_regime())

    @app.route("/api/v3/audit/grade")
    def _audit_grade_route():
        return jsonify(_grade_both())
