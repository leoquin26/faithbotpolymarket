import sys
sys.path.insert(0, '/home/ubuntu/v3-bot')
import config
print('Config loaded OK')
issues = config.validate()
if issues:
    for i in issues:
        print(f'  ISSUE: {i}')
else:
    print('  All credentials present')
print(f'  DRY_RUN: {config.DRY_RUN}')
print(f'  Entry cap: {config.ABSOLUTE_MAX_ENTRY}')
print(f'  Bankroll: {config.BANKROLL_BALANCE}')

from market_data import get_market_info, get_binance_price
btc = get_binance_price('BTCUSDT')
print(f'  BTC price: ' if btc else '  BTC price: FAILED')

info = get_market_info('BTC')
if info:
    print(f'  BTC market: threshold= dist={info.distance_percent*100:.3f}%')
    print(f'  UP: {info.up_poly_price*100:.0f}c  DOWN: {info.down_poly_price*100:.0f}c  Time: {info.time_remaining}min')
else:
    print('  BTC market: NO ACTIVE WINDOW')

from predictor import Predictor
pred = Predictor()
if info:
    result = pred.predict(info)
    if result:
        print(f'  Prediction: {result.coin} {result.direction} | prob={result.probability*100:.0f}% edge={result.edge*100:.1f}% conf={result.confidence}')
    else:
        print('  Prediction: None (no momentum data or too close)')
print('BOOT TEST COMPLETE')
