# Claude Code Agent Context — FaithBot Polymarket 15m Bot

> **Last updated:** 2026-06-09 (post settlement-direction + relax patches)  
> **Purpose:** Single source of truth for AI coding agents working on this bot.  
> **Read this first** before any code change, deploy, or debug session.

---

## 1. Executive summary

FaithBot is a live automated trader on **Polymarket 15-minute crypto Up/Down markets** (BTC, ETH, SOL). It estimates the probability that spot finishes above/below a **strike** (window-open Chainlink price), compares that to the CLOB ask, and bets when edge exceeds thresholds.

**Current production state (Jun 9, 2026):**
- **Losing day:** 9 trades, ~$11.65 losses vs ~$1.90 wins (daily PnL file on EC2)
- **Root cause identified:** Old direction logic bet **momentum** instead of **settlement** (price vs strike at expiry)
- **Fix deployed:** Settlement-first direction (`ed90dff`, tag `settlement-direction-jun09-2026`)
- **Follow-up:** Relaxed far-strike + lowered MIDDAY min_trend (`2b86472`)
- **Post-fix:** Bot heavily abstains in chop — intentional; near-strike losses should be blocked

**Markets resolve on:** Chainlink spot vs strike at window close (not Binance, not momentum path).

---

## 2. Infrastructure (CRITICAL)

### Production host

| Item | Value |
|------|--------|
| SSH | `ssh -i polymarket-key.pem ubuntu@44.192.17.18` |
| **Code path** | `/home/ubuntu/v3-bot/` |
| Main bot | `python3 -u run_bot.py` |
| Dashboard | `python3 -m dashboard_v3.app` (port **8080**) |
| 5m bot (separate) | `run_brain_5m.py` — different strategy, do not confuse |
| Logs | `~/v3-bot/logs/bot_YYYY-MM-DD.log`, `~/v3-bot/v3_bot.log` |
| Data | `~/v3-bot/data/` |

> **⚠️ `.cursor/rules/ec2-production.mdc` is OUTDATED.** It references `~/polymarket-bot/` and `run_brain_bot.py`. **Actual production is `~/v3-bot/` and `run_bot.py`.**

### Local repo vs EC2

| Location | Status |
|----------|--------|
| **EC2 `~/v3-bot/`** | **Source of truth** for running bot |
| **Local repo** | Partial mirror; `predictor.py` / `order_manager.py` synced from EC2 on deploy days |
| **`v3-bot/` folder locally** | Snapshot; may lag EC2 |
| **`EC2_BACKUPS/`** | Historical snapshots, not production |

### Git remotes

| Remote | URL | Use |
|--------|-----|-----|
| `faithbot` | `https://github.com/leoquin26/faithbotpolymarket.git` | **Primary** — production branch pushes |
| `origin` | `https://github.com/leoquin26/Randomforest.git` | Legacy / other work |

**Active branch:** `faithbot-production-jun03-2026`

**Recent commits:**
- `2b86472` — Relax far-strike settlement + MIDDAY min_trend 0.15
- `ed90dff` — Settlement-first direction + GTC strike fix
- `913ddb1` — Production Jun 3 2026 baseline

**Tags:**
- `settlement-direction-jun09-2026`

### Secrets — NEVER commit

`.env` on EC2 contains: `POLYMARKET_PRIVATE_KEY`, `POLYMARKET_FUNDER_ADDRESS`, API keys, Telegram tokens. Use `env.production.jun03.example` as template only.

---

## 3. Running processes & safe restart

```bash
# Check
pgrep -af "^python3 -u run_bot.py$"
pgrep -af dashboard_v3

# Safe restart (15m bot)
cd ~/v3-bot
OLD=$(pgrep -f "^python3 -u run_bot.py$" | head -1)
kill "$OLD" 2>/dev/null; sleep 3; kill -9 "$OLD" 2>/dev/null
setsid nohup python3 -u run_bot.py >> v3_bot.log 2>&1 < /dev/null &
sleep 4
pgrep -af run_bot
tail -20 v3_bot.log

# Syntax check after edits
python3 -m py_compile predictor.py order_manager.py run_bot.py
```

