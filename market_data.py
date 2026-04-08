"""
Market Data Module
Fetches crypto prices from Binance, Polymarket market info from Gamma API,
calculates threshold, distance, momentum, and volatility.
"""

import time
import ast
import httpx
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Optional, Dict, List, Tuple
from loguru import logger

import config


@dataclass
class MarketInfo:
    """Snapshot of a Polymarket Up/Down market."""
    coin: str
    threshold_price: float
    current_crypto_price: float
    distance_percent: float  # +above / -below threshold
    up_poly_price: float
    down_poly_price: float
    up_token_id: str
    down_token_id: str
    time_remaining: int  # minutes
    window_start: int
    timeframe: str = "15m"


# ---------------------------------------------------------------------------
# Persistent HTTP client (connection pooling)
# ---------------------------------------------------------------------------
_http = httpx.Client(
    timeout=httpx.Timeout(8.0, connect=3.0),
    limits=httpx.Limits(max_keepalive_connections=5, max_connections=10, keepalive_expiry=30),
    http2=False,
    follow_redirects=True,
)
_http_failures = 0

def _get_with_retry(url: str, timeout: float = 5.0, retries: int = 2, **kwargs):
    global _http, _http_failures
    for attempt in range(retries):
        try:
            resp = _http.get(url, timeout=timeout, **kwargs)
            _http_failures = 0
            return resp
        except Exception as e:
            _http_failures += 1
            if attempt < retries - 1:
                import time as _t
                _t.sleep(0.5)
                continue
            if _http_failures >= 5:
                try:
                    _http.close()
                except Exception:
                    pass
                _http = httpx.Client(
                    timeout=httpx.Timeout(8.0, connect=3.0),
                    limits=httpx.Limits(max_keepalive_connections=5, max_connections=10, keepalive_expiry=30),
                    http2=False,
                    follow_redirects=True,
                )
                _http_failures = 0
                logger.warning("[HTTP] Recreated HTTP client after 5 consecutive failures")
            raise


# ---------------------------------------------------------------------------
# Price history for momentum
# ---------------------------------------------------------------------------
_price_history: Dict[str, List[Tuple[float, float]]] = {}  # symbol -> [(ts, price)]
_MAX_HISTORY = 120


def get_binance_price(symbol: str) -> Optional[float]:
    """Fetch latest price from Binance REST."""
    try:
        resp = _get_with_retry(f"{config.BINANCE_API}/ticker/price?symbol={symbol}", timeout=3.0)
        if resp.status_code == 200:
            return float(resp.json()["price"])
    except Exception as e:
        logger.debug(f"Binance price error for {symbol}: {e}")
    return None


