#!/usr/bin/env python3
"""Tier-0 fixes from Jun 3 audit: daily stop, Kelly cap, vol/edge gates."""
import shutil
import time
from pathlib import Path

ROOT = Path("/home/ubuntu/v3-bot")
STAMP = time.strftime("%Y%m%d_%H%M%S")


def patch_run_bot():
    p = ROOT / "run_bot.py"
    shutil.copy(p, p.with_suffix(p.suffix + f".bak_audit_{STAMP}"))
    text = p.read_text(encoding="utf-8")
    old = """                if won:
                    pnl = payout - cost
                    logger.info(f"[WIN] {coin} {side} | +${pnl:.2f} | Entry: {entry*100:.0f}c x{shares} | Payout: ${payout:.2f}")
                    predictor.record_outcome(True)
                    tg.notify_result(coin, side, True, cost, payout)
                else:
                    logger.info(f"[LOSS] {coin} {side} | -${cost:.2f} | Entry: {entry*100:.0f}c x{shares}")
                    predictor.record_outcome(False)
                    tg.notify_result(coin, side, False, cost)"""
    new = """                if won:
                    pnl = payout - cost
                    orders.daily_wins += pnl
                    logger.info(f"[WIN] {coin} {side} | +${pnl:.2f} | Entry: {entry*100:.0f}c x{shares} | Payout: ${payout:.2f}")
                    predictor.record_outcome(True)
                    tg.notify_result(coin, side, True, cost, payout)
                else:
                    orders.daily_losses += cost
                    logger.info(f"[LOSS] {coin} {side} | -${cost:.2f} | Entry: {entry*100:.0f}c x{shares} | day_loss=${orders.daily_losses:.2f}")
                    predictor.record_outcome(False)
                    tg.notify_result(coin, side, False, cost)
                    if orders.is_daily_stop_loss_hit():
                        logger.warning(f"[DAILY STOP] Loss limit hit (${orders.daily_losses:.2f}) — no new trades today")"""
    if old not in text:
        raise SystemExit("run_bot WIN/LOSS block not found")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")
    print("patched run_bot.py")


def patch_order_manager():
    p = ROOT / "order_manager.py"
    shutil.copy(p, p.with_suffix(p.suffix + f".bak_audit_{STAMP}"))
    text = p.read_text(encoding="utf-8")
    old = """            kelly_max_bet_env = float(os.getenv("KELLY_MAX_BET", "0"))
            # Compounding: max bet = 8% of bankroll, with  floor and no hard ceiling
            kelly_max_bet = max(kelly_max_bet_env, bankroll * 0.08) if kelly_max_bet_env > 0 else bankroll * 0.08"""
    new = """            kelly_max_bet_env = float(os.getenv("KELLY_MAX_BET", "0"))
            pct_cap = bankroll * float(os.getenv("KELLY_MAX_PCT", "0.05"))
            if kelly_max_bet_env > 0:
                kelly_max_bet = min(kelly_max_bet_env, pct_cap) if pct_cap > 0 else kelly_max_bet_env
            else:
                kelly_max_bet = pct_cap if pct_cap > 0 else bankroll * 0.05"""
    if old not in text:
        raise SystemExit("Kelly max block not found")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")
    print("patched order_manager.py")


def patch_predictor():
    p = ROOT / "predictor.py"
    shutil.copy(p, p.with_suffix(p.suffix + f".bak_audit_{STAMP}"))
    text = p.read_text(encoding="utf-8")

    old_sigma = """        SIGMA_FLOOR = 1e-05
        if sigma < SIGMA_FLOOR:
            sigma = SIGMA_FLOOR"""
    new_sigma = """        SIGMA_FLOOR = 5e-04
        if sigma < SIGMA_FLOOR:
            self._diag_log(
                f"lowvol-{coin}",
                f"[LOW VOL] {coin}: sigma={sigma:.2e} < {SIGMA_FLOOR:.2e} — abstaining",
                15.0,
            )
            return None"""
    if old_sigma not in text:
        raise SystemExit("sigma floor block not found")
    text = text.replace(old_sigma, new_sigma, 1)

    anchor = """        if edge < min_edge:
            self._diag_log(
                f"lowedge-{coin}-{direction}",
                f"[LOW EDGE] {coin} {direction}: prob={win_prob:.1%} ask={ask*100:.0f}c edge={edge*100:.1f}% < {min_edge*100:.0f}%",
                15.0,
            )
            return None

        confidence = \"HIGH\""""
    edge_gate = """        if edge < min_edge:
            self._diag_log(
                f"lowedge-{coin}-{direction}",
                f"[LOW EDGE] {coin} {direction}: prob={win_prob:.1%} ask={ask*100:.0f}c edge={edge*100:.1f}% < {min_edge*100:.0f}%",
                15.0,
            )
            return None

        # Expensive entry needs more edge (Jun-3 audit: 66-72c @ 8% edge = -EV)
        _hi_ask = float(os.getenv("HIGH_ASK_EDGE_MIN_ASK", "0.62"))
        _hi_edge = float(os.getenv("HIGH_ASK_EDGE_MIN_EDGE", "0.12"))
        if ask >= _hi_ask and edge < _hi_edge:
            self._diag_log(
                f"thin-{coin}-{direction}",
                f"[THIN EDGE] {coin} {direction}: ask={ask*100:.0f}c edge={edge*100:.1f}% < {_hi_edge*100:.0f}% needed at {_hi_ask*100:.0f}c+",
                15.0,
            )
            return None

        confidence = \"HIGH\""""
    if anchor not in text:
        raise SystemExit("edge anchor not found")
    text = text.replace(anchor, edge_gate, 1)
    p.write_text(text, encoding="utf-8")
    print("patched predictor.py")


def patch_env():
    p = ROOT / ".env"
    shutil.copy(p, p.with_suffix(p.suffix + f".bak_audit_{STAMP}"))
    updates = {
        "MIN_EDGE_THRESHOLD": "0.10",
        "MIN_WIN_PROB": "0.74",
        "KELLY_MAX_BET": "4.00",
        "KELLY_MAX_PCT": "0.04",
        "KELLY_FRACTION": "0.20",
        "HIGH_ASK_EDGE_MIN_ASK": "0.62",
        "HIGH_ASK_EDGE_MIN_EDGE": "0.12",
        "ENTRY_MAX": "0.70",
    }
    lines = p.read_text(encoding="utf-8").splitlines()
    for k, v in updates.items():
        found = False
        for i, line in enumerate(lines):
            if line.startswith(k + "="):
                lines[i] = f"{k}={v}"
                found = True
                break
        if not found:
            lines.append(f"{k}={v}")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("patched .env")


if __name__ == "__main__":
    patch_run_bot()
    patch_order_manager()
    patch_predictor()
    patch_env()
    print(f"[OK] backups stamped {STAMP}")