**Rules:**
- Kill **old PID first** — zombies run stale code
- Never `pkill -f 'brain\.py'` — won't match `run_bot.py`
- Verify restart: `[TOR] Selective proxy` in log + new PID

---

## 4. Architecture

```
                    FAITHBOT v3 — PRODUCTION ARCHITECTURE

  Chainlink WS ─────┐     Gamma API ─────── market_data.py
  Binance WS ───────┼──►  (strike, slug, tokens, dist)
  Bybit WS ─────────┘              │
  Polymarket WS ──── CLOB books ───┤
                                   ▼
                            predictor.py
                     (EWMA σ, trend, settlement dir,
                      BS N(d2), gates, calibration)
                                   │
              ┌────────────────────┼────────────────────┐
              ▼                    ▼                    ▼
     morning_strategy.py    regime_aware/         session_calibration.py
     (P1/P3 filters)        confidence_calibrator   (ET phases + gates)
              │                    │
              └────────┬───────────┘
                       ▼
                  run_bot.py
           (scan loop, locks, morning/afternoon dispatch,
            resolve positions, daily stop)
                       │
                       ▼
               order_manager.py
            (Kelly size, FOK/GTC, CLOB v2, positions)
                       │
                       ▼
              Polymarket CLOB + Gamma resolve
```

### Core files (production)

| File | Lines ~ | Role |
|------|---------|------|
| `run_bot.py` | 711 | Main loop, scan, morning/afternoon paths, resolution |
| `predictor.py` | 1022 | Signal engine — **most changes happen here** |
| `order_manager.py` | 782 | Orders, Kelly, GTC, `data/open_positions.json` |
| `market_data.py` | — | Gamma events, strike from Chainlink/Binance |
| `poly_resolution.py` | — | Gamma winner lookup for resolve |
| `session_calibration.py` | 192 | ET session phases + per-session thresholds |
| `morning_strategy.py` | 61 | Extra filters on morning predictions |
| `morning_predictor.py` | — | Morning-specific predictor wrapper |
| `chainlink_ws.py` | — | Primary spot for dist vs strike |
| `binance_ws.py` | — | Tick vol, fallback price |
| `bybit_ws.py` | — | Spot failover |
| `polymarket_ws.py` | — | WS order books |
| `force_tor.py` | — | Tor for Polymarket only |
| `config.py` | — | Defaults; many overridden by `.env` |
| `telegram_notifier.py` | — | Fill/loss alerts |

### Dashboard (`dashboard_v3/`)

| File | Role |
|------|------|
| `app.py` | Flask app, `/api/v3/snapshot` |
| `log_parser.py` | Parse `bot_*.log` for metrics |
| `trade_reconciler.py` | Gamma truth for W/L (fixes log-only drift) |
| `state_reader.py` | Read bot JSON state |

---

## 5. Main loop (`run_bot.py`)

**Scan interval:** `SCAN_INTERVAL=1` (every ~1s)

**Per iteration:**
1. `resolve_expired_positions()` — Gamma resolve; **never guess LOSS** if unresolved
2. `check_gtc_fills()` / `cancel_stale_gtc()`
3. `session_calibration.can_trade_now()` — OFF / WEEKEND / US_OPEN_CHOP blocks
4. Parallel `scan_coin()` for BTC, ETH, SOL
5. **Morning path** (`is_morning_session`): `morning_strategy.filter_morning_signal` → max 1–2 positions
6. **Afternoon path** (`is_afternoon_session`): direct predictor picks
7. Atomic window lock — one bet per coin per 15m window
8. Re-fetch CLOB ask before `place_bet`

**Price for dist vs strike:** Chainlink first (`chainlink_ws`), not Binance.

---

## 6. Session schedule (America/New_York)

