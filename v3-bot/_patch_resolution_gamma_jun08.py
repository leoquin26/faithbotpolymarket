#!/usr/bin/env python3
"""Fix resolution: never default to LOSS when Gamma unresolved; reconcile Jun 8 PNL."""
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path("/home/ubuntu/v3-bot")
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")


def backup(p: Path):
    if p.exists():
        shutil.copy2(p, p.with_suffix(p.suffix + f".bak_{STAMP}"))


OLD_RESOLVE = '''def resolve_expired_positions(orders, predictor, binance_ws_module):
    """Resolve open positions using Polymarket Gamma outcome, else Chainlink vs strike."""
    current_time = int(time.time())
    for coin in list(orders.positions.keys()):
        pos = orders.positions.get(coin)
        if not pos:
            continue
        ws = pos.get("window_start", 0)
        if ws <= 0 or current_time <= ws + 900 + 60:
            continue
        pos = orders.remove_position(coin)
        if not pos:
            continue
        side = pos.get("side", "?")
        entry = pos.get("entry_price", 0)
        shares = pos.get("shares", 0)
        cost = entry * shares
        payout = shares * 1.0
        won = False
        resolve_src = "unknown"
        try:
            import poly_resolution as _pr
            tf = pos.get("timeframe", "15m")
            gamma = _pr.resolve_position(coin, side, ws, tf)
            if gamma and gamma.get("winner"):
                winner = gamma["winner"]
                won = side == winner
                resolve_src = f"gamma:{gamma.get('slug', '')}"
                logger.info(
                    f"[RESOLVE] {coin} {side} | gamma winner={winner} | "
                    f"{'WIN' if won else 'LOSS'}"
                )
        except Exception as _ge:
            logger.debug(f"[RESOLVE] gamma failed {coin}: {_ge}")
        if resolve_src == "unknown":
            try:
                final_price = None
                try:
                    import chainlink_ws as _cl
                    final_price = _cl.get_price(coin)
                    if final_price:
                        resolve_src = "chainlink_end"
                except Exception:
                    pass
                if not final_price:
                    final_price = binance_ws_module.get_price(coin)
                    resolve_src = "binance_end"
                strike = pos.get("strike", 0)
                if strike > 0 and final_price and final_price > 0:
                    went_up = final_price >= strike
                    won = (side == "UP" and went_up) or (side == "DOWN" and not went_up)
                    logger.info(
                        f"[RESOLVE] {coin} {side} | {resolve_src} end=${final_price:,.2f} "
                        f"strike=${strike:,.2f} ({'>=' if went_up else '<'}) | "
                        f"{'WIN' if won else 'LOSS'}"
                    )
            except Exception as _e:
                logger.debug(f"[RESOLVE] fallback failed {coin}: {_e}")
        if won:
            pnl = payout - cost
            orders.record_win_pnl(pnl)
            logger.info(
                f"[WIN] {coin} {side} | +${pnl:.2f} | Entry: {entry*100:.0f}c x{shares} | "
                f"Payout: ${payout:.2f} (resolved on startup)"
            )
            predictor.record_outcome(True)
            tg.notify_result(coin, side, True, cost, payout)
        else:
            orders.record_loss_pnl(cost)
            logger.info(
                f"[LOSS] {coin} {side} | -${cost:.2f} | Entry: {entry*100:.0f}c x{shares} | "
                f"day_loss=${orders.daily_losses:.2f} (resolved on startup)"
            )
            predictor.record_outcome(False)
            tg.notify_result(coin, side, False, cost)
            if orders.is_daily_stop_loss_hit():
                logger.warning(
                    f"[DAILY STOP] Loss limit hit (${orders.daily_losses:.2f}) — no new trades today"
                )'''

