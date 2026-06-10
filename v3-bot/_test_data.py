#!/usr/bin/env python3
"""Quick connectivity test for V3 bot."""
from market_data import get_binance_price, get_market_info
from predictor import Predictor

btc = get_binance_price("BTCUSDT")
print(f"BTC price: ${btc}")

info = get_market_info("BTC")
if info:
    print(f"Threshold: ${info.threshold_price}")
    print(f"Distance: {info.distance_percent*100:.3f}%")
    print(f"UP: {info.up_poly_price*100:.0f}c  DOWN: {info.down_poly_price*100:.0f}c")
    print(f"Time left: {info.time_remaining}min")

    p = Predictor()
    pred = p.predict(info)
    if pred:
        print(f"Prediction: {pred.direction} | Prob: {pred.probability*100:.0f}% | Edge: {pred.edge*100:.1f}% | {pred.confidence}")
        print(f"Reasoning: {pred.reasoning}")
    else:
        print("No prediction (momentum unavailable)")
else:
    print("No market info available")
