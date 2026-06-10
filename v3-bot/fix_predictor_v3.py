#!/usr/bin/env python3
"""
Fix V3 predictor: d2 saturation, dominance always 100%, calibration too flat.
Ports key fixes from profitable main bot (brain.py).
"""

import math

FILE = "/home/ubuntu/v3-bot/predictor.py"

with open(FILE, "r") as f:
    code = f.read()

original = code

# =======================================================================
# FIX 1: _compute_d2 — add vol floor/ceiling, zero drift, lower tick threshold
# =======================================================================
old_d2 = '''        if stats and stats["tick_count"] >= 90 and stats["realized_vol"] > 1e-10:
            mu_sec = stats["ewma_drift"]
            sigma_sec = stats["realized_vol"]
            is_enhanced = True
        else:
            mu_sec = 0.0
            ann_vol = FALLBACK_VOL_ANNUAL.get(coin, 0.70)
            sigma_sec = ann_vol / math.sqrt(SECS_PER_YEAR)
            is_enhanced = False'''

new_d2 = '''        if stats and stats["tick_count"] >= 30 and stats["realized_vol"] > 1e-10:
            mu_sec = 0.0  # Direction from log(S/K) only, not drift
            realized = stats["realized_vol"]
            _ann_floor = FALLBACK_VOL_ANNUAL.get(coin, 0.70) * 1.00
            _ann_ceil  = FALLBACK_VOL_ANNUAL.get(coin, 0.70) * 3.00
            _floor_sec = _ann_floor / math.sqrt(SECS_PER_YEAR)
            _ceil_sec  = _ann_ceil / math.sqrt(SECS_PER_YEAR)
            sigma_sec = max(realized, _floor_sec)
            sigma_sec = min(sigma_sec, _ceil_sec)
            is_enhanced = True
        else:
            mu_sec = 0.0
            ann_vol = FALLBACK_VOL_ANNUAL.get(coin, 0.70)
            sigma_sec = ann_vol / math.sqrt(SECS_PER_YEAR)
            is_enhanced = False'''

if old_d2 in code:
    code = code.replace(old_d2, new_d2)
    print("[OK] FIX 1: _compute_d2 vol floor/ceiling + zero drift + tick>=30")
else:
    print("[SKIP] FIX 1: _compute_d2 pattern not found")

# =======================================================================
# FIX 2: _compute_d2 — d2 clamp from +-10 to +-2.5
# =======================================================================
old_clamp = '        d2 = max(-10.0, min(10.0, d2))'
new_clamp = '        d2 = max(-2.5, min(2.5, d2))'
if old_clamp in code:
    code = code.replace(old_clamp, new_clamp)
    print("[OK] FIX 2: d2 clamp -> +-2.5")
else:
    print("[SKIP] FIX 2: d2 clamp pattern not found")

# =======================================================================
# FIX 3: _compute_d2_band — d2_robust cap from +-2.0 to +-3.0
# =======================================================================
old_cap = '        d2_robust = max(-2.0, min(2.0, d2_robust))'
new_cap = '        d2_robust = max(-3.0, min(3.0, d2_robust))'
if old_cap in code:
    code = code.replace(old_cap, new_cap)
    print("[OK] FIX 3: d2_robust cap -> +-3.0")
else:
    print("[SKIP] FIX 3: d2_robust cap pattern not found")

# =======================================================================
# FIX 4: _calibrate_posterior — sigma-adaptive (not fixed 0.20 max_dev)
# =======================================================================
old_calib = '''    @staticmethod
    def _calibrate_posterior(raw: float) -> float:
        calibrated = 0.50 + 0.20 * math.tanh(2.5 * (raw - 0.50))
        return max(0.05, min(0.95, calibrated))'''

new_calib = '''    @staticmethod
    def _calibrate_posterior(raw: float, sigma: float = 0.30) -> float:
        """Sigma-adaptive: strong signals produce higher posteriors."""
        max_dev = 0.20 + 0.15 * min(sigma / 1.0, 1.0)
        calibrated = 0.50 + max_dev * math.tanh(2.5 * (raw - 0.50))
        return max(0.05, min(0.95, calibrated))'''

if old_calib in code:
    code = code.replace(old_calib, new_calib)
    print("[OK] FIX 4: _calibrate_posterior sigma-adaptive")
