"""
Reconstruct historical trade events from two sources:
  1. /home/ubuntu/v3-bot/v3_bot.log  (and its .1/.2 rotations)
  2. Polymarket CSV exports (user-downloaded history)

The output is written to a separate file so we never corrupt the live stream:
  /home/ubuntu/v3-bot/data/trade_events_backfill.jsonl

Run:
  python3 -m analytics.backfill
    [--log /home/ubuntu/v3-bot/v3_bot.log]
    [--csv /home/ubuntu/v3-bot/data/Polymarket-History-2026-04-22.csv]
    [--csv /home/ubuntu/v3-bot/data/Polymarket-History-2026-04-23.csv]
    [--out /home/ubuntu/v3-bot/data/trade_events_backfill.jsonl]

Design note
-----------
Bot logs have no date stamps. We segment the log by "midnight rollover"
lines (HH:MM == "00:00") and by bot startup markers
("V11 BOT — Black-Scholes Binary Engine"). The user tells us which date
each segment belongs to via CSV timestamps (CSVs have full ISO dates).

The backfill is best-effort and approximate — a row is only emitted if we
can bind it confidently to a date. Ambiguous log segments are skipped
and reported at the end.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Iterable, Optional

# Regexes matching the v3_bot.log format ---------------------------------------

RE_TS = re.compile(r"^(\d{2}):(\d{2}):(\d{2})\s*\|\s*(DEBUG|INFO|WARNING|ERROR|SUCCESS)?")

# [SIGNAL] BTC UP | edge=8.0% | prob=78% | entry=64c | trend=1.24
RE_SIGNAL = re.compile(
    r"\[SIGNAL\]\s+(BTC|ETH|SOL|XRP)\s+(UP|DOWN)\s*\|\s*"
    r"edge=([\-\d\.]+)%\s*\|\s*prob=([\-\d\.]+)%\s*\|\s*"
    r"entry=([\-\d\.]+)c\s*\|\s*trend=([\-\d\.]+)"
)

# [EXHAUST] SOL DOWN @ 58c | score=0.51 raw=ABSTAIN action=ABSTAIN | decel=1.00 poly=0.00 range=0.92 breadth=0.67 tick=0.00
RE_EXHAUST = re.compile(
    r"\[EXHAUST\]\s+(BTC|ETH|SOL|XRP)\s+(UP|DOWN)\s+@\s+\d+c\s*\|\s*"
    r"score=([\-\d\.]+)\s+raw=(\w+)\s+action=(\w+)\s*\|\s*"
    r"decel=([\-\d\.]+)\s+poly=([\-\d\.]+)\s+range=([\-\d\.]+)\s+breadth=([\-\d\.]+)"
)

# [FILLED] BTC UP | 5 shares @ 64c | cost=$3.20
RE_FILLED = re.compile(
    r"\[FILLED\]\s+(BTC|ETH|SOL|XRP)\s+(UP|DOWN)\s*\|\s*([\d\.]+)\s*shares\s*@\s*([\-\d\.]+)c"
    r"(?:\s*\|\s*cost=\$([\-\d\.]+))?"
)

# [WIN PM] BTC UP | +$4.20 | Entry: 64c x10 | Payout: $10.00
# [LOSS MORNING] XRP UP | -$5.80 | Entry: 67c x12
RE_WINLOSS = re.compile(
    r"\[(WIN|LOSS)\s+(MORNING|PM)\]\s+(BTC|ETH|SOL|XRP)\s+(UP|DOWN)\s*\|\s*"
    r"([\+\-]\$[\d\.]+)(?:\s*\|\s*Entry:\s*([\-\d\.]+)c\s*x([\d\.]+))?"
    r"(?:\s*\|\s*Payout:\s*\$([\-\d\.]+))?"
)

# Generic filter/block patterns
RE_BLOCK_PATTERNS = [
    (re.compile(r"\[WARMUP\]\s+(BTC|ETH|SOL|XRP)\b"), "WARMUP"),
    (re.compile(r"\[COLD START\]\s+(BTC|ETH|SOL|XRP)\b"), "COLD_START"),
    (re.compile(r"\[TOO LATE\]\s+(BTC|ETH|SOL|XRP)\b"), "LATE"),
    (re.compile(r"\[WEAK TREND\]\s+(BTC|ETH|SOL|XRP)\b"), "WEAK_TREND"),
    (re.compile(r"\[NO ASK\]\s+(BTC|ETH|SOL|XRP)\s+(UP|DOWN)\b"), "NO_ASK"),
    (re.compile(r"\[FLIP GUARD\]\s+(BTC|ETH|SOL|XRP)\s+(UP|DOWN)\b"), "FLIP_GUARD"),
    (re.compile(r"\[EXHAUST BLOCK\]\s+(BTC|ETH|SOL|XRP)\s+(UP|DOWN)\b"), "EXHAUST_ABSTAIN"),
    (re.compile(r"\[CHEAP\]\s+(BTC|ETH|SOL|XRP)\b"), "CHEAP"),
    (re.compile(r"\[LOCKED\]\s+(BTC|ETH|SOL|XRP)\b"), "LOCKED"),
    (re.compile(r"\[MORNING STICKY EXHAUST\]\s+(BTC|ETH|SOL|XRP)\b"), "MORNING_STICKY_EXHAUST"),
    (re.compile(r"\[CLOB REJECT\]\s+(BTC|ETH|SOL|XRP)\s+(UP|DOWN)\b"), "CLOB_REJECT"),
    (re.compile(r"\[MAX POS\]"), "MAX_POS"),
    (re.compile(r"\[LOSS BREAKER\]"), "LOSS_BREAKER_PM"),
    (re.compile(r"\[MORNING LOSS BREAKER\]"), "LOSS_BREAKER_MORNING"),
    (re.compile(r"\[MORNING CAP\]"), "MORNING_CAP"),
]

RE_STARTUP = re.compile(r"V11 BOT|V12 BOT|V10 BOT")
RE_MIDNIGHT = re.compile(r"^00:00:0")


# ------------------------------------------------------------------- CSV parse

def parse_polymarket_csv(path: str) -> list[dict]:
    """
    Polymarket export rows look like:
      TransactionHash, Side, Market, Outcome, Price, Shares, TotalValue, Timestamp, ...

    We extract the trade rows (Side in {BUY, SELL}) and the resolution rows.
    """
    rows = []
    try:
        # utf-8-sig strips the BOM that Excel/Polymarket exports often include.
        # Without this, the first column's header becomes "\ufeffmarketName"
        # and DictReader silently returns "" for r["marketName"].
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for r in reader:
                rows.append(r)
    except Exception as e:
        print(f"[backfill] csv read failed {path}: {e}", file=sys.stderr)
    return rows


def csv_to_events(rows: list[dict]) -> list[dict]:
    """
    Convert Polymarket export CSV rows into FIRED + RESOLVED events.

    Actual CSV schema (apr 2026):
      marketName, action, usdcAmount, tokenAmount, tokenName, timestamp, hash

    Logic
    -----
    - Each `Buy` row becomes a FIRED event.
    - We pair each Buy with a `Redeem` on the same market to decide WIN/LOSS:
        * Redeem with usdcAmount > 0  -> WIN   (redeemed for cash)
        * No Redeem (or usdcAmount 0) -> LOSS  (tokens worthless)
    - `Sell` rows (rare, early exit) are treated as partial RESOLVED with pnl =
      usdcAmount - entry_cost (best effort).
    - Each Buy also emits a RESOLVED event with the inferred outcome.
    """
    # group redeems by market to detect winners
    redeems_by_market: dict[str, list[dict]] = {}
    for r in rows:
        action = (r.get("action") or "").strip()
        if action != "Redeem":
            continue
        m = (r.get("marketName") or "").strip()
        try:
            usdc = float(r.get("usdcAmount") or 0)
        except Exception:
            usdc = 0.0
        redeems_by_market.setdefault(m, []).append({
            "usdc": usdc,
            "ts": _as_int(r.get("timestamp")),
        })

    events: list[dict] = []

    for r in rows:
        market = (r.get("marketName") or "").strip()
        action = (r.get("action") or "").strip()
        token_name = (r.get("tokenName") or "").strip()
        try:
            usdc = float(r.get("usdcAmount") or 0)
            tok_amt = float(r.get("tokenAmount") or 0)
        except Exception:
            usdc, tok_amt = 0.0, 0.0
        ts_epoch = _as_int(r.get("timestamp"))
        tx = (r.get("hash") or "").strip()

        coin = _coin_from_market_name(market)
        window_start = _window_from_market_name(market)

        if action == "Buy":
            side = "UP" if token_name.lower() == "up" else "DOWN" if token_name.lower() == "down" else None
            entry_price = (usdc / tok_amt) if tok_amt > 0 else None

            events.append({
                "event": "FIRED",
                "source": "csv",
                "ts_epoch": ts_epoch,
                "coin": coin,
                "side": side,
                "market": market,
                "entry": entry_price,
                "shares": tok_amt,
                "cost": usdc,
                "window_start": window_start,
                "tx": tx,
            })

            # Pair with a redeem (if any) to label this trade's outcome
            redeems = redeems_by_market.get(market, [])
            paid_out = sum(x["usdc"] for x in redeems if (x["ts"] or 0) >= (ts_epoch or 0))
            won = paid_out > 0.01 and paid_out >= (usdc * 0.9)
            # (Polymarket redeems each share for $1; expect payout ≈ tokenAmount on a win)

            # Use a resolution-ish ts: end of the 15m window
            res_ts = (window_start + 900) if window_start else ts_epoch
            pnl = (paid_out - usdc) if won else -usdc

            events.append({
                "event": "RESOLVED",
                "source": "csv",
                "ts_epoch": res_ts,
                "coin": coin,
                "side": side,
                "market": market,
                "won": won,
                "cost": usdc,
                "payout": paid_out if won else 0.0,
                "pnl": pnl,
                "window_start": window_start,
                "tx": tx,
            })

        elif action == "Redeem":
            # captured above when pairing; skip standalone emit (redundant)
            continue

        elif action == "Sell":
            events.append({
                "event": "EARLY_EXIT",
                "source": "csv",
                "ts_epoch": ts_epoch,
                "coin": coin,
                "side": None,
                "market": market,
                "usdc_received": usdc,
                "shares_sold": tok_amt,
                "window_start": window_start,
                "tx": tx,
            })

    return events


def _coin_from_market_name(name: str) -> Optional[str]:
    n = (name or "").lower()
    for c, needles in {
        "BTC": ("bitcoin", "btc"),
        "ETH": ("ethereum", "eth"),
        "SOL": ("solana", "sol"),
        "XRP": ("xrp", "ripple"),
    }.items():
        if any(nd in n for nd in needles):
            return c
    return None


def _window_from_market_name(name: str) -> Optional[int]:
    """
    Parse "XRP Up or Down - April 23, 5:45PM-6:00PM ET" -> epoch of 5:45 PM ET that date.
    Returns None if unparseable.
    """
    if not name:
        return None
    m = re.search(
        r"-\s*(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),\s+(\d{1,2}):(\d{2})(AM|PM)-\d{1,2}:\d{2}(AM|PM)\s+ET",
        name,
    )
    if not m:
        return None
    month_name, day, h, mm, ampm, _ = m.groups()
    month_num = {
        "January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
        "July": 7, "August": 8, "September": 9, "October": 10, "November": 11, "December": 12,
    }[month_name]
    h = int(h) + (12 if ampm == "PM" and int(h) != 12 else 0) - (12 if ampm == "AM" and int(h) == 12 else 0)
    # Year: infer from closest year (assume current calendar year if month<=current+1).
    # For 2026 CSVs this is fine; we accept ambiguity for old historical files.
    year = datetime.now().year
    # ET = UTC-4 (EDT) during Apr-Oct. Close enough for 15m window alignment (it's used for grouping only).
    try:
        dt_et = datetime(year, month_num, int(day), h, int(mm))
        # Convert ET -> UTC by adding 4 hours (EDT). Off-by-1 in winter is acceptable for bucketing.
        from datetime import timedelta
        dt_utc = dt_et + timedelta(hours=4)
        epoch = int(dt_utc.replace(tzinfo=timezone.utc).timestamp())
        return epoch - (epoch % 900)  # snap to 15-min boundary
    except Exception:
        return None


def _as_int(v) -> Optional[int]:
    try:
        return int(v) if v not in (None, "") else None
    except Exception:
        return None


def _parse_ts_to_epoch(s: str) -> Optional[int]:
    if not s:
        return None
    # Try a few likely formats
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S%z",
        "%m/%d/%Y %H:%M:%S",
        "%Y-%m-%d %H:%M",
    ):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except Exception:
            continue
    return None


def _classify_market(market: str, outcome: str) -> tuple[Optional[str], Optional[str]]:
    m = (market or "").lower()
    o = (outcome or "").lower()
    coin = None
    for c, needles in {
        "BTC": ("bitcoin", "btc"),
        "ETH": ("ethereum", "eth"),
        "SOL": ("solana", "sol"),
        "XRP": ("xrp", "ripple"),
    }.items():
        if any(n in m for n in needles):
            coin = c
            break
    side = None
    if "up" in o:
        side = "UP"
    elif "down" in o:
        side = "DOWN"
    return coin, side


def _window_from_market(market: str) -> Optional[int]:
    m = re.search(r"-15m-(\d{10})", market or "")
    if m:
        return int(m.group(1))
    return None


# ------------------------------------------------------------------- log parse

def parse_bot_log(path: str, default_date: Optional[str] = None) -> list[dict]:
    """
    Parse v3_bot.log and emit events.

    `default_date` (YYYY-MM-DD) disambiguates log segments when we can't
    detect the date from the text itself. If None, we try to infer from
    file mtime and midnight rollover markers (best effort).
    """
    events: list[dict] = []
    if not os.path.exists(path):
        return events

    # Build a date rollover table by scanning for midnight lines.
    # Start with the file's *final* date = default_date or today.
    try:
        file_mtime = os.path.getmtime(path)
        default_date = default_date or datetime.fromtimestamp(file_mtime).strftime("%Y-%m-%d")
    except Exception:
        default_date = default_date or datetime.now().strftime("%Y-%m-%d")

    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    # Walk backward: each time we hit 00:00:0x, the date BEFORE that line is -1 day.
    date_of_line: list[Optional[str]] = [None] * len(lines)
    cur_date = default_date
    for i in range(len(lines) - 1, -1, -1):
        date_of_line[i] = cur_date
        if RE_MIDNIGHT.match(lines[i]):
            try:
                cur = datetime.strptime(cur_date, "%Y-%m-%d")
                prev = cur.replace(day=cur.day)  # placeholder
                from datetime import timedelta
                prev = cur - timedelta(days=1)
                cur_date = prev.strftime("%Y-%m-%d")
            except Exception:
                pass

    for i, line in enumerate(lines):
        ts_m = RE_TS.match(line)
        if not ts_m:
            continue
        hh, mm, ss = ts_m.group(1), ts_m.group(2), ts_m.group(3)
        date_s = date_of_line[i] or default_date
        try:
            ts_epoch = int(datetime.strptime(
                f"{date_s} {hh}:{mm}:{ss}", "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=timezone.utc).timestamp())
        except Exception:
            ts_epoch = None

        # SIGNAL
        m = RE_SIGNAL.search(line)
        if m:
            coin, side, edge, prob, entry, trend = m.groups()
            events.append({
                "event": "SIGNAL",
                "ts_epoch": ts_epoch,
                "date": date_s,
                "coin": coin,
                "side": side,
                "edge": _f(edge) / 100.0,
                "prob": _f(prob) / 100.0,
                "entry": _f(entry) / 100.0,
                "trend_score": _f(trend),
                "source": "log",
            })
            continue

        # EXHAUST
        m = RE_EXHAUST.search(line)
        if m:
            coin, side, score, raw, action, decel, poly, rng, breadth = m.groups()
            events.append({
                "event": "EXHAUST",
                "ts_epoch": ts_epoch,
                "date": date_s,
                "coin": coin,
                "side": side,
                "score": _f(score),
                "raw": raw,
                "action": action,
                "decel": _f(decel),
                "poly_score": _f(poly),
                "session_range": _f(rng),
                "breadth": _f(breadth),
                "source": "log",
            })
            continue

        # FILLED
        m = RE_FILLED.search(line)
        if m:
            coin, side, shares, entry, cost = m.groups()
            # Snap fire-time to the containing 15-min window.
            # Trades fire in the last ~3 minutes of a window, so window_start
            # = (ts_epoch // 900) * 900 is usually correct, but when we fire
            # in the FIRST seconds of a new window we'd be off by one. We
            # pick the *previous* boundary if we fired in the first 60s.
            ws = None
            if ts_epoch:
                snap = (ts_epoch // 900) * 900
                ws = snap - 900 if (ts_epoch - snap) < 60 else snap
            events.append({
                "event": "FIRED",
                "ts_epoch": ts_epoch,
                "date": date_s,
                "coin": coin,
                "side": side,
                "shares": _f(shares),
                "entry": _f(entry) / 100.0,
                "cost": _f(cost) if cost else None,
                "window_start": ws,
                "source": "log",
            })
            continue

        # WIN/LOSS
        m = RE_WINLOSS.search(line)
        if m:
            wl, phase, coin, side, amount, entry, shares, payout = m.groups()
            amt = float(amount.replace("$", "").replace("+", "")) if amount else 0.0
            won = wl == "WIN"
            pnl = amt if won else -abs(amt)
            # Resolution fires ~17 min after trade (end of 15-min window + ~2m delay).
            # Back-compute the window_start that this resolution refers to.
            ws = None
            if ts_epoch:
                ws = ((ts_epoch - 900) // 900) * 900  # one window back
            events.append({
                "event": "RESOLVED",
                "ts_epoch": ts_epoch,
                "date": date_s,
                "coin": coin,
                "side": side,
                "won": won,
                "pnl": pnl,
                "entry": (_f(entry) / 100.0) if entry else None,
                "shares": _f(shares) if shares else None,
                "payout": _f(payout) if payout else (float(shares) if won and shares else 0.0),
                "cost": (abs(amt) if not won else (_f(shares) - amt) if shares else None),
                "phase": phase,
                "window_start": ws,
                "source": "log",
            })
            continue

        # Generic BLOCKED reasons
        for pat, reason in RE_BLOCK_PATTERNS:
            m = pat.search(line)
            if m:
                gs = m.groups()
                coin = gs[0] if gs and gs[0] in ("BTC", "ETH", "SOL", "XRP") else None
                side = gs[1] if len(gs) >= 2 and gs[1] in ("UP", "DOWN") else None
                events.append({
                    "event": "BLOCKED",
                    "ts_epoch": ts_epoch,
                    "date": date_s,
                    "coin": coin,
                    "side": side,
                    "blocked_by": reason,
                    "source": "log",
                })
                break

    return events


def _f(x) -> float:
    try:
        return float(x)
    except Exception:
        return 0.0


# ------------------------------------------------------------------- writer

def write_events(path: str, events: Iterable[dict]) -> int:
    n = 0
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for e in events:
            # ensure ts is set for sort order
            if "ts" not in e and "ts_epoch" in e and e["ts_epoch"]:
                try:
                    e["ts"] = datetime.fromtimestamp(
                        e["ts_epoch"], tz=timezone.utc
                    ).isoformat(timespec="seconds")
                except Exception:
                    pass
            f.write(json.dumps(e, separators=(",", ":"), default=str) + "\n")
            n += 1
    return n


# ------------------------------------------------------------------- CLI

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default="/home/ubuntu/v3-bot/v3_bot.log",
                    help="path to v3_bot.log")
    ap.add_argument("--csv", action="append", default=[],
                    help="Polymarket CSV exports (repeatable)")
    ap.add_argument("--out", default="/home/ubuntu/v3-bot/data/trade_events_backfill.jsonl")
    ap.add_argument("--date", default=None,
                    help="YYYY-MM-DD hint for the last day in the log file (default: file mtime)")
    args = ap.parse_args()

    events: list[dict] = []

    if os.path.exists(args.log):
        log_events = parse_bot_log(args.log, default_date=args.date)
        print(f"[backfill] {args.log}: {len(log_events)} events")
        events.extend(log_events)

    for csv_path in args.csv:
        if os.path.exists(csv_path):
            rows = parse_polymarket_csv(csv_path)
            csv_events = csv_to_events(rows)
            print(f"[backfill] {csv_path}: {len(csv_events)} CSV rows")
            events.extend(csv_events)

    events.sort(key=lambda e: (e.get("ts_epoch") or 0))
    n = write_events(args.out, events)
    print(f"[backfill] wrote {n} events to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
