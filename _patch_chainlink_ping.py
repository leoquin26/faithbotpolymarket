"""Add app-level 5s PING keepalive + tighter protocol ping to chainlink_ws.py."""

f = "chainlink_ws.py"
s = open(f).read()

# 1) on_open: start an app-level PING-every-5s thread (docs require this).
old_open = '                on_open=lambda ws: ws.send(subs),'
new_open = '                on_open=_on_open,'
if "_on_open" in s and "def _on_open" in s:
    print("on_open already patched")
elif old_open in s:
    s = s.replace(old_open, new_open)
    # Insert the _on_open helper + pinger right before def _run()
    helper = (
        'def _on_open_factory(subs):\n'
        '    def _on_open(ws):\n'
        '        ws.send(subs)\n'
        '\n'
        '        def _pinger():\n'
        '            # RTDS docs: send app-level "PING" every 5s to stay alive.\n'
        '            while getattr(ws, "keep_running", False):\n'
        '                try:\n'
        '                    ws.send("PING")\n'
        '                except Exception:\n'
        '                    break\n'
        '                time.sleep(5)\n'
        '\n'
        '        threading.Thread(target=_pinger, daemon=True,\n'
        '                         name="chainlink-ping").start()\n'
        '    return _on_open\n'
        '\n\n'
        'def _run():\n'
    )
    s = s.replace('def _run():\n', helper, 1)
    # Bind _on_open inside _run (after subs is defined)
    s = s.replace(
        '    })\n\n    # Exponential backoff',
        '    })\n    _on_open = _on_open_factory(subs)\n\n    # Exponential backoff',
        1,
    )
    print("patched on_open + pinger OK")
else:
    print("WARN: on_open block not found")

# 2) Tighten protocol-level ping interval 15 -> 8 (server pings every 5, we answer).
s = s.replace("ping_interval=15, ping_timeout=10", "ping_interval=8, ping_timeout=6")

open(f, "w").write(s)
print("done")
