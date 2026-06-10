"""
May 04 — Three structural fixes to prevent correlated/whipsaw losses.

Fix A: [CORR DOUBLE-UP] cap (order_manager.py)
  Block second same-direction trade in same window if a highly-correlated
  coin (BTC<->ETH) already has a position open in that direction.

Fix B: Late-entry tightening for whipsaw (predictor.py)
  If T < 300s AND we previously saw the opposite direction signal-attempt
  for this coin in this window, require A-tier (prob >= 88% AND edge >= 22%).

Fix C: Recent-flip detector (predictor.py)
  If |trend| flipped sign within the last 30 seconds (and both magnitudes
  were non-trivial), suppress the new direction for one cycle.

Idempotent: safe to re-run; bails if patch markers already present.
"""
from pathlib import Path
import sys

ROOT = Path("/home/ubuntu/v3-bot")
PRED = ROOT / "predictor.py"
ORDM = ROOT / "order_manager.py"


def patch_order_manager() -> bool:
    src = ORDM.read_text()
    if "[CORR DOUBLE-UP]" in src:
        print("[skip] order_manager.py already patched (CORR DOUBLE-UP present)")
        return False

    anchor = (
        "        # Correlation limit: max 3 same-direction bets per window\n"
        "        same_dir_count = self.count_same_direction_trades(direction, window_start)\n"
        "        if same_dir_count >= 3:\n"
        "            logger.info(\n"
        "                f\"[CORR GATE] {coin}: Already {same_dir_count} {direction} \"\n"
        "                f\"bets this window (max 3)\"\n"
        "            )\n"
        "            return False\n"
    )
    if anchor not in src:
        print("[fail] CORR GATE anchor not found in order_manager.py")
        sys.exit(2)

    addition = (
        "\n"
        "        # may04 fix A: correlated double-up cap.\n"
        "        # Block a 2nd same-direction trade in the same window when a\n"
        "        # highly-correlated coin (BTC<->ETH) already has an open\n"
        "        # position in that direction. Prevents 2x correlated exposure\n"
        "        # that pays off / loses together (e.g. BTC UP + ETH UP both -$5\n"
        "        # when the macro tape reverses).\n"
        "        _corr_pairs = {(\"BTC\", \"ETH\"), (\"ETH\", \"BTC\")}\n"
        "        for _other_coin, _pos in list(self.positions.items()):\n"
        "            if _other_coin == coin:\n"
        "                continue\n"
        "            if (coin, _other_coin) not in _corr_pairs:\n"
        "                continue\n"
        "            _pos_side = _pos.get(\"side\")\n"
        "            _pos_window = _pos.get(\"window_start\")\n"
        "            if _pos_side == direction and _pos_window == window_start:\n"
        "                logger.info(\n"
        "                    f\"[CORR DOUBLE-UP] {coin} {direction}: already have \"\n"
        "                    f\"{_other_coin} {_pos_side} open in same window \"\n"
        "                    f\"(window_start={window_start}) — blocking 2x correlated exposure\"\n"
        "                )\n"
        "                return False\n"
    )

    new_src = src.replace(anchor, anchor + addition, 1)
    if new_src == src:
        print("[fail] order_manager.py unchanged after replace")
        sys.exit(3)

    ORDM.write_text(new_src)
    print("[ok] order_manager.py: added [CORR DOUBLE-UP] cap")
    return True


