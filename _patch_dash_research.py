#!/usr/bin/env python3
"""Add a 🔬 Research tab to the control center: the per-window feature/outcome
dataset (clean_bot_research.csv) — summary, skip-reason 'did we skip winners?'
breakdown, recent rows, and the live log. Verifies backtick balance. Run on EC2."""
import sys, ast
from pathlib import Path

API = Path("dashboard_v3/control_api.py")
HTML = Path("dashboard_v3/templates/control.html")

READER = '''def _research():
    """CleanBot research dataset (clean_bot_research.csv): every real-move window,
    traded or skipped, with features + true outcome. Summary + skip-reason edge view."""
    res = {"n": 0, "summary": {}, "by_reason": [], "recent": []}
    f = BOT_DIR / "clean_bot_research.csv"
    if not f.exists():
        return res
    try:
        import collections
        rows = list(csv.DictReader(f.open(encoding="utf-8", errors="ignore")))
        res["n"] = len(rows)
        def dc(rs):
            rs = [r for r in rs if r.get("winner")]
            return round(100 * sum(1 for r in rs if r.get("drift_correct") == "1") / len(rs), 1) if rs else None
        traded = [r for r in rows if r.get("decision") == "ENTER"]
        skipped = [r for r in rows if r.get("decision") == "SKIP"]
        res["summary"] = {"total": len(rows), "traded": len(traded), "skipped": len(skipped),
                          "traded_correct": dc(traded), "skipped_correct": dc(skipped),
                          "overall_correct": dc(rows)}
        byr = collections.defaultdict(list)
        for r in skipped:
            byr[r.get("reason", "?")].append(r)
        res["by_reason"] = sorted(
            [{"reason": k, "n": len(v), "drift_correct": dc(v)} for k, v in byr.items()],
            key=lambda x: -x["n"])
        res["recent"] = rows[-50:][::-1]
    except Exception as e:
        res["err"] = str(e)
    return res


'''
READER_ANCHOR = "def _clean():"

ROUTE_ANCHOR = '''    @app.route("/api/v3/clean/raw")
    def clean_raw_ep():
        try:
            n = int(request.args.get("n", 400))
        except Exception:
            n = 400
        return jsonify(_clean_raw(min(n, 2000)))
'''
ROUTE_NEW = ROUTE_ANCHOR + '''
    @app.route("/api/v3/research")
    def research_ep():
        return jsonify(_research())
'''

NAV_OLD = "['clean','🤖','CleanBot'],"
NAV_NEW = "['clean','🤖','CleanBot'],['research','🔬','Research'],"

DISP_OLD = "    if(TAB==='clean')return cleanTab(v);"
DISP_NEW = "    if(TAB==='clean')return cleanTab(v);\n    if(TAB==='research')return researchTab(v);"

