#!/usr/bin/env python3
"""Surface DRY/SIM mode + bankroll on the CleanBot dashboard tab so the weekend
paper-trade is clearly labelled. Exposes mode/version/bankroll from state. Run on EC2."""
import sys, ast
from pathlib import Path

API = Path("dashboard_v3/control_api.py")
HTML = Path("dashboard_v3/templates/control.html")

API_OLD = '''            res["state"] = {"day": d.get("day"), "wins": round(d.get("wins", 0), 2),
                            "losses": round(d.get("losses", 0), 2),
                            "net": round(d.get("wins", 0) - d.get("losses", 0), 2)}'''
API_NEW = '''            res["state"] = {"day": d.get("day"), "wins": round(d.get("wins", 0), 2),
                            "losses": round(d.get("losses", 0), 2),
                            "net": round(d.get("wins", 0) - d.get("losses", 0), 2),
                            "bankroll": round(d.get("bankroll", 0), 2),
                            "mode": d.get("mode", "LIVE"), "version": d.get("version", "")}'''

# Unique cleanTab anchor (CleanBot title appears only here) — prepend a DRY banner
# + relabel the first card. No problematic special chars in the OLD anchor.
HTML_OLD = '  v.innerHTML=`<div class="grid g4">\n    <div class="card"><h3>CleanBot</h3>'
HTML_NEW = ("  v.innerHTML=`${st.mode==='DRY'?'<div class=\"card\" style=\"background:#3a2e00;"
            "border-color:#7a5d00;margin-bottom:12px;font-size:15px\">🧪 <b>DRY / SIMULATION</b> — "
            "paper-trading the weekend, no real money. Sim bankroll $'+(st.bankroll||0)+'.</div>':''}"
            "<div class=\"grid g4\">\n    <div class=\"card\"><h3>${st.mode==='DRY'?'🧪 SIM':'CleanBot'}"
            "</h3>")


def patch(path, edits, is_py, skip):
    s = path.read_text(encoding="utf-8")
    if skip in s:
        print(f"{path.name}: already patched"); return
    for old, new in edits:
        if s.count(old) != 1:
            print(f"ABORT {path.name}: anchor x{s.count(old)} (need 1): {old[:55]!r}"); sys.exit(1)
        s = s.replace(old, new, 1)
    if not is_py and s.count(chr(96)) % 2 != 0:
        print(f"ABORT {path.name}: odd backticks"); sys.exit(1)
    path.with_suffix(path.suffix + ".bak_drysim").write_text(
        path.read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text(s, encoding="utf-8")
    if is_py:
        ast.parse(s)
    print(f"{path.name}: patched OK")


patch(API, [(API_OLD, API_NEW)], True, '"mode": d.get("mode"')
patch(HTML, [(HTML_OLD, HTML_NEW)], False, "DRY / SIMULATION")
print("done — restart dashboard")
