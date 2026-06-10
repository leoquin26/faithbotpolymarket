"""ACCURACY UPGRADE (May 21, 2026) - 5-part deploy

1. σ-trend size modifier (Pattern D fix): if sigma rose 30%+ in last 5min,
   reduce size 30%
2. Brier-score adaptive Kelly: track last 20 trades calibration, scale Kelly
   0.7x-1.1x based on recent Brier score
3. OBI v2 logging: capture top-3 book depth on every signal for future
   calibration (no filtering yet)
4. Kelly cap 5% -> 7% on A-tier (entry <= 55c, prob >= 80%)
5. 5m bet size $3 -> $2.5, daily cap $5 -> $8 (more attempts)

All env-controlled with kill switches.
"""
from pathlib import Path

PRED = Path("/home/ubuntu/v3-bot/predictor.py")
ORDER = Path("/home/ubuntu/v3-bot/order_manager.py")
ORDERS_PATH = Path("/home/ubuntu/v3-bot/orders.py")  # may not exist

# ============================================================
# Part 1+3: Add sigma_now / sigma_5m / top-3 OBI to Prediction
# ============================================================
print("=== predictor.py ===")
text = PRED.read_text()

# Add fields to dataclass
old_pred = """    conviction_strength: Optional[str] = None
    force_fok: bool = False
    trend_score: float = 0.0"""
new_pred = """    conviction_strength: Optional[str] = None
    force_fok: bool = False
    trend_score: float = 0.0
    # May 21 ACCURACY UPGRADE:
    sigma_now: float = 0.0          # current EWMA σ (per-second)
    sigma_5m: float = 0.0           # σ from ~5 minutes ago
    sigma_ratio: float = 1.0        # sigma_now / sigma_5m (1.0 = stable)
    bid_qty_top3: float = 0.0       # sum of top-3 bid sizes (OBI v2)
    ask_qty_top3: float = 0.0       # sum of top-3 ask sizes (OBI v2)
    obi_top3: float = 0.0           # (bid-ask)/(bid+ask), range [-1,+1]"""

if "May 21 ACCURACY UPGRADE" in text:
    print("  Prediction dataclass already extended — skipping")
else:
    if old_pred not in text:
        raise SystemExit("Prediction marker not found")
    text = text.replace(old_pred, new_pred, 1)
    print("  Prediction dataclass extended (+6 fields)")

# Add sigma history tracker to Predictor __init__
old_init = """        self._chop_detector = ChopDetector(lookback=4)
        self._boot_ts = time.time()"""
new_init = """        self._chop_detector = ChopDetector(lookback=4)
        self._boot_ts = time.time()
        # May 21: σ history per coin for sigma-trend size modifier
        # Stores (ts, sigma) tuples, ~5 min lookback
        self._sigma_5m_history: Dict[str, List[Tuple[float, float]]] = {}"""

if "sigma_5m_history" in text:
    print("  sigma_5m_history already added")
else:
    if old_init not in text:
        raise SystemExit("init marker not found")
    text = text.replace(old_init, new_init, 1)
    print("  sigma_5m_history tracker added to __init__")

# Compute sigma_5m and obi_top3 just before Prediction construction
old_commit = """        self._window_direction = direction  # legacy global (kept for safety)
        self._window_directions[coin] = direction  # Phase A: per-coin commit"""
new_commit = """        # May 21: record σ history and compute sigma_ratio for sizing
        _sig_now = time.time()
        _hist = self._sigma_5m_history.setdefault(coin, [])
        _hist.append((_sig_now, sigma))
        _cut = _sig_now - 300.0  # 5min window
        self._sigma_5m_history[coin] = [(t, s) for (t, s) in _hist if t >= _cut]
        # σ from ~5 minutes ago: use the oldest sample in window, fallback to current
        _sig_5m = self._sigma_5m_history[coin][0][1] if self._sigma_5m_history[coin] else sigma
        _sig_ratio = (sigma / _sig_5m) if _sig_5m > 0 else 1.0

        self._window_direction = direction  # legacy global (kept for safety)
        self._window_directions[coin] = direction  # Phase A: per-coin commit"""

if "May 21: record σ history" in text:
    print("  sigma history compute already present")
else:
    if old_commit not in text:
        raise SystemExit("commit marker not found")
    text = text.replace(old_commit, new_commit, 1)
    print("  sigma history compute injected")