def patch_predictor() -> bool:
    src = PRED.read_text()
    if "_window_dir_seen" in src and "_last_trend_score" in src and "[LATE WHIPSAW]" in src and "[RECENT FLIP]" in src:
        print("[skip] predictor.py already patched (B+C present)")
        return False

    # ---- patch 1: extend __init__ with new state dicts ----
    init_anchor = (
        "        self._window_directions: Dict[str, str] = {}  # may01: per-coin dir lock\n"
        "        self._window_start_ts: int = 0\n"
        "        self._window_trends: Dict[str, str] = {}\n"
    )
    if init_anchor not in src:
        print("[fail] predictor __init__ anchor not found")
        sys.exit(4)
    init_replacement = init_anchor + (
        "        self._window_dir_seen: Dict[str, set] = {}  # may04 fix B: every direction attempted per coin per window\n"
        "        self._last_trend_score: Dict[str, tuple] = {}  # may04 fix C: (trend_score, ts) for sign-flip detection\n"
    )
    src = src.replace(init_anchor, init_replacement, 1)

    # ---- patch 2: clear new state on window reset ----
    reset_anchor = (
        "        # Cross-asset / per-coin direction consistency (may01: per-coin)\n"
        "        if window_start != self._window_start_ts:\n"
        "            self._window_direction = None\n"
        "            self._window_directions.clear()  # may01: per-coin reset each window\n"
        "            self._window_start_ts = window_start\n"
        "            self._window_trends.clear()\n"
    )
    if reset_anchor not in src:
        print("[fail] window-reset anchor not found")
        sys.exit(5)
    reset_replacement = reset_anchor + (
        "            self._window_dir_seen.clear()  # may04 fix B\n"
    )
    src = src.replace(reset_anchor, reset_replacement, 1)

    # ---- patch 3: track every direction attempt + add LATE WHIPSAW gate after DIR LOCK ----
    dirlock_anchor = (
        "        # Record this coin's trend for consensus\n"
        "        self._window_trends[coin] = direction\n"
        "        \n"
        "        # may01: only block if THIS coin already committed to a different direction\n"
        "        # in this window (was: cross-asset lock, too restrictive — CONSENSUS handles cross-asset).\n"
        "        prior = self._window_directions.get(coin)\n"
        "        if prior is not None and direction != prior:\n"
        "            self._diag_log(\n"
        "                f\"dirlock-{coin}\",\n"
        "                f\"[DIR LOCK] {coin} {direction}: this coin committed to {prior} this window — skipping\",\n"
        "                15.0,\n"
        "            )\n"
        "            return None\n"
    )
    if dirlock_anchor not in src:
        print("[fail] DIR LOCK anchor not found")
        sys.exit(6)

    dirlock_replacement = (
        "        # Record this coin's trend for consensus\n"
        "        self._window_trends[coin] = direction\n"
        "\n"
        "        # may04 fix B: track every direction attempt this window\n"
        "        # (BEFORE the DIR LOCK so we capture both sides even when the\n"
        "        # second one would be locked). Used by LATE WHIPSAW gate below.\n"
        "        self._window_dir_seen.setdefault(coin, set()).add(direction)\n"
        "\n"
        "        # may01: only block if THIS coin already committed to a different direction\n"
        "        # in this window (was: cross-asset lock, too restrictive — CONSENSUS handles cross-asset).\n"
        "        prior = self._window_directions.get(coin)\n"
        "        if prior is not None and direction != prior:\n"
        "            self._diag_log(\n"
        "                f\"dirlock-{coin}\",\n"
        "                f\"[DIR LOCK] {coin} {direction}: this coin committed to {prior} this window — skipping\",\n"
        "                15.0,\n"
        "            )\n"
        "            return None\n"
        "\n"
        "        # may04 fix B: late-entry tightening for whipsaw.\n"
        "        # If we already saw the OPPOSITE direction earlier this window\n"
        "        # for this coin AND we're in the last 5 minutes, demand A-tier\n"
        "        # (prob >= 88% AND edge >= 22%). Prevents chasing a flipped\n"
        "        # micro-trend after DIR LOCK released or the prior commit.\n"
        "        _seen = self._window_dir_seen.get(coin, set())\n"
        "        _opposite = \"DOWN\" if direction == \"UP\" else \"UP\"\n"
        "        if _opposite in _seen and time_remaining < 300:\n"
        "            _ovr_p = float(getattr(config, \"MORNING_OVERRIDE_PROB\", 0.88) or 0.88)\n"
        "            _ovr_e = float(getattr(config, \"MORNING_OVERRIDE_EDGE\", 0.22) or 0.22)\n"
        "            _edge_local = win_prob - ask\n"
        "            if win_prob < _ovr_p or _edge_local < _ovr_e:\n"
        "                self._diag_log(\n"
        "                    f\"latewhip-{coin}\",\n"
        "                    f\"[LATE WHIPSAW] {coin} {direction}: opposite seen this window AND \"\n"
        "                    f\"T={time_remaining:.0f}s<300 — need A-tier (prob>={_ovr_p*100:.0f}% \"\n"
        "                    f\"edge>={_ovr_e*100:.0f}%); have prob={win_prob*100:.0f}% \"\n"
        "                    f\"edge={_edge_local*100:+.1f}%\",\n"
        "                    10.0,\n"
        "                )\n"
        "                return None\n"
    )
    src = src.replace(dirlock_anchor, dirlock_replacement, 1)

    # ---- patch 4: recent-flip detector right after trend_score is computed ----
    flip_anchor = (
        "        trend_score = 0.0\n"
        "        trend_score += dist_pct * 200.0        # position vs strike (strongest signal)\n"
        "        trend_score += roc_60 * 500.0          # 60s momentum\n"
        "        trend_score += roc_120 * 300.0         # 2min trend\n"
        "        trend_score += momentum_raw * 400.0    # weighted momentum (10s/30s/60s)\n"
    )
    if flip_anchor not in src:
        print("[fail] trend_score anchor not found")
        sys.exit(7)

    flip_replacement = flip_anchor + (
        "\n"
        "        # may04 fix C: recent-flip detector.\n"
        "        # If trend sign flipped within the last 30s and BOTH sides were\n"
        "        # non-trivial (|trend|>0.40 each), this is a whipsaw at window\n"
        "        # open — abstain for one cycle and let the new direction confirm.\n"
        "        _flip_now = now_ts\n"
        "        _flip_prev = self._last_trend_score.get(coin)\n"
        "        self._last_trend_score[coin] = (trend_score, _flip_now)\n"
        "        if _flip_prev is not None:\n"
        "            _prev_trend, _prev_ts = _flip_prev\n"
        "            _flip_dt = _flip_now - _prev_ts\n"
        "            if (\n"
        "                _flip_dt > 0\n"
        "                and _flip_dt < 30.0\n"
        "                and (_prev_trend > 0) != (trend_score > 0)\n"
        "                and abs(trend_score) > 0.40\n"
        "                and abs(_prev_trend) > 0.40\n"
        "            ):\n"
        "                self._diag_log(\n"
        "                    f\"recentflip-{coin}\",\n"
        "                    f\"[RECENT FLIP] {coin}: trend was {_prev_trend:+.2f} \"\n"
        "                    f\"{_flip_dt:.0f}s ago, now {trend_score:+.2f} — \"\n"
        "                    f\"waiting one cycle for confirmation\",\n"
        "                    10.0,\n"
        "                )\n"
        "                return None\n"
    )
    src = src.replace(flip_anchor, flip_replacement, 1)

    PRED.write_text(src)
    print("[ok] predictor.py: added _window_dir_seen, _last_trend_score, [LATE WHIPSAW], [RECENT FLIP]")
    return True


if __name__ == "__main__":
    a = patch_order_manager()
    b = patch_predictor()
    print(f"\nDONE. order_manager_changed={a}, predictor_changed={b}")