| Phase | ET window | Name | Trading? |
|-------|-----------|------|----------|
| P1a | 08:30–09:30 | PRE_OPEN | Yes (morning) |
| P2 | 09:30–11:00 | US_OPEN_CHOP | **NO** — blackout |
| P1b | 11:00–12:30 | POST_OPEN | Yes (morning) |
| P3 | 12:30–15:00 | MIDDAY | Yes (morning path) |
| PM | 15:00–18:00 | AFTERNOON | Yes (afternoon path) |
| — | before 08:30 / after 18:00 | OFF | No |
| — | Sat/Sun | WEEKEND | No |

**Server timezone:** Lima (UTC-5) — same as ET in summer. Session logic uses `ZoneInfo("America/New_York")`.

**Morning vs afternoon:**
- **Morning** (PRE_OPEN, POST_OPEN, MIDDAY): `morning_strategy` + half Kelly
- **Afternoon** (15:00–18:00 ET): main predictor only, looser gates

---

## 7. Predictor pipeline (current — Jun 9)

### Design principle (post-patch)

> **Direction = settlement question** (will spot be above/below strike at expiry?)  
> **Trend/momentum = confidence sizing only**, not primary direction near strike.

### Step 1 — Trend score (confidence / pre-filters)

```python
trend_score = dist×W + roc60×400 + roc120×350 + roc300×300 + momentum×300
```

Gates before direction:
- `[FEW TICKS]`, `[COLD START]`, `[LOW VOL]`, `[VOL SPIKE]`
- `[TF DISAGREE]` — dampen trend when roc60 vs roc300 oppose
- `[CHOPPY]` / `[FADE]` — chop detector + mean-reversion override
- `[WEAK TREND]` — session `min_trend` (MIDDAY now **0.15**)
- `[CHOPPY STRICT]` — extra chop gate

### Step 2 — Settlement-first direction

**Near strike** (`|dist| < SETTLEMENT_NEAR_DIST`, default **0.12%**):
- Direction = sign(dist)
- Require agreement: roc300, book (±2%), BS N(d2) (±2%) — else `[SETTLEMENT] abstain`

**Far from strike** (`|dist| ≥ 0.12%`):
- Direction = sign(dist) — **dist leads**
- BS only **vetoes** if strongly disagrees (`SETTLEMENT_FAR_BS_VETO=0.05` → N(d2) < 45% for UP dist or > 55% for DOWN dist)

**Removed (Jun 9):**
- `sigmoid(trend)` as direction source
- DIR VOTE afternoon bypass (`model=DOWN vote=UP` trades)
- Engine conviction **flips** — now `[ENGINE CONFLICT] skip`

### Step 3 — Post-direction gates (stacked)

| Log tag | Env / threshold | Notes |
|---------|-----------------|-------|
| `[THIN DIST]` | `MIN_DIST_UP/DOWN_PCT=0.0006` | 0.06% min cushion |
| `[BOUNCE]` | roc60+ & thin dist | Blocks dead-cat DOWN |
| `[BOOK CONFLICT]` | `BOOK_DIRECTION_GAP=0.04` | Ask cheaper on opposite side |
| `[DIR VOTE]` | dist+roc+book vote | Must match settlement dir |
| `[DIR LOCK]` | per-coin window commit | Blocks weak flips |
| `[CONSENSUS]` | 2+ coins | Minority direction blocked |
| `[FLIP GUARD]` | `FLIP_TREND_MIN_15M=0.80` | After 3 opposite history |
| `[MOM CONFLICT]` | DOWN + positive roc300 | Unless strong trend |
| `[EXPENSIVE UP/DOWN]` | ask + min dist | Tier 1 protection |
| `[LOW PROB]` | `MIN_WIN_PROB=0.60` | |
| `[LOW EDGE]` | `MIN_EDGE_THRESHOLD=0.07` | 7% edge |
| `[THIN EDGE]` | `HIGH_ASK_EDGE_MIN_*` | Expensive asks need more edge |
| `[TOO LATE]` | <120s left | |

### Calibration

`CALIBRATION_LIVE=on` → `regime_aware/confidence_calibrator.py` adjusts prob in logs as `[CALIBRATION LIVE]`.

---

## 8. Order flow (`order_manager.py`)

