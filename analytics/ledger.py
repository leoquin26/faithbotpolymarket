"""
SQLite trade ledger — secondary sink for events the bot already emits via
analytics.event_logger (JSONL).

Why both?
  • JSONL stays the canonical, append-only audit log (POSIX-atomic on small
    writes, plays well with text tools).
  • SQLite gives us indexed query, joinable trade lifecycles, and a stable
    schema we can backtest / calibrate against without parsing log text.

This module is feature-flagged separately from JSONL:
    LEDGER_ENABLED=on    (default off so it can be rolled out per-bot)
    LEDGER_DB_PATH=...   (default /home/ubuntu/v3-bot/data/trade_ledger.db)

Schema (created if absent on first call):

    events
      id          INTEGER PRIMARY KEY AUTOINCREMENT
      ts_epoch    INTEGER NOT NULL
      ts_iso      TEXT    NOT NULL
      event       TEXT    NOT NULL    -- SIGNAL / EXHAUST / BLOCKED / FIRED / RESOLVED / ...
      trade_id    TEXT
      coin        TEXT
      side        TEXT
      window_start INTEGER
      data_json   TEXT    NOT NULL    -- full event payload (mirrors JSONL)

    trades   -- denormalized per-trade view, populated on FIRED + RESOLVED
      trade_id    TEXT PRIMARY KEY
      coin        TEXT
      side        TEXT
      window_start INTEGER
      placed_ts   INTEGER
      resolved_ts INTEGER
      entry       REAL
      shares      INTEGER
      cost        REAL
      prob        REAL
      edge        REAL
      trend_score REAL
      regime      TEXT
      reversion_action TEXT
      reversion_risk   REAL
      phase       TEXT
      won         INTEGER     -- 0/1/NULL
      pnl         REAL

Indexes:  events(trade_id), events(coin, ts_epoch), trades(coin, placed_ts)

Public API (mirrors what event_logger emits):
    init_ledger()                   -- create db + indexes if missing (idempotent)
    is_enabled() -> bool
    log_event_dict(d: dict)          -- append one row to events
    log_resolved_trade(trade_id, ...) -- helper to build the trades-table row

The bot calls `log_event_dict` from inside event_logger.log() (one-line hook),
so every JSONL event is mirrored to sqlite for free.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import Any, Optional


_DB_PATH = os.getenv(
    "LEDGER_DB_PATH",
    "/home/ubuntu/v3-bot/data/trade_ledger.db",
)
_FLAG = os.getenv("LEDGER_ENABLED", "off").strip().lower()
_ENABLED = _FLAG in ("on", "true", "1", "yes")

_LOCK = threading.Lock()
_INITIALIZED = False
_WARNED_ONCE = False


def is_enabled() -> bool:
    return _ENABLED


def db_path() -> str:
    return _DB_PATH


def _warn_once(msg: str) -> None:
    global _WARNED_ONCE
    if _WARNED_ONCE:
        return
    _WARNED_ONCE = True
    try:
        from loguru import logger  # type: ignore
        logger.warning(f"[LEDGER] {msg}")
    except Exception:
        print(f"[LEDGER] {msg}")


# ── Schema ───────────────────────────────────────────────────────────────────
_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_epoch     INTEGER NOT NULL,
    ts_iso       TEXT    NOT NULL,
    event        TEXT    NOT NULL,
    trade_id     TEXT,
    coin         TEXT,
    side         TEXT,
    window_start INTEGER,
    data_json    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_trade ON events(trade_id);
CREATE INDEX IF NOT EXISTS idx_events_coin_ts ON events(coin, ts_epoch);
CREATE INDEX IF NOT EXISTS idx_events_event_ts ON events(event, ts_epoch);

CREATE TABLE IF NOT EXISTS trades (
    trade_id          TEXT PRIMARY KEY,
    coin              TEXT,
    side              TEXT,
    window_start      INTEGER,
    placed_ts         INTEGER,
    resolved_ts       INTEGER,
    entry             REAL,
    shares            INTEGER,
    cost              REAL,
    prob              REAL,
    edge              REAL,
    trend_score       REAL,
    regime            TEXT,
    reversion_action  TEXT,
    reversion_risk    REAL,
    phase             TEXT,
    won               INTEGER,
    pnl               REAL
);
CREATE INDEX IF NOT EXISTS idx_trades_coin_placed ON trades(coin, placed_ts);
CREATE INDEX IF NOT EXISTS idx_trades_window ON trades(window_start);
"""


