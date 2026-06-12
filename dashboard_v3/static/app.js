/* ═════════════════════════════════════════════════
   Polymarket Command Center v3 — frontend logic
   ═════════════════════════════════════════════════ */

const POLL_MS = 2000;
const TRADES_POLL_MS = 15000; // CLOB trades (heavier, poll slower)
const SETTINGS_POLL_MS = 60000;
const LAYOUT_KEY = "polybot_dash_layout_v1";

let scanFilter = "all";
let logFilter15m = "all";
let logFilter5m = "all";
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
  } else if (parent.dataset.logTabs === "15m") {
    logFilter15m = t.dataset.cat;
    pollLogs15m();
  } else if (parent.dataset.logTabs === "5m") {
    logFilter5m = t.dataset.cat;
    pollLogs5m();
  }
});

// ─── Clock ────────────────────────────────────────────────────
function tickClock() {
  if (!lastServerTime) {
    $("hdr-clock").textContent = new Date().toLocaleTimeString();
    return;
  }
  const d = new Date(lastServerTime.replace(" ", "T") + "-05:00");
  d.setSeconds(d.getSeconds() + 1);
  lastServerTime = d.toISOString().replace("T", " ").slice(0, 19);
  $("hdr-clock").textContent = d.toTimeString().slice(0, 8) + " (Lima)";
}
setInterval(tickClock, 1000);

// ─── Renderers ────────────────────────────────────────────────
let _lastSignals = [];
let _lastSnap    = null;
let _last5m      = null;

