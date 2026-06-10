"""Quick liveness check for polymarket_ws cache."""
import sys, time
sys.path.insert(0, "/home/ubuntu/v3-bot")
import polymarket_ws as pws

time.sleep(8)

s = pws.get_singleton()
print("connected:", s.is_connected())
print("subscribed token count:", len(s._subscribed))

hits, misses = 0, 0
samples = []
for tok in list(s._subscribed)[:10]:
    book = pws.get_book(tok)
    if book and book.get("ask"):
        hits += 1
        age = time.time() - book.get("ts", 0)
        ask = book.get("ask")
        bid = book.get("bid")
        samples.append(f"  tok={tok[:14]} ask={ask} bid={bid} age={age:.1f}s")
    else:
        misses += 1

print(f"WS cache hits: {hits} / {hits + misses}")
for s_ in samples[:5]:
    print(s_)
