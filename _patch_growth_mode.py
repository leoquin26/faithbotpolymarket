"""GROWTH MODE deployment (May 13, 2026)
=====================================================

Data-driven strategic shift based on 30-day audit:
  BTC  73.7% WR  +$68.16   ← THE MONEY MAKER
  SOL  63.5% WR   +$0.38   (breakeven)
  ETH  51.1% WR  -$18.72   ← LOSING
  XRP  40.0% WR  -$14.03   ← LOSING

5m bot:
  5m BTC 85.7% WR  $1.56/trade  ← UNTAPPED EDGE
  5m SOL 55.6% WR  ~$0/trade

Strategy:
  1) Coin whitelist:   BOT_COIN_WHITELIST=BTC,SOL  (drops ETH+XRP)
  2) BTC A-tier amp:   8% Kelly cap (vs 5%) + skip V6 dampening
                       (only when prob>=78% AND edge>=18% AND entry<=0.55)
  3) 5m BTC amp:       M5_MIN_TREND_BTC=0.55 (vs 0.85 default)
                       M5_MAX_CONCURRENT=3 (vs 2)

ALL changes are env-var-controlled. Kill switches:
  BOT_COIN_WHITELIST=BTC,ETH,SOL,XRP    -> back to all 4 coins
  GROWTH_MODE_BTC_AMPLIFY=off           -> back to 5% Kelly + dampen
  M5_MIN_TREND_BTC unset                -> back to M5_MIN_TREND default

All proven loss-preventers stay: V6 / V8 / V9 / Phase-2.
"""
import re
from pathlib import Path

CONFIG = Path("/home/ubuntu/v3-bot/config.py")
ORDER  = Path("/home/ubuntu/v3-bot/order_manager.py")
BRAIN5 = Path("/home/ubuntu/v3-bot/run_brain_5m.py")


def patch_config():
    text = CONFIG.read_text()
    # Insert whitelist filter immediately after SYMBOLS dict definition.
    marker = '''SYMBOLS = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
    "XRP": "XRPUSDT",
}'''
    if marker not in text:
        raise SystemExit("SYMBOLS block not found in config.py")
    if "BOT_COIN_WHITELIST" in text:
        print("[config.py] BOT_COIN_WHITELIST already present — skipping")
        return False
    addition = '''

# ── Growth Mode (May 13, 2026): coin whitelist (15m bot) ──
# 30-day data: BTC +$68 / SOL ~$0 / ETH -$19 / XRP -$14.
# Default whitelist drops ETH+XRP; full set with BOT_COIN_WHITELIST="BTC,ETH,SOL,XRP".
_coin_whitelist_raw = os.getenv("BOT_COIN_WHITELIST", "BTC,SOL")
BOT_COIN_WHITELIST = set(
    c.strip().upper() for c in _coin_whitelist_raw.split(",") if c.strip()
)
if BOT_COIN_WHITELIST:
    SYMBOLS = {k: v for k, v in SYMBOLS.items() if k in BOT_COIN_WHITELIST}'''
    text = text.replace(marker, marker + addition)
    CONFIG.write_text(text)
    print("[config.py] Coin whitelist filter injected")
    return True


def patch_order_manager():
    text = ORDER.read_text()
    if "GROWTH_MODE_BTC_AMPLIFY" in text:
        print("[order_manager.py] GROWTH_MODE_BTC_AMPLIFY already present — skipping")
        return False
    # Inject before "size = bankroll * capped" in _calc_size.
    marker_old = """            fractional = full_kelly * kelly_fraction
            # Pct-of-bankroll ceiling (primary risk control)
            capped = min(fractional, kelly_max_pct)
            size = bankroll * capped"""
    marker_new = """            fractional = full_kelly * kelly_fraction
            # Pct-of-bankroll ceiling (primary risk control)
            capped = min(fractional, kelly_max_pct)

            # ── Growth Mode (May 13, 2026): BTC A-tier amplification ──
            # 30d data: BTC = 73.7% WR / +$68. Don't throttle our best edge.
            # Conditions:
            #   - BTC only
            #   - prob >= 78%, edge >= 18%, entry <= 0.55 (true A-tier)
            #   - GROWTH_MODE_BTC_AMPLIFY=on (default)
            # Effect:
            #   - Raise pct cap from 5% to 8% (GROWTH_BTC_KELLY_PCT)
            #   - Skip V6 dampening (treat as override_full_size)
            _growth_btc_amplify = (
                os.getenv("GROWTH_MODE_BTC_AMPLIFY", "on").lower() == "on"
                and pred.coin == "BTC"
                and p >= 0.78
                and pred.edge >= 0.18
            )
            _entry_for_amp = (
                pred.entry_price if pred.entry_price > 0.05 else pred.poly_price
            )
            if _growth_btc_amplify and _entry_for_amp <= 0.55:
                _g_cap_pct = float(os.getenv("GROWTH_BTC_KELLY_PCT", "0.08"))
                _new_capped = min(fractional, _g_cap_pct)
                if _new_capped > capped:
                    capped = _new_capped
                if getattr(pred, "_dampened", False):
                    try:
                        pred._override_full_size = True
                    except Exception:
                        pass
                logger.info(
                    f"[GROWTH BTC AMPLIFY] {pred.coin}: A-tier "
                    f"p={p:.0%} edge={pred.edge:.1%} entry={_entry_for_amp:.2f} "
                    f"-> cap={_g_cap_pct:.0%} no_dampen"
                )

            size = bankroll * capped"""
    if marker_old not in text:
        raise SystemExit("[order_manager.py] marker for Kelly cap not found")
    text = text.replace(marker_old, marker_new)
    ORDER.write_text(text)
    print("[order_manager.py] GROWTH BTC AMPLIFY block injected")
    return True


def patch_brain5():
    text = BRAIN5.read_text()
    if "M5_MIN_TREND_BTC" in text:
        print("[run_brain_5m.py] per-coin trend already present — skipping")
        return False
    marker_old = '''            actionable = [
                p for p in predictions
                if p.confidence in ("HIGH", "MEDIUM")
                and p.edge >= config.M5_MIN_EDGE
                and abs(getattr(p, "trend_score", 0.0)) >= config.M5_MIN_TREND
            ]'''
    marker_new = '''            # ── Growth Mode (May 13): per-coin trend threshold ──
            # 5m BTC: 85.7% WR over 30 days. Loosen to capture more BTC signals.
            # 5m SOL: 55.6% WR (breakeven). Keep tight to avoid noise.
            _m5_trend_btc = float(os.getenv("M5_MIN_TREND_BTC", str(config.M5_MIN_TREND)))
            def _trend_floor(_p):
                return _m5_trend_btc if _p.coin == "BTC" else config.M5_MIN_TREND
            actionable = [
                p for p in predictions
                if p.confidence in ("HIGH", "MEDIUM")
                and p.edge >= config.M5_MIN_EDGE
                and abs(getattr(p, "trend_score", 0.0)) >= _trend_floor(p)
            ]'''
    if marker_old not in text:
        raise SystemExit("[run_brain_5m.py] actionable filter marker not found")
    text = text.replace(marker_old, marker_new)
    BRAIN5.write_text(text)
    print("[run_brain_5m.py] per-coin trend floor injected")
    return True


if __name__ == "__main__":
    print("=== GROWTH MODE PATCH ===")
    a = patch_config()
    b = patch_order_manager()
    c = patch_brain5()
    print()
    print(f"Patched: config.py={a}  order_manager.py={b}  run_brain_5m.py={c}")
