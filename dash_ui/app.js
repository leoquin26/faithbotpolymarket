// CleanBot Desk — React SPA (no build step: React+htm via ESM, Chart.js UMD)
import React from "react";
import { createRoot } from "react-dom/client";
import htm from "htm";
const html = htm.bind(React.createElement);
const { useState, useEffect, useRef, useMemo } = React;

/* ── helpers ─────────────────────────────────────────────────────────── */
const money = v => v == null ? "—" : (v < 0 ? "-$" : "$") + Math.abs(+v).toFixed(2);
const sgn = v => v == null ? "—" : (v >= 0 ? "+$" : "-$") + Math.abs(+v).toFixed(2);
const cls = v => v > 0 ? "pos" : v < 0 ? "neg" : "dim";
const pct = v => v == null ? "—" : (+v).toFixed(1) + "%";
const C = { profit:"#45C48A", loss:"#E5636C", blue:"#6FA8FF", amber:"#E0B34C",
            violet:"#9D8CFF", cyan:"#4CC3D9", grid:"#1E2A40", ink2:"#9AA9C0", mut:"#64748F" };

function useApi(url, ms) {
  const [data, setData] = useState(null);
  useEffect(() => {
    let live = true;
    const go = () => fetch(url).then(r => r.json()).then(d => { if (live) setData(d); }).catch(() => {});
    go();
    const t = setInterval(go, ms);
    return () => { live = false; clearInterval(t); };
  }, [url, ms]);
  return data;
}

/* one Chart.js instance per canvas, rebuilt on spec change */
function Chart_({ build, deps, height = 260 }) {
  const ref = useRef(null);
  const chart = useRef(null);
  useEffect(() => {
    if (!ref.current) return;
    if (chart.current) { chart.current.destroy(); chart.current = null; }
    const spec = build();
    if (spec) chart.current = new window.Chart(ref.current.getContext("2d"), spec);
    return () => { if (chart.current) { chart.current.destroy(); chart.current = null; } };
  }, deps);
  return html`<canvas ref=${ref} style=${{ maxHeight: height + "px" }}></canvas>`;
}
const axis = { ticks: { color: C.mut, font: { family: "'IBM Plex Mono'", size: 10 }, maxTicksLimit: 8 },
               grid: { color: C.grid } };
const noLegend = { plugins: { legend: { display: false }, tooltip: { mode: "index", intersect: false } },
                   animation: false, responsive: true, maintainAspectRatio: false };

/* ── tape header (signature) ─────────────────────────────────────────── */
function Tape({ d, sys }) {
  if (!d) return html`<div class="tape"><div class="lbl">connecting…</div></div>`;
  const sig = d.signal || {};
  const paused = sys && sys.watchdog_paused === false && sig.threshold >= 900 ? true : (sig.threshold >= 900);
  const mode = d.status === "DOWN" ? ["down", "PROCESS DOWN"]
    : d.status === "STALE" ? ["down", "HEARTBEAT STALE"]
    : d.active_stop ? ["standby", "STOP: " + d.active_stop]
    : sig.threshold >= 900 ? ["paused", "TRADING PAUSED"]
    : sig.trading === false ? ["standby", "STANDING DOWN"]
    : ["trading", "TRADING"];
  const edge = sig.edge, span = 12;
  const pin = edge == null ? 50 : Math.max(2, Math.min(98, (edge + span) / (2 * span) * 100));
  const notch = (-2 + span) / (2 * span) * 100;
  return html`<div class="tape">
    <div class="wallet">
      <div class="lbl">Wallet · chain truth</div>
      <div class="v">${money(d.chain_bankroll)}</div>
      <div class="s ${cls(d.day_pnl)}" style=${{ font: "600 13px 'IBM Plex Mono'" }}>${sgn(d.day_pnl)} today</div>
    </div>
    <div class="gauge">
      <div class="lbl">Market signal · edge vs break-even</div>
      <div class="track">
        <div class="notch" style=${{ left: notch + "%" }}></div>
        <div class="pin" style=${{ left: `calc(${pin}% - 2px)` }}></div>
      </div>
      <div class="val ${edge == null ? "dim" : edge >= -2 ? "pos" : "neg"}">
        ${edge == null ? "insufficient data" : (edge > 0 ? "+" : "") + edge + " pts"}
        <span class="dim"> · gate at −2</span>
      </div>
    </div>
    <div class=${"lamp " + mode[0]}><span class="dot"></span>${mode[1]}</div>
    <div class="meta">
      v${d.version || "?"} · scan ${d.scan ?? "—"}<br/>
      hb ${d.hb_age == null ? "—" : d.hb_age + "s"} · ${new Date(d.ts * 1000).toLocaleTimeString()}
    </div>
  </div>`;
}

