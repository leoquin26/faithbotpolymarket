#!/usr/bin/env python3
"""Cloudflare Quick Tunnel runner for the Quantum Desk.
Runs `cloudflared tunnel --url http://localhost:8096`, extracts the public
https://<random>.trycloudflare.com URL, writes it to quantum_url.txt, and pushes
it to Telegram. Keeps cloudflared alive and re-notifies when the URL rotates
(quick-tunnel URLs change on every restart). No Cloudflare account required."""
import os, re, subprocess, time

V3 = os.path.dirname(os.path.abspath(__file__))
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(V3, ".env"))
except Exception:
    pass
try:
    import telegram_notifier as tg
except Exception:
    tg = None

CLOUDFLARED = os.path.join(V3, "cloudflared")
TARGET = "http://localhost:8096"
URLFILE = os.path.join(V3, "quantum_url.txt")
RX = re.compile(r"https://[a-z0-9][a-z0-9-]*\.trycloudflare\.com")


def notify(url):
    try:
        open(URLFILE, "w").write(url + "\n")
    except Exception:
        pass
    print(f"[cf] tunnel URL: {url}", flush=True)
    if tg:
        try:
            tg._send(f"\U0001F310 <b>Quantum Desk</b> is live — join from anywhere:\n{url}\n"
                     f"user <code>leo</code> · your saved password",
                     dedup_key=f"cfurl-{url}")
        except Exception as e:
            print(f"[cf] telegram send failed: {e}", flush=True)


def run():
    while True:
        try:
            p = subprocess.Popen(
                [CLOUDFLARED, "tunnel", "--no-autoupdate", "--url", TARGET],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        except Exception as e:
            print(f"[cf] launch failed: {e} — retry 10s", flush=True)
            time.sleep(10)
            continue
        sent = False
        for line in p.stdout:
            m = RX.search(line)
            if m and not sent:
                notify(m.group(0))
                sent = True
        p.wait()
        print("[cf] cloudflared exited — restarting in 5s (URL will rotate)", flush=True)
        time.sleep(5)


if __name__ == "__main__":
    run()