1. Edge re-check vs real CLOB ask (2% floor in code)
2. **FOK** if real ask available; else **GTC** if ≥3m left
3. **FOK→GTC fallback** if FOK killed and ≥120s left
4. Kelly sizing: `USE_KELLY_SIZING`, `KELLY_MAX_BET`, `KELLY_MAX_PCT`
5. Position saved to `data/open_positions.json`

**GTC strike fix (Jun 9):** `_strike_fields()` stores strike/slug/timeframe on pending GTC. Old GTC orders queued before patch may still have `strike: 0`.

**Entry bands:**
- UP: `ENTRY_MAX_UP=0.66`
- DOWN: `ENTRY_MAX_DOWN=0.78`
- `PM_ENTRY_MAX=0.64` — additional PM cap in some paths

---

## 9. Resolution (`run_bot.py` + `poly_resolution.py`)

- Primary: **Gamma API** winner per slug
- On unresolved: `[RESOLVE PENDING]` — keep position, **do not default to LOSS**
- Fixed Jun 8: bot was guessing LOSS when Gamma slow → false losses in dashboard

**Reconciliation:** Polymarket CSV / Gamma is ground truth; bot logs under-count wins after restarts.

---

## 10. Persistence files

| File | Contents |
|------|----------|
| `data/open_positions.json` | Open fills: coin, side, entry, shares, strike, window_start |
| `data/daily_pnl.json` | `date`, `losses`, `wins`, `trades` |
| `data/traded_windows.json` | Per-window dedup |
| `data/daily_stop_loss.json` | Daily stop state |

**Jun 9 EOD snapshot:**
```json
{"date": "2026-06-09", "losses": 11.65, "wins": 1.90, "trades": 9}
```

---

## 11. Key environment variables (production `.env`)

### Direction / settlement (Jun 9)
```
SETTLEMENT_NEAR_DIST=0.0012      # default in code if unset
SETTLEMENT_MIN_ROC300=0.00003
SETTLEMENT_BOOK_EDGE=0.02
SETTLEMENT_BS_EDGE=0.02
SETTLEMENT_FAR_BS_VETO=0.05
```

### Session gates
```
SESSION_P3_MIN_TREND=0.15        # was 0.22 — lowered Jun 9
SESSION_P3_MIN_DIST=0.0006
SESSION_AFTERNOON_MIN_TREND=0.20
SESSION_AFTERNOON_MIN_EDGE=0.05
```

### Core trading
```
BOT_COIN_WHITELIST=BTC,ETH,SOL
DRY_RUN=false
MIN_TREND_SCORE=0.28
MIN_DIST_UP_PCT=0.0006
MIN_DIST_DOWN_PCT=0.0006
MIN_WIN_PROB=0.60
MIN_EDGE_THRESHOLD=0.07
SIGMA_FLOOR_MIN=2.5e-4
ACCURACY_GATE_ON=on
CALIBRATION_LIVE=on
ENGINE_CONVICTION_ON=on
ENGINE_LOCK_ON=off
BOOK_DIRECTION_ENFORCE=on
CONSENSUS_GATE_ON=on
FLIP_TREND_MIN_15M=0.80
USE_DAILY_STOP_LOSS=true
DAILY_LOSS_LIMIT=10
```

### Legacy / inactive (present in `.env` but not used by current `run_bot.py`)
Many keys: `REGIME_INVERT_*`, `EXHAUST_*`, `ML_*`, `HYBRID_*`, `M5_*`, `TRAP_*` — **do not assume they are wired**.

`regime_aware/` is **enabled** for calibration only; full regime invert stack was disabled Jun 3.

---

## 12. Jun 8–9 incident timeline (learned lessons)

### Jun 8
- **09:00–10:22:** Zero trades — stacked gates (THIN DIST, WEAK TREND, EXPENSIVE), not daily stop
- **Session misalignment:** P2 US open chop was wrong timezone → rebuilt `session_calibration.py` with ET
- **Resolution bug:** Gamma unresolved → bot defaulted LOSS → fixed in `run_bot.py`
- **Dashboard 500:** `now` UnboundLocalError in `api_snapshot()` — fixed
- **Dashboard W/L wrong:** added `trade_reconciler.py`

