"""
Jun 2 PM — fix missing 45c ETH DOWN at 14:30:22.

14:30:22 log: [CHEAP] ETH DOWN: ask=45c < 55c
  Bot was FAST (22s into window). Killed by ENTRY_MIN=0.55 before SIGNAL.
  At 14:30:42: LOW PROB 67% (would need 72% or compound bypass)
  At 14:31:11: already 74c EXPENSIVE

FIXES:
1. Early window: EARLY_ENTRY_MIN=0.40 for first EARLY_ENTRY_SEC=120s
2. Compound cheap bypass BEFORE hard entry_min reject (ask<=52 + prob>=58)
3. Early window: EARLY_MIN_WIN_PROB=0.65 for first 120s
4. Batch POLY-WS subscribe once per scan (all 6 tokens) — in order_manager or run_bot
"""
from pathlib import Path

PRED = Path("/home/ubuntu/v3-bot/predictor.py")
ENV = Path("/home/ubuntu/v3-bot/.env")
OM = Path("/home/ubuntu/v3-bot/order_manager.py")
RB = Path("/home/ubuntu/v3-bot/run_bot.py")


def patch_predictor():
    text = PRED.read_text()

    old = """        # Entry price filters
        entry_min = getattr(config, "ENTRY_MIN", 0.10)
        entry_max = getattr(config, "ENTRY_MAX", 0.75)

        if ask <= 0.01:
            self._diag_log(f"noask-{coin}-{direction}", f"[NO ASK] {coin} {direction}: ask=0", 30.0)
            return None

        if ask < entry_min:
            self._diag_log(
                f"cheap-{coin}-{direction}",
                f"[CHEAP] {coin} {direction}: ask={ask*100:.0f}c < {entry_min*100:.0f}c", 30.0,
            )
            return None

        if ask > entry_max:
            self._diag_log(
                f"exp-{coin}-{direction}",
                f"[EXPENSIVE] {coin} {direction}: ask={ask*100:.0f}c > {entry_max*100:.0f}c", 30.0,
            )
            return None

        # Edge = our probability minus cost
        edge = win_prob - ask
        min_edge = getattr(config, "MIN_EDGE", 0.05)

        min_prob = getattr(config, "MIN_WIN_PROB", 0.65)
        _cheap_ask = float(os.getenv("COMPOUND_CHEAP_ASK", "0.52"))
        _cheap_prob = float(os.getenv("COMPOUND_MIN_PROB", "0.58"))
        if win_prob < min_prob:
            if ask <= _cheap_ask and win_prob >= _cheap_prob:
                logger.debug(
                    f"[COMPOUND CHEAP] {coin} {direction}: prob={win_prob*100:.0f}% "
                    f"ask={ask*100:.0f}c — cheap-entry bypass"
                )
            else:
                self._diag_log(f"lowprob-{coin}", f"[LOW PROB] {coin} {direction}: prob={win_prob*100:.0f}% < {min_prob*100:.0f}%", 15.0)
                return None"""

    new = """        # Entry price filters
        entry_min = getattr(config, "ENTRY_MIN", 0.10)
        entry_max = getattr(config, "ENTRY_MAX", 0.75)
        _early_entry_sec = int(os.getenv("EARLY_ENTRY_SEC", "120"))
        _early_entry_min = float(os.getenv("EARLY_ENTRY_MIN", "0.40"))
        if window_age < _early_entry_sec:
            entry_min = min(entry_min, _early_entry_min)

        _cheap_ask = float(os.getenv("COMPOUND_CHEAP_ASK", "0.52"))
        _cheap_prob = float(os.getenv("COMPOUND_MIN_PROB", "0.58"))
        _compound_cheap_ok = ask <= _cheap_ask and win_prob >= _cheap_prob

        if ask <= 0.01:
            self._diag_log(f"noask-{coin}-{direction}", f"[NO ASK] {coin} {direction}: ask=0", 30.0)
            return None

        if ask < entry_min and not _compound_cheap_ok:
            self._diag_log(
                f"cheap-{coin}-{direction}",
                f"[CHEAP] {coin} {direction}: ask={ask*100:.0f}c < {entry_min*100:.0f}c", 30.0,
            )
            return None

        if ask < entry_min and _compound_cheap_ok:
            logger.info(
                f"[EARLY CHEAP EDGE] {coin} {direction}: ask={ask*100:.0f}c "
                f"prob={win_prob*100:.0f}% edge={(win_prob-ask)*100:.1f}% "
                f"(compound/early floor, age={window_age}s)"
            )

        if ask > entry_max:
            self._diag_log(
                f"exp-{coin}-{direction}",
                f"[EXPENSIVE] {coin} {direction}: ask={ask*100:.0f}c > {entry_max*100:.0f}c", 30.0,
            )
            return None

        # Edge = our probability minus cost
        edge = win_prob - ask
        min_edge = getattr(config, "MIN_EDGE", 0.05)

        min_prob = getattr(config, "MIN_WIN_PROB", 0.65)
        if window_age < _early_entry_sec:
            min_prob = min(min_prob, float(os.getenv("EARLY_MIN_WIN_PROB", "0.65")))
        if win_prob < min_prob:
            if _compound_cheap_ok:
                logger.info(
                    f"[COMPOUND CHEAP] {coin} {direction}: prob={win_prob*100:.0f}% "
                    f"ask={ask*100:.0f}c edge={edge*100:.1f}% — bypass min prob"
                )
            else:
                self._diag_log(f"lowprob-{coin}", f"[LOW PROB] {coin} {direction}: prob={win_prob*100:.0f}% < {min_prob*100:.0f}%", 15.0)
                return None"""

    if "EARLY CHEAP EDGE" in text:
        print("[SKIP] predictor early cheap already patched")
        return True
    if old not in text:
        print("[FAIL] entry filter block not found")
        return False
    PRED.write_text(text.replace(old, new, 1))
    print("[OK] predictor: early entry floor + compound cheap before reject")
    return True


