# CleanBot — Working Trader (current: v1.1.0, see CHANGELOG.md)

> **STATUS: ✅ WORKING / PROFITABLE / COMPOUNDING.** First profitable session
> (v1.0): 48 trades, 67% WR, +$22.50 real (account ~$28 → ~$50). v1.1 adds
> bankroll-scaled compounding. **The running version is in `clean_bot.py`
> `VERSION`, logged on startup + shown in Telegram + dashboard.** Per-version
> details and rollback points: **`CHANGELOG.md`** + git tags `cleanbot-vX.Y.Z`.

## What it is
A minimal, single-purpose **early-drift** trader for Polymarket 15-minute crypto
Up/Down markets (ETH/SOL). It replaced the old `run_bot.py`, which had ~10 stacked
gates that collectively paralysed it (it stopped placing trades). CleanBot trades
**one validated edge with no accreted gates**.

## The edge (validated on 767 windows + live)
In the **first ~5 minutes** of a 15m window (`time_remaining >= 600s`), if price
has **drifted >= 7 bps** from the window-open strike, that direction predicts the
15-minute close (**65–72%** backtest; **67%** live). Then:
- **Maker-first**: rest a GTC limit 1¢ below the ask → capture the spread, pay
  **0 taker fee**.
- Enter **only if ask <= 66¢** → never overpay a move the market already priced
  (paying >66¢ for a priced move was the historical leak).
- **ETH/SOL only**, **5-share** min size, **$6 net daily stop**.

**It is REGIME-DEPENDENT.** It pays in less-efficient hours (overnight / thin
liquidity: cheap entries + drift continues) and is weak in US-active midday
(efficient, priced, mean-reverting). Live proof: overnight 73% +$22 vs one
US-midday session 0/3 −$7.

## Strategy flow (`clean_bot.py`)
1. Each ~5s scan, for ETH and SOL: `get_market_info(coin)` → strike, tokens, window.
2. Skip unless window age in [warmup 60s, 300s] (i.e. `T >= 600s`) — **early only**.
3. `dist = (price − strike) / strike`; skip if `|dist| < DRIFT_BPS`.
4. Direction = sign(dist). Get the favored side's ask; skip unless `MIN_ASK <= ask <= MAX_ASK`.
5. Place a **maker GTC** at `ask − 1¢`, size = SHARES. Dedup one bet per window.
6. `check_orders()` polls fills; cancels unfilled GTC after `GTC_MAX_AGE` or near window close.
7. `resolve()` settles each filled window via **gamma (Chainlink), decisive ≥0.99**
   → win/loss → daily P&L. `$6` net stop blocks new entries once breached.
8. **Telegram** pings on startup / fill / win / loss / stop. Restart-safe state.

## Config (env knobs, all `CLEAN_*`)
| Env | Default | Meaning |
|-----|---------|---------|
| `CLEAN_DRY` | `true` | dry-run (no orders). Set `false` to go live. |
| `CLEAN_DRIFT_BPS` | `7` | min early move to commit |
| `CLEAN_MIN_T` | `600` | only enter with ≥ this many sec left (early-only) |
| `CLEAN_WARMUP` | `60` | let strike settle before entering |
| `CLEAN_MAX_ASK` | `0.66` | never overpay above this |
| `CLEAN_MIN_ASK` | `0.45` | avoid junk longshots |
| `CLEAN_MAKER_OFFSET` | `0.01` | rest this far below ask |
| `CLEAN_SHARES` | `5` | size (exchange min) |
| `CLEAN_GTC_MAX_AGE` | `180` | cancel unfilled GTC after (sec) |

### Compounding (v1.1)
| Env | Default | Meaning |
|-----|---------|---------|
| `CLEAN_COMPOUND` | `true` | scale bet size with bankroll (off = fixed `CLEAN_SHARES`) |
| `CLEAN_START_BANKROLL` | `48` | seed bankroll on first run (set to real balance) |
| `CLEAN_KELLY_FRAC` | `0.06` | bet = this fraction of bankroll (half-Kelly) |
| `CLEAN_MAX_BET_PCT` | `0.10` | hard cap: one bet never exceeds this % of bankroll |
| `CLEAN_MAX_OPEN_PCT` | `0.25` | cap total simultaneous open exposure |
| `CLEAN_STOP_PCT` | `0.15` | daily stop = this % of bankroll … |
| `CLEAN_DAILY_STOP` | `6.0` | … with this $ floor |

Bankroll is tracked in `clean_bot_state.json` and grows `+= pnl` on every
resolution → the account compounds. Kelly math (from 51 trades): 65% WR,
b=0.72 → full Kelly 16.4%, we run **6% (half-Kelly)** for safety.

### Whipsaw breaker (v1.2)
| Env | Default | Meaning |
|-----|---------|---------|
| `CLEAN_LOSS_BREAKER` | `3` | pause after this many losses in a row (0 = off) |
| `CLEAN_BREAKER_COOLDOWN` | `1800` | pause duration (sec) — 30 min |