### Jun 9 trades (all old code except last resolve)
| ET | Trade | Dist | Result |
|----|-------|------|--------|
| 08:47 | SOL DOWN @ 59¢ | -0.075% | LOSS |
| 11:08 | BTC UP @ 62¢ | +0.070% | WIN |
| 11:32 | BTC DOWN @ 57¢ | -0.074% | LOSS |
| 11:21→11:22 | BTC DOWN @ 58¢ GTC | -0.080% | LOSS (resolved 13:02) |
| 12:02 | ETH UP @ 59¢ GTC | +0.092% | LOSS |

**Loss pattern:** Thin DOWN bets (~7 bps below strike), negative ROC300, market bounced UP by close.

### Jun 9 patches
1. **`_patch_settlement_direction_jun09.py`** — settlement-first direction, remove DIR VOTE bypass, GTC strike
2. **`_patch_settlement_relax_jun09.py`** — far-strike dist leads; MIDDAY min_trend 0.15

### Post-patch behavior (11:31–13:05 ET)
- **0 new SIGNALs** — heavy abstain in chop
- Top blockers: `[SETTLEMENT]`, `[WEAK TREND]`, `[THIN DIST]`, `[LOW EDGE]`, `[FLIP GUARD]`
- This is **partially correct** — avoids repeating Jun 9 loss pattern

---

## 13. Deploy workflow for agents

### Standard patch flow

```bash
# 1. Read production first
ssh -i polymarket-key.pem ubuntu@44.192.17.18 "sed -n '1,100p' ~/v3-bot/predictor.py"

# 2. Write patch script locally (_patch_*.py)
# 3. SCP and apply
scp -i polymarket-key.pem _patch_foo.py ubuntu@44.192.17.18:~/v3-bot/
ssh -i polymarket-key.pem ubuntu@44.192.17.18 "cd ~/v3-bot && python3 _patch_foo.py . && python3 -m py_compile predictor.py"

# 4. Restart bot (see section 3)
# 5. Verify logs
ssh -i polymarket-key.pem ubuntu@44.192.17.18 "tail -50 ~/v3-bot/logs/bot_\$(date +%Y-%m-%d).log"

# 6. Sync to local + push faithbot (if user asks)
scp -i polymarket-key.pem ubuntu@44.192.17.18:~/v3-bot/predictor.py ./predictor.py
git add predictor.py && git commit && git push faithbot faithbot-production-jun03-2026
```

### Local patch scripts (repo root)

| Script | Purpose |
|--------|---------|
| `_patch_settlement_direction_jun09.py` | Settlement-first direction |
| `_patch_settlement_relax_jun09.py` | Far-strike relax + MIDDAY trend |
| `_patch_session_calibrate_jun08.py` | ET session phases |
| `_patch_resolution_gamma_jun08.py` | Gamma resolve fix |
| `_patch_dashboard_resolution_jun08.py` | Dashboard reconciler |
| `_patch_unblock_trades_jun08.py` | Gate loosen (Jun 8) |
| `_patch_afternoon_trading_jun08.py` | Afternoon path |
| `_patch_peak_restore_jun05.py` | Peak quality restore |

---

## 14. Debugging cheat sheet

### Log tags → meaning

```bash
# Count blockers today
grep -oE '\[[A-Z][A-Z _-]+\]' ~/v3-bot/logs/bot_$(date +%Y-%m-%d).log | sort | uniq -c | sort -rn

# Trades only
grep -E 'SIGNAL|ORDER|FILLED|RESOLVE|LOSS|WIN' ~/v3-bot/logs/bot_$(date +%Y-%m-%d).log

# Settlement blocks
grep SETTLEMENT ~/v3-bot/logs/bot_$(date +%Y-%m-%d).log | tail -30
```

### Common false assumptions

