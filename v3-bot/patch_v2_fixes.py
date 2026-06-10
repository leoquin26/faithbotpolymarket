"""
Patch V2 bot files on EC2:
1. Fix .env overrides (ENTRY_MIN, ENTRY_MAX)
2. Add diagnostic logging to silent gates in predictor.py
3. Ensure all plan items are correctly implemented
"""
import re

# ============================================================
# 1. PREDICTOR.PY — Add diagnostic logging to silent gates
# ============================================================

PREDICTOR_PY = r'''"""
V3 Predictor V2 — Rebuilt from profitable brain.py evaluation logic.

Uses momentum-projected direction, direction stability, choppiness detection,
logit jump-diffusion evidence, and multi-layer conviction gating.
"""

import math
import time
from dataclasses import dataclass
from typing import Optional, Dict, List, Tuple
from loguru import logger

import config
from market_data import MarketInfo, calculate_momentum


@dataclass
class Prediction:
    coin: str
    direction: str
    probability: float
    poly_price: float
    edge: float
    confidence: str
    reasoning: str
    token_id: str
    market_info: MarketInfo
    stale_price: bool = False
    stale_gap: float = 0.0
    conviction_strength: Optional[str] = None
    force_fok: bool = False
    entry_price: float = 0.0
    directional_edge: float = 0.0


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


SECS_PER_YEAR = 365.25 * 24 * 3600
FALLBACK_VOL_ANNUAL = {"BTC": 0.55, "ETH": 0.65, "SOL": 0.85, "XRP": 0.90}


class Predictor:

    def __init__(self):
        self._tick_stats: Dict[str, dict] = {}
        self._price_tick_history: Dict[str, List[Tuple[float, float]]] = {}
        self._last_poly_prices: Dict[str, list] = {}
        self._pred_count: Dict[str, int] = {}
        self._start_time: float = time.time()
        self._diag_last: Dict[str, float] = {}

    def _diag_log(self, key: str, msg: str, interval: float = 30.0):
        """Throttled diagnostic log — prints at most once per interval seconds per key."""
        now = time.time()
        if now - self._diag_last.get(key, 0) >= interval:
            logger.debug(msg)
            self._diag_last[key] = now

    # ==================================================================
    # TICK STATS
    # ==================================================================
    def update_tick(self, coin: str, price: float, ts: float = None):
        if ts is None:
            ts = time.time()
        if price <= 0:
            return

        if coin not in self._tick_stats:
            self._tick_stats[coin] = {
                "prices": [],
                "log_returns": [],
                "ewma_drift": 0.0,
                "realized_vol": 0.0,
                "last_price": price,
                "last_time": ts,
                "tick_count": 0,
            }
            return

        stats = self._tick_stats[coin]
        prev_price = stats["last_price"]
        prev_time = stats["last_time"]
        dt = ts - prev_time

        if dt <= 0 or prev_price <= 0:
            return

        log_ret = math.log(price / prev_price)

        stats["prices"].append((ts, price))
        stats["log_returns"].append((ts, log_ret, dt))
        stats["last_price"] = price
        stats["last_time"] = ts
        stats["tick_count"] += 1

        cutoff = ts - 300
        stats["prices"] = [(t, p) for t, p in stats["prices"] if t > cutoff]
        stats["log_returns"] = [(t, r, d) for t, r, d in stats["log_returns"] if t > cutoff]

        HALF_LIFE = 60.0
        alpha = 1.0 - math.exp(-dt / HALF_LIFE)
        alpha = max(0.001, min(alpha, 1.0))

        if dt > 0.01:
            instant_drift = log_ret / dt
            stats["ewma_drift"] = alpha * instant_drift + (1.0 - alpha) * stats["ewma_drift"]

        recent_cutoff = ts - 180
        recent = [(t, r, d) for t, r, d in stats["log_returns"] if t > recent_cutoff]

        if len(recent) >= 10:
            total_var = 0.0
            total_time = 0.0
            for _, r, d in recent:
                total_var += r * r
                total_time += d
            if total_time > 0:
                stats["realized_vol"] = math.sqrt(total_var / total_time)
                stats["realized_vol"] = max(1e-8, stats["realized_vol"])

    # ==================================================================
    # SIGMA: distance normalized by volatility + time
    # ==================================================================
    def _calculate_sigma(self, coin: str, abs_distance_pct: float,
                         time_remaining_min: float) -> float:
        stats = self._tick_stats.get(coin)

        if stats and stats["tick_count"] >= 30 and stats["realized_vol"] > 1e-10:
            vol_sec = stats["realized_vol"]
            ann_vol = vol_sec * math.sqrt(SECS_PER_YEAR)
            ann_vol = max(0.10, min(ann_vol, 5.0))
        else:
            ann_vol = FALLBACK_VOL_ANNUAL.get(coin, 0.70)

        time_years = max(time_remaining_min, 0.5) / (365.25 * 24 * 60)
        expected_move = ann_vol * math.sqrt(time_years)

        if expected_move > 0:
            return (abs_distance_pct * 100) / (expected_move * 100)
        return 0.0

    # ==================================================================
    # MOMENTUM SLOPE — linear regression on 90s of tick prices
    # ==================================================================
    def _compute_slope(self, coin: str) -> Optional[float]:
        stats = self._tick_stats.get(coin)
        if not stats or len(stats.get("prices", [])) < 15:
            return None

        now = time.time()
        ticks = [(t, p) for t, p in stats["prices"] if t > now - 90]
        if len(ticks) < 10:
            return None

        n = len(ticks)
        t_vals = [t for t, p in ticks]
        p_vals = [p for t, p in ticks]
        t_mean = sum(t_vals) / n
        p_mean = sum(p_vals) / n
        num = sum((t_vals[i] - t_mean) * (p_vals[i] - p_mean) for i in range(n))
        den = sum((t_vals[i] - t_mean) ** 2 for i in range(n))
        if den > 0:
            return num / den
        return None

    # ==================================================================
    # DIRECTION STABILITY — % of ticks on predicted side in last 90s
    # ==================================================================
    def _check_direction_stability(self, coin: str, direction: str,
                                   threshold: float) -> float:
        stats = self._tick_stats.get(coin)
        if not stats or len(stats.get("prices", [])) < 10:
            return 1.0

        now = time.time()
        recent = [p for t, p in stats["prices"] if t > now - 90]
        if len(recent) < 5:
            return 1.0

        ups = sum(1 for p in recent if p > threshold)
        if direction == "UP":
            return ups / len(recent)
        else:
            return (len(recent) - ups) / len(recent)

    # ==================================================================
    # CHOPPINESS — count threshold crossings in last 3 min
    # ==================================================================
    def _count_threshold_crossings(self, coin: str, threshold: float) -> int:
        stats = self._tick_stats.get(coin)
        if not stats or len(stats.get("prices", [])) < 20:
            return 0

        now = time.time()
        recent = [(t, p) for t, p in stats["prices"] if t > now - 180]
        if len(recent) < 20:
            return 0

        crossings = 0
        for i in range(1, len(recent)):
            if (recent[i][1] > threshold) != (recent[i - 1][1] > threshold):
                crossings += 1
        return crossings

    # ==================================================================
    # SPIKE FILTER
    # ==================================================================
    def _detect_poly_spike(self, coin: str, poly_price: float) -> bool:
        if coin not in self._last_poly_prices:
            self._last_poly_prices[coin] = []

        h = self._last_poly_prices[coin]
        h.append(poly_price)
        if len(h) > 5:
            h.pop(0)
        if len(h) < 3:
            return False

        prev_avg = sum(h[:-1]) / len(h[:-1])
        return abs(poly_price - prev_avg) > 0.10

    # ==================================================================
    # MAIN PREDICT — rebuilt from brain.py's bayesian_evaluate()
    # ==================================================================
    def predict(self, info: MarketInfo) -> Optional[Prediction]:
        coin = info.coin
        bp = info.current_crypto_price
        tp = info.threshold_price
        distance = info.distance_percent
        abs_distance = abs(distance)
        time_left = info.time_remaining
        time_left_sec = time_left * 60.0

        self.update_tick(coin, bp)

        stats = self._tick_stats.get(coin)
        tick_count = stats["tick_count"] if stats else 0

        # ── WARMUP: need 30s of tick history ──
        warmup_elapsed = time.time() - self._start_time
        if warmup_elapsed < 30:
            self._diag_log(f"warmup-{coin}", f"[WARMUP] {coin}: {warmup_elapsed:.0f}s < 30s", 10.0)
            return None

        # ── SIGMA PRE-FILTER: price hasn't moved enough ──
        sigma = self._calculate_sigma(coin, abs_distance, time_left)
        if sigma < 0.15:
            self._diag_log(
                f"sigma-{coin}",
                f"[SIGMA] {coin}: sigma={sigma:.3f} < 0.15 | dist={abs_distance:.6f} "
                f"ticks={tick_count} time={time_left:.1f}m",
                30.0,
            )
            return None

        # ── MOMENTUM SLOPE (90s linear regression) ──
        slope = self._compute_slope(coin)
        instant_signal = (bp - tp) / tp if bp > 0 and tp > 0 else 0.0

        if slope is not None and bp > 0 and tp > 0:
            proj_horizon = min(time_left_sec, 120.0)
            projected_price = bp + slope * proj_horizon
            projected_signal = (projected_price - tp) / tp
            blended_signal = 0.40 * instant_signal + 0.60 * projected_signal
        else:
            projected_signal = instant_signal
            blended_signal = instant_signal

        # ── DIRECTION from projected trajectory ──
        direction = "UP" if blended_signal > 0 else "DOWN"
        is_up = direction == "UP"
        instant_dir = "UP" if instant_signal > 0 else "DOWN"

        if direction != instant_dir and slope is not None:
            logger.info(
                f"[MOMENTUM FLIP] {coin}: price says {instant_dir} but "
                f"momentum projects {direction} (slope={slope:.8f})"
            )

        # ── CHOPPINESS GATE: too many threshold crossings ──
        crossings = self._count_threshold_crossings(coin, tp)
        if crossings > 15:
            logger.info(f"[CHOPPY] {coin}: {crossings} crossings > 15 — mean-reverting, skip")
            return None

        # ── DIRECTION STABILITY: 55%+ ticks must agree ──
        stability = self._check_direction_stability(coin, direction, tp)
        if stability < 0.55:
            self._diag_log(
                f"unstable-{coin}",
                f"[UNSTABLE] {coin} {direction}: {stability:.0%} < 55% | "
                f"sigma={sigma:.2f} blended={blended_signal:.6f}",
                30.0,
            )
            return None

        # ── NOISE SIGNAL GATE ──
        if abs(blended_signal) < 0.0015:
            self._diag_log(
                f"noise-{coin}",
                f"[NOISE] {coin} {direction}: |blended|={abs(blended_signal):.6f} < 0.0015 | "
                f"sigma={sigma:.2f} stab={stability:.0%}",
                30.0,
            )
            return None

        # ── MINIMUM WINDOW AGE (dynamic) ──
        window_age = max(0, int(time.time()) - info.window_start) if info.window_start else 0
        ann_vol = FALLBACK_VOL_ANNUAL.get(coin, 0.70)
        expected_move = ann_vol * math.sqrt(max(time_left_sec, 60) / SECS_PER_YEAR)
        expected_move = max(expected_move, 1e-6)
        dist_ratio = abs_distance / expected_move
        dist_bonus = min(dist_ratio * 120, 180)
        min_window_age = max(config.MIN_WINDOW_AGE, int(180 - dist_bonus))
        if window_age < min_window_age:
            self._diag_log(
                f"early-{coin}",
                f"[EARLY] {coin} {direction}: age={window_age}s < {min_window_age}s | "
                f"sigma={sigma:.2f} dist_ratio={dist_ratio:.2f}",
                30.0,
            )
            return None

        # ── POLY PRICE + TOKEN ID ──
        prior = info.up_poly_price if is_up else info.down_poly_price
        prior = max(0.01, min(0.99, prior or 0.50))
        poly_price = prior
        token_id = info.up_token_id if is_up else info.down_token_id

        # ── PRIOR DISAGREEMENT: market says >55% opposite ──
        if prior < 0.45:
            logger.info(
                f"[PRIOR DISAGREE] {coin} {direction}: prior={prior:.2f} — "
                f"Polymarket strongly disagrees, skip"
            )
            return None

        # ── SPIKE FILTER ──
        if self._detect_poly_spike(coin, poly_price):
            return None

        # ── LOGIT JUMP-DIFFUSION (from brain.py) ──
        z = 0.0
        if bp > 0 and tp > 0:
            vol_sec = stats["realized_vol"] if (stats and stats["realized_vol"] > 1e-10) else 0
            if vol_sec < 1e-10:
                _ann_vol = FALLBACK_VOL_ANNUAL.get(coin, 0.70)
            else:
                _ann_vol = min(5.0, max(0.10, vol_sec * math.sqrt(SECS_PER_YEAR)))
            _exp_move = _ann_vol * math.sqrt(max(time_left_sec, 1.0) / SECS_PER_YEAR)
            _exp_move = max(_exp_move, 1e-6)

            z = blended_signal / _exp_move
            z = max(-3.0, min(3.0, z))
            p_up = _norm_cdf(z)
        else:
            p_up = 0.50

        evidence_prob = p_up if is_up else (1.0 - p_up)
        evidence_prob = 0.50 + (evidence_prob - 0.50) * 0.90

        # ── EVIDENCE FLOOR: model must have real conviction ──
        if evidence_prob < 0.55:
            self._diag_log(
                f"lowrule-{coin}",
                f"[LOW RULE] {coin} {direction}: evidence={evidence_prob:.0%} < 55% | "
                f"z={z:+.2f} sigma={sigma:.2f}",
                30.0,
            )
            return None

        # ── BAYESIAN UPDATE ──
        numerator = evidence_prob * prior
        denominator = evidence_prob * prior + (1.0 - evidence_prob) * (1.0 - prior)
        posterior = numerator / max(denominator, 0.001)
        posterior = max(0.01, min(0.85, posterior))

        # ── TIME DECAY: regress toward 50% in last 5 min ──
        if time_left < 5.0:
            decay = min(time_left / 5.0, 1.0)
            decay = max(decay, 0.50)
            posterior = 0.50 + (posterior - 0.50) * decay

        # ── CONVICTION GATE ──
        if posterior < config.MIN_CONVICTION:
            self._diag_log(
                f"lowconv-{coin}",
                f"[LOW CONV] {coin} {direction}: post={posterior:.2f} < "
                f"{config.MIN_CONVICTION:.0%} | z={z:+.2f} prior={prior:.2f}",
                30.0,
            )
            return None

        # ── DIRECTIONAL EDGE GATE ──
        directional_edge = posterior - 0.50
        if directional_edge < config.MIN_DIRECTIONAL_EDGE:
            logger.info(
                f"[WEAK DIR] {coin} {direction}: post={posterior:.2f} "
                f"dir_edge={directional_edge*100:.1f}% < "
                f"{config.MIN_DIRECTIONAL_EDGE*100:.0f}%"
            )
            return None

        # ── ENTRY PRICE (poly_price as fallback, CLOB ask injected by run_bot) ──
        entry_price = poly_price

        # ── MARKET DISAGREES GATES ──
        if entry_price < 0.35:
            logger.info(
                f"[MARKET SAYS NO] {coin} {direction}: entry={entry_price*100:.0f}c < 35c"
            )
            return None
        if entry_price < 0.40 and directional_edge < 0.08:
            logger.info(
                f"[MARKET WARNS] {coin} {direction}: entry={entry_price*100:.0f}c + "
                f"dir_edge={directional_edge*100:.1f}% < 8%"
            )
            return None

        # ── EDGE against entry price ──
        edge = posterior - entry_price

        # ── ENTRY EDGE minimum ──
        if edge < 0.02:
            self._diag_log(
                f"lowedge-{coin}",
                f"[LOW EDGE] {coin} {direction}: edge={edge*100:.1f}% < 2% | "
                f"post={posterior:.2f} entry={entry_price:.2f}",
                30.0,
            )
            return None

        # ── MOMENTUM CONFIRMATION ──
        momentum = calculate_momentum(info.coin)
        mom_confirms = False
        if momentum:
            change_5m = momentum.get("change_5m", 0)
            mom_confirms = (
                (is_up and change_5m > 0) or
                (not is_up and change_5m < 0)
            )

        # ── CONFIDENCE ASSIGNMENT ──
        confidence = "LOW"
        reasons = []
        reasons.append(f"z={z:+.2f}")
        reasons.append(f"post={posterior*100:.0f}%")
        reasons.append(f"prior={prior*100:.0f}c")
        reasons.append(f"evid={evidence_prob:.2f}")
        reasons.append(f"stab={stability:.0%}")
        reasons.append(f"sigma={sigma:.2f}")

        if edge >= config.MIN_EDGE and directional_edge >= config.MIN_DIRECTIONAL_EDGE:
            if mom_confirms and directional_edge >= 0.08:
                confidence = "HIGH"
                reasons.append("STRONG")
            else:
                confidence = "MEDIUM"
                reasons.append("SOLID")

        reasoning = " | ".join(reasons)

        logger.info(
            f"[PRED] {coin} {direction} | z={z:+.2f} | "
            f"Post={posterior*100:.0f}% | Poly={poly_price*100:.0f}c | "
            f"Edge={edge*100:.1f}% | DirEdge={directional_edge*100:.1f}% | "
            f"{confidence} | stab={stability:.0%} | sigma={sigma:.2f}"
        )

        return Prediction(
            coin=coin, direction=direction, probability=posterior,
            poly_price=poly_price, edge=edge, confidence=confidence,
            reasoning=reasoning, token_id=token_id, market_info=info,
            entry_price=entry_price, directional_edge=directional_edge,
        )
'''

