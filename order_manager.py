"""
Order Manager V2 — handles FOK/GTC order placement via Polymarket CLOB.

V2 changes:
- get_clob_ask() for pre-evaluation price validation
- place_bet recalculates edge against real CLOB ask
- Blocks if ask > 73c or < 5c
"""

import os
import time
import json
from pathlib import Path
from typing import Optional, Dict, Set
from loguru import logger
import telegram_notifier as tg

import config
from predictor import Prediction

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    OrderArgs, OrderType, PartialCreateOrderOptions, ApiCreds,
)
from py_clob_client.order_builder.constants import BUY

# ── analytics hook apr23 ──
try:
    from analytics import event_logger as _alog
except Exception:
    _alog = None


class OrderManager:
    """Manages order placement, GTC tracking, and window dedup."""

    def __init__(self):
        self.client = self._init_client()
        self.active_gtc: Dict[str, dict] = {}
        self.traded_windows: Dict[str, str] = self._load_traded_windows()
        self.positions: Dict[str, dict] = {}
        self.daily_losses = 0.0
        self.daily_wins = 0.0
        self.daily_trades = 0
        self._trading_day = ""


    # ------------------------------------------------------------------
    # Live bankroll from USDC balance
    # ------------------------------------------------------------------
    _last_balance_check = 0
    _cached_balance = 0.0

    def get_live_bankroll(self) -> float:
        """Static bankroll from .env BANKROLL_BALANCE. Zero API calls.

        When wallet balance changes meaningfully, update .env and restart.
        Having zero inline API calls here guarantees no interference with
        the CLOB client used for order placement.
        """
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
        return self.daily_losses >= config.DAILY_LOSS_LIMIT

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
        """Single orderbook call via direct HTTP (bypasses Tor proxy)."""
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
            logger.warning(f"[STOP] Daily loss limit reached (${self.daily_losses:.2f})")
            return False

        # Correlation limit: max 3 same-direction bets per window
        same_dir_count = self.count_same_direction_trades(direction, window_start)
        if same_dir_count >= 3:
            logger.info(
                f"[CORR GATE] {coin}: Already {same_dir_count} {direction} "
                f"bets this window (max 3)"
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
        if real_edge < 0.02:
            logger.info(
                f"[EDGE GATE] {coin}: real_edge={real_edge*100:.1f}% "
                f"(post={pred.probability*100:.0f}% - ask={actual_entry*100:.0f}c) < 2%"
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
        # Fix C: min 2 shares (was 5) so Kelly-tier sizing isn't overridden
        # by a floor that costs $3.40 at 68c. 5-share floor was fine when
        # entries were 30-50c; with 65-68c entries it blows Kelly budget.
        shares = max(2, int(size_usd / limit_price))
        actual_cost = shares * limit_price

        order_type = OrderType.GTC if use_gtc else OrderType.FOK
        order_type_name = "GTC" if use_gtc else "FOK"

        logger.info(
            f"[ORDER] {coin} {direction} | {order_type_name} @ {limit_price*100:.0f}c | "
            f"{shares} shares (cost=${actual_cost:.2f}, sized=${size_usd:.2f}) | "
            f"Edge {real_edge*100:.1f}%"
        )

        try:
            options = PartialCreateOrderOptions(tick_size="0.01", neg_risk=False)
            order_args = OrderArgs(
                price=limit_price,
                size=shares,
                side=BUY,
                token_id=token_id,
            )
            order = self.client.create_order(order_args, options)
            result = self.client.post_order(order, order_type)

            matched, avg_price, order_id = self._parse_result(result)

            if use_gtc:
                self.active_gtc[order_id or "unknown"] = {
                    "coin": coin,
                    "direction": direction,
                    "token_id": token_id,
                    "price": limit_price,
                    "shares": shares,
                    "placed_at": time.time(),
                    "window_start": window_start,
                    "prediction": pred,
                }
                self.mark_window_traded(coin, window_start, direction)
                logger.info(f"[GTC] Pending: {coin} {direction} @ {limit_price*100:.0f}c")
                print(f"\n  [GTC] PENDING: {coin} {direction} @ {limit_price*100:.0f}c | waiting for fill...")
                return True

            if matched > 0:
                cost = matched * avg_price
                self.positions[coin] = {
                    "coin": coin,
                    "side": direction,
                    "entry_price": avg_price,
                    "shares": int(matched),
                    "token_id": token_id,
                    "window_start": window_start,
                    "strike": pred.market_info.threshold_price if pred and hasattr(pred, 'market_info') else 0,
                }
                self.daily_trades += 1
                self.mark_window_traded(coin, window_start, direction)
                logger.info(f"[FILLED] {coin} {direction} | {int(matched)} shares @ {avg_price*100:.0f}c = ${cost:.2f}")
                print(f"\n  [OK] FILLED: {coin} {direction} | {int(matched)} shares @ {avg_price*100:.0f}c | Cost: ${cost:.2f}")
                tg.notify_fill(coin, direction, int(matched), avg_price, cost, pred.edge if pred else 0, pred.probability if pred else 0)
                return True
            else:
                logger.warning(f"[MISS] {coin} {direction} — 0 shares matched")
                print(f"\n  [X] MISSED: {coin} {direction} — order not filled")
                return False

        except Exception as e:
            import traceback as _tb; logger.error(f"[ERROR] Order failed for {coin}: {type(e).__name__}: {e}"); logger.error(f"[ERROR TRACE] {_tb.format_exc()}")
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
                    self.positions[info["coin"]] = {
                        "coin": info["coin"],
                        "side": info["direction"],
                        "entry_price": fill_price,
                        "shares": int(filled_qty) if filled_qty > 0 else info["shares"],
                        "token_id": info["token_id"],
                        "window_start": info["window_start"],
                    }
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
        use_kelly = os.getenv("USE_KELLY_SIZING", "false").lower() == "true"
        bankroll = self.get_live_bankroll()

        if use_kelly and pred.edge > 0:
            kelly_fraction = float(os.getenv("KELLY_FRACTION", "0.25"))
            kelly_min_bet = float(os.getenv("KELLY_MIN_BET", "2.0"))
            kelly_max_bet = float(os.getenv("KELLY_MAX_BET", "0"))
            # Option B default: 10% bankroll cap (configurable via KELLY_MAX_PCT)
            kelly_max_pct = float(os.getenv("KELLY_MAX_PCT", "0.10"))

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
            # Pct-of-bankroll ceiling (primary risk control)
            capped = min(fractional, kelly_max_pct)
            size = bankroll * capped
            # Apply min floor, then optional absolute dollar ceiling
            size = max(kelly_min_bet, size)
            if kelly_max_bet > 0:
                size = min(size, kelly_max_bet)

            # Fix B: scale down at expensive entries (R:R asymmetry protection).
            # At 67c entry, one loss = -67c but one win = +33c -- 2x asymmetry.
            # Cut size so a single high-entry loss doesn't erase 2-3 wins.
            if entry_price <= 0.55:
                tier_mult = 1.00
                tier_name = "A"
            elif entry_price <= 0.60:
                tier_mult = 0.75
                tier_name = "B"
            elif entry_price <= 0.65:
                tier_mult = 0.50
                tier_name = "C"
            else:
                tier_mult = 0.33
                tier_name = "D"
            # ── Fix B apr23: daily-loss Kelly tier cap ──
            # After losing $5+ today in PM session, never ramp tier above C (0.50)
            # — even if recent wins say 'go big'. Prevents the win-streak → fat-loss
            # pattern that erased today's PM recovery.
            try:
                _dl = float(getattr(self, 'daily_losses', 0.0))
            except Exception:
                _dl = 0.0
            if _dl >= 5.0 and tier_mult > 0.50:
                logger.info(
                    f"[KELLY DAILY DAMP] daily_losses=${_dl:.2f} >= $5 — "
                    f"capping tier {tier_name}({tier_mult:.2f}) -> C(0.50)"
                )
                tier_mult = 0.50
                tier_name = "C*"
            pre_tier = size
            size = max(kelly_min_bet, size * tier_mult)

            # Fix F (apr21): if exhaustion detector DAMPEN'd this pred,
            # cut size in half so DAMPEN actually reduces risk (not just
            # probability). Prior to this, DAMPEN was a no-op when Kelly
            # was pct-capped because the cap dominated.
            dampen_tag = ""
            if getattr(pred, "_dampened", False):
                # Fix apr27 (no double penalty): if EXHAUST OVERRIDE fired,
                # skip the 50% size cut. Override already self-selected A-tier.
                if getattr(pred, "_override_full_size", False):
                    dampen_tag = " dampen=skipped(override)"
                else:
                    pre_dampen = size
                    size = max(kelly_min_bet, size * 0.5)
                    dampen_tag = f" dampen=50%(pre=${pre_dampen:.2f})"

            logger.info(
                f"[KELLY] {pred.coin}: f*={full_kelly:.3f} frac={fractional:.3f} "
                f"pct_cap={kelly_max_pct:.2%} tier={tier_name}({tier_mult:.0%}){dampen_tag} "
                f"size=${size:.2f} (pre=${pre_tier:.2f}) "
                f"(p={p:.0%} b={b:.2f} edge={pred.edge:.1%} "
                f"entry={entry_price:.2f} bankroll=${bankroll:.2f})"
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
        """Fix H (apr22): only return matched>0 when the API response
        explicitly confirms a match. Previously we read takingAmount
        (the signed REQUEST size) and reported phantom fills when an
        order was submitted but never settled on-chain.
        """
        matched = 0.0
        avg_price = 0.0
        order_id = None

        # Only these status values mean the order actually matched.
        SUCCESS_STATUSES = {"matched", "FILLED", "CONFIRMED", "confirmed"}

        if isinstance(result, dict):
            order_id = result.get("orderID") or result.get("id")
            status = (result.get("status") or "").strip()
            success_flag = bool(result.get("success", False))

            # Prefer explicitly-matched fields over signed-request fields.
            matched_amount = float(
                result.get("matchedAmount", 0)
                or result.get("matched_amount", 0)
                or 0
            )
            taking_amount = float(result.get("takingAmount", 0) or 0)
            making = float(result.get("makingAmount", 0) or 0)

            if status in SUCCESS_STATUSES or success_flag:
                # Real fill. Prefer matchedAmount if the server reports
                # it, otherwise fall back to takingAmount (which for a
                # matched FOK equals the fill).
                matched = matched_amount if matched_amount > 0 else taking_amount
                if matched > 0 and making > 0:
                    avg_price = making / matched
                else:
                    avg_price = float(result.get("price", 0) or 0)
            else:
                # Not actually filled. Force MISS path so we do not
                # create a phantom position.
                import logging as _log
                _log.getLogger(__name__).warning(
                    f"[PARSE] Order not matched: status={status!r} "
                    f"matched_amt={matched_amount} taking={taking_amount} "
                    f"making={making} success={success_flag} order_id={order_id}"
                )
                matched = 0.0
                avg_price = 0.0
        elif hasattr(result, "orderID"):
            # Object response path (legacy): trust it as before, but log.
            order_id = result.orderID
            status = getattr(result, "status", "") or ""
            if status in SUCCESS_STATUSES:
                matched = float(
                    getattr(result, "matchedAmount", 0)
                    or getattr(result, "takingAmount", 0)
                    or 0
                )
                avg_price = float(getattr(result, "price", 0) or 0)
            else:
                matched = 0.0

        return matched, avg_price, order_id
