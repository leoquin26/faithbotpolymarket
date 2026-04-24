/* ═════════════════════════════════════════════════
   Polymarket Command Center v3 — frontend logic
   ═════════════════════════════════════════════════ */

const POLL_MS = 2000;
const TRADES_POLL_MS = 15000; // CLOB trades (heavier, poll slower)
const SETTINGS_POLL_MS = 60000;

let scanFilter = "all";
let logFilter = "all";
let lastServerTime = null;

// ─── Tiny utilities ───────────────────────────────────────────
function $(id) { return document.getElementById(id); }
function fmt$(n) {
  if (n === null || n === undefined || isNaN(n)) return "$—";
  const s = Number(n).toFixed(2);
  return (n >= 0 ? "+$" : "-$") + Math.abs(Number(s)).toFixed(2);
}
function fmtDollars(n) {
  if (n === null || n === undefined || isNaN(n)) return "$—";
  return "$" + Number(n).toFixed(2);
}
function fmtPct(n) {
  if (n === null || n === undefined || isNaN(n)) return "—%";
  return Number(n).toFixed(1) + "%";
}
function secondsToHMS(s) {
  if (!s || s < 0) return "0s";
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${sec}s`;
  return `${sec}s`;
}
function toast(msg, kind = "ok") {
  const t = $("toast");
  t.textContent = msg;
  t.className = `toast show ${kind}`;
  clearTimeout(toast._tid);
  toast._tid = setTimeout(() => { t.className = "toast"; }, 3000);
}

// ─── Fetch helpers ────────────────────────────────────────────
async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}
async function postJSON(url) {
  const r = await fetch(url, { method: "POST" });
  return r.json();
}

// ─── Bot controls ─────────────────────────────────────────────
async function botAction(action) {
  try {
    const r = await postJSON(`/api/v3/bot/${action}`);
    if (r.ok) toast(r.msg || `${action} ok`, "ok");
    else toast(r.msg || `${action} failed`, "err");
    setTimeout(pollSnapshot, 1000);
  } catch (e) {
    toast(`${action} error: ${e.message}`, "err");
  }
}
window.botAction = botAction;

// ─── Tab wiring ───────────────────────────────────────────────
document.addEventListener("click", (e) => {
  const t = e.target.closest(".tab");
  if (!t) return;
  const parent = t.parentElement;
  const siblings = parent.querySelectorAll(".tab");
  siblings.forEach(s => s.classList.remove("active"));
  t.classList.add("active");
  if (parent.id === "scan-tabs") {
    scanFilter = t.dataset.filter;
    renderScanner(_lastSignals);
  } else if (parent.id === "log-tabs") {
    logFilter = t.dataset.cat;
    // Re-render immediately from the last snapshot (no network hop),
    // and the regular 2s poll will keep it fresh with the same filter.
    if (_lastSnap) renderLog(_lastSnap.events || []);
  }
});

// ─── Clock ────────────────────────────────────────────────────
function tickClock() {
  if (!lastServerTime) {
    $("hdr-clock").textContent = new Date().toLocaleTimeString();
    return;
  }
  const d = new Date(lastServerTime.replace(" ", "T") + "-05:00");
  // bump by drift
  d.setSeconds(d.getSeconds() + 1);
  lastServerTime = d.toISOString().replace("T", " ").slice(0, 19);
  $("hdr-clock").textContent = d.toTimeString().slice(0, 8) + " (Lima)";
}
setInterval(tickClock, 1000);

// ─── Renderers ────────────────────────────────────────────────
let _lastSignals = [];
let _lastSnap    = null;

function renderHeader(snap) {
  const pnl = snap.pnl?.today ?? 0;
  const pnlEl = $("pnl-value");
  pnlEl.textContent = (pnl >= 0 ? "+" : "-") + "$" + Math.abs(pnl).toFixed(2);
  pnlEl.classList.toggle("green", pnl > 0);
  pnlEl.classList.toggle("red", pnl < 0);

  const hdrPnl = $("hdr-pnl");
  hdrPnl.textContent = (pnl >= 0 ? "+$" : "-$") + Math.abs(pnl).toFixed(2);
  const pill = $("pill-pnl");
  pill.classList.remove("green", "red", "amber");
  pill.classList.add(pnl > 0 ? "green" : pnl < 0 ? "red" : "amber");

  $("hdr-wr").textContent = fmtPct(snap.pnl?.winrate);
  $("pill-wr").className = "stat-pill " + (
    (snap.pnl?.winrate ?? 0) >= 55 ? "green" :
    (snap.pnl?.winrate ?? 0) >= 45 ? "amber" : "red"
  );

  $("hdr-bankroll").textContent = "$" + Number(snap.risk?.bankroll ?? 0).toFixed(0);
  $("hdr-session").textContent = (snap.session || "—").toUpperCase();

  $("pnl-wins").textContent = snap.pnl?.wins ?? 0;
  $("pnl-losses").textContent = snap.pnl?.losses ?? 0;
  $("pnl-winrate").textContent = fmtPct(snap.pnl?.winrate);
  $("pnl-subtitle").textContent = snap.server_time || "";

  // Bot status
  const bot = snap.bot || { running: false };
  const botBox = $("hdr-bot");
  botBox.classList.toggle("running", !!bot.running);
  $("bot-text").textContent = bot.running
    ? `LIVE pid=${bot.pid} up ${secondsToHMS(bot.uptime_sec)}`
    : "OFFLINE";
}

function renderRisk(snap) {
  const r = snap.risk || {};
  $("risk-bankroll").textContent = "$" + Number(r.bankroll ?? 0).toFixed(2);
  $("risk-kelly").textContent = ((r.kelly_max_pct ?? 0) * 100).toFixed(1) + "%";
  $("risk-dsl").textContent = r.daily_loss_limit_enabled
    ? "$" + Number(r.daily_loss_limit ?? 0).toFixed(0)
    : "off";
  const lossEl = $("risk-loss");
  lossEl.textContent = "$" + Number(r.loss_today ?? 0).toFixed(2);
  lossEl.className = r.loss_today > 0 ? "red" : "";
  $("risk-dsl-remaining").textContent =
    (r.dsl_remaining === null || r.dsl_remaining === undefined)
      ? "—"
      : "$" + Number(r.dsl_remaining).toFixed(2);
  $("risk-breakers").textContent = r.breakers_today ?? 0;
}

function renderOutcomes(snap) {
  const cal = snap.calibration || {};
  const o = cal.outcomes || [];
  const row = $("outcome-row");
  row.innerHTML = "";
  o.forEach((won) => {
    const d = document.createElement("div");
    d.className = "outcome-dot " + (won ? "w" : "l");
    d.textContent = won ? "W" : "L";
    row.appendChild(d);
  });
  if (o.length === 0) {
    row.innerHTML = '<div class="empty">no outcomes yet</div>';
  }
  $("cal-subtitle").textContent =
    cal.total ? `${cal.wins}/${cal.total} = ${cal.winrate}%` : "";
}

function renderExhaust(snap) {
  const e = snap.exhaust || {};
  $("ex-signals").textContent = e.signals ?? 0;
  $("ex-blocks").textContent  = e.blocks  ?? 0;
  $("ex-dampens").textContent = e.dampens ?? 0;
  $("ex-flips").textContent   = e.flips   ?? 0;
  $("ex-orders").textContent  = e.orders  ?? 0;
  $("ex-fills").textContent   = e.fills   ?? 0;

  const byCoin = e.by_coin || {};
  const chips = $("ex-bycoin");
  chips.innerHTML = "";
  const entries = Object.entries(byCoin).sort((a,b) => b[1] - a[1]);
  if (entries.length === 0) {
    chips.innerHTML = '<div class="empty">no blocks yet today</div>';
  }
  entries.forEach(([coin, n]) => {
    const c = document.createElement("div");
    c.className = "ex-chip";
    c.innerHTML = `${coin}<strong>${n}</strong>`;
    chips.appendChild(c);
  });
}

function renderMarket(snap) {
  const grid = $("market-grid");
  const coins = snap.market?.coins || [];
  grid.innerHTML = "";
  coins.forEach(c => {
    const card = document.createElement("div");
    card.className = "mkt-card";
    const act = c.last_action;
    const actChip = act
      ? `<span class="mkt-action-chip ${act.action || act.kind}">${act.action || act.kind}${act.score ? ` ${Number(act.score).toFixed(2)}` : ""}</span>`
      : `<span class="mkt-action-chip CLEAN">—</span>`;
    card.innerHTML = `
      <div class="mkt-head">
        <div class="mkt-coin">${c.coin}</div>
        ${actChip}
      </div>
      <div class="mkt-dirs">
        <div class="mkt-dir up">
          <div class="lbl">UP</div>
          <div class="ask">${c.up ? c.up.ask + "c" : "—"}</div>
          <div class="meta">${c.up ? `p=${c.up.prob}% e=${c.up.edge}%` : "no signal"}</div>
        </div>
        <div class="mkt-dir down">
          <div class="lbl">DOWN</div>
          <div class="ask">${c.down ? c.down.ask + "c" : "—"}</div>
          <div class="meta">${c.down ? `p=${c.down.prob}% e=${c.down.edge}%` : "no signal"}</div>
        </div>
      </div>
    `;
    grid.appendChild(card);
  });
}

function renderScanner(signals) {
  _lastSignals = signals || [];
  const stream = $("scan-stream");
  stream.innerHTML = "";
  const filtered = _lastSignals.filter(s => {
    if (scanFilter === "all") return true;
    if (scanFilter === "signal") return s.kind === "SIGNAL";
    if (scanFilter === "block") return s.kind === "BLOCK" || s.kind === "EXHAUST_ABSTAIN";
    if (scanFilter === "dampen") return s.kind === "DAMPEN" || s.kind === "EXHAUST_DAMPEN";
    if (scanFilter === "kelly") return s.kind === "KELLY";
    return true;
  });
  if (filtered.length === 0) {
    stream.innerHTML = '<div class="empty">no events match this filter</div>';
    return;
  }
  filtered.slice(0, 80).forEach(s => {
    const row = document.createElement("div");
    row.className = `scan-row k-${s.kind}`;
    let body = "";
    if (s.kind === "SIGNAL") {
      body = `ask=${s.ask}c  p=${s.prob}%  edge=${s.edge}%  trend=${s.trend ?? "—"}`;
    } else if (s.kind === "EXHAUST_ABSTAIN" || s.kind === "EXHAUST_DAMPEN" || s.kind === "EXHAUST_CLEAN" || s.kind === "EXHAUST_FLIP") {
      body = `@${s.ask}c  score=${s.score?.toFixed?.(2) ?? s.score}${s.gated ? " (gated)" : ""}  raw=${s.raw ?? ""}`;
    } else if (s.kind === "BLOCK") {
      body = `blocked (score=${s.score})`;
    } else if (s.kind === "DAMPEN") {
      body = `dampened`;
    } else if (s.kind === "FLIP") {
      body = `flipped direction`;
    } else if (s.kind === "KELLY") {
      body = `size=$${s.size_usd}  br=$${s.bankroll}`;
    } else {
      body = "";
    }
    row.innerHTML = `
      <span class="scan-time">${s.t}</span>
      <span class="scan-kind">${s.kind}</span>
      <span class="scan-coin">${s.coin || ""}</span>
      <span class="scan-dir-${s.dir || ""}">${s.dir || ""}</span>
      <span class="scan-body">${body}</span>
    `;
    stream.appendChild(row);
  });
}

function renderTrades(snap) {
  const el = $("trades-stream");
  const trades = (snap.trades_today || []).slice().reverse();
  el.innerHTML = "";
  if (trades.length === 0) {
    el.innerHTML = '<div class="empty">no trades yet today</div>';
    $("trades-subtitle").textContent = "";
    return;
  }
  trades.slice(0, 60).forEach(t => {
    const row = document.createElement("div");
    row.className = `trade-row t-${t.type}`;
    let body = "";
    let amt = "";
    if (t.type === "WIN") {
      body = `@${t.entry}c x${t.shares} (${t.session})`;
      amt = `+$${t.amount.toFixed(2)}`;
    } else if (t.type === "LOSS") {
      body = `@${t.entry}c x${t.shares} (${t.session})`;
      amt = `-$${t.amount.toFixed(2)}`;
    } else if (t.type === "ORDER") {
      body = `@${t.ask}c x${t.shares} = $${t.cost.toFixed(2)}`;
    } else if (t.type === "FILLED") {
      body = `@${t.price}c x${t.shares} = $${t.cost.toFixed(2)}`;
    } else {
      body = "";
    }
    row.innerHTML = `
      <span class="trade-time">${t.t}</span>
      <span class="trade-type">${t.type}</span>
      <span class="trade-coin">${t.coin}</span>
      <span class="scan-dir-${t.dir || ""}">${t.dir || ""}</span>
      <span class="scan-body">${body}</span>
      <span class="trade-amt">${amt}</span>
    `;
    el.appendChild(row);
  });
  $("trades-subtitle").textContent = `${trades.length} events`;
}

function renderLog(events) {
  const el = $("log-stream");
  if (!el) return;

  // Respect the user's active filter tab. The snapshot always returns
  // ALL events; we filter client-side so polling never wipes out the
  // currently selected tab.
  const active = (logFilter || "all").toLowerCase();
  let filtered = events || [];
  if (active && active !== "all") {
    filtered = filtered.filter(e => (e.cat || "info").toLowerCase() === active);
  }

  // Preserve scroll position: if the user has scrolled up to read,
  // keep them at that offset. If pinned to the top (newest), stay pinned.
  const pinnedTop = el.scrollTop < 40;

  el.innerHTML = "";
  if (filtered.length === 0) {
    el.innerHTML = '<div class="empty">no events in this category yet — bot is scanning…</div>';
    return;
  }

  filtered.forEach(ev => {
    const row = document.createElement("div");
    row.className = `log-row log-cat-${ev.cat || "info"}`;
    row.innerHTML = `
      <span class="log-time">${ev.t}</span>
      <span class="log-level ${ev.level}">${ev.level}</span>
      <span class="log-msg">${escapeHTML(ev.msg)}</span>
    `;
    el.appendChild(row);
  });

  if (pinnedTop) el.scrollTop = 0;
}

// Last-event heartbeat display — proves the log pipeline is alive
// even when the bot is quiet (no SIGNAL/FILL events, just DEBUG scans).
function renderHeartbeat(snap) {
  const hb = snap.heartbeat || {};
  const subtitle = $("log-subtitle");
  if (!subtitle) return;

  const now = Date.now() / 1000;
  const evAge  = hb.last_event_ts ? Math.max(0, now - hb.last_event_ts) : null;
  const fileAge = hb.log_mtime    ? Math.max(0, now - hb.log_mtime)    : null;

  const fmt = (s) => {
    if (s === null) return "—";
    if (s < 60)  return `${Math.floor(s)}s ago`;
    if (s < 3600) return `${Math.floor(s/60)}m ago`;
    return `${Math.floor(s/3600)}h ago`;
  };

  subtitle.innerHTML = `
    <span class="hb-dot ${fileAge !== null && fileAge < 30 ? 'live' : 'stale'}"></span>
    log file: ${fmt(fileAge)} · last parsed event: ${fmt(evAge)}
  `;
}

function escapeHTML(s) {
  return String(s || "").replace(/[&<>"']/g, c => ({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
  }[c]));
}

// ─── Separate pollers ─────────────────────────────────────────
async function pollSnapshot() {
  try {
    const snap = await getJSON("/api/v3/snapshot");
    _lastSnap = snap;
    lastServerTime = snap.server_time;
    renderHeader(snap);
    renderRisk(snap);
    renderOutcomes(snap);
    renderExhaust(snap);
    renderMarket(snap);
    renderScanner(snap.signals || []);
    renderTrades(snap);
    renderLog(snap.events || []);
    renderHeartbeat(snap);
  } catch (e) {
    console.warn("snapshot failed", e);
  }
}

async function pollLogs() {
  try {
    const url = `/api/v3/logs?limit=180${logFilter && logFilter !== "all" ? `&category=${logFilter}` : ""}`;
    const r = await getJSON(url);
    renderLog(r.events || []);
  } catch (e) {
    console.warn("logs failed", e);
  }
}

async function pollTrades() {
  try {
    const r = await getJSON("/api/v3/trades?limit=30");
    renderClobTable(r.trades || []);
  } catch (e) { /* CLOB may be slow/unavailable */ }
}

async function pollPositions() {
  try {
    const r = await getJSON("/api/v3/positions");
    renderPositionsTable(r.positions || []);
  } catch (e) { /* ignore */ }
}

async function pollSettings() {
  try {
    const r = await getJSON("/api/v3/settings");
    renderSettings(r.settings || {});
  } catch (e) { /* ignore */ }
}

function renderSettings(s) {
  const el = $("settings-list");
  el.innerHTML = "";
  const keys = Object.keys(s).sort();
  if (keys.length === 0) {
    el.innerHTML = '<div class="empty">no settings</div>';
    return;
  }
  keys.forEach(k => {
    const v = s[k];
    const row = document.createElement("div");
    row.className = "setting-row";
    row.innerHTML = `<span>${k}</span><strong>${escapeHTML(v)}</strong>`;
    el.appendChild(row);
  });
}

function renderPositionsTable(positions) {
  const el = $("positions-table");
  $("pos-subtitle").textContent = `${positions.length} open`;
  if (positions.length === 0) {
    el.innerHTML = '<div class="empty">no open positions</div>';
    return;
  }
  const rows = positions.map(p => `
    <tr>
      <td>${p.outcome || "—"}</td>
      <td class="num">${p.size}</td>
      <td class="num">${(p.avg_price * 100).toFixed(1)}c</td>
      <td class="num">$${p.cost.toFixed(2)}</td>
      <td>${p.last_time || ""}</td>
      <td title="${escapeHTML(p.market)}">${escapeHTML((p.market || "").slice(0, 32))}</td>
    </tr>
  `).join("");
  el.innerHTML = `
    <table class="tbl">
      <thead><tr><th>side</th><th>size</th><th>avg</th><th>cost</th><th>t</th><th>market</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

function renderClobTable(trades) {
  const el = $("clob-table");
  $("clob-subtitle").textContent = `${trades.length} confirmed trades`;
  if (trades.length === 0) {
    el.innerHTML = '<div class="empty">no confirmed trades</div>';
    return;
  }
  const rows = trades.slice(0, 30).map(t => `
    <tr>
      <td>${t.time}</td>
      <td><span class="side-${t.side}">${t.side}</span></td>
      <td>${t.outcome}</td>
      <td class="num">${t.size.toFixed(2)}</td>
      <td class="num">${(t.price * 100).toFixed(1)}c</td>
      <td class="num">$${t.notional.toFixed(2)}</td>
      <td title="${escapeHTML(t.market)}">${escapeHTML((t.market || "").slice(0, 24))}</td>
    </tr>
  `).join("");
  el.innerHTML = `
    <table class="tbl">
      <thead><tr><th>t</th><th>side</th><th>outcome</th><th>size</th><th>px</th><th>notional</th><th>market</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

// ─── Boot ─────────────────────────────────────────────────────
pollSnapshot();
pollTrades();
pollPositions();
pollSettings();

setInterval(pollSnapshot, POLL_MS);
setInterval(pollTrades, TRADES_POLL_MS);
setInterval(pollPositions, TRADES_POLL_MS);
setInterval(pollSettings, SETTINGS_POLL_MS);
