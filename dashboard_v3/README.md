# Polymarket Command Center v3

Modern, self-contained dashboard for the v3-bot.

## Design

* **Backend**: `app.py` — Flask (threaded). Endpoints under `/api/v3/*`.
* **Log parser**: `log_parser.py` — background thread tails
  `/home/ubuntu/v3-bot/v3_bot.log` and extracts structured events.
* **CLOB adapter**: `clob_adapter.py` — queries Polymarket CLOB for real
  on-chain trades + positions, with 20 s TTL cache.
* **State reader**: `state_reader.py` — reads `.env`, `outcomes_state.json`,
  `traded_windows.json`, and runs `pgrep` for bot status / process control.
* **Frontend**: `templates/index.html` + `static/app.{css,js}` —
  single-page, dark theme, 3-column layout, polling every 2 s.

## Ground-truth rules

* **Trades / positions / redemptions** → CLOB API only (phantom-fill
  safe after Fix H; CLOB has a ~1 s settlement delay vs. logs).
* **Signals / EXHAUST decisions / Kelly sizing** → bot log only
  (CLOB doesn't know about these).
* **P&L today** → bot log `[WIN]/[LOSS]` events (they are computed from
  on-chain redemption outcomes, so they match reality once Fix H is in).

## Run locally on EC2

```bash
cd /home/ubuntu/v3-bot
pkill -f "dashboard.app_v2"             # stop old dashboard
python3 -m dashboard_v3.app              # port 8080 by default
```

The cloudflared tunnel already points at `http://localhost:8080`,
so the public URL picks up the new dashboard automatically.

## Endpoints

| endpoint | notes |
|---|---|
| `GET /` | Dashboard HTML |
| `GET /api/v3/snapshot` | Aggregated state, poll every 2 s |
| `GET /api/v3/status` | Bot process status + session |
| `GET /api/v3/pnl` | P&L today + on-chain risked |
| `GET /api/v3/trades?limit=50` | Confirmed on-chain trades |
| `GET /api/v3/positions` | Open CLOB positions |
| `GET /api/v3/market` | Per-coin latest signals / actions |
| `GET /api/v3/scanner?limit=80` | Live signal stream |
| `GET /api/v3/risk` | Bankroll / DSL / streak / breakers |
| `GET /api/v3/calibration` | Recent outcomes ring |
| `GET /api/v3/exhaust_stats` | Block / dampen / flip counters |
| `GET /api/v3/logs?category=trade|signal|exhaust|error|warn|all` | Structured log events |
| `GET /api/v3/logs/raw?n=200` | Raw last N log lines |
| `GET /api/v3/settings` | Whitelisted `.env` values (read-only) |
| `POST /api/v3/bot/{start|stop|restart|clear_locks}` | Bot control |

## Phase 2 (pending)

* Editable settings with `.env` write + guarded bot restart.
* P&L historical chart (daily / weekly / monthly) with Chart.js.
* Mobile polish (collapsible columns, swipeable tabs).
* Auth (simple token) on all `POST /api/v3/*` routes.
* Sell-from-UI action on open positions.
