import config
print('Config OK')
print(f'  DRY_RUN={config.DRY_RUN}')
print(f'  BINANCE={config.BINANCE_API}')
print(f'  has_key={bool(config.PRIVATE_KEY)}')
print(f'  has_api_creds={bool(config.API_KEY and config.API_SECRET)}')

from market_data import get_market_info, calculate_momentum, get_binance_price
btc = get_binance_price('BTCUSDT')
print(f'  BTC price: {btc}')

info = get_market_info('BTC')
if info:
    print(f'  Market: BTC threshold={info.threshold_price:.0f} dist={info.distance_percent*100:.3f}% time={info.time_remaining}min')
    print(f'  Poly: UP={info.up_poly_price*100:.0f}c DOWN={info.down_poly_price*100:.0f}c')
else:
    print('  Market: No data')

from predictor import Predictor
pred = Predictor()
if info:
    result = pred.predict(info)
    if result:
        print(f'  Prediction: {result.direction} prob={result.probability*100:.0f}% edge={result.edge*100:.1f}% conf={result.confidence}')
    else:
        print('  Prediction: None (no signal)')

from order_manager import OrderManager
om = OrderManager()
print(f'  OrderManager OK, traded_windows={len(om.traded_windows)}')

print('ALL SYSTEMS GO')
