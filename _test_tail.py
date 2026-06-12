"""Quick test that state_reader.tail_log reads today's log."""
import sys
sys.path.insert(0, "/home/ubuntu/v3-bot")
from dashboard_v3 import state_reader

lines = state_reader.tail_log(5)
print(f"got {len(lines)} lines from {state_reader._active_log_path()}")
for l in lines:
    print("  >", l)
