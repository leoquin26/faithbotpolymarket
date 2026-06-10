"""
Technical Indicators Module V2 â€” Stronger weights, faster MACD for 1-min charts.
"""
from typing import List, Tuple


def _closes(klines: list) -> List[float]:
    return [float(k[4]) for k in klines]

def _highs(klines: list) -> List[float]:
    return [float(k[2]) for k in klines]

def _lows(klines: list) -> List[float]:
    return [float(k[3]) for k in klines]

def _volumes(klines: list) -> List[float]:
    return [float(k[5]) for k in klines]


def rsi(klines: list, period: int = 14) -> float:
    closes = _closes(klines)
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(0, diff))
        losses.append(max(0, -diff))
    if len(gains) < period:
        return 50.0
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss < 1e-10:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def ema(values: List[float], period: int) -> List[float]:
    if not values or len(values) < period:
        return values[:]
    k = 2.0 / (period + 1)
    result = [0.0] * len(values)
    result[period - 1] = sum(values[:period]) / period
    for i in range(period, len(values)):
        result[i] = values[i] * k + result[i - 1] * (1 - k)
    return result


def ema_cross(klines: list, fast: int = 9, slow: int = 21) -> Tuple[str, float]:
    closes = _closes(klines)
    if len(closes) < slow + 1:
        return "FLAT", 0.0
    fast_ema = ema(closes, fast)
    slow_ema = ema(closes, slow)
    f, s = fast_ema[-1], slow_ema[-1]
    if s == 0:
        return "FLAT", 0.0
    gap = (f - s) / s
    if gap > 0.0001:
        return "BULLISH", gap
    elif gap < -0.0001:
        return "BEARISH", gap
    return "FLAT", gap


def macd(klines: list, fast: int = 6, slow: int = 13, signal: int = 5) -> Tuple[float, float, float]:
    """MACD with faster periods for 1-minute charts."""
    closes = _closes(klines)
    if len(closes) < slow + signal:
        return 0.0, 0.0, 0.0
    fast_ema = ema(closes, fast)
    slow_ema = ema(closes, slow)
    macd_line = [f - s for f, s in zip(fast_ema, slow_ema)]
    valid_macd = [m for i, m in enumerate(macd_line) if i >= slow - 1]
    if len(valid_macd) < signal:
        return 0.0, 0.0, 0.0
    signal_ema = ema(valid_macd, signal)
    m, s = valid_macd[-1], signal_ema[-1]
    return m, s, m - s


def stochastic(klines: list, k_period: int = 14, d_period: int = 3) -> Tuple[float, float]:
    highs, lows, closes = _highs(klines), _lows(klines), _closes(klines)
    if len(closes) < k_period:
        return 50.0, 50.0
    k_values = []
    for i in range(k_period - 1, len(closes)):
        wh = max(highs[i - k_period + 1:i + 1])
        wl = min(lows[i - k_period + 1:i + 1])
        if wh - wl < 1e-10:
            k_values.append(50.0)
        else:
            k_values.append(100.0 * (closes[i] - wl) / (wh - wl))
    if not k_values:
        return 50.0, 50.0
    pct_k = k_values[-1]
    pct_d = sum(k_values[-d_period:]) / d_period if len(k_values) >= d_period else pct_k
    return pct_k, pct_d


def vwap(klines: list) -> Tuple[float, float]:
    closes, volumes = _closes(klines), _volumes(klines)
    if not closes or not volumes:
        return 0.0, 0.0
    total_pv = sum(c * v for c, v in zip(closes, volumes))
    total_v = sum(volumes)
    if total_v < 1e-10:
        return closes[-1], 0.0
    vwap_price = total_pv / total_v
    return vwap_price, (closes[-1] - vwap_price) / vwap_price if vwap_price > 0 else 0.0


def atr(klines: list, period: int = 14) -> float:
    highs, lows, closes = _highs(klines), _lows(klines), _closes(klines)
    if len(closes) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(closes)):
        trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1])))
    if len(trs) < period:
        return 0.0
    return (sum(trs[-period:]) / period) / closes[-1] if closes[-1] > 0 else 0.0


def compute_signals(klines: list, is_up: bool) -> dict:
    """V2: Stronger weights â€” TA score is the PRIMARY decision factor."""
    if not klines or len(klines) < 15:
        return {"valid": False, "reason": "insufficient klines"}

    rsi_val = rsi(klines, 14)
    stoch_k, stoch_d = stochastic(klines, 14, 3)
    ema_dir, ema_gap = ema_cross(klines, 9, 21)
    macd_line, signal_line, histogram = macd(klines, 6, 13, 5)
    vwap_price, vwap_dev = vwap(klines)
    atr_pct = atr(klines, 14)

    score = 0.0

    # EMA: Primary trend (strongest weight)
    if is_up and ema_dir == "BULLISH":
        score += 0.15
    elif not is_up and ema_dir == "BEARISH":
        score += 0.15
    elif is_up and ema_dir == "BEARISH":
        score -= 0.20
    elif not is_up and ema_dir == "BULLISH":
        score -= 0.20

    # MACD histogram: Momentum confirmation
    if is_up and histogram > 0:
        score += 0.10
    elif not is_up and histogram < 0:
        score += 0.10
    elif is_up and histogram < 0:
        score -= 0.12
    elif not is_up and histogram > 0:
        score -= 0.12

    # RSI: Overbought/oversold
    if is_up:
        if rsi_val > 75: score -= 0.12
        elif rsi_val < 35: score += 0.08
        elif 40 <= rsi_val <= 60: score += 0.03
    else:
        if rsi_val < 25: score -= 0.12
        elif rsi_val > 65: score += 0.08
        elif 40 <= rsi_val <= 60: score += 0.03

    # Stochastic: Entry timing
    if is_up:
        if stoch_k > 85: score -= 0.10
        elif stoch_k < 25 and stoch_k > stoch_d: score += 0.08
        elif 30 <= stoch_k <= 70: score += 0.03
    else:
        if stoch_k < 15: score -= 0.10
        elif stoch_k > 75 and stoch_k < stoch_d: score += 0.08
        elif 30 <= stoch_k <= 70: score += 0.03

    # VWAP: Value position
    if is_up and vwap_dev > 0.001: score += 0.05
    elif not is_up and vwap_dev < -0.001: score += 0.05
    elif is_up and vwap_dev < -0.002: score -= 0.05
    elif not is_up and vwap_dev > 0.002: score -= 0.05

    # ATR: Volatility filter
    if atr_pct < 0.0002: score -= 0.08
    elif atr_pct > 0.005: score -= 0.08

    return {
        "valid": True, "score": score,
        "rsi": rsi_val, "stoch_k": stoch_k, "stoch_d": stoch_d,
        "ema_dir": ema_dir, "ema_gap": ema_gap,
        "macd_line": macd_line, "macd_hist": histogram,
        "vwap_dev": vwap_dev, "atr_pct": atr_pct,
        "vol_filter": "TOO_LOW" if atr_pct < 0.0002 else ("TOO_HIGH" if atr_pct > 0.005 else "OK"),
    }
