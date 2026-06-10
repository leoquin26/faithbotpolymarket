"""Wire brier_history updates on trade resolution.
Stores prob_at_entry in position dict, appends (prob, won) on resolve.
"""
from pathlib import Path

# Step 1: store prob_at_entry in positions dict (order_manager.py)
ORDER = Path("/home/ubuntu/v3-bot/order_manager.py")
text = ORDER.read_text()

if "prob_at_entry" in text:
    print("prob_at_entry already wired in order_manager")
else:
    old = ("""                self.positions[coin] = {
                    \"coin\": coin,
                    \"side\": direction,
                    \"entry_price\": avg_price,
                    \"shares\": int(matched),
                    \"token_id\": token_id,
                    \"window_start\": window_start,
                    \"strike\": pred.market_info.threshold_price if pred and hasattr(pred, 'market_info') else 0,
                }""")
    new = ("""                self.positions[coin] = {
                    \"coin\": coin,
                    \"side\": direction,
                    \"entry_price\": avg_price,
                    \"shares\": int(matched),
                    \"token_id\": token_id,
                    \"window_start\": window_start,
                    \"strike\": pred.market_info.threshold_price if pred and hasattr(pred, 'market_info') else 0,
                    \"prob_at_entry\": float(pred.probability) if pred else 0.0,
                }""")
    if old not in text:
        raise SystemExit("positions store marker not found")
    text = text.replace(old, new, 1)
    ORDER.write_text(text)
    print("prob_at_entry stored in positions dict")

# Step 2: append to brier_history on resolution (run_bot.py)
RUN = Path("/home/ubuntu/v3-bot/run_bot.py")
text2 = RUN.read_text()

if "brier_history.append" in text2:
    print("brier append already wired in run_bot")
else:
    old2 = """                if won:
                    pnl = payout - cost
                    logger.info(f"[WIN {_tag}] {coin} {side} | +${pnl:.2f} | Entry: {entry*100:.0f}c x{shares} | Payout: ${payout:.2f}")
                    tg.notify_result(coin, side, True, cost, payout)"""
    new2 = """                # May 21: append to brier_history for adaptive Kelly
                try:
                    _prob_e = float(pos.get(\"prob_at_entry\", 0.0))
                    if _prob_e > 0.0:
                        orders._brier_history.append((_prob_e, 1 if won else 0))
                        if len(orders._brier_history) > 20:
                            orders._brier_history.pop(0)
                except Exception:
                    pass
                if won:
                    pnl = payout - cost
                    logger.info(f\"[WIN {_tag}] {coin} {side} | +${pnl:.2f} | Entry: {entry*100:.0f}c x{shares} | Payout: ${payout:.2f}\")
                    tg.notify_result(coin, side, True, cost, payout)"""
    if old2 not in text2:
        raise SystemExit("won handler marker not found")
    text2 = text2.replace(old2, new2, 1)
    RUN.write_text(text2)
    print("brier_history append wired in run_bot")

print("DONE")