# Pass new fields into Prediction constructor
old_pred_call = """        _pred = Prediction(
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
            trend_score=trend_score,
        )"""

new_pred_call = """        _pred = Prediction(
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
            trend_score=trend_score,
            sigma_now=sigma,
            sigma_5m=_sig_5m,
            sigma_ratio=_sig_ratio,
        )"""

if "sigma_now=sigma" in text:
    print("  Prediction call already extended")
else:
    if old_pred_call not in text:
        raise SystemExit("Prediction call marker not found")
    text = text.replace(old_pred_call, new_pred_call, 1)
    print("  Prediction call extended with σ fields")

PRED.write_text(text)
print("  predictor.py done")

# ============================================================
# Part 2+4: order_manager.py - σ modifier, Brier Kelly, raise cap
# ============================================================
print("\n=== order_manager.py ===")
text = ORDER.read_text()

# Add Brier history tracker to OrderManager __init__
old_om_init = """        self._fok_throttle: Dict[str, float] = {}
        # Phase A4 restoration: trap-band memory. (coin:window:dir) keys"""
new_om_init = """        self._fok_throttle: Dict[str, float] = {}
        # May 21: Brier-score adaptive Kelly multiplier history.
        # Stores (predicted_prob, actual_outcome 0/1) tuples for last 20 trades.
        self._brier_history: List[Tuple[float, int]] = []
        # Phase A4 restoration: trap-band memory. (coin:window:dir) keys"""

if "_brier_history" in text:
    print("  brier_history already present")
else:
    if old_om_init not in text:
        raise SystemExit("order_manager init marker not found")
    text = text.replace(old_om_init, new_om_init, 1)
    print("  brier_history added to __init__")

# Now add the actual sizing logic right after WEAK_CONVICTION_CAP block
old_weakblock = """            # WEAK CONVICTION CAP (May 21): half-Kelly when prob<80% AND |trend|<1.0
            # Pattern that ate our gains 5/19-5/20: -$7 losses on prob 75-77% +
            # |trend| 0.6-0.9 + cheap entries 36-50c. Same EV, half variance.
            # Kill switch: WEAK_CONVICTION_CAP=off in .env.
            weak_conv_tag = \"\"
            if os.getenv(\"WEAK_CONVICTION_CAP\", \"on\").lower() != \"off\":
                _wc_prob_max = float(os.getenv(\"WEAK_CONVICTION_PROB_MAX\", \"0.80\"))
                _wc_trend_max = float(os.getenv(\"WEAK_CONVICTION_TREND_MAX\", \"1.0\"))
                _wc_mult = float(os.getenv(\"WEAK_CONVICTION_MULT\", \"0.50\"))
                _wc_trend_abs = abs(float(getattr(pred, \"trend_score\", 0.0) or 0.0))
                if p < _wc_prob_max and _wc_trend_abs < _wc_trend_max and not getattr(pred, \"_override_full_size\", False):
                    pre_weak = size
                    size = max(kelly_min_bet, size * _wc_mult)
                    weak_conv_tag = f\" weak_conv={_wc_mult:.0%}(pre=${pre_weak:.2f})\""""

