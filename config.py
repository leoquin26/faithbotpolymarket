"""
V8 Bot Configuration
Adds trend/momentum config params for V8 predictor.
"""

import os
from dotenv import load_dotenv

load_dotenv()


# === POLYMARKET CREDENTIALS ===
PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", "")
FUNDER_ADDRESS = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
API_KEY = os.getenv("POLYMARKET_API_KEY", "")
API_SECRET = os.getenv("POLYMARKET_API_SECRET", "")
API_PASSPHRASE = os.getenv("POLYMARKET_PASSPHRASE", "")
CHAIN_ID = int(os.getenv("POLYMARKET_CHAIN_ID", "137"))

# === ENDPOINTS ===
CLOB_HOST = "https://clob.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
USE_BINANCE_US = os.getenv("USE_BINANCE_US", "false").lower() == "true"
if USE_BINANCE_US:
    BINANCE_API = "https://api.binance.us/api/v3"
else:
    BINANCE_API = "https://data-api.binance.vision/api/v3"

# === TRADING MODE ===
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

# === COINS ===
SYMBOLS = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
    "XRP": "XRPUSDT",
}

# === POSITION SIZING ===
DEFAULT_POSITION_SIZE = float(os.getenv("DEFAULT_POSITION_SIZE", "5"))
MAX_SINGLE_TRADE = float(os.getenv("MAX_SINGLE_TRADE", "10"))
BANKROLL_BALANCE = float(os.getenv("BANKROLL_BALANCE", "88"))
BANKROLL_PERCENT = float(os.getenv("BANKROLL_PERCENT", "3"))

# === ENTRY ZONE ===
ENTRY_MIN = float(os.getenv("ENTRY_MIN", "0.45"))
ENTRY_MAX = float(os.getenv("ENTRY_MAX", "0.78"))
ABSOLUTE_MAX_ENTRY = ENTRY_MAX

# === EDGE REQUIREMENTS ===
MIN_EDGE = float(os.getenv("MIN_EDGE_THRESHOLD", "0.05"))
MAX_EDGE = float(os.getenv("MAX_EDGE_THRESHOLD", "0.50"))

# === CONVICTION GATES ===
MIN_DIRECTIONAL_EDGE = float(os.getenv("MIN_DIRECTIONAL_EDGE", "0.05"))
MIN_CONVICTION = float(os.getenv("MIN_CONVICTION", "0.55"))
MIN_WINDOW_AGE = int(os.getenv("MIN_WINDOW_AGE", "60"))

# === SCAN TIMING ===
SCAN_INTERVAL = 3
MIN_TIME_REMAINING = 2

# === LATENCY ARB ===
INSTANT_ENTRY = os.getenv("INSTANT_ENTRY", "true").lower() == "true"
LATENCY_WINDOW_SEC = 60

# === DAILY STOP-LOSS ===
DAILY_LOSS_LIMIT = float(os.getenv("DAILY_LOSS_LIMIT", "15"))
USE_DAILY_STOP_LOSS = os.getenv("USE_DAILY_STOP_LOSS", "true").lower() == "true"

# === FOK MODE ===
AGGRESSIVE_FOK = os.getenv("AGGRESSIVE_FOK", "true").lower() == "true"

# === NIGHT HOURS ===
SKIP_NIGHT_HOURS = os.getenv("SKIP_NIGHT_HOURS", "true").lower() == "true"
NIGHT_START_HOUR = int(os.getenv("NIGHT_START_HOUR", "22"))  # 22 UTC = 5pm Lima
NIGHT_END_HOUR = int(os.getenv("NIGHT_END_HOUR", "14"))      # 14 UTC = 9am Lima

# === CONVICTION PREMIUMS ===
EXTREME_PREMIUM = 0.08
STRONG_PREMIUM = 0.06
MODERATE_PREMIUM = 0.04

# === VOLATILITY ===
VOLATILE_COINS = {"ETH", "SOL", "XRP"}
VOLATILITY_MULT = 1.25

# === TELEGRAM ===
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# === LOGGING ===
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# === V8: TREND + MOMENTUM PARAMS ===
MIN_DISTANCE_PCT = float(os.getenv("MIN_DISTANCE_PCT", "0.0008"))
MIN_WIN_PROB = float(os.getenv("MIN_WIN_PROB", "0.68"))
MIN_CROSS_AGE = int(os.getenv("MIN_CROSS_AGE", "60"))
WARMUP_SEC = int(os.getenv("WARMUP_SEC", "75"))
MAX_WINDOW_AGE = int(os.getenv("MAX_WINDOW_AGE", "840"))
DEPTH_IMBALANCE_MIN = float(os.getenv("DEPTH_IMBALANCE_MIN", "1.2"))

# === LEGACY V4 PARAMS (kept for order_manager compatibility) ===
LATE_WINDOW_START = int(os.getenv("LATE_WINDOW_START", "720"))
LATE_WINDOW_END = int(os.getenv("LATE_WINDOW_END", "840"))
MC_PATHS = int(os.getenv("MC_PATHS", "2000"))
MC_WIN_THRESHOLD = float(os.getenv("MC_WIN_THRESHOLD", "0.80"))


def validate():
    """Return list of config issues."""
    issues = []
    if not PRIVATE_KEY:
        issues.append("Missing POLYMARKET_PRIVATE_KEY")
    if not FUNDER_ADDRESS:
        issues.append("Missing POLYMARKET_FUNDER_ADDRESS")
    if not API_KEY or not API_SECRET or not API_PASSPHRASE:
        issues.append("Missing API credentials (key/secret/passphrase)")
    return issues
