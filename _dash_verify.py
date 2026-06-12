"""Quick health check of dashboard snapshot."""
import json
import time
import urllib.request


def main():
    with urllib.request.urlopen("http://127.0.0.1:8080/api/v3/snapshot", timeout=10) as r:
        d = json.loads(r.read())

    now = int(time.time())
    hb15 = d.get("heartbeat", {}).get("last_event_ts")
    hb5 = d.get("bot_5m", {}).get("heartbeat", {}).get("last_event_ts")

    print("==== HEARTBEATS ====")
    if hb15:
        print("  15m last event: {}s ago".format(now - int(hb15)))
    if hb5:
        print("  5m  last event: {}s ago".format(now - int(hb5)))
    print()

    print("==== 15m TODAY ====")
    trades = d.get("trades_today", [])
    print("  trades_today rows: {}".format(len(trades)))
    for t in trades[:8]:
        coin = t.get("coin", "?")
        side = t.get("side", "?")
        ev = t.get("event") or t.get("kind") or "?"
        tm = t.get("t") or t.get("ts") or "?"
        print("    {} {} {} -> {}".format(tm, coin, side, ev))
    print("  pnl:", d.get("pnl"))
    print("  pnl_total:", d.get("pnl_total"))
    print("  session keys:", list((d.get("session") or {}).keys()))
    print()

    print("==== 5m TODAY ====")
    s5 = d.get("bot_5m", {}).get("stats", {})
    print(json.dumps(s5, indent=2))
    print()

    print("==== LATEST 6 EVENTS ====")
    for e in d.get("events", [])[-6:]:
        msg = (e.get("msg") or "")[:120]
        print("  {} [{}] {}".format(e.get("t"), e.get("bot"), msg))


if __name__ == "__main__":
    main()