function renderHeader(snap) {
  // apr28: hero P&L card now reflects 15M + 5M combined. Per-bot
  // numbers are still rendered in their dedicated bot cards.
  const total = snap.pnl_total || snap.pnl || {};
  const pnl = total.today ?? 0;
  const wr  = total.winrate ?? 0;
  const pnlEl = $("pnl-value");
  pnlEl.textContent = (pnl >= 0 ? "+" : "-") + "$" + Math.abs(pnl).toFixed(2);
  pnlEl.classList.toggle("green", pnl > 0);
  pnlEl.classList.toggle("red", pnl < 0);

  const hdrPnl = $("hdr-pnl");
  hdrPnl.textContent = (pnl >= 0 ? "+$" : "-$") + Math.abs(pnl).toFixed(2);
  const pill = $("pill-pnl");
  pill.classList.remove("green", "red", "amber");
  pill.classList.add(pnl > 0 ? "green" : pnl < 0 ? "red" : "amber");

  $("hdr-wr").textContent = fmtPct(wr);
  $("pill-wr").className = "stat-pill " + (
    wr >= 55 ? "green" : wr >= 45 ? "amber" : "red"
  );

  $("hdr-bankroll").textContent = "$" + Number(snap.risk?.bankroll ?? 0).toFixed(0);
  $("hdr-session").textContent = (snap.session || "—").toUpperCase();

  $("pnl-wins").textContent = total.wins ?? 0;
  $("pnl-losses").textContent = total.losses ?? 0;
  $("pnl-winrate").textContent = fmtPct(wr);

  // Subtitle: per-bot split so the user can see how the total
  // breaks down at a glance.
  const sub = $("pnl-subtitle");
  if (sub) {
    const b15 = total.bot_15m || {};
    const b5  = total.bot_5m  || {};
    const fmt = (v) => (v >= 0 ? "+$" : "-$") + Math.abs(v ?? 0).toFixed(2);
    sub.textContent = `15M ${fmt(b15.pnl)} · 5M ${fmt(b5.pnl)}`;
  }

  const bot = snap.bot || { running: false };
  const botBox = $("hdr-bot");
  botBox.classList.toggle("running", !!bot.running);
  $("bot-text").textContent = bot.running
    ? `LIVE pid=${bot.pid} up ${secondsToHMS(bot.uptime_sec)}`
    : "OFFLINE";

  const bot5 = snap.bot_5m?.status || { running: false };
  const bot5Box = $("hdr-bot-5m");
  if (bot5Box) {
    bot5Box.classList.toggle("running", !!bot5.running);
    $("bot-5m-text").textContent = bot5.running
      ? `LIVE pid=${bot5.pid} up ${secondsToHMS(bot5.uptime_sec)}`
      : "OFFLINE";
  }
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

// Generic log renderer — used for both 15M and 5M panels.
function renderLog(events, targetId, filter) {
  const el = $(targetId);
  if (!el) return;

  const active = (filter || "all").toLowerCase();
  let filtered = events || [];
  if (active && active !== "all") {
    filtered = filtered.filter(e => (e.cat || "info").toLowerCase() === active);
  }

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

function render15mCard(snap) {
  if (!snap) return;
  const pnl   = snap.pnl   || {};
  const risk  = snap.risk  || {};
  const ex    = snap.exhaust || {};
  const bot   = snap.bot   || { running: false };

  // status line
  const statusEl = $("b15-status");
  if (statusEl) {
    statusEl.textContent = bot.running
      ? `LIVE • pid ${bot.pid} • up ${secondsToHMS(bot.uptime_sec)}`
      : "OFFLINE";
  }

  // P&L hero
  const today = pnl.today ?? 0;
  const pnlEl = $("b15-pnl");
  if (pnlEl) {
    pnlEl.textContent = (today >= 0 ? "+$" : "-$") + Math.abs(today).toFixed(2);
    pnlEl.classList.toggle("green", today > 0);
    pnlEl.classList.toggle("red",   today < 0);
  }
  $("b15-wins").textContent   = pnl.wins   ?? 0;
  $("b15-losses").textContent = pnl.losses ?? 0;
  $("b15-wr").textContent     = fmtPct(pnl.winrate);

  // stats grid
  $("b15-signals").textContent = ex.signals ?? 0;
  $("b15-orders").textContent  = ex.orders  ?? 0;
  $("b15-fills").textContent   = ex.fills   ?? 0;
  $("b15-blocks").textContent  = ex.blocks  ?? 0;
  $("b15-dampens").textContent = ex.dampens ?? 0;
  $("b15-flips").textContent   = ex.flips   ?? 0;

  $("b15-loss").textContent =
    "$" + Number(risk.loss_today ?? 0).toFixed(2);
  $("b15-dsl-remaining").textContent =
    (risk.dsl_remaining === null || risk.dsl_remaining === undefined)
      ? "—"
      : "$" + Number(risk.dsl_remaining).toFixed(2);
  // Streak: prefer server value, else derive from recent outcomes (W=true/L=false)
  let streak = risk.streak;
  if (streak === null || streak === undefined) {
    const outcomes = (snap.calibration && snap.calibration.outcomes) || [];
    if (outcomes.length) {
      const last = outcomes[outcomes.length - 1];
      let n = 0;
      for (let i = outcomes.length - 1; i >= 0; i--) {
        if (outcomes[i] === last) n++; else break;
      }
      streak = `${n}${last ? "W" : "L"}`;
    } else {
      streak = "—";
    }
  }
  $("b15-streak").textContent   = streak;
  $("b15-breakers").textContent = risk.breakers_today ?? 0;
  $("b15-bankroll").textContent =
    "$" + Number(risk.bankroll ?? 0).toFixed(2);
  $("b15-kelly").textContent =
    ((risk.kelly_max_pct ?? 0) * 100).toFixed(1) + "%";

  // env-driven config (read from settings if present)
  const settings = window._lastSettings || {};
  const pmBlocked = settings.PM_BLOCKED_COINS || "—";
  const pmEntry   = settings.PM_ENTRY_MAX
    ? Number(settings.PM_ENTRY_MAX).toFixed(2) + "c"
    : "—";
  $("b15-pm-blocked").textContent   = pmBlocked;
  $("b15-pm-entry-max").textContent = pmEntry;

  // recent trades
  const trades = (snap.trades_today || []).slice().reverse().slice(0, 8);
  const trEl = $("b15-trades");
  if (!trEl) return;
  trEl.innerHTML = "";
  if (trades.length === 0) {
    trEl.innerHTML = '<div class="empty small">no 15M trades yet today</div>';
    return;
  }
  trades.forEach(t => {
    const row = document.createElement("div");
    row.className = `m5-trade-row t-${t.type}`;
    let body = "";
    let amt  = "";
    if (t.type === "WIN") {
      body = `@${t.entry}c x${t.shares}`;
      amt  = `+$${t.amount.toFixed(2)}`;
    } else if (t.type === "LOSS") {
      body = `@${t.entry}c x${t.shares}`;
      amt  = `-$${t.amount.toFixed(2)}`;
    } else if (t.type === "ORDER") {
      body = `@${t.ask}c x${t.shares} = $${(t.cost || 0).toFixed(2)}`;
    } else if (t.type === "FILLED") {
      body = `@${t.price}c x${t.shares} = $${(t.cost || 0).toFixed(2)}`;
    }
    row.innerHTML = `
      <span class="m5-t">${t.t}</span>
      <span class="m5-type">${t.type}</span>
      <span class="m5-coin">${t.coin}</span>
      <span class="scan-dir-${t.dir || ''}">${t.dir || ''}</span>
      <span class="m5-body">${body}</span>
      <span class="m5-amt">${amt}</span>
    `;
    trEl.appendChild(row);
  });
}

function render5mCard(snap) {
  if (!snap) return;
  _last5m = snap;
  const stats = snap.stats || {};
  const cfg = snap.config || {};
  const status = snap.bot || { running: false };

  $("m5-status").textContent = status.running
    ? `LIVE • pid ${status.pid} • up ${secondsToHMS(status.uptime_sec)}`
    : (cfg.enabled ? "OFFLINE (M5_ENABLED=1)" : "DISABLED");

  const pnl = stats.pnl_usd ?? 0;
  const pnlEl = $("m5-pnl");
  if (pnlEl) {
    pnlEl.textContent = (pnl >= 0 ? "+$" : "-$") + Math.abs(pnl).toFixed(2);
    pnlEl.classList.toggle("green", pnl > 0);
    pnlEl.classList.toggle("red", pnl < 0);
  }
  $("m5-wins").textContent = stats.wins ?? 0;
  $("m5-losses").textContent = stats.losses ?? 0;
  $("m5-wr").textContent = fmtPct(stats.winrate);

  $("m5-signals").textContent  = stats.signals  ?? 0;
  $("m5-orders").textContent   = stats.orders   ?? 0;
  $("m5-fills").textContent    = stats.fills    ?? 0;
  $("m5-blocks").textContent   = stats.blocks   ?? 0;
  $("m5-dampens").textContent  = stats.dampens  ?? 0;
  $("m5-overrides").textContent = stats.overrides ?? 0;

  $("m5-loss").textContent = "$" + Number(snap.loss_today ?? 0).toFixed(2);
  $("m5-cap-remaining").textContent = (snap.cap_remaining === null || snap.cap_remaining === undefined)
    ? "—"
    : "$" + Number(snap.cap_remaining).toFixed(2);

  $("m5-coins").textContent = cfg.coins || "—";
  $("m5-hours").textContent = cfg.trade_hours || "—";
  $("m5-size").textContent  = cfg.test_size_usd ? `$${cfg.test_size_usd}` : "—";

  const trades = (snap.trades || []).slice().reverse().slice(0, 8);
  const trEl = $("m5-trades");
  trEl.innerHTML = "";
  if (trades.length === 0) {
    trEl.innerHTML = '<div class="empty small">no 5M trades yet today</div>';
    return;
  }
  trades.forEach(t => {
    const row = document.createElement("div");
    row.className = `m5-trade-row t-${t.type}`;
    let body = "";
    let amt = "";
    if (t.type === "WIN") {
      body = `@${t.entry}c x${t.shares}`;
      amt = `+$${t.amount.toFixed(2)}`;
    } else if (t.type === "LOSS") {
      body = `@${t.entry}c x${t.shares}`;
      amt = `-$${t.amount.toFixed(2)}`;
    } else if (t.type === "ORDER") {
      body = `@${t.ask}c x${t.shares} = $${(t.cost||0).toFixed(2)}`;
    } else if (t.type === "FILLED") {
      body = `@${t.price}c x${t.shares} = $${(t.cost||0).toFixed(2)}`;
    }
    row.innerHTML = `
      <span class="m5-t">${t.t}</span>
      <span class="m5-type">${t.type}</span>
      <span class="m5-coin">${t.coin}</span>
      <span class="scan-dir-${t.dir || ''}">${t.dir || ''}</span>
      <span class="m5-body">${body}</span>
      <span class="m5-amt">${amt}</span>
    `;
    trEl.appendChild(row);
  });
}

function renderHeartbeat15m(snap) {
  const hb = snap.heartbeat || {};
  const subtitle = $("log-subtitle-15m");
  if (!subtitle) return;
  const now = Date.now() / 1000;
  const evAge   = hb.last_event_ts ? Math.max(0, now - hb.last_event_ts) : null;
  const fileAge = hb.log_mtime    ? Math.max(0, now - hb.log_mtime)     : null;
  subtitle.innerHTML = `
    <span class="hb-dot ${fileAge !== null && fileAge < 30 ? 'live' : 'stale'}"></span>
    log file: ${fmtAge(fileAge)} · last parsed event: ${fmtAge(evAge)}
  `;
}

function renderHeartbeat5m(snap) {
  const hb = snap.heartbeat || {};
  const subtitle = $("log-subtitle-5m");
  if (!subtitle) return;
  const now = Date.now() / 1000;
  const evAge   = hb.last_event_ts ? Math.max(0, now - hb.last_event_ts) : null;
  const fileAge = hb.log_mtime    ? Math.max(0, now - hb.log_mtime)     : null;
  subtitle.innerHTML = `
    <span class="hb-dot ${fileAge !== null && fileAge < 30 ? 'live' : 'stale'}"></span>
    log file: ${fmtAge(fileAge)} · last parsed event: ${fmtAge(evAge)}
  `;
}

function fmtAge(s) {
  if (s === null || s === undefined) return "—";
  if (s < 60)   return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s/60)}m ago`;
  return `${Math.floor(s/3600)}h ago`;
}

function escapeHTML(s) {
  return String(s || "").replace(/[&<>"']/g, c => ({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
  }[c]));
}

// ─── Pollers ──────────────────────────────────────────────────
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
    render15mCard(snap);
    renderHeartbeat15m({ heartbeat: snap.heartbeat });
  } catch (e) {
    console.warn("snapshot failed", e);
  }
}

async function pollLogs15m() {
  try {
    const params = new URLSearchParams({ limit: "240", bot: "15m" });
    if (logFilter15m && logFilter15m !== "all") params.set("category", logFilter15m);
    const r = await getJSON(`/api/v3/logs?${params.toString()}`);
    renderLog(r.events || [], "log-stream-15m", logFilter15m);
  } catch (e) {
    console.warn("15m logs failed", e);
  }
}

async function pollLogs5m() {
  try {
    const params = new URLSearchParams({ limit: "240" });
    if (logFilter5m && logFilter5m !== "all") params.set("category", logFilter5m);
    const r = await getJSON(`/api/v3/5m/logs?${params.toString()}`);
    renderLog(r.events || [], "log-stream-5m", logFilter5m);
  } catch (e) {
    console.warn("5m logs failed", e);
  }
}

async function poll5m() {
  try {
    const r = await getJSON("/api/v3/5m/snapshot");
    render5mCard(r);
    renderHeartbeat5m(r);
  } catch (e) {
    console.warn("5m snapshot failed", e);
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
  window._lastSettings = s;
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

// ─── Drag-and-drop layout (SortableJS) ────────────────────────
function saveLayout() {
  const layout = {};
  document.querySelectorAll(".col").forEach(col => {
    const colName = col.dataset.col;
    layout[colName] = Array.from(col.querySelectorAll(".card"))
      .map(c => c.dataset.cardId);
  });
  try {
    localStorage.setItem(LAYOUT_KEY, JSON.stringify(layout));
  } catch (e) { /* localStorage might be full / blocked */ }
}

function restoreLayout() {
  let layout;
  try {
    layout = JSON.parse(localStorage.getItem(LAYOUT_KEY) || "null");
  } catch (e) { layout = null; }
  if (!layout) return;

  // Build a map of card-id -> element from the entire dashboard
  const cardMap = {};
  document.querySelectorAll(".card[data-card-id]").forEach(c => {
    cardMap[c.dataset.cardId] = c;
  });

  Object.entries(layout).forEach(([colName, cardIds]) => {
    const col = document.querySelector(`.col[data-col="${colName}"]`);
    if (!col) return;
    cardIds.forEach(id => {
      const el = cardMap[id];
      if (el) col.appendChild(el);
    });
  });
}

function initSortable() {
  if (typeof Sortable === "undefined") {
    console.warn("SortableJS not loaded — drag/drop disabled");
    return;
  }
  document.querySelectorAll(".col").forEach(col => {
    new Sortable(col, {
      group: "dashboard",         // allow cross-column moves
      handle: ".drag-grip",       // only the grip starts a drag
      animation: 180,
      ghostClass: "sortable-ghost",
      chosenClass: "sortable-chosen",
      dragClass:   "sortable-drag",
      onEnd: saveLayout,
    });
  });
}

function resetLayout() {
  try { localStorage.removeItem(LAYOUT_KEY); } catch (e) {}
  toast("Layout reset — reloading…", "ok");
  setTimeout(() => location.reload(), 600);
}
window.resetLayout = resetLayout;

// ─── Boot ─────────────────────────────────────────────────────
restoreLayout();          // before pollers — ensures DOM is in saved order
initSortable();           // wire up grips on the current DOM

pollSnapshot();
pollLogs15m();
pollLogs5m();
poll5m();
pollTrades();
pollPositions();
pollSettings();

setInterval(pollSnapshot, POLL_MS);
setInterval(pollLogs15m, POLL_MS);
setInterval(pollLogs5m,  POLL_MS);
setInterval(poll5m, POLL_MS);
setInterval(pollTrades, TRADES_POLL_MS);
setInterval(pollPositions, TRADES_POLL_MS);
setInterval(pollSettings, SETTINGS_POLL_MS);
