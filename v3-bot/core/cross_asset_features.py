"""
Cross-asset features — pure feature extractors that read the multi-coin
state already collected by `run_bot.scan_coin` and produce numeric features
that consider all 4 coins at once.

Today the predictor sees only its own coin. The bot already builds
`_raw_coin_info[coin] = (up_ask, down_ask)` for every coin per scan and a
`_window_trends` dict mapping coin → "UP"/"DOWN" direction commitments.
This module turns those into features the predictor and (eventually) the
calibrator can read.

Three feature families:

  1. cross_breadth
        Net direction across the 4 coins, normalized to [-1, +1].
        +1.0 = all 4 coins agree UP. -1.0 = all 4 agree DOWN.
        Computed from poly-ask thresholds (>=0.60 → that side is committed).

  2. dominant_direction_age_sec
        How long has the dominant direction (≥ 3-of-4 coins) been in place?
        High age + strong breadth = mature trend. Low age = fresh setup.

  3. btc_eth_correlation_15m
        Spearman-like rank correlation of BTC vs ETH spot moves over the
        last N seconds. Near +1 = coupled. Near 0 = decoupled.
        We *don't* use Pearson because crypto in 15m windows has fat tails.

Conventions:
  • All features are deterministic given the input snapshot.
  • Module is stateless; the caller (a small wrapper in run_bot.py) owns
    the "breadth history" rolling buffer for `dominant_direction_age_sec`.
"""
from __future__ import annotations

import math
import time
from collections import deque
from typing import Deque, Dict, List, Optional, Sequence, Tuple


# ── Cross-breadth ────────────────────────────────────────────────────────────
def coin_directions_from_asks(coin_asks: Dict[str, Tuple[float, float]],
                              ask_commit_threshold: float = 0.60) -> Dict[str, str]:
    """Map coin → "UP" or "DOWN" or None based on which side has an ask
    above the commitment threshold.

    Input: {"BTC": (up_ask, down_ask), ...}  asks in 0..1 outcome-token units.
    """
    out: Dict[str, str] = {}
    for coin, (up_ask, down_ask) in coin_asks.items():
        up_ask = float(up_ask or 0.0)
        down_ask = float(down_ask or 0.0)
        if up_ask >= ask_commit_threshold and up_ask < 0.99:
            out[coin] = "UP"
        elif down_ask >= ask_commit_threshold and down_ask < 0.99:
            out[coin] = "DOWN"
    return out


def cross_breadth(directions: Dict[str, str]) -> float:
    """Net direction across coins, normalized to [-1, +1]."""
    if not directions:
        return 0.0
    n_up = sum(1 for d in directions.values() if d == "UP")
    n_down = sum(1 for d in directions.values() if d == "DOWN")
    total = n_up + n_down
    if total == 0:
        return 0.0
    return (n_up - n_down) / total


# ── Dominant direction age (stateful via injected history) ──────────────────
def dominant_direction(directions: Dict[str, str],
                       min_majority: int = 3) -> Optional[str]:
    """Returns "UP" or "DOWN" if at least `min_majority` of the directions
    point that way, else None."""
    if not directions:
        return None
    n_up = sum(1 for d in directions.values() if d == "UP")
    n_down = sum(1 for d in directions.values() if d == "DOWN")
    if n_up >= min_majority and n_up > n_down:
        return "UP"
    if n_down >= min_majority and n_down > n_up:
        return "DOWN"
    return None


def dominant_direction_age_sec(
    history: Sequence[Tuple[float, Optional[str]]],
    current_dom: Optional[str],
) -> float:
    """How long has the current dominant direction been in place?

    history: list of (ts, dom_dir) tuples in chronological order.
    Returns 0.0 when there is no current dominant or it just flipped.
    """
    if current_dom is None or not history:
        return 0.0
    now = history[-1][0]
    # Walk backwards until we find an entry that doesn't agree.
    age = 0.0
    for ts, dom in reversed(history):
        if dom == current_dom:
            age = now - ts
        else:
            break
    return age