function Banner({ d }) {
  if (!d) return null;
  const sig = d.signal || {};
  let kind = null, msg = "";
  if (d.status === "DOWN") { kind = "err"; msg = "⛔ Bot process not running — the watchdog restarts it within 5 minutes."; }
  else if (d.status === "STALE") { kind = "warn"; msg = `⚠️ Heartbeat stale (${d.hb_age ?? "?"}s) — bot may be hung.`; }
  else if (sig.threshold >= 900) {
    if (sig.late_live) { kind = "warn"; msg = `🧪 Early drift strategy paused (negative-EV out-of-sample). Live now: late-window Strategy #3 audition — min-size on ${sig.late_coins || "SOL,XRP"}, independent of the signal-health gate.`; }
    else { kind = "warn"; msg = "⏸ Early strategy paused (negative-EV out-of-sample). Research, scout and data collection continue."; }
  }
  else if (d.active_stop) { kind = "warn"; msg = `🔒 Stop active: ${d.active_stop} — no new entries (auto-clears at midnight).`; }
  else if (sig.trading === false) { kind = "warn"; msg = `🛡️ Signal-health stand-down (${sig.edge} pts) — auto-resumes when the tape is winnable.`; }
  if (!kind) return null;
  return html`<div class=${"banner " + kind}>${msg}</div>`;
}