NEW_RESOLVE = '''def _resolve_one_position(pos: dict, binance_ws_module) -> tuple:
    """Return (resolved: bool, won: bool|None, resolve_src: str, detail: str)."""
    coin = pos.get("coin", "?")
    side = pos.get("side", "?")
    ws = pos.get("window_start", 0)
    tf = pos.get("timeframe", "15m")
    try:
        import poly_resolution as _pr
        slug = _pr.market_slug(coin, ws, tf)
        for attempt in range(3):
            gamma = _pr.resolve_position(coin, side, ws, tf)
            if gamma and gamma.get("winner"):
                winner = gamma["winner"]
                won = side == winner
                return True, won, f"gamma:{slug}", f"gamma winner={winner}"
            if attempt < 2:
                import time as _t
                _t.sleep(1.5)
        market = _pr.fetch_market_by_slug(slug)
        if market and not market.get("closed"):
            return False, None, "pending", f"gamma market not closed yet ({slug})"
    except Exception as _ge:
        logger.debug(f"[RESOLVE] gamma failed {coin}: {_ge}")

    try:
        final_price = None
        src = "unknown"
        try:
            import chainlink_ws as _cl
            final_price = _cl.get_price(coin)
            if final_price:
                src = "chainlink_live"
        except Exception:
            pass
        if not final_price:
            final_price = binance_ws_module.get_price(coin)
            src = "binance_live"
        strike = pos.get("strike", 0)
        if strike > 0 and final_price and final_price > 0:
            went_up = final_price >= strike
            won = (side == "UP" and went_up) or (side == "DOWN" and not went_up)
            detail = (
                f"{src} price=${final_price:,.2f} strike=${strike:,.2f} "
                f"({'>=' if went_up else '<'})"
            )
            logger.warning(
                f"[RESOLVE FALLBACK] {coin} {side} | {detail} — gamma unavailable, "
                f"using live price (may disagree with Polymarket)"
            )
            return True, won, src, detail
    except Exception as _e:
        logger.debug(f"[RESOLVE] fallback failed {coin}: {_e}")

    return False, None, "unknown", "no gamma outcome and no price fallback"


def resolve_expired_positions(orders, predictor, binance_ws_module):
    """Resolve open positions — Polymarket Gamma first; never guess LOSS if unresolved."""
    current_time = int(time.time())
    for coin in list(orders.positions.keys()):
        pos = orders.positions.get(coin)
        if not pos:
            continue
        ws = pos.get("window_start", 0)
        if ws <= 0 or current_time <= ws + 900 + 60:
            continue

        resolved, won, resolve_src, detail = _resolve_one_position(pos, binance_ws_module)
        if not resolved or won is None:
            logger.info(
                f"[RESOLVE PENDING] {coin} {pos.get('side', '?')} | {detail} — keeping position"
            )
            continue

        pos = orders.remove_position(coin)
        if not pos:
            continue

        side = pos.get("side", "?")
        entry = pos.get("entry_price", 0)
        shares = pos.get("shares", 0)
        cost = entry * shares
        payout = shares * 1.0

        logger.info(
            f"[RESOLVE] {coin} {side} | {resolve_src} {detail} | "
            f"{'WIN' if won else 'LOSS'}"
        )

        if won:
            pnl = payout - cost
            orders.record_win_pnl(pnl)
            logger.info(
                f"[WIN] {coin} {side} | +${pnl:.2f} | Entry: {entry*100:.0f}c x{shares} | "
                f"Payout: ${payout:.2f}"
            )
            predictor.record_outcome(True)
            tg.notify_result(coin, side, True, cost, payout)
        else:
            orders.record_loss_pnl(cost)
            logger.info(
                f"[LOSS] {coin} {side} | -${cost:.2f} | Entry: {entry*100:.0f}c x{shares} | "
                f"day_loss=${orders.daily_losses:.2f}"
            )
            predictor.record_outcome(False)
            tg.notify_result(coin, side, False, cost)
            if orders.is_daily_stop_loss_hit():
                logger.warning(
                    f"[DAILY STOP] Loss limit hit (${orders.daily_losses:.2f}) — no new trades today"
                )'''


def patch_run_bot():
    p = ROOT / "run_bot.py"
    backup(p)
    text = p.read_text(encoding="utf-8")
    if OLD_RESOLVE not in text:
        if "_resolve_one_position" in text:
            print("run_bot resolution already patched")
            return
        raise SystemExit("resolve block not found")
    text = text.replace(OLD_RESOLVE, NEW_RESOLVE)
    p.write_text(text, encoding="utf-8")
    print("patched run_bot.py resolution")


def reconcile_daily_pnl():
    """Fix Jun 8: SOL 12:45 DOWN was wrongly counted as loss."""
    f = ROOT / "data" / "daily_pnl.json"
    backup(f)
    data = json.loads(f.read_text(encoding="utf-8"))
    if data.get("date") != "2026-06-08":
        print(f"skip reconcile: date is {data.get('date')}")
        return
    # Was: losses=6.20 (BTC + wrongful SOL), wins=4.35
    # Correct: losses=3.10 (BTC only), wins=6.25 (+$1.90 SOL 12:45 DOWN redeem)
    if float(data.get("losses", 0)) >= 6.0:
        data["losses"] = 3.10
        data["wins"] = 6.25
    f.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"reconciled daily_pnl.json: {data}")


def main():
    patch_run_bot()
    reconcile_daily_pnl()
    subprocess.run([sys.executable, "-m", "py_compile", str(ROOT / "run_bot.py")], check=True)
    print("OK")


if __name__ == "__main__":
    main()
