# FaithBot - Complete Conversation Context

> **Purpose:** This document provides everything a new AI conversation needs to fully understand and continue working on this trading bot. Read this first before making any changes.

---

## 1. System Overview

FaithBot is an automated trading bot for Polymarket's crypto "Up or Down" 15-minute binary options markets. It predicts whether BTC, ETH, SOL, or XRP will be above or below a threshold price at the end of each 15-minute window, then places bets when it identifies a mathematical edge.

- **Production server:** EC2 at `ubuntu@44.192.17.18`
- **EC2 path:** `/home/ubuntu/v3-bot/`
- **GitHub repo:** `https://github.com/leoquin26/faithbotpolymarket.git`
- **Branches:** `main` = stable production-tested, `demo` = latest changes (merge to main once validated)
- **Local workspace:** `c:\Users\leona\Projects\polymarket-no-maxi-bot\` (NOT up to date — always read EC2 files before editing)

---

## 2. Current Production State (as of April 15, 2026)

| Property | Value |
|----------|-------|
| Bot version | V12 with Morning Strategy |
| EC2 path | `/home/ubuntu/v3-bot/` |
| SSH key | `polymarket-key.pem` (in local workspace root) |
| Current bankroll | ~$106 (started at $110 today, won BTC +$4.20, SOL +$2.72, lost ETH -$7.54) |
| Bot PID | 2549721 |
| Trading hours | 9am-5pm Lima/ET (morning strategy 9am-2pm, afternoon main 2pm-5pm) |
| Bet sizing | Compounding Kelly Criterion (8% of live bankroll cap) |
| Order type | Fill-or-Kill (FOK) |
| Tor proxy | Enabled (required for order placement from US EC2) |
| Arbitrage | Disabled (caused $20 loss, permanently off) |

---

## 3. Critical Files on EC2

All files live at `/home/ubuntu/v3-bot/`. **ALWAYS read from EC2 before editing** — local repo is NOT up to date.

| File | Purpose |
|------|---------|
| `run_bot.py` | Main loop, scanning, trade gating, morning/afternoon routing |
| `predictor.py` | V12 prediction engine (BS + EWMA + Momentum + 5-min ROC) |
| `morning_strategy.py` | 3-phase morning filter (does NOT replace predictor) |
| `order_manager.py` | Kelly sizing, CLOB orders, compounding (8% bankroll cap) |
| `binance_ws.py` | Price data (WebSocket + REST poller) |
| `market_data.py` | Polymarket event lookup, threshold calculation |
| `config.py` | Configuration with .env overrides |
| `telegram_notifier.py` | Trade notifications via Telegram |
| `force_tor.py` | Tor proxy management |
| `.env` | All runtime parameters — **DO NOT commit secrets** |
| `chop_state.json` | ChopDetector persistence (survives restarts) |
| `outcomes.json` | Trade outcome history for cold streak detection |
| `morning_dir_state.json` | Morning direction history |
| `data/traded_windows.json` | Window dedup persistence |
| `logs/bot_YYYY-MM-DD.log` | Daily persistent logs |

---

## 4. Key Parameters (.env)

```
BANKROLL_BALANCE=110
KELLY_FRACTION=0.25
KELLY_MAX_BET=0              # 0 = compounding mode (8% of live bankroll)
KELLY_MIN_BET=2.00
MIN_WIN_PROB=0.75
MIN_EDGE_THRESHOLD=0.05
ENTRY_MIN=0.15
ENTRY_MAX=0.68
WARMUP_SEC=45
DAILY_LOSS_LIMIT=15          # Scales: max(15, bankroll * 0.10)
USE_DAILY_STOP_LOSS=true
USE_TOR=true                 # Required for order placement
ARB_ENABLED=false            # Permanently disabled after losses
SKIP_NIGHT_HOURS=true
NIGHT_START_HOUR=22          # UTC = 5pm Lima
NIGHT_END_HOUR=14            # UTC = 9am Lima
DRY_RUN=false
AGGRESSIVE_FOK=true
```

---

## 5. Prediction Engine Details

### Trend Score Formula
```
trend_score = dist_pct * 200
            + roc_60  * 400
            + roc_120 * 350
            + roc_300 * 250      (5-min ROC — added in V12)
            + momentum_raw * 300
```

- `dist_pct = (price - strike) / strike`
- `roc_N = (price_now - price_N_ago) / price_N_ago`
- `momentum_raw = 0.50 * roc_10 + 0.30 * roc_30 + 0.20 * roc_60`

### Probability Blending
```
raw_prob = sigmoid(trend_score * 3.0)
combined_prob = 0.70 * raw_prob + 0.30 * bs_n_d2
```

### Distance Penalty
When `|dist_pct| < 0.1%`, probability is dampened toward 50% to prevent false confidence at the strike.

### Timeframe Disagreement Dampener
```
if sign(roc_60) != sign(roc_300) AND abs(roc_300) > 0.30 * abs(roc_60):
    trend_score *= 0.50