/* ── views ───────────────────────────────────────────────────────────── */
function Overview({ d }) {
  if (!d) return null;
  const mt = d.metrics_today || {}, ma = d.metrics_all || {};
  const eq = d.equity || [];
  return html`<div>
    <div class="grid cards" style=${{ marginBottom: "14px" }}>
      <div class="card"><div class="k">Today</div><div class=${"v " + cls(mt.total_pnl)}>${sgn(mt.total_pnl)}</div>
        <div class="s">${mt.wins ?? 0}W / ${mt.losses ?? 0}L ${mt.n ? `· ${mt.win_rate}%` : ""}</div></div>
      <div class="card"><div class="k">Streak</div><div class="v">${mt.streak || 0}</div>
        <div class="s">${mt.streak_type === "WIN" ? "wins in a row" : mt.streak_type === "LOSS" ? "losses in a row" : "—"}</div></div>
      <div class="card"><div class="k">All-time</div><div class="v">${pct(ma.win_rate)}</div><div class="s">${ma.n ?? 0} trades</div></div>
      <div class="card"><div class="k">Profit factor</div><div class="v">${mt.profit_factor ?? "—"}</div><div class="s">today</div></div>
      <div class="card"><div class="k">Avg win / loss</div>
        <div class="v" style=${{ fontSize: "16px" }}><span class="pos">${sgn(mt.avg_win)}</span> <span class="dim">/</span> <span class="neg">${sgn(mt.avg_loss)}</span></div>
        <div class="s">per trade</div></div>
    </div>
    <div class="panel" style=${{ marginBottom: "14px" }}>
      <h2>Engines — self-governance <span class="right dim mono">verdict at n=40: ≤−3% off · ≥+3% scale</span></h2>
      <div class="grid g3">
        ${(d.engines || []).filter(e => e.engine !== "voldiv" || e.n > 0).map(e => html`<div key=${e.engine} style=${{ padding: "10px 12px", background: "var(--panel2)", borderRadius: "10px", border: "1px solid var(--line)" }}>
          <div class="kv" style=${{ borderBottom: "none", padding: "0 0 6px" }}>
            <b class="mono" style=${{ textTransform: "uppercase" }}>${e.engine}</b>
            <span class=${"chip " + (e.off ? "cL" : e.mult > 1 ? "cW" : "cN")}>${e.status}</span>
          </div>
          <div class="meter" style=${{ marginBottom: "8px" }}><i style=${{ width: Math.min(100, (e.n / e.target) * 100) + "%", background: e.off ? "var(--loss)" : "var(--blue)" }}></i></div>
          <div class="kv" style=${{ borderBottom: "none", padding: "2px 0" }}><span class="dim">progress</span><b class="mono">${e.n}/${e.target}</b></div>
          <div class="kv" style=${{ borderBottom: "none", padding: "2px 0" }}><span class="dim">win rate</span><b class="mono">${e.wr == null ? "—" : e.wr + "%"}</b></div>
          <div class="kv" style=${{ borderBottom: "none", padding: "2px 0" }}><span class="dim">EV per $</span><b class=${"mono " + (e.ev == null ? "dim" : e.ev > 0 ? "pos" : "neg")}>${e.ev == null ? "—" : (e.ev > 0 ? "+" : "") + e.ev}</b></div>
          <div class="kv" style=${{ borderBottom: "none", padding: "2px 0" }}><span class="dim">net / size</span><b class=${"mono " + cls(e.net)}>${sgn(e.net)} · x${e.mult}</b></div>
        </div>`)}
      </div>
    </div>
    <div class="grid g23">
      <div class="panel"><h2>Equity — chain truth <span class="right dim mono">${eq.length} pts</span></h2>
        <${Chart_} deps=${[eq.length, eq.length && eq[eq.length-1].v]} height=${290} build=${() => eq.length && ({
          type: "line",
          data: { labels: eq.map(p => p.ts.slice(5, 16)),
            datasets: [{ data: eq.map(p => p.v), borderColor: C.profit, borderWidth: 2, pointRadius: 0,
              tension: .25, fill: true,
              backgroundColor: (ctx) => { const g = ctx.chart.ctx.createLinearGradient(0,0,0,280);
                g.addColorStop(0,"rgba(69,196,138,.25)"); g.addColorStop(1,"rgba(69,196,138,0)"); return g; } }] },
          options: { ...noLegend, scales: { x: axis, y: { ...axis, ticks: { ...axis.ticks, callback: v => "$" + v } } } }
        })} />
      </div>
      <div>
        <div class="panel" style=${{ marginBottom: "14px" }}>
          <h2>Open positions <span class="right"><span class="chip cN">${(d.open_positions||[]).length}</span></span></h2>
          ${(d.open_positions || []).length ? d.open_positions.map(p => {
            const mm = Math.floor(p.t_left / 60), ss = String(p.t_left % 60).padStart(2, "0");
            return html`<div class="kv" key=${p.coin + p.ws}>
              <span><span class="chip cN">${p.coin}</span> <span class=${"chip " + (p.dir === "UP" ? "cU" : "cD")}>${p.dir}</span> @${Math.round(p.entry * 100)}c</span>
              <span>${p.drift_bps != null ? html`<b class=${p.drift_bps > 0 ? "pos" : "neg"}>${(p.drift_bps > 0 ? "+" : "") + p.drift_bps}bps</b> ` : ""}
                ${p.winning == null ? "" : p.winning ? html`<span class="chip cW">WINNING</span>` : html`<span class="chip cL">LOSING</span>`}
                <b class="mono"> ${mm}:${ss}</b></span>
            </div>`; }) : html`<div class="dim">none — scanning</div>`}
        </div>
        <div class="panel"><h2>Last trades</h2>
          ${(d.trades || []).slice(0, 6).map(t => html`<div class="kv" key=${t.ts}>
            <span class="dim mono">${t.ts.slice(5, 16)}</span>
            <span>${t.coin} <span class=${"chip " + (t.dir === "UP" ? "cU" : "cD")}>${t.dir}</span> ${t.entry}c</span>
            <b class=${cls(t.pnl)}>${sgn(t.pnl)}</b></div>`)}
        </div>
      </div>
    </div>
  </div>`;
}

