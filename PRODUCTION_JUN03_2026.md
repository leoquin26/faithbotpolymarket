# Production Bot — June 3, 2026 (Working Configuration)

This branch captures the **live EC2 bot** as of the end of the Jun 3 session, after audit fixes and the post-restart winning streak (9W/0L on new entries from 4:45 PM ET per Polymarket CSV).

## Deployment

| Item | Value |
|------|--------|
| Host | `ubuntu@44.192.17.18` |
| Path | `/home/ubuntu/v3-bot/` |
| Process | `python3 -u run_bot.py` |
| **Not** | `run_brain_bot.py`, `brain.py` |
| CLOB SDK | `py_clob_client_v2` (V1 → `order_version_mismatch`) |
| Regime stack | **Disabled** — `regime_aware/` renamed to `regime_aware.disabled_*` |

### Restart (safe)

```bash
cd ~/v3-bot
OLD=$(pgrep -f "python3 -u run_bot.py" | head -1)
kill "$OLD" 2>/dev/null; sleep 2
nohup python3 -u run_bot.py >> logs/nohup_stdout.log 2>&1 &
python3 -m py_compile run_bot.py order_manager.py predictor.py
```

---

## Architecture (demo core + production I/O)

```
run_bot.py          Main loop: scan → predict → place → resolve
predictor.py        V12 trend (70%) + BS (30%), chop, strike gate
order_manager.py    CLOB V2 orders, Kelly sizing, persistence
market_data.py      Gamma API + Binance strike/threshold
polymarket_ws.py    WS order books (6 tokens/window)
bybit_ws.py         Failover spot prices
binance_ws.py       Tick vol + resolution price
morning_strategy.py Morning filters (9–14 Lima)
morning_predictor.py
```

### Afternoon path (14:00–17:00 Lima)

- `Predictor.predict()` → HIGH/MEDIUM + edge ≥ `MIN_EDGE`
- One trade per coin per window (atomic lock in `run_bot.py`)
- Re-fetch CLOB ask before `place_bet`
- FOK when real ask available; GTC fallback if thin book

### Disabled / inactive

- `regime_aware/` (invert, exhaust, calibration live)
- `REGIME_INVERT_ENABLED=off`, `REVERSION_INVERT=off`
- Most `.env` keys for ML/hybrid/exhaust are **legacy** — demo `run_bot` does not read them

---

## Active gates (code-enforced)

| Gate | Setting | Effect |
|------|---------|--------|
| Strike direction | `STRIKE_DIRECTION_ENFORCE=on`, `STRIKE_DIRECTION_MIN_DIST=0.00015` | Block DOWN above strike / UP below |
| Min edge | `MIN_EDGE_THRESHOLD=0.10` → `config.MIN_EDGE` | 10% edge minimum |
| Min win prob | `MIN_WIN_PROB=0.74` | 74% model prob |
| Entry band | `ENTRY_MIN=0.55`, `ENTRY_MAX=0.70` | 55–70¢ |
| Thin edge @ expensive ask | `HIGH_ASK_EDGE_MIN_ASK=0.62`, `HIGH_ASK_EDGE_MIN_EDGE=0.12` | ≥62¢ ask needs ≥12% edge |
| Low vol abstain | σ &lt; `5e-4` in predictor | Blocks bogus 95% signals |
| Weak trend | `\|trend_score\| < 0.40` (non-chop) | No trade |
| Kelly cap | `KELLY_MAX_BET=4`, `KELLY_MAX_PCT=0.04` | ~$4 max bet @ ~$113 bankroll |
| Daily stop | `USE_DAILY_STOP_LOSS=true`, `DAILY_LOSS_LIMIT=10` | Stops new trades when hit |
| CLOB | `py_clob_client_v2` | Required for orders |

---

## Persistence (survives restart)

| File | Contents |
|------|----------|
| `data/open_positions.json` | Open fills: coin, side, entry, shares, strike, window_start |
| `data/daily_pnl.json` | `date`, `losses`, `wins`, `trades` |
| `data/traded_windows.json` | Per-window dedup locks |

On startup: `resolve_expired_positions()` settles windows that ended while bot was down.

---

## Position sizing (Kelly)

