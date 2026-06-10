"""
State mirror — keep snapshots of the bot's JSON state files in sqlite,
alongside the live JSON. JSON remains the canonical store; sqlite is for
queryable history + future replay.

Sources mirrored:
  • outcomes_state.json       → table  state_outcomes
  • chop_state.json           → table  state_chop
  • data/traded_windows.json  → table  state_traded_windows
  • data/regime_detector_state.json → table state_regime_summary

This module is intentionally *additive*: when called from a scheduler hook,
each function takes a Python dict (already parsed from JSON), writes one
timestamped row into the corresponding table, and returns.

Read code in the bot continues to use the JSON files. Once we trust the
mirror, a follow-up commit will switch the writers to sqlite directly and
demote JSON to a one-way export.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any, Dict, Optional


_DEFAULT_DB = "/home/ubuntu/v3-bot/data/trade_ledger.db"

_LOCK = threading.Lock()
_SCHEMA_OK = False


def _db_path() -> str:
    return os.getenv("LEDGER_DB_PATH", _DEFAULT_DB)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS state_outcomes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_epoch     INTEGER NOT NULL,
    n_outcomes   INTEGER NOT NULL,
    accuracy_pct REAL,
    last_outcome INTEGER,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS state_chop (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_epoch      INTEGER NOT NULL,
    chop_score    REAL,
    summary_text  TEXT,
    payload_json  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS state_traded_windows (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_epoch        INTEGER NOT NULL,
    n_active_locks  INTEGER NOT NULL,
    payload_json    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS state_regime_summary (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_epoch        INTEGER NOT NULL,
    n_recent        INTEGER NOT NULL,
    wr              REAL,
    regime          TEXT,
    buckets_tracked INTEGER,
    payload_json    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_outcomes_ts ON state_outcomes(ts_epoch);
CREATE INDEX IF NOT EXISTS idx_chop_ts ON state_chop(ts_epoch);
CREATE INDEX IF NOT EXISTS idx_traded_ts ON state_traded_windows(ts_epoch);
CREATE INDEX IF NOT EXISTS idx_regime_ts ON state_regime_summary(ts_epoch);
"""


def _ensure_schema() -> bool:
    global _SCHEMA_OK
    if _SCHEMA_OK:
        return True
    path = _db_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with _LOCK:
            with sqlite3.connect(path, timeout=2.0) as conn:
                conn.executescript(_SCHEMA)
                conn.commit()
        _SCHEMA_OK = True
        return True
    except Exception:
        return False


def _dump(obj: Any) -> str:
    try:
        return json.dumps(obj, default=str, ensure_ascii=False)
    except Exception:
        return "{}"


def snapshot_outcomes(outcomes: Dict) -> bool:
    """Snapshot of outcomes_state.json.

    Expects the parsed dict {"outcomes": [bool, bool, ...]}.
    """
    if not _ensure_schema():
        return False
    arr = outcomes.get("outcomes", []) if isinstance(outcomes, dict) else []
    n = len(arr)
    acc = round(sum(1 for x in arr if x) / max(1, n) * 100.0, 1) if n else 0.0
    last = 1 if arr and arr[-1] else (0 if arr else None)
    try:
        with _LOCK:
            with sqlite3.connect(_db_path(), timeout=2.0) as conn:
                conn.execute(
                    "INSERT INTO state_outcomes (ts_epoch, n_outcomes, "
                    "accuracy_pct, last_outcome, payload_json) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (int(time.time()), n, acc, last, _dump(outcomes)),
                )
                conn.commit()
        return True
    except Exception:
        return False