function Trading({ d }) {
  if (!d) return null;
  const hist = d.day_history || [];
  return html`<div class="grid g23">
    <div class="panel"><h2>Trades</h2>
      <div class="tscroll"><table>
        <thead><tr><th>Time</th><th>Coin</th><th>Dir</th><th>Entry</th><th>Result</th><th>P&L</th><th>Wallet</th></tr></thead>
        <tbody>${(d.trades || []).map(t => html`<tr key=${t.ts + t.coin}>
          <td class="dim">${t.ts.slice(5, 16)}</td><td>${t.coin}</td>
          <td><span class=${"chip " + (t.dir === "UP" ? "cU" : "cD")}>${t.dir}</span></td>
          <td>${t.entry}c</td>
          <td><span class=${"chip " + (t.result === "WIN" ? "cW" : "cL")}>${t.result}</span></td>
          <td class=${cls(t.pnl)}>${sgn(t.pnl)}</td><td class="dim">${money(t.bankroll)}</td></tr>`)}
        </tbody></table></div>
    </div>
    <div>
      <div class="panel" style=${{ marginBottom: "14px" }}><h2>Daily P&L — last 7</h2>
        <${Chart_} deps=${[hist.map(h => h.pnl).join()]} height=${170} build=${() => hist.length && ({
          type: "bar",
          data: { labels: hist.map(h => h.day.slice(5)),
            datasets: [{ data: hist.map(h => h.pnl),
              backgroundColor: hist.map(h => h.pnl >= 0 ? "rgba(69,196,138,.75)" : "rgba(229,99,108,.75)"),
              borderRadius: 4, borderSkipped: "bottom" }] },
          options: { ...noLegend, scales: { x: axis, y: { ...axis, ticks: { ...axis.ticks, callback: v => "$" + v } } } }
        })} />
        <table style=${{ marginTop: "10px" }}><tbody>
          ${hist.slice().reverse().map(h => html`<tr key=${h.day}>
            <td class="dim">${h.day.slice(5)}</td><td>${h.w}–${h.l}</td><td>${h.wr}%</td>
            <td class=${cls(h.pnl)}>${sgn(h.pnl)}</td></tr>`)}
        </tbody></table>
      </div>
      <div class="panel" style=${{ marginBottom: "14px" }}><h2>Today splits</h2>
        ${Object.entries(d.splits_today || {}).map(([k, v]) => html`<div class="kv" key=${k}>
          <span>${k}</span><span>${v.wr}% <span class="dim">(${v.w}/${v.n})</span></span>
          <b class=${cls(v.pnl)}>${sgn(v.pnl)}</b></div>`)}
        ${!Object.keys(d.splits_today || {}).length && html`<div class="dim">no trades yet</div>`}
      </div>
      <div class="panel"><h2>Guards today</h2>
        ${Object.entries(d.guards_today || {}).map(([k, v]) => html`<div class="kv" key=${k}><span>${k}</span><b>${v}</b></div>`)}
        <div class="kv"><span class="dim">weak drift (no signal)</span><b class="dim">${(d.skips_today || {}).weak_drift || 0}</b></div>
        <div class="kv"><span class="dim">price out of band</span><b class="dim">${(d.skips_today || {}).ask_out_of_zone || 0}</b></div>
      </div>
    </div>
  </div>`;
}