def get_binance_klines(symbol: str, interval: str = "1m", limit: int = 15) -> Optional[list]:
    """Fetch recent klines (candles) from Binance."""
    try:
        resp = _get_with_retry(
            f"{config.BINANCE_API}/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
            timeout=5.0,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.debug(f"Binance klines error: {e}")
    return None


def get_threshold_from_binance(coin: str, window_start: int, timeframe: str = "15m") -> Optional[float]:
    """
    Get the threshold (opening price at window start) from Binance klines.
    The threshold is the FIRST candle open that falls on window_start.
    """
    symbol = config.SYMBOLS.get(coin)
    if not symbol:
        return None

    try:
        resp = _get_with_retry(
            f"{config.BINANCE_API}/klines",
            params={
                "symbol": symbol,
                "interval": "1m",
                "startTime": window_start * 1000,
                "limit": 2,
            },
            timeout=5.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data:
                return float(data[0][1])  # open price of first candle
    except Exception as e:
        logger.debug(f"Threshold fetch error for {coin}: {e}")
    return None


def _record_price(coin: str, price: float):
    """Store price in history for momentum calculation."""
    now = time.time()
    if coin not in _price_history:
        _price_history[coin] = []
    _price_history[coin].append((now, price))
    if len(_price_history[coin]) > _MAX_HISTORY:
        _price_history[coin] = _price_history[coin][-_MAX_HISTORY:]


def calculate_momentum(coin: str) -> Optional[dict]:
    """
    Calculate momentum from recent Binance klines.
    Returns dict with change_1m, change_5m, trend_strength, acceleration,
    recent_change, volatility, reversal_score, peak_detected, divergence.
    """
    symbol = config.SYMBOLS.get(coin)
    if not symbol:
        return None

    klines = get_binance_klines(symbol, "1m", 15)
    if not klines or len(klines) < 5:
        return None

    closes = [float(k[4]) for k in klines]
    current = closes[-1]

    _record_price(coin, current)

    change_1m = (closes[-1] - closes[-2]) / closes[-2] if len(closes) >= 2 else 0
    change_5m = (closes[-1] - closes[-6]) / closes[-6] if len(closes) >= 6 else 0

    # Trend strength: consecutive candles in same direction
    trend = 0
    for i in range(len(closes) - 1, 0, -1):
        if closes[i] > closes[i - 1]:
            if trend >= 0:
                trend += 1
            else:
                break
        elif closes[i] < closes[i - 1]:
            if trend <= 0:
                trend -= 1
            else:
                break
        else:
            break

    # Acceleration (comparing recent vs earlier change)
    recent_change = (closes[-1] - closes[-3]) / closes[-3] if len(closes) >= 3 else 0
    earlier_change = (closes[-3] - closes[-5]) / closes[-5] if len(closes) >= 5 else 0

    if abs(recent_change) > abs(earlier_change) * 1.5 and recent_change * earlier_change > 0:
        acceleration = "ACCELERATING"
    elif recent_change * earlier_change < 0:
        acceleration = "REVERSING"
    else:
        acceleration = "STEADY"

    # Volatility (std of last 10 candle returns)
    returns = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))]
    volatility = (sum(r ** 2 for r in returns) / len(returns)) ** 0.5 if returns else 0

    # Reversal score
    reversal_score = 0
    peak_detected = False
    peak_direction = None
    divergence = False
    divergence_direction = None

    if acceleration == "REVERSING":
        reversal_score += 30

    # Peak detection: recent high/low vs current
    if len(closes) >= 5:
        recent_high = max(closes[-5:])
        recent_low = min(closes[-5:])
        if closes[-1] < recent_high * 0.998 and closes[-2] >= recent_high * 0.999:
            peak_detected = True
            peak_direction = "DOWN"
            reversal_score += 25
        elif closes[-1] > recent_low * 1.002 and closes[-2] <= recent_low * 1.001:
            peak_detected = True
            peak_direction = "UP"
            reversal_score += 25

    # Divergence: price going one way, momentum slowing
    if len(closes) >= 4:
        price_dir = closes[-1] - closes[-4]
        mom_dir = (closes[-1] - closes[-2]) - (closes[-3] - closes[-4])
        if price_dir > 0 and mom_dir < 0:
            divergence = True
            divergence_direction = "DOWN"
            reversal_score += 20
        elif price_dir < 0 and mom_dir > 0:
            divergence = True
            divergence_direction = "UP"
            reversal_score += 20

    return {
        "change_1m": change_1m,
        "change_5m": change_5m,
        "trend_strength": trend,
        "acceleration": acceleration,
        "recent_change": recent_change,
        "volatility": volatility,
        "reversal_score": reversal_score,
        "peak_detected": peak_detected,
        "peak_direction": peak_direction,
        "divergence": divergence,
        "divergence_direction": divergence_direction,
    }


def get_market_info(coin: str, timeframe: str = "15m") -> Optional[MarketInfo]:
    """
    Fetch Polymarket event data + Binance price and build MarketInfo.
    Extracted directly from friend_package get_market_info().
    """
    current_time = int(time.time())
    window_seconds = {"15m": 900, "1h": 3600, "4h": 14400}.get(timeframe, 900)
    current_window = (current_time // window_seconds) * window_seconds
    slug = f"{coin.lower()}-updown-{timeframe}-{current_window}"

    try:
        resp = _get_with_retry(f"{config.GAMMA_API}/events?slug={slug}", timeout=5.0)
        if resp.status_code != 200:
            return None
        data = resp.json()
        if not data or not data[0].get("markets"):
            return None

        event = data[0]
        market = event["markets"][0]

        prices = market.get("outcomePrices", [])
        if isinstance(prices, str):
            prices = ast.literal_eval(prices)
        token_ids = market.get("clobTokenIds", [])
        if isinstance(token_ids, str):
            token_ids = ast.literal_eval(token_ids)
        if len(prices) != 2 or len(token_ids) != 2:
            return None

        symbol = config.SYMBOLS.get(coin)
        current_price = get_binance_price(symbol) if symbol else None
        if not current_price:
            return None

        threshold = get_threshold_from_binance(coin, current_window, timeframe)
        if not threshold:
            threshold = current_price

        distance = (current_price - threshold) / threshold

        end_date = market.get("endDate", "")
        try:
            if "T" in str(end_date):
                end_time = int(datetime.fromisoformat(str(end_date).replace("Z", "+00:00")).timestamp())
            else:
                end_time = current_window + window_seconds
        except Exception:
            end_time = current_window + window_seconds

        time_remaining = (end_time - current_time) // 60

        return MarketInfo(
            coin=coin,
            threshold_price=threshold,
            current_crypto_price=current_price,
            distance_percent=distance,
            up_poly_price=float(prices[0]),
            down_poly_price=float(prices[1]),
            up_token_id=token_ids[0],
            down_token_id=token_ids[1],
            time_remaining=time_remaining,
            window_start=current_window,
            timeframe=timeframe,
        )
    except Exception as e:
        logger.debug(f"get_market_info error for {coin}: {e}")
        return None