| Wrong | Right |
|-------|-------|
| Local `predictor.py` is production | EC2 `~/v3-bot/predictor.py` is production |
| `~/polymarket-bot/` | `~/v3-bot/` |
| `run_brain_bot.py` is 15m bot | `run_bot.py` is 15m bot |
| Momentum direction = settlement | Polymarket resolves level vs strike |
| All `.env` keys are wired | Many are legacy; verify in code |
| Bot silent = crashed | Often stacked gates in chop |
| Log W/L = truth | Use Gamma / Polymarket CSV |

### Performance reference

- **Peak day (Jun 3 post-restart):** 9W/0L on new entries (~4:45 PM ET+)
- **Best historical window:** 2–3 PM ET (documented 80%+ WR in older docs)
- **Worst pattern:** Thin dist DOWN (~7 bps) + momentum DOWN + book UP → bounce loss

---

## 15. Open issues / next work

| Priority | Issue | Notes |
|----------|-------|-------|
| P1 | Gate stack too aggressive post-settlement | Settlement correct but 0 trades in chop — tune far-strike / afternoon only |
| P2 | `FLIP GUARD` blocks valid UP | After DOWN streak, trend 0.76 < 0.80 |
| P3 | `LOW EDGE` when ask ≈ prob | Market efficient midday — may need lower `MIN_EDGE` for afternoon only |
| P4 | Chainlink WS disconnects | `[CHAINLINK-WS] closed` in logs — monitor |
| P5 | GTC orders pre-patch missing strike | Fixed going forward; historical positions may have `strike: 0` |
| P6 | Local repo / cursor rule drift | Update `ec2-production.mdc` path to `v3-bot` |
| P7 | EC2 git has exposed token in remote URL | Rotate GitHub PAT; use credential helper |

### Recommended tuning direction (not yet done)

1. Keep **near-strike settlement strict** (|dist| < 0.12%)
2. Afternoon-only: `MIN_EDGE` 0.05, relax `FLIP GUARD` to 0.65
3. Do **not** revert to momentum-led direction
4. Consider simplifying to OPEN/ACTIVE/LATE sessions vs morning+predictor double gates

---

## 16. Related documentation

| File | Contents |
|------|----------|
| `FAITHBOT_DOCUMENTATION.md` | Older comprehensive docs (pre-settlement; partially stale) |
| `PRODUCTION_JUN03_2026.md` | Jun 3 working config snapshot |
| `v3-bot/PRODUCTION_JUN03_2026.md` | Same, in v3-bot folder |
| `MARKDOWNS/CONVERSATION_CONTEXT_FULL.md` | Historical conversation context |
| `.cursor/rules/ec2-production.mdc` | **Stale paths** — see section 2 |

---

## 17. Agent rules of engagement

1. **Always read EC2 before edit** — never assume local matches production
2. **Patch scripts preferred** over hand-editing on EC2 — reproducible, committable
3. **`python3 -m py_compile`** after every Python edit
4. **Restart + verify PID + log tail** after deploy
5. **Do not tune `.env` alone** when the bug is architectural — direction must stay settlement-first
6. **Do not commit** `.env`, keys, CSVs with account data, `EC2_BACKUPS/` wholesale
7. **Ask before** force push, daily stop override, or disabling settlement near-strike
8. **User preference:** fix code logic over threshold thrashing; minimal focused diffs
9. **Commits to faithbot** only when user requests; tag major production releases

---

## 18. Quick reference — predictor direction (current code)

```python
# NEAR strike (|dist| < 0.12%): ALL must agree or abstain
level_dir = sign(dist)
roc_dir must match or abstain
book_dir must match or abstain  
bs_dir must match or abstain

# FAR strike (|dist| >= 0.12%): dist leads
settlement_dir = sign(dist)
abstain only if BS strongly vetoes (5% from 50%)

# Then: THIN DIST, BOOK CONFLICT, DIR VOTE, CONSENSUS, FLIP GUARD,
#       MOM CONFLICT, EXPENSIVE, LOW PROB, LOW EDGE → Prediction or None
```

---

*End of agent context. Update this file after every production deploy that changes architecture, gates, or session logic.*