function Research() {
  const [coin, setCoin] = useState(""); const [phase, setPhase] = useState(""); const [dec, setDec] = useState("");
  const url = `/api/research?coin=${coin}&phase=${phase}&decision=${dec}&limit=200`;
  const d = useApi(url, 30000);
  const agg = (d && d.agg) || {}, cov = (d && d.coverage) || {};
  return html`<div>
    <div class="toolbar">
      <select value=${coin} onChange=${e => setCoin(e.target.value)}>
        <option value="">All coins</option>${["ETH","SOL","BTC","XRP"].map(c => html`<option key=${c} value=${c}>${c}</option>`)}
      </select>
      <select value=${phase} onChange=${e => setPhase(e.target.value)}>
        <option value="">Both phases</option><option value="early">Early window</option><option value="late">Late window (momentum-into-close)</option>
      </select>
      <select value=${dec} onChange=${e => setDec(e.target.value)}>
        <option value="">All decisions</option><option value="ENTER">Entered</option><option value="SKIP">Skipped</option>
      </select>
    </div>
    <div class="grid cards" style=${{ marginBottom: "14px" }}>
      <div class="card"><div class="k">Windows logged</div><div class="v">${agg.total ?? "—"}</div><div class="s">${agg.resolved ?? 0} resolved</div></div>
      <div class="card"><div class="k">In-band signal</div><div class="v">${agg.wr != null ? agg.wr + "%" : "—"}</div>
        <div class="s">vs break-even ${agg.be != null ? agg.be + "%" : "—"} · n=${agg.inband ?? 0}</div></div>
      <div class="card"><div class="k">Regime tagged</div><div class="v">${cov.er ?? 0}</div><div class="s">ER rows</div></div>
      <div class="card"><div class="k">Flow tagged</div><div class="v">${cov.flow60 ?? 0}</div><div class="s">order-flow rows</div></div>
      <div class="card"><div class="k">HMM tagged</div><div class="v">${cov.hmm ?? 0}</div><div class="s">regime posteriors</div></div>
      <div class="card"><div class="k">Late snapshots</div><div class="v">${cov.late ?? 0}</div><div class="s">strategy #3 audition</div></div>
    </div>
    <div class="panel"><h2>Window log <span class="right dim mono">newest first</span></h2>
      <div class="tscroll"><table>
        <thead><tr><th>ts (UTC)</th><th>Coin</th><th>Dir</th><th>Drift%</th><th>Ask</th><th>ER</th><th>Flow</th><th>HMM</th><th>Decision</th><th>Winner</th><th>✓</th><th>Phase</th></tr></thead>
        <tbody>${((d && d.rows) || []).map((r, i) => html`<tr key=${i}>
          <td class="dim">${(r.ts || "").slice(5, 16)}</td><td>${r.coin}</td>
          <td><span class=${"chip " + (r.dir === "UP" ? "cU" : "cD")}>${r.dir}</span></td>
          <td class=${cls(+r.drift_pct)}>${r.drift_pct}</td><td>${r.fav_ask}c</td>
          <td class="dim">${r.er}</td><td class="dim">${r.flow60}</td><td class="dim">${(r.hmm || "").slice(0, 5)}</td>
          <td>${r.decision === "ENTER" ? html`<span class="chip cA">ENTER</span>` : html`<span class="dim">${r.reason || "skip"}</span>`}</td>
          <td>${r.winner}</td>
          <td>${r.drift_correct === "1" ? html`<span class="pos">✓</span>` : r.drift_correct === "0" ? html`<span class="neg">✗</span>` : "—"}</td>
          <td>${r.phase === "late" ? html`<span class="chip cD">late</span>` : html`<span class="dim">early</span>`}</td></tr>`)}
        </tbody></table></div>
    </div>
  </div>`;
}

function Strategy2({ d }) {
  const sc = (d && d.scout) || {};
  const b = sc.band || {};
  const gn = (b.sweet && b.sweet.n) || 0;
  return html`<div class="grid g2">
    <div>
      <div class="panel" style=${{ marginBottom: "14px" }}><h2>Live model vs market — daily thresholds</h2>
        ${Object.entries(sc.live || {}).map(([c, v]) => {
          const big = Math.abs(v.edge) >= 8, sweet = Math.abs(v.edge) >= 5 && !big;
          return html`<div class="kv" key=${c}>
            <span>${c} <span class="dim">${">"}$${v.strike}</span> <span class="dim mono">${v.ts}</span></span>
            <span class="mono">model <b>${v.model}%</b> · mkt <b>${v.mkt}%</b></span>
            <span><b class=${cls(v.edge)}>${(v.edge > 0 ? "+" : "") + v.edge}%</b>
              ${big ? html`<span class="chip cL"> TRAP ZONE</span>` : sweet ? html`<span class="chip cW"> IN BAND</span>` : html`<span class="chip cN"> no edge</span>`}</span>
          </div>`; })}
        ${!Object.keys(sc.live || {}).length && html`<div class="dim">no readings yet</div>`}
      </div>
      <div class="panel"><h2>Verification gate</h2>
        <div class="kv"><span>Samples in the 5–8% band</span><b>${gn} / 80</b></div>
        <div class="meter"><i style=${{ width: Math.min(100, gn / 80 * 100) + "%", background: C.cyan }}></i></div>
        <div class="dim" style=${{ fontSize: "12px", marginTop: "6px" }}>
          +${sc.cal_n || 0} calibration rows since scout v2 · trades ONLY on a gate pass (n≥80, z≥1.64, EV>0)
        </div>
      </div>
    </div>
    <div>
      <div class="grid g2" style=${{ marginBottom: "14px" }}>
        <div class="card"><div class="k">5–8% band (the edge)</div>
          <div class=${"v " + (b.sweet && b.sweet.ev > 0 ? "pos" : "dim")}>${b.sweet && b.sweet.n ? (b.sweet.ev > 0 ? "+" : "") + "$" + b.sweet.ev + "/$" : "—"}</div>
          <div class="s">n=${(b.sweet && b.sweet.n) || 0} · WR ${(b.sweet && b.sweet.wr) || "—"}%</div></div>
        <div class="card"><div class="k">≥8% (informed — never trade)</div>
          <div class=${"v " + (b.trap && b.trap.ev > 0 ? "" : "neg")}>${b.trap && b.trap.n ? (b.trap.ev > 0 ? "+" : "") + "$" + b.trap.ev + "/$" : "—"}</div>
          <div class="s">n=${(b.trap && b.trap.n) || 0}</div></div>
      </div>
      <div class="panel"><h2>Latest divergence flags</h2>
        <div class="tscroll" style=${{ maxHeight: "300px" }}><table>
          <thead><tr><th>Time</th><th>Coin</th><th>Strike</th><th>Model</th><th>Mkt</th><th>Edge</th></tr></thead>
          <tbody>${((sc.flags) || []).slice().reverse().map((f, i) => html`<tr key=${i}>
            <td class="dim">${f.ts}</td><td>${f.coin}</td><td>$${f.strike}</td>
            <td>${f.model}%</td><td>${f.mkt}%</td>
            <td class=${cls(f.edge)}>${(f.edge > 0 ? "+" : "") + f.edge}%</td></tr>`)}
          </tbody></table></div>
      </div>
    </div>
  </div>`;
}