else:
    print("[SKIP] FIX 4: _calibrate_posterior pattern not found")

# =======================================================================
# FIX 5: _get_smoothed_direction — magnitude-based dominance
# =======================================================================
old_smooth = '''        total_w = 0.0
        up_w = 0.0
        for t, d in recent:
            age = now - t
            base = 1.0 + (WINDOW_SECS - age) / WINDOW_SECS
            if early_window and window_start > 0:
                if t >= window_start:
                    base *= 10.0
                else:
                    base *= 0.1
            total_w += base
            if d > 0:
                up_w += base

        up_pct = up_w / max(total_w, 0.001)
        smoothed_dir = "UP" if up_pct > 0.50 else "DOWN"
        dominance = max(up_pct, 1.0 - up_pct)'''

new_smooth = '''        total_w = 0.0
        signed_sum = 0.0
        abs_sum = 0.0
        for t, d in recent:
            age = now - t
            base = 1.0 + (WINDOW_SECS - age) / WINDOW_SECS
            if early_window and window_start > 0:
                if t >= window_start:
                    base *= 10.0
                else:
                    base *= 0.1
            total_w += base
            signed_sum += base * d
            abs_sum += base * abs(d)

        if abs_sum < 1e-12:
            return instant_dir, 0.50, 0.0, instant_dir

        weighted_avg = signed_sum / max(total_w, 0.001)
        smoothed_dir = "UP" if weighted_avg > 0 else "DOWN"
        dominance = min(abs(signed_sum) / max(abs_sum, 1e-12), 1.0)'''

if old_smooth in code:
    code = code.replace(old_smooth, new_smooth)
    print("[OK] FIX 5: _get_smoothed_direction magnitude-based dominance")
else:
    print("[SKIP] FIX 5: _get_smoothed_direction pattern not found")

# =======================================================================
# FIX 6: predict() — is_enhanced threshold from 90 to 30
# =======================================================================
old_enh = '        is_enhanced = tick_count >= 90'
new_enh = '        is_enhanced = tick_count >= 30'
if old_enh in code:
    code = code.replace(old_enh, new_enh)
    print("[OK] FIX 6: is_enhanced threshold -> 30")
else:
    print("[SKIP] FIX 6: is_enhanced pattern not found")

# =======================================================================
# FIX 7: predict() — WARMUP from 90 to 30
# =======================================================================
old_warmup = '            reasons.append(f"WARMUP({tick_count}/90)")'
new_warmup = '            reasons.append(f"WARMUP({tick_count}/30)")'
if old_warmup in code:
    code = code.replace(old_warmup, new_warmup)
    print("[OK] FIX 7: WARMUP -> /30")
else:
    print("[SKIP] FIX 7: WARMUP pattern not found")

# =======================================================================
# FIX 8: predict() — pass sigma to _calibrate_posterior
# =======================================================================
old_cal_call = '        posterior = self._calibrate_posterior(raw_posterior)'

new_cal_call = '''        _cal_sigma = 0.30
        _cal_stats = self._tick_stats.get(coin)
        if _cal_stats and _cal_stats["realized_vol"] > 1e-10:
            _ann_vol_est = _cal_stats["realized_vol"] * math.sqrt(SECS_PER_YEAR)
            _ann_vol_est = max(0.10, min(_ann_vol_est, 5.0))
            _time_yrs = max(time_left, 0.5) / (365.25 * 24 * 60)
            _expected_move = _ann_vol_est * math.sqrt(_time_yrs)
            if _expected_move > 0:
                _cal_sigma = (abs_distance * 100) / (_expected_move * 100)
        posterior = self._calibrate_posterior(raw_posterior, sigma=_cal_sigma)'''

if old_cal_call in code:
    code = code.replace(old_cal_call, new_cal_call)
    print("[OK] FIX 8: _calibrate_posterior with sigma")
else:
    print("[SKIP] FIX 8: _calibrate_posterior call pattern not found")

# =======================================================================
# Write
# =======================================================================
if code != original:
    with open(FILE, "w") as f:
        f.write(code)
    print(f"\n[DONE] All fixes written to {FILE}")
else:
    print("\n[WARN] No changes made")
