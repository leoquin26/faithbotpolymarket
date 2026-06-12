#!/usr/bin/env python3
"""Persist open positions + daily PnL across bot restarts."""
import shutil
import time
from pathlib import Path

ROOT = Path("/home/ubuntu/v3-bot")
STAMP = time.strftime("%Y%m%d_%H%M%S")


def patch_order_manager():
    p = ROOT / "order_manager.py"
    shutil.copy(p, p.with_suffix(p.suffix + f".bak_persist_{STAMP}"))
    text = p.read_text(encoding="utf-8")

    old_init = """        self.traded_windows: Dict[str, str] = self._load_traded_windows()
        self.positions: Dict[str, dict] = {}
        self.daily_losses = 0.0
        self.daily_wins = 0.0
        self.daily_trades = 0
        self._trading_day = \"\""""
    new_init = """        self.traded_windows: Dict[str, str] = self._load_traded_windows()
        self.positions: Dict[str, dict] = self._load_positions()
        self.daily_losses = 0.0
        self.daily_wins = 0.0
        self.daily_trades = 0
        self._trading_day = ""
        self._load_daily_pnl()
        if self.positions:
            coins = ", ".join(
                f"{c} {self.positions[c].get('side', '?')}@{self.positions[c].get('entry_price', 0)*100:.0f}c"
                for c in self.positions
            )
            logger.info(f"[POSITIONS] Restored {len(self.positions)} open: {coins}")"""
    if old_init not in text:
        raise SystemExit("init block not found")
    text = text.replace(old_init, new_init, 1)

    anchor = """    def _save_traded_windows(self):
        self._TRADED_FILE.parent.mkdir(exist_ok=True)
        with open(self._TRADED_FILE, "w") as f:
            json.dump(self.traded_windows, f)

    def is_window_traded(self, coin: str, window_start: int) -> bool:"""
    persist_block = """    def _save_traded_windows(self):
        self._TRADED_FILE.parent.mkdir(exist_ok=True)
        with open(self._TRADED_FILE, "w") as f:
            json.dump(self.traded_windows, f)

    _POSITIONS_FILE = Path("data/open_positions.json")
    _DAILY_PNL_FILE = Path("data/daily_pnl.json")

    def _load_positions(self) -> Dict[str, dict]:
        try:
            if self._POSITIONS_FILE.exists():
                with open(self._POSITIONS_FILE) as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return {k: v for k, v in data.items() if isinstance(v, dict)}
        except Exception as e:
            logger.warning(f"[POSITIONS] load failed: {e}")
        return {}

    def _save_positions(self):
        try:
            self._POSITIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(self._POSITIONS_FILE, "w") as f:
                json.dump(self.positions, f, indent=2)
        except Exception as e:
            logger.warning(f"[POSITIONS] save failed: {e}")

    def set_position(self, coin: str, pos: dict):
        self.positions[coin] = pos
        self._save_positions()

    def remove_position(self, coin: str) -> Optional[dict]:
        pos = self.positions.pop(coin, None)
        self._save_positions()
        return pos

    def _load_daily_pnl(self):
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        self._trading_day = today
        try:
            if self._DAILY_PNL_FILE.exists():
                with open(self._DAILY_PNL_FILE) as f:
                    data = json.load(f)
                if data.get("date") == today:
                    self.daily_losses = float(data.get("losses", 0))
                    self.daily_wins = float(data.get("wins", 0))
                    self.daily_trades = int(data.get("trades", 0))
                    logger.info(
                        f"[DAILY PNL] Restored {today}: "
                        f"losses=${self.daily_losses:.2f} wins=${self.daily_wins:.2f} trades={self.daily_trades}"
                    )
                    return
        except Exception as e:
            logger.warning(f"[DAILY PNL] load failed: {e}")
        self.daily_losses = 0.0
        self.daily_wins = 0.0
        self.daily_trades = 0

    def _save_daily_pnl(self):
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        self._trading_day = today
        try:
            self._DAILY_PNL_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(self._DAILY_PNL_FILE, "w") as f:
                json.dump({
                    "date": today,
                    "losses": round(self.daily_losses, 4),
                    "wins": round(self.daily_wins, 4),
                    "trades": self.daily_trades,
                }, f, indent=2)
        except Exception as e:
            logger.warning(f"[DAILY PNL] save failed: {e}")

    def record_win_pnl(self, pnl: float):
        self.daily_wins += pnl
        self.daily_trades += 1
        self._save_daily_pnl()

    def record_loss_pnl(self, cost: float):
        self.daily_losses += cost
        self.daily_trades += 1
        self._save_daily_pnl()

    def is_window_traded(self, coin: str, window_start: int) -> bool:"""
    if anchor not in text:
        raise SystemExit("traded_windows anchor not found")
    text = text.replace(anchor, persist_block, 1)

    text = text.replace("self.positions[coin] = {", "self.set_position(coin, {", 1)
    text = text.replace('self.positions[info["coin"]] = {', 'self.set_position(info["coin"], {', 1)
    # Close set_position( calls
    text = text.replace(
        '"strike": pred.market_info.threshold_price if pred and hasattr(pred, \'market_info\') else 0,\n                }',
        '"strike": pred.market_info.threshold_price if pred and hasattr(pred, \'market_info\') else 0,\n                })',
        1,
    )
    if '"strike": info.get("strike", 0),' not in text:
        text = text.replace(
            '"window_start": info["window_start"],\n                    }',
            '"window_start": info["window_start"],\n                        "strike": info.get("strike", 0),\n                    })',
            1,
        )
    else:
        text = text.replace(
            '"window_start": info["window_start"],\n                        "strike": info.get("strike", 0),\n                    }',
            '"window_start": info["window_start"],\n                        "strike": info.get("strike", 0),\n                    })',
            1,
        )

    p.write_text(text, encoding="utf-8")
    print("patched order_manager.py")


