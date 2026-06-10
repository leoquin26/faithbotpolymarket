"""
Patch run_bot.py so the per-coin per-window lock survives bot restarts.

Root cause of today's 2 PM losses:
 - _traded_set is an in-memory Python set.
 - Every bot restart wipes it.
 - Fresh bot re-fires contradictory trades on coins already traded this window.

Fix:
 1. Persist _traded_set to /home/ubuntu/v3-bot/traded_windows.json on every add.
 2. On startup, reload from disk + from CLOB open positions + from today's log.
"""
import re
import sys
from pathlib import Path

TARGET = Path("/home/ubuntu/v3-bot/run_bot.py")
src = TARGET.read_text()

# ── 1. Replace the lock block with persistent version ──
OLD_BLOCK = '''# ======================================================================
# FIX 1: Atomic one-trade-per-window lock
# ======================================================================
_trade_lock = threading.Lock()
_traded_set: set = set()


def is_window_locked(coin: str, window_start: int) -> bool:
    key = f"{coin}_{window_start}"
    with _trade_lock:
        return key in _traded_set


def lock_window(coin: str, window_start: int) -> bool:
    """Try to lock this coin+window for trading. Returns True if we got the lock."""
    key = f"{coin}_{window_start}"
    with _trade_lock:
        if key in _traded_set:
            return False
        _traded_set.add(key)
        return True


def unlock_window(coin: str, window_start: int):
    """Release a window lock (e.g. when FOK order fails)."""
    key = f"{coin}_{window_start}"
    with _trade_lock:
        _traded_set.discard(key)


def cleanup_old_windows():
    """Remove window locks older than 20 minutes to prevent memory leak."""
    now = int(time.time())
    with _trade_lock:
        stale = [k for k in _traded_set if int(k.split("_")[-1]) < now - 1200]
        for k in stale:
            _traded_set.discard(k)'''