function Signals({ d }) {
  const sh = useApi("/api/sighist", 30000);
  const pts = (sh && sh.points) || [];
  const hmm = ((d && d.signal) || {}).hmm || {};
  const shadow = (d && d.shadow) || {};
  return html`<div>
    <div class="panel" style=${{ marginBottom: "14px" }}>
      <h2>Signal health — market-wide edge vs break-even <span class="right dim mono">gate at −2 · ${pts.length} pts</span></h2>
      <${Chart_} deps=${[pts.length, pts.length && pts[pts.length-1].edge]} height=${240} build=${() => pts.length && ({
        type: "line",
        data: { labels: pts.map(p => p.ts.slice(5, 16)),
          datasets: [
            { label: "edge", data: pts.map(p => p.edge), borderColor: C.amber, borderWidth: 2,
              pointRadius: 0, tension: .2 },
            { label: "gate", data: pts.map(() => -2), borderColor: C.mut, borderWidth: 1,
              borderDash: [5, 4], pointRadius: 0 }] },
        options: { ...noLegend, scales: { x: axis, y: { ...axis, ticks: { ...axis.ticks, callback: v => v + "pt" } } } }
      })} />
    </div>
    <div class="grid g2">
      <div class="panel"><h2>Regime per coin — ER structure + HMM state</h2>
        ${Object.entries((d && d.regime) || {}).map(([c, v]) => {
          const trend = v != null && v >= 0.32;
          const h = hmm[c] || "";
          return html`<div class="kv" key=${c}>
            <span style=${{ minWidth: "88px" }}>${c} ${v == null ? "" : trend ? html`<span class="chip cW">TREND</span>` : html`<span class="chip cL">CHOP</span>`}</span>
            <span style=${{ flex: 1 }}><span class="meter"><i style=${{ width: Math.min(100, Math.round((v || 0) * 100)) + "%",
              background: trend ? C.profit : C.loss }}></i></span></span>
            <b class="mono">${v ?? "—"}</b>
            <span class="chip cN mono" title="HMM posterior T/C/P">${h ? "HMM " + h.split("/")[0] : "hmm —"}</span>
          </div>`; })}
      </div>
      <div class="panel"><h2>Shadow coins — verifier gate</h2>
        ${["BTC", "XRP"].map(c => { const v = shadow[c] || {};
          if (!v.n) return html`<div class="kv" key=${c}><span>${c}</span><span class="dim">no data</span></div>`;
          return html`<div key=${c} style=${{ marginBottom: "12px" }}>
            <div class="kv" style=${{ border: "none" }}>
              <span><b>${c}</b> <span class="dim mono">WR ${v.wr}% vs BE ${v.be}% · z ${v.z} · EV ${(v.ev > 0 ? "+" : "") + v.ev}</span></span>
              ${v.gate ? html`<span class="chip cW">GATE PASS ✓</span>` :
                (v.ev > 0 && v.z >= 1.64) ? html`<span class="chip cN">needs n≥80 (${v.n})</span>` :
                v.ev > 0 ? html`<span class="chip cA">needs z≥1.64 (${v.z})</span>` : html`<span class="chip cL">failing</span>`}
            </div>
            <div class="meter"><i style=${{ width: Math.min(100, v.n / 80 * 100) + "%", background: v.ev > 0 ? C.cyan : C.loss }}></i></div>
          </div>`; })}
        <div class="dim" style=${{ fontSize: "12px", marginTop: "8px" }}>A coin trades only after passing: n≥80 · z≥1.64 vs break-even · EV>0, out-of-sample.</div>
      </div>
    </div>
  </div>`;
}

