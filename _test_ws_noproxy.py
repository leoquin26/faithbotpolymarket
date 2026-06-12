"""Debug websocket-client no_proxy matching."""
import os
os.environ['ALL_PROXY'] = 'socks5h://127.0.0.1:9050'
os.environ['HTTPS_PROXY'] = 'socks5h://127.0.0.1:9050'
os.environ['HTTP_PROXY'] = 'socks5h://127.0.0.1:9050'

from websocket._url import _is_no_proxy_host, get_proxy_info, parse_url

print("=== Test 1: hostname='stream.bybit.com' in ['stream.bybit.com'] ===")
print("  match:", _is_no_proxy_host("stream.bybit.com", ["stream.bybit.com", "*.bybit.com"]))

print("\n=== Test 2: with leading dot ===")
print("  match:", _is_no_proxy_host("stream.bybit.com", [".bybit.com"]))

print("\n=== Test 3: get_proxy_info(stream.bybit.com, http_no_proxy=['stream.bybit.com']) ===")
result = get_proxy_info("stream.bybit.com", True, no_proxy=["stream.bybit.com"])
print("  proxy_host, port, auth:", result)

print("\n=== Test 4: get_proxy_info no override (env all set) ===")
result = get_proxy_info("stream.bybit.com", True)
print("  proxy_host, port, auth:", result)

print("\n=== Test 5: parse_url on the actual WS_URL ===")
parsed = parse_url("wss://stream.bybit.com/v5/public/spot")
print("  result:", parsed)
