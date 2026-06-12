"""
Order Manager V2 — handles FOK/GTC order placement via Polymarket CLOB.

V2 changes:
- get_clob_ask() for pre-evaluation price validation
- place_bet recalculates edge against real CLOB ask
- Blocks if ask > 73c or < 5c
"""

import os
import time
import threading
try:
    import polymarket_ws as _pws
except Exception:
    _pws = None
import json
from pathlib import Path
from typing import Optional, Dict, Set
from loguru import logger
import telegram_notifier as tg

import config
from predictor import Prediction

# CLOB V2 migration apr28: switched from py_clob_client to py_clob_client_v2
# (V2 went live 2026-04-28 11:00 UTC; V1 returns order_version_mismatch.)
from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import (
    OrderArgs, OrderType, PartialCreateOrderOptions, ApiCreds,
)
from py_clob_client_v2.order_builder.constants import BUY


class OrderManager:
    """Manages order placement, GTC tracking, and window dedup."""

    _place_lock = threading.Lock()

    def __init__(self):
        self.client = self._init_client()
        self.active_gtc: Dict[str, dict] = {}
        self.traded_windows: Dict[str, str] = self._load_traded_windows()
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
            logger.info(f"[POSITIONS] Restored {len(self.positions)} open: {coins}")


    # ------------------------------------------------------------------
    # Live bankroll from USDC balance
    # ------------------------------------------------------------------
    _last_balance_check = 0
    _cached_balance = 0.0

    def get_live_bankroll(self) -> float:
        import time as _t
        now = _t.time()
        if now - OrderManager._last_balance_check < 300:
            return OrderManager._cached_balance if OrderManager._cached_balance > 0 else config.BANKROLL_BALANCE

        try:
            http = self._get_direct_http()
            addr = config.FUNDER_ADDRESS
            resp = http.get(
                f"https://clob.polymarket.com/balance?address={addr}",
                timeout=5,
            )
            if resp.status_code == 200:
                data = resp.json()
                balance = float(data) if isinstance(data, (int, float, str)) else 0.0
                if balance <= 0 and isinstance(data, dict):
                    balance = float(data.get("balance", 0) or data.get("amount", 0) or 0)
                if balance > 0:
                    old = config.BANKROLL_BALANCE
                    config.BANKROLL_BALANCE = balance
                    OrderManager._cached_balance = balance
                    OrderManager._last_balance_check = now
                    if abs(balance - old) > 0.50:
                        logger.info(f"[BANKROLL] Updated: ${old:.2f} -> ${balance:.2f}")
                    return balance
        except Exception as e:
            logger.debug(f"[BANKROLL] Balance fetch error: {e}")

        OrderManager._last_balance_check = now
        return config.BANKROLL_BALANCE

        # ------------------------------------------------------------------
    # CLOB client init
    # ------------------------------------------------------------------
    def _init_client(self) -> ClobClient:
        client = ClobClient(
            config.CLOB_HOST,
            key=config.PRIVATE_KEY,
            chain_id=config.CHAIN_ID,
            signature_type=int(os.getenv("POLYMARKET_SIGNATURE_TYPE", "1")),
            funder=config.FUNDER_ADDRESS,
        )
        if config.API_KEY and config.API_SECRET and config.API_PASSPHRASE:
            client.set_api_creds(ApiCreds(
                api_key=config.API_KEY,
                api_secret=config.API_SECRET,
                api_passphrase=config.API_PASSPHRASE,
            ))
            logger.info("[OK] API credentials loaded")
        else:
            client.set_api_creds(client.create_or_derive_api_creds())
            logger.warning("No API creds in .env — derived new ones")
        try:
            v = client.get_version()
            logger.info(f"[CLOB] backend version={v}")
        except Exception as e:
            logger.warning(f"[CLOB] get_version failed: {e}")
        return client

    # ------------------------------------------------------------------
    # Traded-window persistence
    # ------------------------------------------------------------------
    _TRADED_FILE = Path("data/traded_windows.json")

    def _load_traded_windows(self) -> Dict[str, str]:
        try:
            if self._TRADED_FILE.exists():
                with open(self._TRADED_FILE) as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _save_traded_windows(self):
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

    def is_window_traded(self, coin: str, window_start: int) -> bool:
        key = f"{coin}-{window_start}"
        return key in self.traded_windows

    def mark_window_traded(self, coin: str, window_start: int, direction: str):
        key = f"{coin}-{window_start}"
        self.traded_windows[key] = direction
        self._save_traded_windows()

    # ------------------------------------------------------------------
    # Correlation limit: count same-direction trades this window
    # ------------------------------------------------------------------
    def count_same_direction_trades(self, direction: str, window_start: int) -> int:
        count = 0
        for wk, d in self.traded_windows.items():
            parts = wk.rsplit("-", 1)
            if len(parts) == 2:
                try:
                    ws = int(parts[1])
                    if ws == window_start and d == direction:
                        count += 1
                except (ValueError, IndexError):
                    pass
        return count

    # ------------------------------------------------------------------
    # Daily stop-loss
    # ------------------------------------------------------------------
    def is_daily_stop_loss_hit(self) -> bool:
        if not config.USE_DAILY_STOP_LOSS:
            return False
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        if self._trading_day != today:
            self.daily_losses = 0.0
            self.daily_wins = 0.0
            self.daily_trades = 0
            self._trading_day = today
        # Scale stop-loss with bankroll: 10% of current bankroll, min 5
        dynamic_limit = max(config.DAILY_LOSS_LIMIT, self.get_live_bankroll() * 0.10)
        # jun12 audit: stop on NET drawdown (losses - wins), not gross losses.
        # Gross halted a -$12.67 net day at $24.33 "losses" — a $20 daily
        # stop should mean "down $20 on the day". DAILY_STOP_MODE=gross
        # restores the old behavior.
        import os as _os_ds
        if _os_ds.getenv("DAILY_STOP_MODE", "net").lower() == "net":
            return (self.daily_losses - self.daily_wins) >= dynamic_limit
        return self.daily_losses >= dynamic_limit

    # ------------------------------------------------------------------
    # Order-book helpers
    # ------------------------------------------------------------------
    def get_orderbook_asks(self, token_id: str) -> dict:
        result = {"real_ask": None, "raw_ask": None}
        try:
            book = self.client.get_order_book(token_id)
            asks = getattr(book, "asks", []) if not isinstance(book, dict) else book.get("asks", [])
            if asks:
                real_asks = []
                all_asks = []
                for a in asks:
                    p = getattr(a, "price", None) if not isinstance(a, dict) else a.get("price")
                    if p is not None:
                        p = float(p)
                        all_asks.append(p)
                        if 0.01 < p < 0.96:
                            real_asks.append(p)
                if real_asks:
                    result["real_ask"] = min(real_asks)
                if all_asks:
                    result["raw_ask"] = min(all_asks)
        except Exception as e:
            logger.debug(f"Order book error: {e}")
        return result

    def get_clob_ask(self, token_id: str) -> Optional[float]:
        """Fetch the real executable CLOB ask via direct HTTP."""
        book = self.get_clob_book(token_id)
        return book.get("ask")

    _direct_http = None

    @classmethod
    def _get_direct_http(cls):
        if cls._direct_http is None:
            import httpx
            cls._direct_http = httpx.Client(timeout=5, follow_redirects=True, proxy=None)
        return cls._direct_http

    def get_clob_book(self, token_id: str) -> dict:
        """WS cache first, REST fallback."""
        if _pws is not None:
            try:
                _ws_book = _pws.get_book(token_id)
                if _ws_book and _ws_book.get("ask"):
                    _ws_age = time.time() - _ws_book.get("ts", 0)
                    if _ws_age <= 12.0:
                        return {
                            "ask": _ws_book.get("ask"),
                            "bid": _ws_book.get("bid"),
                            "mid": _ws_book.get("mid"),
                            "depth_ratio": _ws_book.get("depth_ratio", 0.0),
                            "source": "ws",
                        }
            except Exception:
                pass
        result = {"ask": None, "bid": None, "mid": None, "depth_ratio": 0.0}
        try:
            http = self._get_direct_http()
            resp = http.get(f"https://clob.polymarket.com/book?token_id={token_id}")
            if resp.status_code != 200:
                return result
            book = resp.json()
            asks = book.get("asks", [])
            bids = book.get("bids", [])
            bid_total = 0.0
            ask_total = 0.0
            if asks:
                real_asks = []
                for a in asks:
                    p = getattr(a, "price", None) if not isinstance(a, dict) else a.get("price")
                    s = getattr(a, "size", None) if not isinstance(a, dict) else a.get("size")
                    if p is not None:
                        p = float(p)
                        if 0.01 < p < 0.96:
                            real_asks.append(p)
                        if s is not None:
                            ask_total += p * float(s)
                if real_asks:
                    result["ask"] = min(real_asks)
            if bids:
                all_bids = []
                for b in bids:
                    p = getattr(b, "price", None) if not isinstance(b, dict) else b.get("price")
                    s = getattr(b, "size", None) if not isinstance(b, dict) else b.get("size")
                    if p is not None:
                        all_bids.append(float(p))
                        if s is not None:
                            bid_total += float(p) * float(s)
                if all_bids:
                    result["bid"] = max(all_bids)
            if result["ask"] and result["bid"]:
                result["mid"] = (result["ask"] + result["bid"]) / 2.0
            elif result["ask"]:
                result["mid"] = result["ask"]
            if ask_total > 0:
                result["depth_ratio"] = bid_total / ask_total
        except Exception as e:
            logger.debug(f"[CLOB BOOK] Error: {e}")
        return result


    def get_full_depth(self, token_id: str) -> dict:
        """Fetch full orderbook depth: lists of (price, size) for bids and asks."""
        result = {"bids": [], "asks": [], "bid_total": 0.0, "ask_total": 0.0}
        try:
            book = self.client.get_order_book(token_id)
            asks = getattr(book, "asks", []) if not isinstance(book, dict) else book.get("asks", [])
            bids = getattr(book, "bids", []) if not isinstance(book, dict) else book.get("bids", [])
            for a in (asks or []):
                p = float(getattr(a, "price", None) if not isinstance(a, dict) else a.get("price", 0))
                s = float(getattr(a, "size", None) if not isinstance(a, dict) else a.get("size", 0))
                if p > 0 and s > 0:
                    result["asks"].append((p, s))
                    result["ask_total"] += p * s
            for b in (bids or []):
                p = float(getattr(b, "price", None) if not isinstance(b, dict) else b.get("price", 0))
                s = float(getattr(b, "size", None) if not isinstance(b, dict) else b.get("size", 0))
                if p > 0 and s > 0:
                    result["bids"].append((p, s))
                    result["bid_total"] += p * s
            result["bids"].sort(key=lambda x: x[0], reverse=True)
            result["asks"].sort(key=lambda x: x[0])
        except Exception as e:
            logger.debug(f"[DEPTH] Error fetching depth for {token_id}: {e}")
        return result

    def get_depth_imbalance(self, token_id: str) -> float:
        """Returns bid/ask depth ratio. >1 means more buying pressure. 0 on error."""
        depth = self.get_full_depth(token_id)
        if depth["ask_total"] <= 0:
            return 0.0
        return depth["bid_total"] / depth["ask_total"]

    # ------------------------------------------------------------------
    # MAIN: place_bet
    # ------------------------------------------------------------------
    def _strike_fields(self, pred, window_start: int) -> dict:
        mi = pred.market_info if pred and hasattr(pred, "market_info") else None
        return {
            "window_start": window_start,
            "strike": mi.threshold_price if mi else 0,
            "slug": getattr(mi, "slug", "") if mi else "",
            "strike_source": getattr(mi, "strike_source", "") if mi else "",
            "timeframe": getattr(mi, "timeframe", "15m") if mi else "15m",
        }

    def place_bet(self, pred: Prediction) -> bool:
        coin = pred.coin
        direction = pred.direction
        token_id = pred.token_id
        window_start = pred.market_info.window_start

        if self.is_window_traded(coin, window_start):
            logger.warning(f"[SKIP] Already traded {coin} in this window")
            return False

        for oid, oinfo in self.active_gtc.items():
            if oinfo.get("coin") == coin:
                logger.warning(f"[SKIP] Active GTC exists for {coin}")
                return False

        if self.is_daily_stop_loss_hit():
            logger.warning(
                f"[STOP] Daily stop hit: gross=-${self.daily_losses:.2f} "
                f"wins=+${self.daily_wins:.2f} "
                f"net=-${self.daily_losses - self.daily_wins:.2f}"
            )
            return False

        # Correlation limit: max 3 same-direction bets per window
        same_dir_count = self.count_same_direction_trades(direction, window_start)
        if same_dir_count >= 3:
            logger.info(
                f"[CORR GATE] {coin}: Already {same_dir_count} {direction} "
                f"bets this window (max 3)"
            )
            return False

        # jun10 fix: correlated double-up cap (BTC<->ETH).
        # Block a 2nd same-direction bet in the same window when a highly-
        # correlated coin already has an open position in that direction.
        # BTC & ETH move together; 2x same-side exposure loses together when
        # the macro tape reverses late-window (root cause of 15:33/15:34 dbl loss).
        _corr_pairs = {("BTC", "ETH"), ("ETH", "BTC")}
        for _other_coin, _pos in list(self.positions.items()):
            if _other_coin == coin:
                continue
            if (coin, _other_coin) not in _corr_pairs:
                continue
            if _pos.get("side") == direction and _pos.get("window_start") == window_start:
                logger.info(
                    f"[CORR DOUBLE-UP] {coin} {direction}: already have "
                    f"{_other_coin} {direction} open same window "
                    f"(window_start={window_start}) — blocking 2x correlated exposure"
                )
                return False

        if config.DRY_RUN:
            logger.info(f"[DRY] Would bet {coin} {direction} @ ~{pred.poly_price*100:.0f}c | Edge {pred.edge*100:.1f}%")
            print(f"\n  [DRY RUN] {coin} {direction} | Edge: {pred.edge*100:.1f}% | Conf: {pred.confidence}")
            return True

        poly_price = pred.poly_price
        max_entry = config.ENTRY_MAX
        time_left = pred.market_info.time_remaining

        ob = self.get_orderbook_asks(token_id)
        real_ask = ob["real_ask"]
        raw_ask = ob["raw_ask"]

        if not raw_ask:
            logger.warning(f"[SKIP] No asks for {coin} {direction}")
            return False

        # CLOB ask validation: real entry price
        if real_ask and real_ask > config.ENTRY_MAX:
            logger.info(f"[CLOB GATE] {coin}: ask={real_ask*100:.0f}c > {config.ENTRY_MAX*100:.0f}c — too expensive")
            return False

        # Recalculate edge against real CLOB ask if available
        actual_entry = real_ask if real_ask else poly_price
        real_edge = pred.probability - actual_entry
        _min_edge = float(getattr(config, "MIN_EDGE", 0.02))
        if real_edge < _min_edge:
            logger.info(
                f"[EDGE GATE] {coin}: real_edge={real_edge*100:.1f}% "
                f"(post={pred.probability*100:.0f}% - ask={actual_entry*100:.0f}c) < {_min_edge*100:.0f}%"
            )
            return False

        our_limit = round(min(poly_price + 0.02, max_entry), 2)

        if real_ask and real_ask <= max_entry:
            fok_price = round(min(real_ask + 0.01, max_entry), 2)
            limit_price = fok_price
            use_gtc = False
            logger.debug(
                f"[PRICE] {coin}: poly={poly_price*100:.0f}c ask={real_ask*100:.0f}c "
                f"limit={limit_price*100:.0f}c (FOK)"
            )
        elif time_left >= 3:
            limit_price = our_limit
            use_gtc = True
            logger.debug(
                f"[PRICE] {coin}: poly={poly_price*100:.0f}c ask={raw_ask*100:.0f}c(no-real) "
                f"limit={limit_price*100:.0f}c (GTC)"
            )
        else:
            logger.debug(f"[SKIP] {coin}: no real asks, <3m left")
            return False

        size_usd = self._calc_size(pred)
        # jun12: honor session/distance sizing for small bets. Old max(5,..)
        # floor forced every reduced morning bet back to 5sh, negating the
        # risk-down. Floor is now env-tunable (SIZE_SH_HARD_FLOOR, default 3).
        import os as _os_sz
        _hard_floor = int(_os_sz.getenv('SIZE_SH_HARD_FLOOR', '3'))
        shares = max(_hard_floor, int(size_usd / limit_price))
        # jun12 pm audit: ANY second same-direction fill in the same window is
        # near-duplicated exposure (BTC/ETH/SOL move together intraday — the
        # BTC<->ETH block above missed today's simultaneous ETH+SOL UP double
        # loss). De-size instead of blocking. CORR_SECOND_MULT=1.0 disables.
        try:
            _cmult = float(_os_sz.getenv("CORR_SECOND_MULT", "0.5"))
            if _cmult < 1.0:
                for _oc, _op in list(self.positions.items()):
                    if (_oc != coin and _op.get("side") == direction
                            and _op.get("window_start") == window_start):
                        _sh_old = shares
                        shares = max(2, int(round(shares * _cmult)))
                        logger.info(
                            f"[CORR DE-SIZE] {coin} {direction}: {_oc} already "
                            f"same-dir this window — {_sh_old} -> {shares} shares "
                            f"(x{_cmult})"
                        )
                        break
        except Exception:
            pass

        order_type = OrderType.GTC if use_gtc else OrderType.FOK
        order_type_name = "GTC" if use_gtc else "FOK"

        logger.info(
            f"[ORDER] {coin} {direction} | {order_type_name} @ {limit_price*100:.0f}c | "
            f"{shares} shares (${size_usd:.2f}) | Edge {real_edge*100:.1f}%"
        )

        try:
            from py_clob_client_v2.exceptions import PolyApiException
            options = PartialCreateOrderOptions(tick_size="0.01")
            order_args = OrderArgs(
                price=limit_price,
                size=shares,
                side=BUY,
                token_id=token_id,
            )
            result = None
            with self._place_lock:
                for attempt in range(3):
                    try:
                        if attempt > 0:
                            self.client._ClobClient__cached_version = None
                            self.client.get_version()
                        result = self.client.create_and_post_order(
                            order_args, options, order_type
                        )
                        break
                    except PolyApiException as e:
                        if "order_version_mismatch" not in str(e).lower() or attempt >= 2:
                            raise
                        logger.warning(
                            f"[CLOB] {coin} order_version_mismatch — retry {attempt + 2}/3"
                        )

            matched, avg_price, order_id = self._parse_result(result)

            if use_gtc:
                self.active_gtc[order_id or "unknown"] = {
                    "coin": coin,
                    "direction": direction,
                    "token_id": token_id,
                    "price": limit_price,
                    "shares": shares,
                    "placed_at": time.time(),
                    "prediction": pred,
                    **self._strike_fields(pred, window_start),
                }
                self.mark_window_traded(coin, window_start, direction)
                logger.info(f"[GTC] Pending: {coin} {direction} @ {limit_price*100:.0f}c")
                print(f"\n  [GTC] PENDING: {coin} {direction} @ {limit_price*100:.0f}c | waiting for fill...")
                return True

            if matched > 0:
                cost = matched * avg_price
                self.set_position(coin, {
                    "coin": coin,
                    "side": direction,
                    "entry_price": avg_price,
                    "shares": int(matched),
                    "token_id": token_id,
                    "window_start": window_start,
                    "strike": pred.market_info.threshold_price if pred and hasattr(pred, 'market_info') else 0,
                    "slug": getattr(pred.market_info, "slug", "") if pred and hasattr(pred, 'market_info') else "",
                    "strike_source": getattr(pred.market_info, "strike_source", "") if pred and hasattr(pred, 'market_info') else "",
                    "timeframe": getattr(pred.market_info, "timeframe", "15m") if pred and hasattr(pred, 'market_info') else "15m",
                })
                self.daily_trades += 1
                self.mark_window_traded(coin, window_start, direction)
                logger.info(f"[FILLED] {coin} {direction} | {int(matched)} shares @ {avg_price*100:.0f}c = ${cost:.2f}")
                # jun12 audit: analytics pipeline was severed by the Jun-3
                # rewrite — restore SIGNAL/FIRED events (jsonl + sqlite ledger).
                try:
                    from analytics import event_logger as _alog_f
                    _tid = _alog_f.new_trade_id()
                    if pred is not None:
                        _alog_f.log_signal(_tid, pred, float(getattr(pred, "trend_score", 0.0) or 0.0),
                                           dist_pct=float(getattr(pred, "dist_pct", 0.0) or 0.0))
                    _alog_f.log_fired(_tid, coin, direction, float(avg_price), float(matched),
                                      float(cost), phase="15M", order_kind="FOK",
                                      window_start=int(window_start or 0))
                except Exception:
                    pass
                print(f"\n  [OK] FILLED: {coin} {direction} | {int(matched)} shares @ {avg_price*100:.0f}c | Cost: ${cost:.2f}")
                tg.notify_fill(coin, direction, int(matched), avg_price, cost, pred.edge if pred else 0, pred.probability if pred else 0)
                return True
            else:
                logger.warning(f"[MISS] {coin} {direction} — 0 shares matched")
                print(f"\n  [X] MISSED: {coin} {direction} — order not filled")
                return False

        except Exception as e:
            err_l = str(e).lower()
            if ("fully filled" in err_l or "killed" in err_l) and not use_gtc and time_left >= 120:
                logger.info(f"[FOK->GTC] {coin}: FOK killed, posting GTC @ {limit_price*100:.0f}c")
                try:
                    gtc_result = self.client.create_and_post_order(
                        order_args, PartialCreateOrderOptions(tick_size="0.01"), OrderType.GTC
                    )
                    gtc_m, gtc_p, gtc_oid = self._parse_result(gtc_result)
                    if gtc_m > 0:
                        cost = gtc_m * gtc_p
                        self.set_position(coin, {
                            "coin": coin, "side": direction, "entry_price": gtc_p,
                            "shares": int(gtc_m), "token_id": token_id,
                            "window_start": window_start,
                            "strike": pred.market_info.threshold_price if pred and hasattr(pred, 'market_info') else 0,
                            "slug": getattr(pred.market_info, "slug", "") if pred and hasattr(pred, 'market_info') else "",
                            "strike_source": getattr(pred.market_info, "strike_source", "") if pred and hasattr(pred, 'market_info') else "",
                            "timeframe": getattr(pred.market_info, "timeframe", "15m") if pred and hasattr(pred, 'market_info') else "15m",
                        })
                        self.daily_trades += 1
                        self.mark_window_traded(coin, window_start, direction)
                        logger.info(f"[FILLED] {coin} {direction} | {int(gtc_m)} shares @ {gtc_p*100:.0f}c (GTC)")
                        try:
                            from analytics import event_logger as _alog_g
                            _tid = _alog_g.new_trade_id()
                            if pred is not None:
                                _alog_g.log_signal(_tid, pred, float(getattr(pred, "trend_score", 0.0) or 0.0),
                                                   dist_pct=float(getattr(pred, "dist_pct", 0.0) or 0.0))
                            _alog_g.log_fired(_tid, coin, direction, float(gtc_p), float(gtc_m),
                                              float(cost), phase="15M", order_kind="GTC",
                                              window_start=int(window_start or 0))
                        except Exception:
                            pass
                        return True
                    self.active_gtc[gtc_oid or "unknown"] = {
                        "coin": coin, "direction": direction, "token_id": token_id,
                        "price": limit_price, "shares": shares, "placed_at": time.time(),
                        "prediction": pred,
                        **self._strike_fields(pred, window_start),
                    }
                    self.mark_window_traded(coin, window_start, direction)
                    logger.info(f"[GTC] Pending after FOK miss: {coin} @ {limit_price*100:.0f}c")
                    return True
                except Exception as gtc_e:
                    logger.warning(f"[FOK->GTC] {coin} fallback failed: {gtc_e}")
            logger.error(f"[ERROR] Order failed for {coin}: {e}")
            tg.notify_error(f"Order failed: {coin} {direction}\n{str(e)[:100]}")
            print(f"\n  [ERROR] {coin} order failed: {e}")
            return False

    # ------------------------------------------------------------------
    # GTC management
    # ------------------------------------------------------------------
    def check_gtc_fills(self):
        if not self.active_gtc:
            return

        filled = []
        for oid, info in list(self.active_gtc.items()):
            try:
                status = self.client.get_order(oid)
                if not status:
                    continue
                s = status.get("status", "").upper()
                filled_qty = float(status.get("size_matched", 0))

                if s == "FILLED" or filled_qty > 0:
                    fill_price = float(status.get("average_price", info["price"]))
                    self.set_position(info["coin"], {
                        "coin": info["coin"],
                        "side": info["direction"],
                        "entry_price": fill_price,
                        "shares": int(filled_qty) if filled_qty > 0 else info["shares"],
                        "token_id": info["token_id"],
                        "window_start": info.get("window_start", 0),
                        "strike": info.get("strike", 0),
                        "slug": info.get("slug", ""),
                        "strike_source": info.get("strike_source", ""),
                        "timeframe": info.get("timeframe", "15m"),
                    })
                    self.daily_trades += 1
                    cost = filled_qty * fill_price
                    logger.info(f"[GTC FILLED] {info['coin']} {info['direction']} @ {fill_price*100:.0f}c ({filled_qty} shares, ${cost:.2f})")
                    tg.notify_fill(info["coin"], info["direction"], int(filled_qty), fill_price, cost, 0, 0)
                    print(f"\n  [OK] GTC FILLED: {info['coin']} {info['direction']} @ {fill_price*100:.0f}c")
                    filled.append(oid)

                elif s in ("CANCELLED", "EXPIRED", "REJECTED"):
                    logger.warning(f"[GTC {s}] {info['coin']} — no fill")
                    filled.append(oid)

            except Exception as e:
                age = time.time() - info.get("placed_at", 0)
                if age > 300:
                    logger.error(f"[GTC] Check failing for {info['coin']} after {age/60:.1f}min")

        for oid in filled:
            self.active_gtc.pop(oid, None)

    def cancel_stale_gtc(self):
        now = time.time()
        to_cancel = []
        for oid, info in list(self.active_gtc.items()):
            age = now - info.get("placed_at", 0)
            if age > 300:
                to_cancel.append(oid)

        for oid in to_cancel:
            try:
                self.client.cancel(oid)
                logger.info(f"[GTC CANCEL] Cancelled stale order {oid}")
            except Exception as e:
                logger.debug(f"Cancel error: {e}")
            self.active_gtc.pop(oid, None)

    # ------------------------------------------------------------------
    # Arbitrage
    # ------------------------------------------------------------------
    def execute_arb(self, coin: str, up_token: str, down_token: str,
                    up_price: float, down_price: float, window_start: int) -> bool:
        if self.is_window_traded(coin, window_start):
            return False

        # mark_window_traded moved to after fill
        combined = up_price + down_price
        fee_pct = 0.02
        net_payout = 1.0 - fee_pct
        profit_pct = (net_payout - combined) / combined * 100

        if config.DRY_RUN:
            print(f"\n  [DRY ARB] {coin}: UP {up_price*100:.0f}c + DOWN {down_price*100:.0f}c = {combined*100:.0f}c | Profit: {profit_pct:.1f}%")
            return True

        arb_size = float(os.getenv("ARB_POSITION_SIZE", "10"))
        up_shares = max(5, int((arb_size / 2) / up_price))
        down_shares = max(5, int((arb_size / 2) / down_price))

        try:
            opts = PartialCreateOrderOptions(tick_size="0.01", neg_risk=False)

            up_args = OrderArgs(price=round(up_price, 2), size=up_shares, side=BUY, token_id=up_token)
            up_order = self.client.create_order(up_args, opts)
            up_result = self.client.post_order(up_order, OrderType.FOK)

            down_args = OrderArgs(price=round(down_price, 2), size=down_shares, side=BUY, token_id=down_token)
            down_order = self.client.create_order(down_args, opts)
            down_result = self.client.post_order(down_order, OrderType.FOK)

            total = up_shares * up_price + down_shares * down_price
            logger.info(f"[ARB] {coin}: UP {up_shares}@{up_price*100:.0f}c + DOWN {down_shares}@{down_price*100:.0f}c = ${total:.2f}")
            print(f"\n  [ARB] EXECUTED: {coin} | Cost: ${total:.2f} | Guaranteed profit: {profit_pct:.1f}%")
            return True
        except Exception as e:
            logger.error(f"[ARB FAIL] {e}")
            return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _calc_size(self, pred: Prediction) -> float:
        import os
        bankroll = self.get_live_bankroll()

        # jun10 sizing fix: model probability has ~0 correlation with
        # realized WR (slope -0.003), so Kelly-on-prob bet BIGGEST on the
        # trades it was most wrong about (losers 7.3sh > winners 6.6sh,
        # net -$5 vs +$25 backtest). The ONLY feature that predicts wins is
        # |distance from strike|: sweet spot [0.10,0.20]% wins 74% vs 55%.
        # Size by distance tier instead. Env: SIZING_MODE=distance|kelly|flat.
        _mode = os.getenv("SIZING_MODE", "distance").lower()
        if _mode == "distance":
            _adist = abs(getattr(pred, "dist_pct", 0.0))  # fraction (0.0012 = 0.12%)
            _sweet_lo = float(os.getenv("SIZE_SWEET_LO", "0.0010"))
            _sweet_hi = float(os.getenv("SIZE_SWEET_HI", "0.0020"))
            _ok_lo = float(os.getenv("SIZE_OK_LO", "0.0007"))
            _ok_hi = float(os.getenv("SIZE_OK_HI", "0.0025"))
            _sh_sweet = float(os.getenv("SIZE_SH_SWEET", "9"))
            _sh_ok = float(os.getenv("SIZE_SH_OK", "6"))
            _sh_tail = float(os.getenv("SIZE_SH_TAIL", "4"))
            if _sweet_lo <= _adist < _sweet_hi:
                _sh = _sh_sweet
            elif _ok_lo <= _adist < _ok_hi:
                _sh = _sh_ok
            else:
                _sh = _sh_tail
            # jun12 audit: MEDIUM-confidence signals won 39.4% (n=33) vs HIGH
            # 58.8% (n=177) — de-size them, do not block. CONF_MULT_MEDIUM=1.0
            # disables.
            if str(getattr(pred, "confidence", "") or "") == "MEDIUM":
                _sh = max(2.0, round(_sh * float(os.getenv("CONF_MULT_MEDIUM", "0.6"))))
            # jun11 session-weighted sizing: trade smoothly ALL day (no
            # blocking) but lean size into when the bot actually wins.
            # 90-trade data by ET session: PRE_OPEN 43%, US_OPEN 54%,
            # POST_OPEN 57%, MIDDAY 57%, AFTERNOON 75%. Moderate multipliers
            # lift PnL +$22 -> +$36 with same capital. All env-tunable.
            if os.getenv("SESSION_SIZING_ON", "on").lower() not in ("off", "0", "false"):
                try:
                    import session_calibration as _sc
                    _sname = _sc.get_session().name
                except Exception:
                    _sname = "AFTERNOON"
                _smult = {
                    "PRE_OPEN": float(os.getenv("SIZE_MULT_PRE_OPEN", "0.5")),
                    "US_OPEN_CHOP": float(os.getenv("SIZE_MULT_US_OPEN", "0.5")),
                    "POST_OPEN": float(os.getenv("SIZE_MULT_POST_OPEN", "0.7")),
                    "MIDDAY": float(os.getenv("SIZE_MULT_MIDDAY", "0.85")),
                    "AFTERNOON": float(os.getenv("SIZE_MULT_AFTERNOON", "1.4")),
                    "OFF": float(os.getenv("SIZE_MULT_OFF", "0.5")),
                    "WEEKEND": float(os.getenv("SIZE_MULT_OFF", "0.5")),
                }.get(_sname, 1.0)
                _sh_pre = _sh
                _sh = max(float(os.getenv("SIZE_SH_FLOOR", "3")), round(_sh * _smult))
                logger.debug(
                    f"[SIZE-SESSION] {pred.coin}: {_sname} x{_smult:.2f} -> "
                    f"{_sh_pre:.0f}sh becomes {_sh:.0f}sh"
                )
            _entry = pred.entry_price if pred.entry_price > 0.05 else (pred.poly_price or 0.55)
            if _entry <= 0.01 or _entry >= 0.99:
                _entry = 0.55
            _size = _sh * _entry
            # bankroll safety cap
            _cap = bankroll * float(os.getenv("SIZE_MAX_PCT", "0.05"))
            if _cap > 0:
                _size = min(_size, _cap)
            _size = max(float(os.getenv("SIZE_MIN_USD", "1.50")), _size)
            logger.debug(
                f"[SIZE-DIST] {pred.coin}: |dist|={_adist*100:.3f}% -> {_sh:.0f}sh "
                f"@ {_entry*100:.0f}c = ${_size:.2f} (bankroll=${bankroll:.0f})"
            )
            return _size

        use_kelly = (_mode == "kelly") or os.getenv("USE_KELLY_SIZING", "false").lower() == "true"

        if use_kelly and pred.edge > 0:
            kelly_fraction = float(os.getenv("KELLY_FRACTION", "0.25"))
            kelly_min_bet = float(os.getenv("KELLY_MIN_BET", "2.0"))
            kelly_max_bet_env = float(os.getenv("KELLY_MAX_BET", "0"))
            pct_cap = bankroll * float(os.getenv("KELLY_MAX_PCT", "0.05"))
            if kelly_max_bet_env > 0:
                kelly_max_bet = min(kelly_max_bet_env, pct_cap) if pct_cap > 0 else kelly_max_bet_env
            else:
                kelly_max_bet = pct_cap if pct_cap > 0 else bankroll * 0.05

            entry_price = pred.entry_price if pred.entry_price > 0.05 else pred.poly_price
            if entry_price <= 0.01 or entry_price >= 0.99:
                entry_price = 0.50

            p = pred.probability
            q = 1.0 - p
            b = (1.0 / entry_price) - 1.0

            if b <= 0:
                return kelly_min_bet

            full_kelly = (b * p - q) / b
            if full_kelly <= 0:
                return kelly_min_bet

            fractional = full_kelly * kelly_fraction
            capped = min(fractional, 0.10)
            size = bankroll * capped
            size = max(kelly_min_bet, min(size, kelly_max_bet))

            logger.debug(
                f"[KELLY] {pred.coin}: f*={full_kelly:.3f} frac={fractional:.3f} "
                f"size=${size:.2f} (p={p:.0%} b={b:.2f} edge={pred.edge:.1%} "
                f"entry={entry_price:.2f} bankroll=${bankroll:.0f})"
            )
            return size

        base = bankroll * (config.BANKROLL_PERCENT / 100)
        if pred.confidence == "HIGH":
            mult = 1.5
        elif pred.confidence == "MEDIUM":
            mult = 1.0
        else:
            mult = 0.5
        size = min(base * mult, config.MAX_SINGLE_TRADE)
        return max(1.50, size)

    @staticmethod
    def _parse_result(result) -> tuple:
        matched = 0.0
        avg_price = 0.0
        order_id = None

        if isinstance(result, dict):
            order_id = result.get("orderID") or result.get("id")
            matched = float(result.get("takingAmount", 0) or result.get("matchedAmount", 0) or 0)
            making = float(result.get("makingAmount", 0) or 0)
            status = result.get("status", "")
            if status == "matched" and matched > 0 and making > 0:
                avg_price = making / matched
            else:
                avg_price = float(result.get("price", 0) or 0)
        elif hasattr(result, "orderID"):
            order_id = result.orderID
            matched = float(getattr(result, "takingAmount", 0) or getattr(result, "matchedAmount", 0) or 0)
            avg_price = float(getattr(result, "price", 0) or 0)

        return matched, avg_price, order_id