- `USE_KELLY_SIZING=true`, `KELLY_FRACTION=0.20`
- Max bet = `min(KELLY_MAX_BET, bankroll × KELLY_MAX_PCT)` → **$4** at $113
- Live bankroll from CLOB balance every 5 min

---

## Trading hours (Lima)

- Trade: **09:00–17:00** (`TRADE_START_HOUR=9`, `TRADE_END_HOUR=17`)
- Morning strategy: **09:00–14:00** (half Kelly via env override in loop)
- Afternoon: **14:00–17:00** main predictor

---

## Jun 3 session results (Polymarket CSV)

| Period | W/L | Net |
|--------|-----|-----|
| Full day (25 windows) | 14W / 11L | **−$27.44** |
| 3:45–4:30 PM ET (chop) | 0W / 5L | **−$45.07** |
| After last restart (4:45 PM+ entries) | **9W / 0L** | **+$17.44** |

Bot logs under-counted wins due to restarts before persistence; CSV is ground truth.

---

## Key env vars (copy to `.env` — see `env.production.jun03.example`)

Secrets: `POLYMARKET_PRIVATE_KEY`, `POLYMARKET_FUNDER_ADDRESS`, `POLYMARKET_API_*`, `TELEGRAM_*` — never commit real values.

### Must-have for this build

```env
DRY_RUN=false
BOT_COIN_WHITELIST=BTC,ETH,SOL
ENTRY_MIN=0.55
ENTRY_MAX=0.70
MIN_EDGE_THRESHOLD=0.10
MIN_WIN_PROB=0.74
USE_KELLY_SIZING=true
KELLY_FRACTION=0.20
KELLY_MAX_BET=4.00
KELLY_MAX_PCT=0.04
USE_DAILY_STOP_LOSS=true
DAILY_LOSS_LIMIT=10
STRIKE_DIRECTION_ENFORCE=on
STRIKE_DIRECTION_MIN_DIST=0.00015
HIGH_ASK_EDGE_MIN_ASK=0.62
HIGH_ASK_EDGE_MIN_EDGE=0.12
POLYMARKET_WS_ENABLED=on
BYBIT_WS_ENABLED=on
SCAN_INTERVAL=1
REGIME_INVERT_ENABLED=off
REVERSION_INVERT=off
ARB_ENABLED=false
```

---

## Files in this branch

| File | Role |
|------|------|
| `v3-bot/run_bot.py` | Main bot + `resolve_expired_positions()` |
| `v3-bot/predictor.py` | Signal engine + strike gate |
| `v3-bot/order_manager.py` | V2 CLOB + persistence |
| `v3-bot/market_data.py` | Markets + strike |
| `v3-bot/config.py` | Env → constants |
| `v3-bot/polymarket_ws.py` | WS books |
| `v3-bot/bybit_ws.py` | Bybit failover |
| `v3-bot/binance_ws.py` | Ticks + resolution |
| `v3-bot/morning_*.py` | Morning path |
| `v3-bot/force_tor.py` | Tor routing |
| `v3-bot/telegram_notifier.py` | Alerts |
| `v3-bot/env.production.jun03.example` | Full non-secret env template |
| `v3-bot/PRODUCTION_JUN03_2026.md` | This document |

---

## Known issues / next steps

1. **Resolution** uses Binance spot vs strike at window end — may differ slightly from Polymarket oracle.
2. **Daily PnL seed** on Jun 3 reflected pre-persistence losses; reset `data/daily_pnl.json` for a fresh day.
3. **Correlated exposure**: ETH+SOL same direction same window still possible (max 2 positions afternoon).
4. Consider **persisting** `traded_windows` cleanup and Telegram token rotation if logs exposed secrets.

---

## Changelog from demo restore (Jun 3)

1. Demo `predictor` / `run_bot` / `morning_*` restored; kept `polymarket_ws`, `bybit_ws`, WS books.
2. `py_clob_client` → `py_clob_client_v2`.
3. Strike-direction gate + dist weight 400.
4. Kelly cap $4 / 4%, min edge 10%, thin-edge filter, low-vol abstain.
5. Daily loss tracking + `open_positions.json` / `daily_pnl.json` persistence.