def patch_run_bot():
    p = ROOT / "run_bot.py"
    shutil.copy(p, p.with_suffix(p.suffix + f".bak_persist_{STAMP}"))
    text = p.read_text(encoding="utf-8")

    if "def resolve_expired_positions(" not in text:
        fn = '''

def resolve_expired_positions(orders, predictor, binance_ws_module):
    """Resolve open positions whose window ended (incl. after restart)."""
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
        try:
            final_price = binance_ws_module.get_price(coin)
            strike = pos.get("strike", 0)
            if strike > 0 and final_price > 0:
                went_up = final_price > strike
                won = (side == "UP" and went_up) or (side == "DOWN" and not went_up)
        except Exception:
            pass
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
                )

'''
        ins = "def lock_window(coin: str, window_start: int) -> bool:"
        if ins not in text:
            raise SystemExit("lock_window anchor not found")
        text = text.replace(ins, fn + ins, 1)

    old_block = """            current_time = int(time.time())
            expired = []
            for coin, pos in orders.positions.items():
                ws = pos.get("window_start", 0)
                if ws > 0 and current_time > ws + 900 + 60:
                    expired.append(coin)
            for coin in expired:
                pos = orders.positions.pop(coin)
                side = pos.get("side", "?")
                entry = pos.get("entry_price", 0)
                shares = pos.get("shares", 0)
                cost = entry * shares
                payout = shares * 1.0

                won = False
                try:
                    final_price = binance_ws.get_price(coin)
                    strike = pos.get("strike", 0)
                    if strike > 0 and final_price > 0:
                        went_up = final_price > strike
                        won = (side == "UP" and went_up) or (side == "DOWN" and not went_up)
                except Exception:
                    pass

                if won:
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

    new_block = """            resolve_expired_positions(orders, predictor, binance_ws)"""

    if old_block in text:
        text = text.replace(old_block, new_block, 1)
    elif "resolve_expired_positions(orders, predictor, binance_ws)" in text:
        print("run_bot expiry loop already patched")
    else:
        raise SystemExit("expiry block not found")

    startup = """    resolve_expired_positions(orders, predictor, binance_ws)

    try:
        while True:"""
    if "resolve_expired_positions(orders, predictor, binance_ws)\n\n    try:" not in text:
        text = text.replace("    try:\n        while True:", startup, 1)

    p.write_text(text, encoding="utf-8")
    print("patched run_bot.py")


if __name__ == "__main__":
    patch_order_manager()
    patch_run_bot()
    print(f"[OK] stamp {STAMP}")
