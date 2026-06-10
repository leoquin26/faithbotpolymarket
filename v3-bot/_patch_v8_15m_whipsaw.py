"""V8 Late-Window Whipsaw Block — 15m bot only.

Inserts:
  A) Ask-history tracker right after `depth = ...` (line ~545) so every
     predict() call records current up_ask/down_ask per (coin, dir) into
     a per-window rolling 90s buffer.

  B) V8 Block check right before `self._window_direction = direction`
     (the commit point, line ~675). If:
        - timeframe is 15m
        - time_remaining < 720s
        - last-90s ask swing >= 12c
        - current ask is in the top 40% of that swing range
     then return None (abstain) with a [V8 WHIPSAW BLOCK] log.

Counterfactual May 4-12 (8 flagged, 3W/5L, +$12.63 net).
Idempotent: re-runnable safely.
"""
import sys
import pathlib

PATH = pathlib.Path("/home/ubuntu/v3-bot/predictor.py")
src = pathlib.Path(PATH).read_text(encoding="utf-8")

# ─────────────────────────────────────────────────────────────────────────
# Patch A — install ask-history tracker after the `depth = ...` line
ANCHOR_A = """        ask = up_ask if is_up else down_ask
        mid = up_mid if is_up else down_mid
        depth = up_depth if is_up else down_depth
        token_id = info.up_token_id if is_up else info.down_token_id
"""

INSERT_A = ANCHOR_A + """
        # ── V8 ask-history tracker (added 2026-05-12) ──
        # Per (coin, dir), keep a rolling 90s buffer of (ts, ask). Used
        # by the V8 Late-Window Whipsaw Block below. 15m only.
        _tf_v8 = getattr(info, "timeframe", "15m")
        if _tf_v8 == "15m":
            if not hasattr(self, "_ask_history_window"):
                self._ask_history_window = {"__window__": window_start}
            elif self._ask_history_window.get("__window__") != window_start:
                self._ask_history_window = {"__window__": window_start}
            _v8_now = time.time()
            for _d8, _a8 in (("UP", up_ask), ("DOWN", down_ask)):
                _k8 = (coin, _d8)
                if _k8 not in self._ask_history_window:
                    self._ask_history_window[_k8] = []
                self._ask_history_window[_k8].append((_v8_now, _a8))
                _cut8 = _v8_now - 90.0
                self._ask_history_window[_k8] = [
                    (t8, a8) for (t8, a8) in self._ask_history_window[_k8]
                    if t8 >= _cut8
                ]
"""

# ─────────────────────────────────────────────────────────────────────────
# Patch B — install V8 Block check right before commit
ANCHOR_B = """        self._window_direction = direction
        self._chop_detector.record_direction(direction)
        regime = "CHOPPY" if self._chop_detector.is_choppy() else "TRENDING"
"""

INSERT_B = """        # ── V8 Late-Window Whipsaw Block (added 2026-05-12) ──
        # 15m only. Blocks trades entered at the top of a recent ask
        # whipsaw with little time left in the window. Counterfactual
        # May 4-12: +$12.63 net (5 losses saved, 3 wins killed).
        if _tf == "15m":
            _v8_hist = self._ask_history_window.get((coin, direction), [])
            if time_remaining < 720 and len(_v8_hist) >= 3:
                _asks_only = [a for (_, a) in _v8_hist]
                _v8_min = min(_asks_only)
                _v8_max = max(_asks_only)
                _v8_swing = _v8_max - _v8_min
                if _v8_swing >= 0.12:
                    _v8_pos = (ask - _v8_min) / _v8_swing
                    if _v8_pos >= 0.60:
                        logger.info(
                            f"[V8 WHIPSAW BLOCK] {coin} {direction}"
                            f"@{ask*100:.0f}c | swing={_v8_swing*100:.0f}c "
                            f"(min={_v8_min*100:.0f}c max={_v8_max*100:.0f}c) "
                            f"pos={_v8_pos:.2f} T={time_remaining:.0f}s "
                            f"edge={edge*100:.1f}% — abstaining"
                        )
                        return None

""" + ANCHOR_B


already_a = "_ask_history_window" in src
already_b = "V8 WHIPSAW BLOCK" in src

if ANCHOR_A not in src:
    print("ANCHOR_A not found — aborting", file=sys.stderr)
    sys.exit(1)
if ANCHOR_B not in src:
    print("ANCHOR_B not found — aborting", file=sys.stderr)
    sys.exit(1)

if already_a and already_b:
    print("Already patched (both A and B) — no-op")
    sys.exit(0)

src2 = src
if not already_a:
    src2 = src2.replace(ANCHOR_A, INSERT_A, 1)
    print("Patch A applied (ask-history tracker)")
if not already_b:
    src2 = src2.replace(ANCHOR_B, INSERT_B, 1)
    print("Patch B applied (V8 block check)")

PATH.write_text(src2, encoding="utf-8")
print("Wrote", PATH)