def patch_batch_subscribe():
    """Subscribe all window tokens once at start of scan_coin batch."""
    text = RB.read_text()
    marker = "            futures_map = {executor.submit(scan_coin, c): c for c in config.SYMBOLS}"
    if "batch_subscribe_window_tokens" in text:
        print("[SKIP] batch subscribe already in run_bot")
        return
    insert = """            # Jun-2 PM: subscribe all UP/DOWN tokens once per scan (not per get_clob_book).
            try:
                import polymarket_ws as _pws_mod
                _batch_ids = []
                for _c in config.SYMBOLS:
                    _inf = get_market_info(_c)
                    if _inf:
                        _batch_ids.extend([_inf.up_token_id, _inf.down_token_id])
                if _batch_ids:
                    _pws_mod.subscribe(_batch_ids)
            except Exception:
                pass

"""
    if marker not in text:
        print("[FAIL] futures_map marker not found in run_bot")
        return
    RB.write_text(text.replace(marker, insert + marker, 1))
    print("[OK] run_bot: batch POLY-WS subscribe per scan")


def patch_env():
    import re
    text = ENV.read_text()
    adds = {
        "EARLY_ENTRY_SEC": "120",
        "EARLY_ENTRY_MIN": "0.40",
        "EARLY_MIN_WIN_PROB": "0.65",
    }
    for k, v in adds.items():
        if re.search(rf"^{k}=", text, re.M):
            text = re.sub(rf"^{k}=.*$", f"{k}={v}", text, flags=re.M)
        else:
            text = text.rstrip() + f"\n{k}={v}\n"
        print(f"[OK] {k}={v}")
    ENV.write_text(text)


if __name__ == "__main__":
    if patch_predictor():
        patch_batch_subscribe()
        patch_env()