TAB_FN = r'''async function researchTab(v){
  const s=await j('/api/v3/research')||{};const sm=s.summary||{};
  const rawd=await j('/api/v3/clean/raw?n=300')||{};const raw=(rawd.lines||[]).slice().reverse();
  v.innerHTML=`<div class="grid g4">
    <div class="card"><h3>Windows logged</h3><div class="kpi">${sm.total||0}</div><div class="sub">traded ${sm.traded||0} · skipped ${sm.skipped||0}</div></div>
    <div class="card"><h3>Drift-correct (traded)</h3><div class="kpi sm">${sm.traded_correct!=null?sm.traded_correct+'%':'—'}</div><div class="sub">our entries</div></div>
    <div class="card"><h3>Drift-correct (SKIPPED)</h3><div class="kpi sm ${sm.skipped_correct>=58?'pos':'mut'}">${sm.skipped_correct!=null?sm.skipped_correct+'%':'—'}</div><div class="sub">did we skip winners?</div></div>
    <div class="card"><h3>Overall</h3><div class="kpi sm">${sm.overall_correct!=null?sm.overall_correct+'%':'—'}</div><div class="sub">all windows</div></div>
  </div>
  <div class="card" style="margin-top:14px"><h3>Skip reasons — are we leaving money on the table?</h3>${(s.by_reason||[]).length?`<table><thead><tr><th>reason</th><th class="r">n</th><th class="r">drift-correct</th></tr></thead><tbody>${s.by_reason.map(r=>`<tr><td>${r.reason}</td><td class="r">${r.n}</td><td class="r ${r.drift_correct>=58?'pos':r.drift_correct!=null&&r.drift_correct<45?'neg':'mut'}">${r.drift_correct!=null?r.drift_correct+'%':'—'}</td></tr>`).join('')}</tbody></table><div class="sub" style="margin-top:6px">If a skip reason shows high drift-correct, we may be over-filtering. If low, the gate is right.</div>`:'<div class="empty">no resolved research windows yet…</div>'}</div>
  <div class="card" style="margin-top:14px"><h3>Recent windows (features + decision + outcome)</h3>${(s.recent||[]).length?`<div style="overflow-x:auto"><table><thead><tr><th>ts</th><th>coin</th><th>dir</th><th class="r">drift%</th><th class="r">roc60</th><th class="r">roc300</th><th class="r">favAsk</th><th class="r">BTCd</th><th class="r">SOLd</th><th>conf</th><th>decision</th><th>reason</th><th>win</th></tr></thead><tbody>${s.recent.map(r=>`<tr><td class="mini">${(r.ts||'').slice(11)}</td><td>${r.coin}</td><td><span class="tag ${r.dir}">${r.dir}</span></td><td class="r">${r.drift_pct}</td><td class="r">${r.roc60_bps}</td><td class="r">${r.roc300_bps}</td><td class="r">${r.fav_ask}c</td><td class="r">${r.btc_drift_pct}</td><td class="r">${r.sol_drift_pct}</td><td>${r.confirmed=='1'?'✓':'✗'}</td><td>${r.decision=='ENTER'?'<span class="pos">ENTER</span>':'<span class="mut">skip</span>'}</td><td class="mini">${r.reason}</td><td class="${r.drift_correct=='1'?'pos':'neg'}">${r.winner||'—'}</td></tr>`).join('')}</tbody></table></div>`:'<div class="empty">accumulating… (writes after each window resolves)</div>'}</div>
  <div class="card" style="margin-top:14px"><h3>Live log (clean_bot.log)</h3><div class="logbox" style="max-height:380px">${raw.length?raw.map(l=>`<div class="logline ${/WIN/.test(l)?'l-INFO pos':/LOSS/.test(l)?'l-INFO neg':/ENTER|FILLED/.test(l)?'l-INFO':'l-DEBUG'}">${esc(l)}</div>`).join(''):'<div class="empty">no log…</div>'}</div></div>`;
}
async function cleanTab(v){'''

TAB_ANCHOR = "async function cleanTab(v){"


def patch(path, edits, is_py, skip):
    s = path.read_text(encoding="utf-8")
    if skip in s:
        print(f"{path.name}: already patched"); return
    for old, new in edits:
        if s.count(old) != 1:
            print(f"ABORT {path.name}: anchor x{s.count(old)} (need 1): {old[:50]!r}"); sys.exit(1)
        s = s.replace(old, new, 1)
    if not is_py and s.count(chr(96)) % 2 != 0:
        print(f"ABORT {path.name}: odd backticks"); sys.exit(1)
    path.with_suffix(path.suffix + ".bak_research").write_text(
        path.read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text(s, encoding="utf-8")
    if is_py:
        ast.parse(s)
    print(f"{path.name}: patched OK")


patch(API, [(READER_ANCHOR, READER + READER_ANCHOR), (ROUTE_ANCHOR, ROUTE_NEW)], True, "_research()")
patch(HTML, [(NAV_OLD, NAV_NEW), (DISP_OLD, DISP_NEW), (TAB_ANCHOR, TAB_FN)], False, "researchTab")
print("done — restart dashboard")