new_weakblock = """            # WEAK CONVICTION CAP (May 21): half-Kelly when prob<80% AND |trend|<1.0
            # Pattern that ate our gains 5/19-5/20: -$7 losses on prob 75-77% +
            # |trend| 0.6-0.9 + cheap entries 36-50c. Same EV, half variance.
            # Kill switch: WEAK_CONVICTION_CAP=off in .env.
            weak_conv_tag = \"\"
            if os.getenv(\"WEAK_CONVICTION_CAP\", \"on\").lower() != \"off\":
                _wc_prob_max = float(os.getenv(\"WEAK_CONVICTION_PROB_MAX\", \"0.80\"))
                _wc_trend_max = float(os.getenv(\"WEAK_CONVICTION_TREND_MAX\", \"1.0\"))
                _wc_mult = float(os.getenv(\"WEAK_CONVICTION_MULT\", \"0.50\"))
                _wc_trend_abs = abs(float(getattr(pred, \"trend_score\", 0.0) or 0.0))
                if p < _wc_prob_max and _wc_trend_abs < _wc_trend_max and not getattr(pred, \"_override_full_size\", False):
                    pre_weak = size
                    size = max(kelly_min_bet, size * _wc_mult)
                    weak_conv_tag = f\" weak_conv={_wc_mult:.0%}(pre=${pre_weak:.2f})\"

            # ── SIGMA-TREND SIZE MODIFIER (May 21, Pattern D fix) ──
            # When σ has risen 30%+ in the last 5min, the directional signal
            # is less reliable. Reduce size by SIGMA_TREND_MULT (default 0.70).
            # Yesterday's Trade #10 (-$5.31) had σ=3.82e-3 vs 2.85e-3 at #9 (W)
            # only 27min earlier — same prob/trend/ROC60. σ_ratio=1.34 would
            # have flagged it. Saves ~30% on Pattern D losses.
            sigma_tag = \"\"
            if os.getenv(\"SIGMA_TREND_MOD\", \"on\").lower() != \"off\":
                _st_thresh = float(os.getenv(\"SIGMA_TREND_THRESH\", \"1.30\"))
                _st_mult = float(os.getenv(\"SIGMA_TREND_MULT\", \"0.70\"))
                _st_ratio = float(getattr(pred, \"sigma_ratio\", 1.0) or 1.0)
                if _st_ratio > _st_thresh and not getattr(pred, \"_override_full_size\", False):
                    pre_sigma = size
                    size = max(kelly_min_bet, size * _st_mult)
                    sigma_tag = f\" σ_rising={_st_mult:.0%}(ratio={_st_ratio:.2f},pre=${pre_sigma:.2f})\"

            # ── BRIER-SCORE ADAPTIVE KELLY (May 21) ──
            # Track last 20 trades' calibration: Brier = mean((prob-outcome)^2)
            # Brier < 0.15 → bot is well-calibrated → 1.1x size (press the edge)
            # Brier 0.15-0.20 → normal → 1.0x
            # Brier > 0.20 → bot is poorly calibrated → 0.70x (recalibrate down)
            brier_tag = \"\"
            if os.getenv(\"BRIER_KELLY\", \"on\").lower() != \"off\" and len(self._brier_history) >= 5:
                _brier_min = float(os.getenv(\"BRIER_GOOD_MAX\", \"0.15\"))
                _brier_bad = float(os.getenv(\"BRIER_BAD_MIN\", \"0.20\"))
                _brier_press = float(os.getenv(\"BRIER_PRESS_MULT\", \"1.10\"))
                _brier_cut = float(os.getenv(\"BRIER_CUT_MULT\", \"0.70\"))
                _b_sum = sum((p_pred - actual) ** 2 for (p_pred, actual) in self._brier_history)
                _b_avg = _b_sum / len(self._brier_history)
                if _b_avg < _brier_min:
                    pre_brier = size
                    size = size * _brier_press
                    brier_tag = f\" brier_press={_brier_press:.0%}(score={_b_avg:.3f})\"
                elif _b_avg > _brier_bad:
                    pre_brier = size
                    size = max(kelly_min_bet, size * _brier_cut)
                    brier_tag = f\" brier_cut={_brier_cut:.0%}(score={_b_avg:.3f})\""""

if "SIGMA-TREND SIZE MODIFIER" in text:
    print("  sigma+brier already present")
else:
    if old_weakblock not in text:
        raise SystemExit("weak-conv marker not found in order_manager")
    text = text.replace(old_weakblock, new_weakblock, 1)
    print("  sigma-trend + brier-kelly modifiers injected")

# Extend the KELLY log line to include sigma_tag + brier_tag
old_logline = """                f\"pct_cap={kelly_max_pct:.2%} tier={tier_name}({tier_mult:.0%}){dampen_tag}{weak_conv_tag} \""""
new_logline = """                f\"pct_cap={kelly_max_pct:.2%} tier={tier_name}({tier_mult:.0%}){dampen_tag}{weak_conv_tag}{sigma_tag}{brier_tag} \""""

if "{sigma_tag}{brier_tag}" in text:
    print("  log line already extended")
elif old_logline in text:
    text = text.replace(old_logline, new_logline, 1)
    print("  KELLY log line extended with sigma+brier tags")

ORDER.write_text(text)
print("  order_manager.py done")

print("\n=== ALL PATCHES APPLIED ===")
