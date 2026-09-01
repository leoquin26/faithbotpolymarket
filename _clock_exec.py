#!/usr/bin/env python3
"""THE CLOCK executor (one-shot cron 2026-09-02 06:05 UTC).
Runs the scorer, telegrams the verdict. Kill branch executes itself
(killing a $0 process is the law's safe branch). GO branch notifies and
waits for the agent/owner — auto-LAUNCH stays revoked per CYCLE_LAW."""
import os, subprocess, sys, time

V3 = "/home/ubuntu/v3-bot"
sys.path.insert(0, V3)

out = subprocess.run(["python3", os.path.join(V3, "_clock_score.py")],
                     capture_output=True, text=True).stdout
print(out)
go = "VERDICT: >= +0.03" in out

try:
    import telegram_notifier as tg
    tg._send("⏱ <b>THE CLOCK (06:00 UTC)</b>\n<pre>" + out[-900:] + "</pre>",
             dedup_key="clock-0902")
except Exception as e:
    print("tg failed:", e)

if not go:
    open(os.path.join(V3, ".watchdog_pause"), "w").close()
    subprocess.run(["pkill", "-f", "python3 -u late_shadow.py"])
    ts = time.strftime("%Y%m%d")
    src = os.path.join(V3, "late_shadow_state.json")
    if os.path.exists(src):
        os.rename(src, os.path.join(V3, f"late_shadow_state.{ts}.archived.json"))
    try:
        import telegram_notifier as tg
        tg._send("⏱ CLOCK: below bar → late_shadow KILLED and archived per the "
                 "no-third-wait law. Watchdog paused (.watchdog_pause left in "
                 "place). No new 1H favourite seat.", dedup_key="clock-kill-0902")
    except Exception:
        pass
    print("KILL branch executed")
else:
    print("GO branch: waiting for agent/owner to write T3 amendment and launch")
