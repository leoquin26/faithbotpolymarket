"""
Selective Tor Proxy for Polymarket

Routes traffic through Tor via environment variables, but excludes
non-Polymarket services using NO_PROXY.
"""
import os
from pathlib import Path

# Load .env FIRST
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent / '.env'
    load_dotenv(env_path)
except ImportError:
    pass

USE_TOR = os.getenv('USE_TOR', 'false').lower() == 'true'

if USE_TOR:
    # Set proxy for ALL traffic
    os.environ['HTTP_PROXY'] = 'socks5h://127.0.0.1:9050'
    os.environ['HTTPS_PROXY'] = 'socks5h://127.0.0.1:9050'
    os.environ['ALL_PROXY'] = 'socks5h://127.0.0.1:9050'
    
    # EXCLUDE these domains from proxy (direct connection)
    # This keeps MongoDB, Binance, Coinbase, Polygon fast
    no_proxy_domains = [
        'localhost',
        '127.0.0.1',
        'mongodb',
        # Binance (all endpoints)
        'binance.com',
        '.binance.com',
        'binance.us',
        '.binance.us',
        'api.binance.us',
        'stream.binance.us',
        'fstream.binance.com',
        'dstream.binance.com',
        'stream.binance.com',
        'api.binance.com',
        'fapi.binance.com',
        'dapi.binance.com',
        'ws-api.binance.com',
        'ws-fapi.binance.com',
        # Coinbase
        'coinbase.com',
        '.coinbase.com',
        'api.coinbase.com',
        'ws-feed.exchange.coinbase.com',
        'exchange.coinbase.com',
        # Bybit
        'bybit.com',
        '.bybit.com',
        'api.bybit.com',
        'stream.bybit.com',
        'stream.bybit.com',
        # Polygon/Alchemy
        'polygon-rpc.com',
        'polygon.io',
        '.polygon.io',
        'alchemy.com',
        '.alchemy.com',
        'g.alchemy.com',
        # Telegram
        'api.telegram.org',
        'telegram.org',
        # Polymarket WebSocket (doesn't work through Tor)
        'ws-live-data.polymarket.com',
    ]
    os.environ['NO_PROXY'] = ','.join(no_proxy_domains)
    
    print("[TOR] Selective proxy enabled:")
    print("      Polymarket → Tor")
    print("      Binance/Coinbase/MongoDB → Direct")
else:
    print("[TOR] Disabled")