```
Prevents trading on short-term noise that contradicts the 5-minute trend.

### Minimum Thresholds (Afternoon)
- 75% win probability
- 5% edge
- 0.40 trend score
- 30 ticks
- 75s warmup (WARMUP_SEC=45 in .env, but effective minimum enforced in code)

### Direction Consensus
First trade in a window commits a direction for all coins. Subsequent trades in the same window must agree with the committed direction.

### ChopDetector
Tracks last 4 window directions. If 2+ flips detected, market is "CHOPPY":
- Lowers trend threshold to 0.20
- Can enable mean-reversion fading
- Records actual market direction (not bot's own bets)

---

## 6. Morning Strategy (morning_strategy.py)

Separate filter layer on top of the main predictor. Does NOT replace `predictor.py`.

| Phase | Time (Lima/ET) | Coins | Min Prob | Min Edge | Min Trend | Sizing | Max Pos |
|-------|---------------|-------|----------|----------|-----------|--------|---------|
| Phase 1 | 9:00 - 10:30 | BTC, ETH only | 80% | 10% | 0.60 | Half Kelly | 1 |
| Phase 2 | 10:30 - 12:00 | NONE | — | — | — | — | 0 |
| Phase 3 | 12:00 - 14:00 | All coins | 78% | 8% | 0.50 | Half Kelly | 1 |

- Phase 2 blocks all trading during US market open (highest reversal risk)
- Historical morning WR: 72% across 78 trades (+$111.70)
- DOWN trades stronger (76%) vs UP trades (69%) in morning sessions

---

## 7. Afternoon Engine (NEVER TOUCH)

- Located at lines 343+ in `run_bot.py`
- Uses same `predictor.predict()` but with standard thresholds (75% prob, 5% edge, 0.40 trend)
- Max 2 positions, full Kelly sizing
- Proven 75-86% win rate in 2pm-4pm window
- Gate: `if unique and can_trade and _is_afternoon:`
- **DO NOT modify the afternoon trading logic unless absolutely necessary. It is the proven profit engine.**

---

## 8. Key Historical Decisions & Lessons

### Version History
| Version | Approach | Result |
|---------|----------|--------|
| V1-V7 | Various (GBM, Monte Carlo with fixed seed, indicator-first) | All failed |
| V8 | Empirical trend model | First profitable version |
| V9 | Added technical indicators | Mixed results |
| V10 | Indicator-first prediction | Failed badly (bankroll down to $61) |
| V11 | Black-Scholes + EWMA | Better but had DOWN bias (broken momentum) |
| V12 | Trend-based direction + BS blend | Current production, best results |

### Hard-Learned Rules
1. **Arbitrage experiment:** Caused $20 loss, permanently disabled (`ARB_ENABLED=false`)
2. **Morning strategy v1:** Too aggressive, caused losses, disabled. V2 (current) uses much stricter filters
3. **Mid-session deploys are DANGEROUS:** April 9 had 6 restarts, caused -$40 loss day. Each restart destroys EWMA/momentum state
4. **DO NOT change the afternoon engine** unless absolutely necessary — it's the proven profit center
5. **Direction consensus lock** prevents contradicting bets in the same window
6. **ChopDetector was previously biased** by bot's own bets — now records actual market direction
7. **Cold start guard:** Bot needs ~2min of data before trading after restart
8. **FOK orders are critical:** GTC orders had fill-rate issues and required complex cancellation logic

---

## 9. Operational Procedures

### SSH Access
```bash
ssh -i polymarket-key.pem ubuntu@44.192.17.18
```

### Start Bot
```bash
cd /home/ubuntu/v3-bot && PYTHONUNBUFFERED=1 nohup python3 -u run_bot.py >> /tmp/v12_run.log 2>&1 &
```

### Stop Bot
```bash
pkill -9 -f 'python3.*run_bot'
```

### Check Logs
```bash
tail -f /home/ubuntu/v3-bot/logs/bot_$(date +%Y-%m-%d).log
```

### Verify Syntax After Edits
```bash
python3 -m py_compile filename.py
```

### Push to Demo Branch
```bash
git checkout demo && git merge main && git add files && git commit && git push origin demo && git checkout main
```

### Critical Rules
- **ALWAYS read EC2 files before editing** — local repo is NOT up to date
- **ALWAYS verify syntax** after edits: `python3 -m py_compile filename.py`
- **NEVER restart mid-session** during profitable trading hours (especially 2pm-5pm)
- **NEVER edit local files expecting them to affect production** — EC2 is the source of truth
- **Kill the correct process** before restart: check `ps aux | grep run_bot` for actual PIDs
- `run_bot.py` is the main process (NOT `brain.py` — that's a legacy name from the local repo)
- Verify restart loaded new code: check startup logs and confirm old PID is gone

---

## 10. Current TODO / Next Steps

1. **Monitor morning strategy** performance over 3-5 sessions before tweaking thresholds
2. **Validate 5-min ROC** direction filter (roc_300) effectiveness — is the timeframe disagreement dampener helping?
3. **Consider extending afternoon to 6pm** once morning proves stable and adds consistent profit
4. **Review Kelly parameters** if bankroll reaches $200+ (may need to adjust KELLY_FRACTION or compounding cap)
5. **Eventually merge demo to main** once all changes are validated over multiple sessions
6. **Track DOWN vs UP morning bias** — if DOWN continues to outperform, consider asymmetric thresholds
7. **Phase 2 boundary tuning** — if 10:30-12:00 block proves too conservative, may narrow to 10:30-11:30

---

## Quick Reference Card

```
SSH:     ssh -i polymarket-key.pem ubuntu@44.192.17.18
Path:    /home/ubuntu/v3-bot/
Start:   cd /home/ubuntu/v3-bot && PYTHONUNBUFFERED=1 nohup python3 -u run_bot.py >> /tmp/v12_run.log 2>&1 &
Stop:    pkill -9 -f 'python3.*run_bot'
Logs:    tail -f /home/ubuntu/v3-bot/logs/bot_$(date +%Y-%m-%d).log
PID:     ps aux | grep run_bot | grep -v grep
Syntax:  python3 -m py_compile <file.py>

Morning:   9:00-10:30 (BTC/ETH, strict) → 10:30-12:00 (NO TRADE) → 12:00-14:00 (all, moderate)
Afternoon: 14:00-17:00 (full engine, DO NOT TOUCH)

Key files: run_bot.py, predictor.py, morning_strategy.py, order_manager.py, config.py, .env
```
