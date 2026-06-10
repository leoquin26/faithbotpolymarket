"""
Morning Strategy Predictor (9am-2pm).
Designed for choppy/reversal markets with:
- Late entry (6+ min into window) to confirm direction
- Higher trend threshold to avoid noise
- Direction streak requirement (2+ same-direction windows)
- Mean-reversion awareness
"""
import math
import time
from typing import Optional, Dict, List, Tuple
from loguru import logger

import config
from market_data import MarketInfo
from predictor import (
    Prediction, EWMAVolatility, MomentumAnalyzer, ChopDetector,
    _bs_binary_prob, _sigmoid
)


class MorningPredictor:
    """Conservative predictor for choppy morning markets."""

    TREND_THRESHOLD = 0.60      # need strong trend (main uses 0.40)
    MIN_WINDOW_AGE = 360        # wait 6 min into window
    LATE_BLOCK = 120            # stop 2 min before end
    MIN_WIN_PROB = 0.78         # higher prob requirement
    MIN_EDGE = 0.08             # higher edge requirement
    MIN_TICKS = 30
    STREAK_REQUIRED = 2         # need 2 same-direction windows in a row

    def __init__(self, main_predictor):
        self._main = main_predictor
        self._direction_history: List[str] = []
        self._max_history = 6
        self._diag_last: Dict[str, float] = {}
        self._window_traded: Dict[str, int] = {}
        self._load_history()

    def _load_history(self):
        try:
            import json
            with open("/home/ubuntu/v3-bot/morning_dir_state.json", "r") as f:
                self._direction_history = json.load(f).get("history", [])[-self._max_history:]
            logger.info(f"[MORNING] Loaded direction history: {' -> '.join(self._direction_history[-4:])}")
        except Exception:
            pass

    def _save_history(self):
        try:
            import json
            with open("/home/ubuntu/v3-bot/morning_dir_state.json", "w") as f:
                json.dump({"history": self._direction_history}, f)
        except Exception:
            pass

    def _record_direction(self, direction: str):
        self._direction_history.append(direction)
        if len(self._direction_history) > self._max_history:
            self._direction_history.pop(0)
        self._save_history()

    def _has_direction_streak(self, direction: str) -> bool:
        if len(self._direction_history) < self.STREAK_REQUIRED:
            return False
        recent = self._direction_history[-self.STREAK_REQUIRED:]
        return all(d == direction for d in recent)

    def _diag_log(self, key: str, msg: str, interval: float = 15.0):
        now = time.time()
        if now - self._diag_last.get(key, 0) >= interval:
            logger.info(msg)
            self._diag_last[key] = now

    def is_window_traded(self, coin: str, window_start: int) -> bool:
        return self._window_traded.get(coin) == window_start

    def predict(self, info: MarketInfo, *,
                ws_price: float = 0.0,
                ticks: Optional[List[Tuple[float, float]]] = None,
                up_ask: float = 0.0, down_ask: float = 0.0,
                up_mid: float = 0.0, down_mid: float = 0.0,
                up_depth: float = 0.0, down_depth: float = 0.0,
                realized_vol: float = 0.0,
                **kwargs) -> Optional[Prediction]:

        coin = info.coin
        current_price = ws_price if ws_price > 0 else info.current_crypto_price
        strike = info.threshold_price
        now_ts = int(time.time())
        window_start = info.window_start or 0
        window_end = window_start + 900
        time_remaining = max(1.0, window_end - now_ts)
        window_age = max(0, now_ts - window_start)

        if current_price <= 0 or strike <= 0:
            return None

        # Late entry: wait 6 minutes into the window
        if window_age < self.MIN_WINDOW_AGE:
            self._diag_log(
                f"morn-wait-{coin}",
                f"[MORNING WAIT] {coin}: {window_age}s < {self.MIN_WINDOW_AGE}s — waiting for confirmation",
                30.0
            )
            return None

        if time_remaining < self.LATE_BLOCK:
            return None

        # Already traded this window?
        if self.is_window_traded(coin, window_start):
            return None

        # Feed ticks into main predictor's analyzers
        if ticks and len(ticks) > 0:
            self._main.feed_ticks(coin, ticks)

        ewma = self._main._get_ewma(coin)
        mom = self._main._get_momentum(coin)

        if ewma.tick_count < self.MIN_TICKS:
            return None

        sigma = ewma.get_sigma()
        if sigma < 1e-05:
            sigma = 1e-05

        # Cold streak from main predictor
        if self._main._recent_accuracy() < 0.45:
            self._diag_log(f"morn-cold-{coin}", f"[MORNING COLD] accuracy={self._main._recent_accuracy():.0%}", 30.0)
            return None

        momentum_raw = mom.get_momentum()
        roc_60 = mom._roc(60)
        roc_120 = mom._roc(120)
        roc_300 = mom._roc(300)  # 5 min trend for extra confirmation

        if roc_60 == 0.0 and roc_120 == 0.0 and momentum_raw == 0.0:
            return None

        dist_pct = (current_price - strike) / strike if strike > 0 else 0.0

        # Trend score same formula as main
        trend_score = 0.0
        trend_score += dist_pct * 200.0
        trend_score += roc_60 * 500.0
        trend_score += roc_120 * 300.0
        trend_score += momentum_raw * 400.0
        trend_score += roc_300 * 200.0  # extra: 5min trend weight

        # Morning requires STRONGER trend
        if abs(trend_score) < self.TREND_THRESHOLD:
            self._diag_log(
                f"morn-weak-{coin}",
                f"[MORNING WEAK] {coin}: trend={trend_score:+.2f} < {self.TREND_THRESHOLD} — too weak for morning",
                15.0,
            )
            return None

        # Mean reversion check: if price stretched too far, it might snap back
        reversion = mom.get_reversion()
        if abs(reversion) > 0.005:
            self._diag_log(
                f"morn-rev-{coin}",
                f"[MORNING REV RISK] {coin}: reversion={reversion*10000:+.1f}bps — stretched, reversal likely",
                15.0,
            )
            return None

        # Direction
        base_up_prob = _bs_binary_prob(current_price, strike, sigma, time_remaining)
        raw_prob = _sigmoid(trend_score * 3.0)
        combined_prob = 0.70 * raw_prob + 0.30 * base_up_prob
        combined_prob = max(0.01, min(0.99, combined_prob))

        is_up = combined_prob >= 0.5
        direction = "UP" if is_up else "DOWN"
        win_prob = combined_prob if is_up else (1.0 - combined_prob)
        ask = up_ask if is_up else down_ask
        token_id = info.up_token_id if is_up else info.down_token_id
        depth = up_depth if is_up else down_depth

        # Direction streak check: need 2+ consecutive same-direction windows
        if not self._has_direction_streak(direction):
            self._diag_log(
                f"morn-streak-{coin}",
                f"[MORNING NO STREAK] {coin} {direction}: need {self.STREAK_REQUIRED} consecutive "
                f"same-direction windows, have: {' -> '.join(self._direction_history[-3:])}",
                15.0,
            )
            self._record_direction(direction)
            return None

        # Higher probability requirement
        if win_prob < self.MIN_WIN_PROB:
            self._diag_log(
                f"morn-prob-{coin}",
                f"[MORNING LOW PROB] {coin} {direction}: {win_prob:.0%} < {self.MIN_WIN_PROB:.0%}",
                15.0,
            )
            return None

        # Entry price filters
        entry_min = getattr(config, "ENTRY_MIN", 0.10)
        entry_max = getattr(config, "ENTRY_MAX", 0.68)
        if ask <= 0.01 or ask < entry_min or ask > entry_max:
            return None

        edge = win_prob - ask
        if edge < self.MIN_EDGE:
            self._diag_log(
                f"morn-edge-{coin}",
                f"[MORNING LOW EDGE] {coin} {direction}: edge={edge*100:.1f}% < {self.MIN_EDGE*100:.0f}%",
                15.0,
            )
            return None

        confidence = "MEDIUM"
        reasoning = (
            f"MORNING | trend={trend_score:+.2f} dist={dist_pct*100:+.3f}% "
            f"roc60={roc_60*10000:+.1f}bps roc300={roc_300*10000:+.1f}bps "
            f"streak={' -> '.join(self._direction_history[-3:])} "
            f"prob={win_prob:.0%} ask={ask*100:.0f}c edge={edge*100:.1f}%"
        )

        logger.info(
            f"[MORNING SIGNAL] {coin} {direction} | Prob={win_prob:.0%} | Ask={ask*100:.0f}c | "
            f"Edge={edge*100:.1f}% | Trend={trend_score:+.2f} | "
            f"Streak={' -> '.join(self._direction_history[-3:])} | Age={window_age}s"
        )

        self._record_direction(direction)
        self._window_traded[coin] = window_start

        return Prediction(
            coin=coin,
            direction=direction,
            probability=win_prob,
            poly_price=ask,
            edge=edge,
            confidence=confidence,
            reasoning=reasoning,
            token_id=token_id,
            market_info=info,
            entry_price=ask,
            mc_prob=win_prob,
            depth_ratio=depth,
            directional_edge=win_prob - 0.50,
            force_fok=True,  # morning trades use FOK only, no GTC
        )