def init_ledger() -> bool:
    """Create the database and tables if needed.  Idempotent.  Returns True
    on success or no-op (already initialized), False on any failure."""
    global _INITIALIZED
    if not _ENABLED:
        return False
    if _INITIALIZED:
        return True
    try:
        os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
        with _LOCK:
            with sqlite3.connect(_DB_PATH, timeout=2.0) as conn:
                conn.executescript(_SCHEMA)
                conn.commit()
        _INITIALIZED = True
        return True
    except Exception as e:
        _warn_once(f"init failed: {e}")
        return False


def _coerce_int(v) -> Optional[int]:
    try:
        if v is None:
            return None
        return int(v)
    except (TypeError, ValueError):
        return None


def _coerce_float(v) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def log_event_dict(d: dict) -> None:
    """Append one row to events.  Pulls common columns out for indexability;
    keeps the full payload in data_json so we never lose information."""
    if not _ENABLED:
        return
    if not _INITIALIZED and not init_ledger():
        return
    try:
        ts_epoch = _coerce_int(d.get("ts_epoch")) or 0
        ts_iso = str(d.get("ts") or "")
        event = str(d.get("event") or "")
        trade_id = d.get("trade_id")
        coin = d.get("coin")
        side = d.get("side") or d.get("direction")
        window_start = _coerce_int(d.get("window_start"))
        try:
            payload_json = json.dumps(d, default=str, ensure_ascii=False)
        except Exception:
            payload_json = json.dumps({"_dump_failed": True, "event": event})
        with _LOCK:
            with sqlite3.connect(_DB_PATH, timeout=2.0) as conn:
                conn.execute(
                    "INSERT INTO events (ts_epoch, ts_iso, event, trade_id, "
                    "coin, side, window_start, data_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (ts_epoch, ts_iso, event, trade_id, coin, side,
                     window_start, payload_json),
                )

                # Denormalize useful per-trade rows on FIRED + RESOLVED.
                if event == "FIRED" and trade_id:
                    conn.execute(
                        "INSERT OR REPLACE INTO trades "
                        "(trade_id, coin, side, window_start, placed_ts, "
                        " entry, shares, cost, prob, edge, trend_score, "
                        " regime, reversion_action, reversion_risk, phase) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            trade_id, coin, side, window_start, ts_epoch,
                            _coerce_float(d.get("entry")),
                            _coerce_int(d.get("shares")),
                            _coerce_float(d.get("cost")),
                            _coerce_float(d.get("prob")),
                            _coerce_float(d.get("edge")),
                            _coerce_float(d.get("trend_score")),
                            d.get("regime"),
                            d.get("reversion_action"),
                            _coerce_float(d.get("reversion_risk")),
                            d.get("phase"),
                        ),
                    )
                elif event == "RESOLVED" and trade_id:
                    won_int = None
                    won_v = d.get("won")
                    if won_v is not None:
                        won_int = 1 if bool(won_v) else 0
                    conn.execute(
                        "UPDATE trades SET resolved_ts = ?, won = ?, pnl = ? "
                        "WHERE trade_id = ?",
                        (
                            ts_epoch, won_int, _coerce_float(d.get("pnl")),
                            trade_id,
                        ),
                    )
                conn.commit()
    except Exception as e:
        _warn_once(f"insert failed: {e}")


# ── Read helpers (used by graders / dashboards / interactive ipython) ────────
def fetch_events(limit: int = 200, event: Optional[str] = None,
                 coin: Optional[str] = None) -> list:
    if not _INITIALIZED and not init_ledger():
        return []
    where = []
    params: list = []
    if event:
        where.append("event = ?")
        params.append(event)
    if coin:
        where.append("coin = ?")
        params.append(coin)
    sql = "SELECT ts_iso, event, coin, side, trade_id, window_start, data_json FROM events"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(int(limit))
    try:
        with sqlite3.connect(_DB_PATH, timeout=2.0) as conn:
            cur = conn.execute(sql, params)
            rows = cur.fetchall()
        return rows
    except Exception as e:
        _warn_once(f"fetch failed: {e}")
        return []


def fetch_trade_summary(coin: Optional[str] = None, limit: int = 50) -> list:
    if not _INITIALIZED and not init_ledger():
        return []
    sql = ("SELECT trade_id, coin, side, placed_ts, resolved_ts, entry, "
           "shares, cost, prob, regime, reversion_action, won, pnl "
           "FROM trades")
    params: list = []
    if coin:
        sql += " WHERE coin = ?"
        params.append(coin)
    sql += " ORDER BY placed_ts DESC LIMIT ?"
    params.append(int(limit))
    try:
        with sqlite3.connect(_DB_PATH, timeout=2.0) as conn:
            cur = conn.execute(sql, params)
            return cur.fetchall()
    except Exception as e:
        _warn_once(f"fetch trades failed: {e}")
        return []
