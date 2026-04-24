"""
Analytics package for the v3-bot.

Feature-flagged via environment variable `ENABLE_ANALYTICS` (default: true).
Set `ENABLE_ANALYTICS=false` to disable all writes/reads with zero code changes.

Modules
-------
event_logger : append-only JSONL writer for every trade decision
resolver     : joins pending fired events with their Gamma/CLOB outcomes
backfill     : reconstructs historical events from v3_bot.log + Polymarket CSVs
analyze      : computes calibration, per-feature lift, counterfactual, R:R matrix
"""
from .event_logger import log, EVENTS_PATH, is_enabled  # noqa: F401

__all__ = ["log", "EVENTS_PATH", "is_enabled"]