function Logs() {
  const [buf, setBuf] = useState([]);
  const [filter, setFilter] = useState("all");
  const [q, setQ] = useState("");
  const [paused, setPaused] = useState(false);
  const [scroll, setScroll] = useState(true);
  const off = useRef(-1);
  const box = useRef(null);
  useEffect(() => {
    let live = true;
    const tick = () => {
      if (paused) return;
      fetch("/api/logs?since=" + off.current).then(r => r.json()).then(d => {
        if (!live) return;
        off.current = d.offset;
        if (d.lines.length) setBuf(b => [...b, ...d.lines].slice(-1500));
      }).catch(() => {});
    };
    tick();
    const t = setInterval(tick, 3000);
    return () => { live = false; clearInterval(t); };
  }, [paused]);
  useEffect(() => { if (scroll && box.current) box.current.scrollTop = box.current.scrollHeight; }, [buf, scroll]);
  const cat = l => /\[(WIN|LOSS|ENTER|FILLED|GTC|CANCEL)/.test(l) ? "trade"
    : /(SKIP|COOLDOWN|DIVERGE|CORR|STOP|BREAKER|SIGNAL-HEALTH|DEEP CHOP)/.test(l) ? "guard"
    : /(ERROR|FAIL|Traceback)/i.test(l) ? "err" : "sys";
  const color = l => /LOSS/.test(l) ? "lg-l" : /\[(WIN|ENTER|FILLED|GTC)/.test(l) ? "lg-t"
    : /(SKIP|COOLDOWN|STOP|SIGNAL-HEALTH|DEEP CHOP|CORR)/.test(l) ? "lg-g"
    : /(ERROR|FAIL|Traceback)/i.test(l) ? "lg-e"
    : /(RECONCILED|SYNC|STRIKE|SIG\]|alive|start )/.test(l) ? "lg-s" : "lg-x";
  const vis = buf.filter(l => (filter === "all" || cat(l) === filter) && (!q || l.toLowerCase().includes(q)));
  return html`<div class="panel">
    <h2>Live log <span class="right dim mono">${vis.length} lines</span></h2>
    <div class="toolbar">
      ${["all", "trade", "guard", "sys", "err"].map(f => html`
        <button key=${f} class=${"btn" + (filter === f ? " on" : "")} onClick=${() => setFilter(f)}>${f}</button>`)}
      <input type="text" placeholder="search…" value=${q} onInput=${e => setQ(e.target.value.toLowerCase())} />
      <button class=${"btn" + (paused ? " on" : "")} onClick=${() => setPaused(p => !p)}>${paused ? "▶ resume" : "⏸ pause"}</button>
      <button class=${"btn" + (scroll ? " on" : "")} onClick=${() => setScroll(s => !s)}>⤓ autoscroll</button>
    </div>
    <div class="logbox" ref=${box}>
      ${vis.map((l, i) => html`<div key=${i} class=${color(l)}>${l}</div>`)}
    </div>
  </div>`;
}

function SystemView() {
  const s = useApi("/api/system", 30000);
  if (!s) return html`<div class="dim">loading…</div>`;
  return html`<div class="grid g2">
    <div>
      <div class="panel" style=${{ marginBottom: "14px" }}><h2>Processes</h2>
        ${Object.entries(s.procs || {}).map(([k, v]) => html`<div class="kv" key=${k}>
          <span>${k}</span>
          <span>${v.up ? html`<span class="chip cW">UP</span>` : html`<span class="chip cL">DOWN</span>`}
            <span class="dim mono"> pid ${v.pids.join(",") || "—"}</span></span></div>`)}
        <div class="kv"><span>watchdog (cron */5)</span>
          <span>${s.watchdog_paused ? html`<span class="chip cA">PAUSED</span>` : html`<span class="chip cW">ARMED</span>`}</span></div>
      </div>
      <div class="panel"><h2>Disk</h2>
        <div class="kv"><span>used / free</span><b>${(s.disk || {}).used || "—"} / ${(s.disk || {}).avail || "—"}</b></div>
        <div class="meter"><i style=${{ width: (s.disk || {}).pct || "0%",
          background: parseInt((s.disk || {}).pct) > 88 ? C.loss : parseInt((s.disk || {}).pct) > 75 ? C.amber : C.profit }}></i></div>
        <div class="dim" style=${{ marginTop: "6px", fontSize: "12px" }}>${(s.disk || {}).pct || "—"} used · weekly log rotation Sundays 3am</div>
      </div>
    </div>
    <div>
      <div class="panel" style=${{ marginBottom: "14px" }}><h2>Version history — restarts</h2>
        ${(s.restarts || []).slice().reverse().map((r, i) => html`<div class="kv" key=${i}>
          <span class="dim mono">${r.ts}</span><b>v${r.version}</b></div>`)}
      </div>
      <div class="panel"><h2>Watchdog log</h2>
        ${(s.watchdog || []).length ? s.watchdog.map((l, i) => html`<div class="kv mono" key=${i} style=${{ fontSize: "12px" }}>${l}</div>`)
          : html`<div class="dim">no interventions — everything has stayed up</div>`}
      </div>
    </div>
  </div>`;
}

/* ── shell ───────────────────────────────────────────────────────────── */
const VIEWS = [
  ["overview", "◉", "Overview"],
  ["trading", "⇄", "Trading"],
  ["research", "⌬", "Research"],
  ["strategy2", "🛰", "Strategy #2"],
  ["signals", "∿", "Signals & Regime"],
  ["logs", "≣", "Live Logs"],
  ["system", "⚙", "System"],
];

function App() {
  const [route, setRoute] = useState((location.hash || "#/overview").slice(2));
  useEffect(() => {
    const f = () => setRoute((location.hash || "#/overview").slice(2));
    addEventListener("hashchange", f);
    return () => removeEventListener("hashchange", f);
  }, []);
  const d = useApi("/api/data", 4000);
  const sys = useApi("/api/system", 60000);
  useEffect(() => {
    if (d) document.title = `${money(d.chain_bankroll)} ${d.day_pnl >= 0 ? "▲" : "▼"} CleanBot Desk`;
  }, [d]);
  const view = route === "trading" ? html`<${Trading} d=${d} />`
    : route === "research" ? html`<${Research} />`
    : route === "strategy2" ? html`<${Strategy2} d=${d} />`
    : route === "signals" ? html`<${Signals} d=${d} />`
    : route === "logs" ? html`<${Logs} />`
    : route === "system" ? html`<${SystemView} />`
    : html`<${Overview} d=${d} />`;
  return html`<div class="shell">
    <aside class="side">
      <div class="brand">CLEAN<em>BOT</em> DESK</div>
      <nav class="nav">
        ${VIEWS.map(([id, ic, label]) => html`
          <a key=${id} href=${"#/" + id} class=${route === id ? "on" : ""}>
            <span class="ic">${ic}</span>${label}</a>`)}
      </nav>
      <div class="foot">chain-truth accounting<br/>verifier-gated strategies<br/>v3 · polls 4s</div>
    </aside>
    <main class="main">
      <${Tape} d=${d} sys=${sys} />
      <${Banner} d=${d} />
      ${view}
      <footer>CleanBot Desk · every number is reconciled to the wallet · every strategy passes the gate before it trades</footer>
    </main>
  </div>`;
}

createRoot(document.getElementById("root")).render(html`<${App} />`);