Counter is persisted (restart-safe), resets on a win; Telegram 🧊 alert on trip.
Protects peak gains in choppy/whipsaw regimes the net-based daily stop misses.
v1.2 also raised `CLEAN_DRIFT_BPS` 7→10 and `CLEAN_MIN_ASK` 0.45→0.50 (skip the
54%-WR weak-drift band and "market-disagrees" sub-50¢ entries).

### Cross-coin confirmation (v1.3)
| Env | Default | Meaning |
|-----|---------|---------|
| `CLEAN_CONFIRM_COINS` | `ETH` | coins that need market confirmation (followers) |
| `CLEAN_CONFIRM_MARKET` | `BTC,SOL` | market-proxy coins (their drift = the market) |
| `CLEAN_CONFIRM_BPS` | `3` | a proxy must lean ≥ this to vote |

ETH only trades when the broader market drifts the same way: each proxy leaning
the same direction votes +1, opposing −1 → trade only if net > 0. ETH-solo and
ETH-vs-market (divergent) → `[NO CONFIRM]` skip. SOL is the leader (not confirmed).
Data: ETH-confirmed 64% vs ETH-solo 22% vs ETH-divergent 0%.

### Research data logger (v1.4)
| Env | Default | Meaning |
|-----|---------|---------|
| `CLEAN_RESEARCH` | `on` | log every real-move window (traded or not) for edge-mining |
| `CLEAN_RESEARCH_MIN_BPS` | `3` | minimum drift to log a window |

Writes **`clean_bot_research.csv`** — one row per window with full features
(`drift, roc60, roc300, sigma, up/down ask, BTC/SOL drift, confirmed`) + the
**decision** (ENTER / SKIP + reason) + the **true gamma outcome** (`winner`,
`drift_correct`). Captures the windows we *skip* with their outcomes → shows if a
gate is over-filtering. **Read-only, fully isolated** from trading (own
try/except). View it on the dashboard **🔬 Research** tab (`/api/v3/research`).

**Reused infra env (must be set in `.env`, NOT in git):** `POLYMARKET_*`
(creds/key), `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`, `PROXY_HOST`/`PROXY_PORT`
(`9055` = Ireland SSH SOCKS tunnel for order routing).

## Run / Stop (on EC2, `~/v3-bot`)
```bash
# LIVE (single instance — never run two bots on the account):
CLEAN_DRY=false setsid nohup python3 -u clean_bot.py >> logs/clean_bot_console.log 2>&1 </dev/null &

# DRY test:
CLEAN_DRY=true  setsid nohup python3 -u clean_bot.py >> logs/clean_bot_console.log 2>&1 </dev/null &

# STOP (kill by PID — do NOT grep a pattern containing the literal "clean_bot.py",
# your own ssh cmdline self-matches → kills the session):
PID=$(ps -eo pid,args | grep python | grep 'clean''_bot' | grep -v grep | awk '{print $1}'); kill $PID
```

## Rollback to this version
```bash
git checkout cleanbot-v1.0 -- clean_bot.py     # restore just the bot
# or full: git checkout cleanbot-v1.0
scp clean_bot.py ec2:~/v3-bot/   # redeploy, then restart per above
```

## Dependencies (reused, proven — not rebuilt)
- `order_manager.py` → authed `ClobClient` (py-clob-client-v2 signing) + `get_clob_book`
- `market_data.py` → `get_market_info`
- `binance_ws.py` → live price feed (binance.us)
- `force_tor.py` → selective SOCKS proxy (Ireland tunnel, `PROXY_PORT=9055`)
- `telegram_notifier.py` → `tg._send`
- gamma-api.polymarket.com → Chainlink-settled resolution (inline)

## Performance breakdown (2026-06-19, n=48)
- **Overall 67% WR, +$20 (bot) / +$22.50 (real account).**
- By coin: **SOL 74% +$23** (engine) · ETH 53% −$3 (marginal).
- By direction: UP 71% +$14 · DOWN 63% +$6 (DOWN weaker — fades against up-trends).
- By entry: ≤58¢ 71% +$13 · 59–62¢ 58% +$1 · 63–66¢ 68% +$6 (cheap wins).
- By regime: overnight (UTC 00–12h) strong; US-midday (UTC 17–18h) the only losses.

## Known limitations
- **Regime-dependent** — strong overnight, weak US-active midday.
- **SOL >> ETH**; cheap entries (≤62¢) >> expensive (63–66¢); UP >> DOWN in up-trends.
- `$6` net stop can **overshoot by one trade** (counts resolved losses only, not
  open/in-flight positions).

## Companion files
- `clean_bot_state.json` — runtime state (gitignored, auto-regenerates).
- `clean_bot.log` — runtime log (gitignored).
- `clean_analysis.py` — performance analyzer (parses `clean_bot.log`).
- Dashboard 🤖 CleanBot tab — `dashboard_v3/control_api.py` (`_clean`/`_clean_raw`
  + routes) and `templates/control.html` (`cleanTab`), applied on EC2 via
  `_patch_dash_clean.py`.
