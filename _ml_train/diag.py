#!/usr/bin/env python3
"""Diagnose the signal<->outcome join so we maximize labeled training rows."""
import json, csv, collections, datetime as dt

# outcomes
out = {}
ws_out = []
for r in csv.DictReader(open("poly_reconciled.csv", encoding="utf-8")):
    w = (r.get("winner") or "").strip().upper()[:1]
    try:
        ws = int(float(r["ws"]))
    except Exception:
        continue
    if w in ("U", "D"):
        out[(r["coin"].upper(), ws)] = w
        ws_out.append(ws)
print(f"OUTCOMES: {len(out)} | ws range {min(ws_out)}..{max(ws_out)} "
      f"({dt.datetime.utcfromtimestamp(min(ws_out)):%Y-%m-%d}..{dt.datetime.utcfromtimestamp(max(ws_out)):%Y-%m-%d})")

# signals
sig_ws = []
sig = {}
evt = collections.Counter()
for l in open("trade_events.jsonl", encoding="utf-8"):
    try:
        d = json.loads(l)
    except Exception:
        continue
    evt[d.get("event")] += 1
    if d.get("event") != "SIGNAL":
        continue
    try:
        ws = int(float(d.get("window_start")))
    except Exception:
        continue
    sig[(str(d.get("coin", "")).upper(), ws)] = d
    sig_ws.append(ws)
print(f"SIGNALS: {len(sig)} unique (coin,ws) | ws range {min(sig_ws)}..{max(sig_ws)} "
      f"({dt.datetime.utcfromtimestamp(min(sig_ws)):%Y-%m-%d}..{dt.datetime.utcfromtimestamp(max(sig_ws)):%Y-%m-%d})")

# overlap
keys_o = set(out); keys_s = set(sig)
print(f"exact (coin,ws) overlap: {len(keys_o & keys_s)}")
# maybe ws off by alignment — check if signal ws are 900-aligned vs outcome
print("sample outcome ws:", sorted(ws_out)[:3], "| sample signal ws:", sorted(sig_ws)[:3])
print("outcome ws %900==0:", sum(1 for w in ws_out if w % 900 == 0), "/", len(ws_out))
print("signal  ws %900==0:", sum(1 for w in sig_ws if w % 900 == 0), "/", len(sig_ws))
# coins present
print("outcome coins:", collections.Counter(k[0] for k in keys_o))
print("signal  coins:", collections.Counter(k[0] for k in keys_s))
