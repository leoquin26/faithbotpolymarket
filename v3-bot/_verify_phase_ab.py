import sys
sys.path.insert(0, "/home/ubuntu/v3-bot")
from dotenv import load_dotenv
load_dotenv("/home/ubuntu/v3-bot/.env")
import config
print("CHOPPY_MIN_TREND_ABS:", config.CHOPPY_MIN_TREND_ABS)
print("FLIP_TREND_MIN_5M:   ", config.FLIP_TREND_MIN_5M)
print("FLIP_TREND_MIN_15M:  ", config.FLIP_TREND_MIN_15M)
print("TRAP_BAND_OVERRIDE_PROB:", config.TRAP_BAND_OVERRIDE_PROB)
print("TRAP_BAND_OVERRIDE_EDGE:", config.TRAP_BAND_OVERRIDE_EDGE)
print("OBI_HARD_MIN:", config.OBI_HARD_MIN)
print("OBI_SOFT_MIN:", config.OBI_SOFT_MIN)
print("OBI_SOFT_EDGE_BOOST:", config.OBI_SOFT_EDGE_BOOST)
print("15m SYMBOLS:", list(config.SYMBOLS.keys()))
print("5m M5_COINS:", config.M5_COINS)
print("M5_MAX_CONCURRENT:", config.M5_MAX_CONCURRENT)