# ============================================================
# 2. Write files
# ============================================================

with open("/home/ubuntu/v3-bot/predictor.py", "w") as f:
    f.write(PREDICTOR_PY)
print("[OK] predictor.py written")

# ============================================================
# 3. Fix .env overrides
# ============================================================
env_path = "/home/ubuntu/v3-bot/.env"
with open(env_path, "r") as f:
    env_content = f.read()

# Fix ENTRY_MIN and ENTRY_MAX
env_content = re.sub(r'^ENTRY_MIN=.*$', 'ENTRY_MIN=0.35', env_content, flags=re.MULTILINE)
env_content = re.sub(r'^ENTRY_MAX=.*$', 'ENTRY_MAX=0.72', env_content, flags=re.MULTILINE)
env_content = re.sub(r'^ABSOLUTE_MAX_ENTRY=.*$', 'ABSOLUTE_MAX_ENTRY=0.72', env_content, flags=re.MULTILINE)
env_content = re.sub(r'^MAX_ENTRY_PRICE=.*$', 'MAX_ENTRY_PRICE=0.72', env_content, flags=re.MULTILINE)
env_content = re.sub(r'^SAFETY_MAX_ENTRY=.*$', 'SAFETY_MAX_ENTRY=0.72', env_content, flags=re.MULTILINE)
env_content = re.sub(r'^DAILY_LOSS_LIMIT=.*$', 'DAILY_LOSS_LIMIT=15', env_content, flags=re.MULTILINE)

with open(env_path, "w") as f:
    f.write(env_content)
print("[OK] .env updated: ENTRY_MIN=0.35, ENTRY_MAX=0.72, DAILY_LOSS_LIMIT=15")

print("\n[DONE] All V2 patches applied.")