NEW_BLOCK = '''# ======================================================================
# FIX 1: Atomic one-trade-per-window lock  (persistent across restarts)
# ======================================================================
import json as _json_lock
_trade_lock = threading.Lock()
_traded_set: set = set()
_TRADED_SET_PATH = "/home/ubuntu/v3-bot/traded_windows.json"


def _persist_traded_set_unlocked():
    """Write the current _traded_set to disk. Caller holds _trade_lock."""
    try:
        with open(_TRADED_SET_PATH, "w") as f:
            _json_lock.dump(sorted(_traded_set), f)
    except Exception as e:
        try:
            import logging as _lg
            _lg.getLogger(__name__).warning(f"[TRADED SET] persist failed: {e}")
        except Exception:
            pass


def is_window_locked(coin: str, window_start: int) -> bool:
    key = f"{coin}_{window_start}"
    with _trade_lock:
        return key in _traded_set


def lock_window(coin: str, window_start: int) -> bool:
    """Try to lock this coin+window for trading. Returns True if we got the lock."""
    key = f"{coin}_{window_start}"
    with _trade_lock:
        if key in _traded_set:
            return False
        _traded_set.add(key)
        _persist_traded_set_unlocked()
        return True


def unlock_window(coin: str, window_start: int):
    """Release a window lock (e.g. when FOK order fails)."""
    key = f"{coin}_{window_start}"
    with _trade_lock:
        _traded_set.discard(key)
        _persist_traded_set_unlocked()


def cleanup_old_windows():
    """Remove window locks older than 20 minutes to prevent memory leak."""
    now = int(time.time())
    with _trade_lock:
        stale = [k for k in _traded_set if int(k.split("_")[-1]) < now - 1200]
        for k in stale:
            _traded_set.discard(k)
        if stale:
            _persist_traded_set_unlocked()


def bootstrap_traded_set():
    """
    Rehydrate _traded_set on startup from three sources (any is enough):
      1. /home/ubuntu/v3-bot/traded_windows.json (previous process's state)
      2. CLOB open positions (proxyWallet positions with slug containing a ts)
      3. Today's [FILLED] log lines

    Only windows within the last 20 minutes (still live) are loaded.
    """
    import logging as _lg
    _log = _lg.getLogger(__name__)
    now = int(time.time())
    cutoff = now - 1200  # 20 min

    loaded = set()

    # ---- 1. disk ----
    try:
        import os as _os
        if _os.path.exists(_TRADED_SET_PATH):
            with open(_TRADED_SET_PATH) as f:
                keys = _json_lock.load(f) or []
            for k in keys:
                try:
                    ts = int(str(k).split("_")[-1])
                except Exception:
                    continue
                if ts >= cutoff:
                    loaded.add(k)
    except Exception as e:
        _log.warning(f"[TRADED SET] disk load failed: {e}")

    # ---- 2. CLOB open positions ----
    try:
        import requests as _rq
        addr = _os.getenv("POLYMARKET_FUNDER_ADDRESS") or _os.getenv("POLY_ADDRESS") or ""
        if addr:
            r = _rq.get(
                f"https://data-api.polymarket.com/positions?user={addr}&sizeThreshold=0.1",
                timeout=10,
            )
            if r.ok:
                for p in r.json() or []:
                    slug = (p.get("slug") or "")
                    m = re.search(r"-15m-(\d{10})$", slug)
                    if not m:
                        continue
                    ws = int(m.group(1))
                    if ws < cutoff:
                        continue
                    title = (p.get("title") or "").lower()
                    coin = None
                    for c, needles in {
                        "BTC": ("bitcoin", "btc"),
                        "ETH": ("ethereum", "eth"),
                        "SOL": ("solana", "sol"),
                        "XRP": ("xrp",),
                    }.items():
                        if any(n in title for n in needles):
                            coin = c
                            break
                    if coin:
                        loaded.add(f"{coin}_{ws}")
    except Exception as e:
        _log.warning(f"[TRADED SET] CLOB load failed: {e}")

    # ---- 3. today's fill log ----
    try:
        import os as _os
        logpath = "/home/ubuntu/v3-bot/v3_bot.log"
        if _os.path.exists(logpath):
            today_prefix = datetime.now().strftime("%Y-%m-%d")
            # Only scan last ~500 KB; fills happen rarely
            with open(logpath, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 500_000))
                tail = f.read().decode(errors="ignore")
            # Parse lines like: "14:52:15 | INFO | [FILLED] BTC DOWN | ..."
            # We need the epoch; use today + HH:MM:SS
            for line in tail.splitlines():
                m = re.match(r"^(\d{2}):(\d{2}):(\d{2}).*\[FILLED\]\s+(BTC|ETH|SOL|XRP)\s+(UP|DOWN)", line)
                if not m:
                    continue
                hh, mm, ss, coin, _dir = m.groups()
                try:
                    from datetime import datetime as _dt, timezone as _tz
                    today = _dt.now().date()
                    lt = _dt(today.year, today.month, today.day, int(hh), int(mm), int(ss))
                    # bot logs in server local time; approximate ws from this
                    epoch = int(lt.timestamp())
                except Exception:
                    continue
                # round down to the 15-min window
                ws = epoch - (epoch % 900)
                if ws >= cutoff:
                    loaded.add(f"{coin}_{ws}")
    except Exception as e:
        _log.warning(f"[TRADED SET] log load failed: {e}")

    with _trade_lock:
        _traded_set.update(loaded)
        _persist_traded_set_unlocked()

    if loaded:
        _log.info(
            f"[TRADED SET] bootstrapped {len(loaded)} active window locks: "
            + ", ".join(sorted(loaded))
        )
    else:
        _log.info("[TRADED SET] bootstrap found no live windows to restore")'''

if OLD_BLOCK not in src:
    print("ERROR: could not locate lock block verbatim. Aborting.")
    sys.exit(1)

src = src.replace(OLD_BLOCK, NEW_BLOCK, 1)

# ── 2. Call bootstrap_traded_set() right after predictor = Predictor() ──
ANCHOR = "    predictor = Predictor()\n    morning_pred = MorningPredictor(predictor)"
INJECT = (
    "    predictor = Predictor()\n"
    "    morning_pred = MorningPredictor(predictor)\n"
    "    bootstrap_traded_set()"
)
if ANCHOR not in src:
    print("ERROR: could not locate Predictor() anchor.")
    sys.exit(1)
src = src.replace(ANCHOR, INJECT, 1)

TARGET.write_text(src)
print("OK: run_bot.py patched.")