def snapshot_chop(chop: Dict) -> bool:
    """Snapshot of chop_state.json. Expects {"history": [...]} dict."""
    if not _ensure_schema():
        return False
    history = chop.get("history", []) if isinstance(chop, dict) else []
    flips = 0
    if len(history) >= 2:
        flips = sum(1 for i in range(1, len(history))
                    if history[i] != history[i - 1])
    score = flips / max(1, len(history) - 1) if len(history) >= 2 else 0.0
    summary = " -> ".join(history[-4:]) if history else ""
    try:
        with _LOCK:
            with sqlite3.connect(_db_path(), timeout=2.0) as conn:
                conn.execute(
                    "INSERT INTO state_chop (ts_epoch, chop_score, "
                    "summary_text, payload_json) VALUES (?, ?, ?, ?)",
                    (int(time.time()), round(score, 3), summary, _dump(chop)),
                )
                conn.commit()
        return True
    except Exception:
        return False


def snapshot_traded_windows(traded: Any) -> bool:
    """Snapshot of traded_windows.json. Accepts list (run_bot's format) or
    dict (order_manager's format)."""
    if not _ensure_schema():
        return False
    if isinstance(traded, list):
        n_locks = len(traded)
    elif isinstance(traded, dict):
        n_locks = len(traded)
    else:
        n_locks = 0
    try:
        with _LOCK:
            with sqlite3.connect(_db_path(), timeout=2.0) as conn:
                conn.execute(
                    "INSERT INTO state_traded_windows (ts_epoch, "
                    "n_active_locks, payload_json) VALUES (?, ?, ?)",
                    (int(time.time()), n_locks, _dump(traded)),
                )
                conn.commit()
        return True
    except Exception:
        return False


def snapshot_regime(stats_summary: Dict) -> bool:
    """Snapshot of RegimeDetector.stats_summary() output."""
    if not _ensure_schema() or not isinstance(stats_summary, dict):
        return False
    try:
        with _LOCK:
            with sqlite3.connect(_db_path(), timeout=2.0) as conn:
                conn.execute(
                    "INSERT INTO state_regime_summary (ts_epoch, n_recent, "
                    "wr, regime, buckets_tracked, payload_json) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        int(time.time()),
                        int(stats_summary.get("n", 0) or 0),
                        float(stats_summary.get("wr", 0.0) or 0.0),
                        stats_summary.get("regime"),
                        int(stats_summary.get("buckets_tracked", 0) or 0),
                        _dump(stats_summary),
                    ),
                )
                conn.commit()
        return True
    except Exception:
        return False


# ── Convenience: scan known JSON paths and snapshot whatever's present ──────
def snapshot_all(repo_dir: str = "/home/ubuntu/v3-bot") -> Dict[str, bool]:
    """Read every known JSON state file (best-effort) and snapshot it."""
    results: Dict[str, bool] = {}

    def _load(p: str) -> Optional[Any]:
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    out = _load(os.path.join(repo_dir, "outcomes_state.json"))
    if out is not None:
        results["outcomes"] = snapshot_outcomes(out)

    chop = _load(os.path.join(repo_dir, "chop_state.json"))
    if chop is not None:
        results["chop"] = snapshot_chop(chop)

    twins = _load(os.path.join(repo_dir, "traded_windows.json"))
    if twins is not None:
        results["traded_windows_run_bot"] = snapshot_traded_windows(twins)

    om_twins = _load(os.path.join(repo_dir, "data/traded_windows.json"))
    if om_twins is not None:
        results["traded_windows_order_manager"] = snapshot_traded_windows(om_twins)

    # Regime detector state is structured; produce stats_summary if possible
    try:
        from regime_aware.persistence import load_state as _load_regime
        det = _load_regime(os.path.join(repo_dir, "data/regime_detector_state.json"))
        if det is not None:
            results["regime"] = snapshot_regime(det.stats_summary())
    except Exception:
        pass

    return results


if __name__ == "__main__":
    # CLI: snapshot every JSON state file we can find.
    import sys
    repo = sys.argv[1] if len(sys.argv) > 1 else "/home/ubuntu/v3-bot"
    res = snapshot_all(repo)
    print(f"[state_mirror] db={_db_path()}")
    for k, v in res.items():
        print(f"  {k}: {'ok' if v else 'failed'}")
    if not res:
        print("  (no JSON state files found)")
