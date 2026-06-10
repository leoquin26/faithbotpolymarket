"""Patch order_manager.py with depth-aware sizing + FOK retry throttle.

Applied surgically via string replacement; verifies each substitution
hits exactly 1 occurrence.
"""
from pathlib import Path
import sys

PATH = Path("/home/ubuntu/v3-bot/order_manager.py")
src = PATH.read_text()


def replace_once(haystack: str, needle: str, repl: str, label: str) -> str:
    n = haystack.count(needle)
    if n != 1:
        sys.exit(f"[FAIL] {label}: expected 1 match, got {n}")
    print(f"[OK] {label}")
    return haystack.replace(needle, repl, 1)


# 1) __init__: add throttle dict
src = replace_once(
    src,
    """        self.active_gtc: Dict[str, dict] = {}
        self.traded_windows: Dict[str, str] = self._load_traded_windows()
        self.positions: Dict[str, dict] = {}""",
    """        self.active_gtc: Dict[str, dict] = {}
        self.traded_windows: Dict[str, str] = self._load_traded_windows()
        self.positions: Dict[str, dict] = {}
        # [FILL-RATE 2026-05-08] Per-(coin, direction, window) cooldown after FOK kills
        # to stop the 5-second retry storms that produced 28 errors on May 6.
        # Records UNIX ts of last failure; place_bet skips for FOK_RETRY_COOLDOWN_SEC.
        self._fok_throttle: Dict[str, float] = {}""",
    "init: throttle dict",
)

# 2) place_bet: insert throttle check right after is_window_traded
src = replace_once(
    src,
    """        if self.is_window_traded(coin, window_start):
            logger.warning(f"[SKIP] Already traded {coin} in this window")
            return False""",
    """        if self.is_window_traded(coin, window_start):
            logger.warning(f"[SKIP] Already traded {coin} in this window")
            return False

        # [FILL-RATE 2026-05-08] Throttle: if we just FOK-killed on this exact
        # (coin, direction, window) within COOLDOWN seconds, skip. Stops the
        # 5s retry spam (XRP did 6 attempts in 24s on 2026-05-06 13:17).
        import os as _os, time as _time
        _throttle_key = f"{coin}|{direction}|{window_start}"
        _cooldown = float(_os.getenv("FOK_RETRY_COOLDOWN_SEC", "30"))
        _last_fail = self._fok_throttle.get(_throttle_key, 0)
        if _last_fail and (_time.time() - _last_fail) < _cooldown:
            _age = _time.time() - _last_fail
            logger.info(
                f"[FOK THROTTLE] {coin} {direction}: last fail {_age:.0f}s ago "
                f"(<{_cooldown:.0f}s cooldown) — skip"
            )
            return False""",
    "place_bet: throttle check",
)

# 3) place_bet: depth-aware sizing right before [ORDER] log
src = replace_once(
    src,
    """        shares = max(2, int(size_usd / limit_price))
        actual_cost = shares * limit_price

        order_type = OrderType.GTC if use_gtc else OrderType.FOK
        order_type_name = "GTC" if use_gtc else "FOK"

        logger.info(
            f"[ORDER] {coin} {direction} | {order_type_name} @ {limit_price*100:.0f}c | "
            f"{shares} shares (cost=${actual_cost:.2f}, sized=${size_usd:.2f}) | "
            f"Edge {real_edge*100:.1f}%"
        )""",
    """        shares = max(2, int(size_usd / limit_price))

        # [FILL-RATE 2026-05-08] Depth-aware sizing: shrink shares to what
        # the orderbook actually has at-or-below our limit_price. FOK kills on
        # XRP/SOL were caused by displayed best ask being only 1-3 shares deep,
        # while we tried to fill 10-15. Prefer a smaller fill over zero fill.
        # GTC orders skip this (they wait for liquidity to come).
        if not use_gtc:
            try:
                _depth = self.get_full_depth(token_id)
                _avail = sum(s for p, s in _depth.get("asks", []) if p <= limit_price)
                _avail = int(_avail)
                _min_shares = int(_os.getenv("MIN_FOK_SHARES", "2"))
                if _avail < _min_shares:
                    logger.info(
                        f"[DEPTH SKIP] {coin} {direction}: only {_avail} shares "
                        f"available <= {limit_price*100:.0f}c (min={_min_shares}) — skip"
                    )
                    self._fok_throttle[_throttle_key] = _time.time()
                    return False
                if _avail < shares:
                    logger.info(
                        f"[DEPTH SHRINK] {coin} {direction}: book has {_avail} shares "
                        f"<= {limit_price*100:.0f}c (wanted {shares}) — shrinking"
                    )
                    shares = _avail
            except Exception as _e:
                logger.debug(f"[DEPTH] check failed: {_e} — proceeding with {shares}")

        actual_cost = shares * limit_price

        order_type = OrderType.GTC if use_gtc else OrderType.FOK
        order_type_name = "GTC" if use_gtc else "FOK"

        logger.info(
            f"[ORDER] {coin} {direction} | {order_type_name} @ {limit_price*100:.0f}c | "
            f"{shares} shares (cost=${actual_cost:.2f}, sized=${size_usd:.2f}) | "
            f"Edge {real_edge*100:.1f}%"
        )""",
    "place_bet: depth-aware sizing",
)

# 4) place_bet exception path: record throttle timestamp
src = replace_once(
    src,
    """        except Exception as e:
            import traceback as _tb; logger.error(f"[ERROR] Order failed for {coin}: {type(e).__name__}: {e}"); logger.error(f"[ERROR TRACE] {_tb.format_exc()}")
            tg.notify_error(f"Order failed: {coin} {direction}\\n{str(e)[:100]}")
            print(f"\\n  [ERROR] {coin} order failed: {e}")
            return False""",
    """        except Exception as e:
            import traceback as _tb; logger.error(f"[ERROR] Order failed for {coin}: {type(e).__name__}: {e}"); logger.error(f"[ERROR TRACE] {_tb.format_exc()}")
            # [FILL-RATE 2026-05-08] Mark cooldown so we don't immediately retry
            # the same (coin, direction, window) on the next engine tick.
            try:
                self._fok_throttle[_throttle_key] = _time.time()
            except Exception:
                pass
            tg.notify_error(f"Order failed: {coin} {direction}\\n{str(e)[:100]}")
            print(f"\\n  [ERROR] {coin} order failed: {e}")
            return False""",
    "place_bet: exception throttle update",
)

# 5) Same throttle update on [MISS] path (0-share match returns False)
src = replace_once(
    src,
    """            else:
                logger.warning(f"[MISS] {coin} {direction} — 0 shares matched")
                print(f"\\n  [X] MISSED: {coin} {direction} — order not filled")
                return False""",
    """            else:
                logger.warning(f"[MISS] {coin} {direction} — 0 shares matched")
                # [FILL-RATE 2026-05-08] Mark cooldown on MISS path too.
                try:
                    self._fok_throttle[_throttle_key] = _time.time()
                except Exception:
                    pass
                print(f"\\n  [X] MISSED: {coin} {direction} — order not filled")
                return False""",
    "place_bet: MISS throttle update",
)

PATH.write_text(src)
print("[DONE] order_manager.py patched")
