"""
Jun 2 evening recovery — Tier 0b (data-driven corrections to Tier 0).

Source of truth:
- PEAK_VERSION_MAY04/ + Apr 23 env (the $199 bot)
- PATTERN_BANK_MAY20.md (Pattern A sweet spot)
- AUDIT_MAY27_2026.md (calibrator + reversion-risk = 2.5x P&L)
- 878-position CSV analysis of Jan 12 - Feb 20 2026 (the $30 -> $1,319 run):
    * ETH 64.5% WR, +$687, +30.6% ROI   <- BIGGEST WINNER
    * BTC 67.2% WR, +$262, +15.2% ROI
    * SOL 63.5% WR, +$401, +19.0% ROI
    * XRP 60.4% WR, -$136, -6.4% ROI    <- NET LOSER
    * Entry 58-65c = 71% WR, 40% ROI    <- SWEET SPOT
    * Entry  <50c  = 42% WR             <- LOTTERY TICKETS
"""
import re
import sys
from pathlib import Path

ENV = Path("/home/ubuntu/v3-bot/.env")

# Key -> value. Order doesn't matter (idempotent).
TARGET = {
    # ── KILL THE BLEED (Jun-1 experiments that lost money) ──
    "TREND_INVERT": "off",
    "LATENCY_ARB_ENABLED": "off",
    "GAP_DETECTOR_ENABLED": "off",

    # ── RESTORE PEAK PREDICTOR MATH ──
    "TREND_SIGMOID_STEEPNESS": "3.0",   # was 1.5; peak hardcoded 3.0
    "TREND_BS_BLEND": "0.30",            # was 0.50; peak hardcoded 30% BS
    "WARMUP_SEC": "75",                  # was 45; peak required 75

    # ── RESTORE PEAK QUALITY BAR (Tier 0b: data-corrected) ──
    "MIN_WIN_PROB": "0.72",              # was 0.60; split peak Apr23 0.75 vs code 0.68
    "ENTRY_MIN": "0.55",                 # was 0.45; data: <50c was 42% WR (149 trades)
    "ENTRY_MAX": "0.72",                 # was 0.68; peak was 0.72-0.78

    # ── TIER 0b: DATA-DRIVEN COIN WHITELIST ──
    # CSV analysis Jan-Feb 2026: ETH +$687, BTC +$262, SOL +$401, XRP -$136
    # Drop XRP (net loser), add ETH (biggest winner)
    "BOT_COIN_WHITELIST": "BTC,ETH,SOL",
    "MORNING_P1_ALLOWED": "BTC,ETH,SOL",
    "MORNING_P3_ALLOWED": "BTC,ETH,SOL",
    "PM_BLOCKED_COINS": "XRP",           # belt-and-suspenders block

    # ── RESTORE PEAK FLIP DISCIPLINE ──
    "FLIP_TREND_MIN_15M": "1.5",         # was 0.30 (!!); peak was 1.5
    "EXHAUST_TH_FLIP": "0.70",           # was 0.60; peak default 0.70

    # ── RE-TIGHTEN JUN-1 LOOSENINGS ──
    "P2_HIGH_EDGE_BYPASS": "0.20",       # was 0.15; require real edge
    "V8_SWING_MIN": "0.12",              # was 0.20; peak default

    # ── KEEP MAY 27 AUDIT EDGE (verify on) ──
    "REVERSION_RISK_LIVE": "on",
    "CALIBRATION_LIVE": "on",
    "REGIME_AWARE": "on",
    "DEPTH_AWARE_KELLY": "on",

    # ── KEEP CONSERVATIVE SIZING WHILE RECOVERING ──
    "KELLY_FRACTION": "0.25",
    "KELLY_MAX_PCT": "0.05",
    "KELLY_MAX_BET": "4.00",
    "DAILY_LOSS_LIMIT": "10",
    "USE_DAILY_STOP_LOSS": "true",
}


def upsert(text: str, key: str, value: str) -> str:
    # Match KEY=anything (start of line, no whitespace handling weirdness)
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    new_line = f"{key}={value}"
    if pattern.search(text):
        return pattern.sub(new_line, text, count=1)
    if not text.endswith("\n"):
        text += "\n"
    return text + new_line + "\n"


def main():
    if not ENV.exists():
        print(f"FATAL: {ENV} not found", file=sys.stderr)
        sys.exit(1)

    text = ENV.read_text()
    original = text
    changes = []

    for key, value in TARGET.items():
        old_match = re.search(rf"^{re.escape(key)}=(.*)$", text, re.MULTILINE)
        old_value = old_match.group(1) if old_match else "<missing>"
        if old_value != value:
            changes.append(f"  {key}: {old_value} -> {value}")
        text = upsert(text, key, value)

    # Add a marker comment so future-us knows what happened
    marker = "\n# === Jun-2 RECOVERY: restored PEAK_VERSION_MAY04 quality bar ===\n"
    if marker.strip() not in text:
        text += marker

    if text == original:
        print("No changes needed — env already matches target.")
        return

    ENV.write_text(text)

    print("CHANGES APPLIED:")
    for c in changes:
        print(c)
    print()
    print(f"Total: {len(changes)} keys updated.")
    print(f"Backup: {ENV}.backup_jun02_pre_recovery (made earlier)")


if __name__ == "__main__":
    main()