# ── Correlation ──────────────────────────────────────────────────────────────
def _rank(xs: List[float]) -> List[float]:
    """Average-rank (handles ties); same shape as input."""
    n = len(xs)
    if n == 0:
        return []
    pairs = sorted(range(n), key=lambda i: xs[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and xs[pairs[j + 1]] == xs[pairs[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[pairs[k]] = avg
        i = j + 1
    return ranks


def spearman_correlation(xs: List[float], ys: List[float]) -> float:
    """Spearman rank correlation; returns 0.0 if inputs are too short
    or stdev is zero."""
    if len(xs) != len(ys) or len(xs) < 8:
        return 0.0
    rx = _rank(xs)
    ry = _rank(ys)
    n = len(xs)
    mean_rx = sum(rx) / n
    mean_ry = sum(ry) / n
    cov = sum((rx[i] - mean_rx) * (ry[i] - mean_ry) for i in range(n))
    sx = math.sqrt(sum((r - mean_rx) ** 2 for r in rx))
    sy = math.sqrt(sum((r - mean_ry) ** 2 for r in ry))
    if sx <= 0 or sy <= 0:
        return 0.0
    return cov / (sx * sy)


def correlation_from_tick_buffers(
    a_ticks: Sequence[Tuple[float, float]],
    b_ticks: Sequence[Tuple[float, float]],
    lookback: float = 180.0,
) -> float:
    """Sample both tick streams at common timestamps (or nearest) over the
    last `lookback` seconds and return Spearman rank correlation."""
    if not a_ticks or not b_ticks:
        return 0.0
    now_a = a_ticks[-1][0]
    now_b = b_ticks[-1][0]
    cutoff = min(now_a, now_b) - lookback

    # Build per-second samples from each (last price at or before each second)
    by_sec_a: Dict[int, float] = {}
    by_sec_b: Dict[int, float] = {}
    for ts, p in a_ticks:
        if ts >= cutoff:
            by_sec_a[int(ts)] = p
    for ts, p in b_ticks:
        if ts >= cutoff:
            by_sec_b[int(ts)] = p

    common = sorted(set(by_sec_a.keys()) & set(by_sec_b.keys()))
    if len(common) < 10:
        return 0.0
    xs = [by_sec_a[s] for s in common]
    ys = [by_sec_b[s] for s in common]

    # Use returns, not levels — crypto trends would inflate level-correlation
    rx = [xs[i] / xs[i - 1] - 1.0 for i in range(1, len(xs))]
    ry = [ys[i] / ys[i - 1] - 1.0 for i in range(1, len(ys))]
    return spearman_correlation(rx, ry)


# ── Stateful aggregator (one instance per bot process) ──────────────────────
class CrossAssetState:
    """Maintains a rolling history of dominant-direction flags so we can
    compute `dominant_direction_age_sec` without making the predictor own
    yet another stateful object."""

    def __init__(self, history_secs: int = 1800):
        self._history: Deque[Tuple[float, Optional[str]]] = deque(maxlen=2400)
        self._history_secs = history_secs
        self._latest_snapshot: Dict = {}

    def update(self, coin_asks: Dict[str, Tuple[float, float]]) -> Dict:
        """Single entry point. Returns the feature snapshot."""
        now = time.time()
        # Prune entries older than the history window
        while self._history and self._history[0][0] < now - self._history_secs:
            self._history.popleft()
        directions = coin_directions_from_asks(coin_asks)
        breadth = cross_breadth(directions)
        dom = dominant_direction(directions)
        self._history.append((now, dom))
        age_sec = dominant_direction_age_sec(self._history, dom)
        snap = {
            "directions": directions,
            "breadth": round(breadth, 3),
            "dominant_direction": dom,
            "dominant_age_sec": round(age_sec, 1),
            "snapshot_ts": now,
        }
        self._latest_snapshot = snap
        return snap

    def get_latest_snapshot(self) -> Dict:
        """Return the most recently computed snapshot (or {} if never updated).

        Safe to call concurrently with `update`. Predictor reads this each
        time it calls the calibrator, so the cross-asset breadth at signal
        time can shrink/lift probability before Kelly sizing.
        """
        return dict(self._latest_snapshot)  # shallow copy is safe


def format_log_line(snapshot: Dict, *,
                    btc_eth_corr: Optional[float] = None) -> str:
    dom = snapshot.get("dominant_direction") or "—"
    age = snapshot.get("dominant_age_sec", 0)
    corr_s = (f" corr_btc_eth={btc_eth_corr:+.2f}"
              if btc_eth_corr is not None else "")
    return (
        f"[XASSET] breadth={snapshot['breadth']:+.2f} dom={dom} "
        f"age={age:.0f}s{corr_s}"
    )
