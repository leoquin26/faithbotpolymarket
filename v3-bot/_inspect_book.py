from order_manager import OrderManager
from market_data import get_market_info
om = OrderManager()
info = get_market_info('BTC')
if info:
    book = om.client.get_order_book(info.up_token_id)
    print('type:', type(book))
    print('dir:', [x for x in dir(book) if not x.startswith('_')])
    asks = getattr(book, 'asks', 'MISSING')
    print('asks type:', type(asks), 'len:', len(asks) if hasattr(asks, '__len__') else 'N/A')
    print('bool(asks):', bool(asks))
    if asks and asks != 'MISSING':
        a = asks[0]
        print('first ask type:', type(a))
        print('first ask:', a)
        print('  .price:', getattr(a, 'price', 'MISSING'))
        print('  dict?:', isinstance(a, dict))
    else:
        print('asks empty/missing')
else:
    print('no market info')
