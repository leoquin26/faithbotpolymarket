# FaithBot Polymarket Trading Bot - Complete Documentation

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Module Reference](#module-reference)
4. [Prediction Engine](#prediction-engine)
5. [Trade Lifecycle](#trade-lifecycle)
6. [Safety Systems](#safety-systems)
7. [Configuration Guide](#configuration-guide)
8. [Trading Schedule](#trading-schedule)
9. [Performance Data](#performance-data)
10. [Deployment](#deployment)

---

## Overview

FaithBot is an automated trading bot for Polymarket's crypto "Up or Down" 15-minute binary markets. It predicts whether BTC, ETH, SOL, or XRP will be above or below a threshold price at the end of each 15-minute window, then places bets when it identifies a mathematical edge.

**Core Strategy:** The bot calculates the probability that a crypto asset will finish above/below a threshold using Black-Scholes binary option pricing, EWMA tick-level volatility, and multi-timeframe momentum analysis. It only trades when its calculated probability significantly exceeds the market price (the "edge").

**Key Stats:**
- Markets: BTC, ETH, SOL, XRP (15-minute Up/Down)
- Best performance window: 2:00 PM - 3:00 PM Lima/ET (historically 80-86% win rate)
- Trading hours: 2:00 PM - 5:00 PM Lima/ET, weekdays only
- Bet sizing: Kelly Criterion with fractional sizing
- Order type: Fill-or-Kill (FOK) for instant execution

---

## Architecture

```
                         FAITHBOT SYSTEM ARCHITECTURE

    +------------------+       +-------------------+
    |   Binance API    |       | Polymarket Gamma  |
    |  (Price Data)    |       |    (Market Info)   |
    +--------+---------+       +--------+----------+
             |                          |
    +--------v---------+       +--------v----------+
    |  binance_ws.py   |       |  market_data.py   |
    |  - WebSocket     |       |  - Event lookup   |
    |  - REST poller   |       |  - Threshold calc |
    |  - Tick buffer   |       |  - MarketInfo     |
    +--------+---------+       +--------+----------+
             |                          |
             +------------+-------------+
                          |
                 +--------v---------+
                 |  predictor.py    |
                 |  - EWMA Vol     |
                 |  - Black-Scholes|
                 |  - Momentum     |
                 |  - Trend Score  |
                 |  - ChopDetector |
                 |  - Consensus    |
                 +--------+---------+
                          |
                 +--------v---------+
                 |   run_bot.py     |
                 |  - Main loop    |
                 |  - Scan coins   |
                 |  - Trade gating |
                 |  - Atomic locks |
                 |  - Outcome track|
                 +--------+---------+
                          |
              +-----------+-----------+
              |                       |
    +---------v--------+    +---------v--------+
    | order_manager.py |    | telegram_notif.py|
    | - Kelly sizing   |    | - Fill alerts    |
    | - CLOB orders    |    | - Win/Loss alerts|
    | - FOK/GTC        |    | - Error alerts   |
    | - Position track |    +------------------+
    +------------------+
              |
    +---------v--------+
    | Polymarket CLOB  |
    |  (Order Book)    |
    +------------------+
```

### Data Flow

1. **Price Data Collection** (continuous, 24/7):
   - `binance_ws.py` connects to Binance US WebSocket (`aggTrade` stream)
   - REST poller hits `/ticker/price` every 0.5 seconds as fallback
   - All ticks stored in a 1200-tick buffer per coin with timestamps

2. **Market Scanning** (every 3 seconds):
   - `run_bot.py` scans all 4 coins in parallel using ThreadPoolExecutor
   - `market_data.py` fetches current Polymarket event data (token IDs, prices, threshold)
   - `order_manager.py` fetches CLOB orderbook (ask/bid/depth) via direct HTTP

3. **Prediction** (per coin per scan):
   - `predictor.py` receives ticks, market info, and orderbook data
   - Runs through safety filters (warmup, ticks, volatility, cold streak, etc.)
   - Calculates trend score from momentum + distance
   - Converts to probability via Black-Scholes + sigmoid blend
   - Returns a `Prediction` object or `None` (abstain)

4. **Trade Execution** (when signal passes all gates):
   - `run_bot.py` applies atomic window lock, re-fetches CLOB ask, validates edge
   - `order_manager.py` calculates Kelly bet size, places FOK order
   - Position tracked until window expiry, then win/loss recorded

---

## Module Reference

### `predictor.py` - Prediction Engine

The brain of the bot. Contains all mathematical models and safety filters.

**Classes:**

| Class | Purpose |
|-------|---------|
| `Prediction` | Dataclass holding trade signal (coin, direction, probability, edge, etc.) |
| `EWMAVolatility` | Tick-level Exponentially Weighted Moving Average volatility estimator |
| `ChopDetector` | Tracks recent window directions to detect choppy vs trending markets |
| `MomentumAnalyzer` | Multi-timeframe rate-of-change calculator from tick buffer |
| `Predictor` | Main prediction class that orchestrates all models and filters |

**Key Functions:**

| Function | Purpose |
|----------|---------|
| `_norm_cdf(x)` | Normal CDF approximation (Abramowitz & Stegun, max error 1.5e-7) |
| `_bs_binary_prob(S, K, sigma, T)` | Black-Scholes binary call probability N(d2) |
| `_logit(p)` / `_sigmoid(x)` | Probability space transforms for momentum adjustment |
| `Predictor.predict(info, ...)` | Main prediction method - returns Prediction or None |
| `Predictor.feed_ticks(coin, ticks)` | Feeds price data into EWMA and momentum analyzers |
| `Predictor.record_outcome(won)` | Records trade result for cold streak detection |

**State Persistence Files:**
- `chop_state.json` - ChopDetector direction history (survives restarts)
- `outcomes.json` - Recent trade outcomes for accuracy tracking

### `run_bot.py` - Main Loop & Orchestrator

Controls the bot's execution flow, scanning, trade gating, and lifecycle management.

**Key Components:**

| Component | Purpose |
|-----------|---------|
| `_traded_set` + `_trade_lock` | Atomic one-trade-per-coin-per-window deduplication |
| `is_good_trading_hour()` | Trading schedule enforcer (2pm-5pm Lima, weekdays only) |
| `scan_coin(coin)` | Parallel scanner - fetches data and calls predictor |
| `cleanup_old_windows()` | Memory leak prevention for old window locks |
| Outcome tracking loop | Detects expired positions, determines win/loss, records results |
| Crash recovery | Auto-restart up to 50 times with exponential backoff |

**Trade Gating Flow (in order):**
1. `is_good_trading_hour()` - are we in trading hours?
2. `is_window_locked()` - already traded this coin this window?
3. `active_count >= 2` - max 2 concurrent positions
4. `lock_window()` - acquire atomic lock
5. `get_clob_ask()` - re-fetch fresh price
6. Edge validation - `real_edge >= MIN_EDGE`
7. Price range check - `ENTRY_MIN <= ask <= ENTRY_MAX`
8. `place_bet()` - execute order

### `config.py` - Configuration

All tunable parameters, loaded from environment variables with sensible defaults.

See the [Configuration Guide](#configuration-guide) section for full details.

### `order_manager.py` - Order Execution & Sizing

Handles all interaction with the Polymarket CLOB API.

**Key Features:**

| Feature | Description |
|---------|-------------|
| Kelly Criterion sizing | Calculates optimal bet size based on edge, probability, and bankroll |
| Live bankroll fetching | Queries CLOB API for real USDC balance every 5 minutes |
| FOK orders | Fill-or-Kill for guaranteed instant execution |
| GTC orders | Good-til-Cancelled for patient fills (used when edge > 8%) |
| Window dedup | Persistent file-based tracking of traded windows |
| Correlation limit | Max 2 same-direction trades per window across all coins |
| Daily stop-loss | Halts trading if daily losses exceed configured limit |

**CLOB Interaction:**
- Orderbook reads use direct `httpx` HTTP (bypasses Tor proxy for speed)
- Order placement uses `py_clob_client` (routes through Tor for geo-unblocking)

### `market_data.py` - Data Fetching

Fetches market data from two sources:

| Source | Data | Endpoint |
|--------|------|----------|
| Binance REST | Current crypto price | `/ticker/price` |
| Binance REST | Historical klines | `/klines` |
| Binance REST | Threshold (open price at window start) | `/klines?startTime=...` |
| Polymarket Gamma API | Event data, token IDs, Poly prices | `/events?slug=...` |

**Output:** `MarketInfo` dataclass containing all data needed for prediction.

### `binance_ws.py` - Real-Time Price Feed

Dual-mode price data collection:

| Mode | Method | Latency | Purpose |
|------|--------|---------|---------|
| WebSocket | `aggTrade` stream | <100ms | Primary: every individual trade |
| REST Poller | `/ticker/price` every 0.5s | ~500ms | Fallback: reliable snapshots |

Both modes store ticks in a shared buffer (1200 ticks per coin). The REST poller runs alongside WebSocket to ensure continuous data even if WS drops.

**Geo-blocking:** Binance.com WebSocket returns HTTP 451 from US-based EC2. The bot uses `stream.binance.us` and falls back to REST polling automatically after 3 failed WS attempts.

### `telegram_notifier.py` - Notifications

Lightweight async Telegram notification system.

| Event | Message |
|-------|---------|
| Bot startup | "BOT STARTED - V12 engine online" |
| Trade filled | Coin, direction, entry price, shares, cost, edge, probability |
| Trade won | Coin, direction, P&L, cost, payout |
| Trade lost | Coin, direction, cost lost |
| Error | Error message (1-minute cooldown to prevent spam) |

All messages sent in background threads with deduplication (5-second cooldown per event type).

---

## Prediction Engine

### Step 1: EWMA Volatility (per-second sigma)

The bot estimates real-time volatility using an Exponentially Weighted Moving Average on tick-level log returns:

```
For each new tick (price, timestamp):
    log_return = ln(price / last_price)
    dt = timestamp - last_timestamp
    r2_per_sec = log_return^2 / dt
    variance = lambda * variance + (1 - lambda) * r2_per_sec
    sigma = sqrt(variance)
```

**Parameters:**
- `lambda = 0.94` (decay factor, standard RiskMetrics value)
- `SIGMA_FLOOR = 1e-05` (prevents decay to zero during low-tick periods)
- Sigma spike detection: abstains if `sigma > 3x mean_sigma`

### Step 2: Black-Scholes Binary Option Probability

Calculates the mathematical probability that the crypto price will finish above the threshold:

```
d2 = [ln(S/K) + (-0.5 * sigma^2) * T] / (sigma * sqrt(T))
P(price > strike) = N(d2)
```

Where:
- `S` = current crypto price (from Binance)
- `K` = threshold/strike price (open price at window start)
- `sigma` = EWMA per-second volatility
- `T` = seconds remaining in window
- `N()` = cumulative normal distribution (Abramowitz & Stegun approximation)

This gives the base probability that "UP" wins. `P(DOWN) = 1 - P(UP)`.

### Step 3: Trend Score (Primary Direction Signal)

The raw Black-Scholes probability only captures the current position vs threshold. The trend score adds directional momentum:

```
trend_score = dist_pct * 200       (position vs strike - strongest weight)
            + roc_60 * 500         (60-second rate of change)
            + roc_120 * 300        (120-second rate of change)
            + momentum_raw * 400   (weighted 10s/30s/60s momentum)
```

Where:
- `dist_pct = (current_price - strike) / strike` (positive = above strike)
- `roc_N = (price_now - price_N_seconds_ago) / price_N_seconds_ago`
- `momentum_raw = 0.50 * roc_10 + 0.30 * roc_30 + 0.20 * roc_60`

**Trend threshold:** In trending markets, `abs(trend_score) >= 0.40` required. In choppy markets, `abs(trend_score) >= 0.20` with additional conditions.

### Step 4: Choppy Market Adaptation

The `ChopDetector` tracks the last 4-6 window directions. If 2+ direction flips occurred:
- Market classified as "CHOPPY"
- Trend threshold lowered to 0.20 (but must have either strong trend OR mean-reversion signal)
- Mean-reversion fading: if price is stretched from 2-minute SMA, the bot can "fade" the trend (trade against it)

### Step 5: Probability Blending

The trend score is converted to a probability and blended with Black-Scholes:

```
raw_prob = sigmoid(trend_score * 3.0)    # Trend-based probability
combined_prob = 0.70 * raw_prob + 0.30 * base_up_prob   # 70% trend, 30% BS math
```

This ensures actual price movement (trend) is the primary driver, with BS math providing a mathematical sanity check.

### Step 6: Direction Decision

```
if combined_prob >= 0.5:
    direction = "UP", win_prob = combined_prob
else:
    direction = "DOWN", win_prob = 1 - combined_prob
```

### Step 7: Edge Calculation

```
edge = win_prob - polymarket_ask_price
```

The bot only trades when `edge >= MIN_EDGE` (5%). This means if the bot calculates 80% win probability and the Polymarket ask is 65c, the edge is 15% -- a strong trade. If the ask is 78c, the edge is only 2% -- skipped.

---

## Trade Lifecycle

### Phase 1: Scanning (every 3 seconds)

```
for each coin in [BTC, ETH, SOL, XRP]:  (parallel)
    1. Fetch MarketInfo from Polymarket Gamma API
    2. Get live Binance price from WebSocket/REST
    3. Fetch CLOB orderbook (ask, bid, depth) via direct HTTP
    4. Get tick history (last 300 seconds)
    5. Call predictor.predict() with all data
    6. Collect valid Prediction objects
```

### Phase 2: Signal Selection

```
1. Filter predictions: confidence in (HIGH, MEDIUM) AND edge >= MIN_EDGE
2. Deduplicate: keep highest-probability prediction per coin
3. Sort by probability descending
4. Select best signal
```

### Phase 3: Trade Gating

```
1. Check trading hours (2pm-5pm Lima, weekdays)
2. Check max positions (2 concurrent)
3. Acquire atomic window lock (prevents double-trade)
4. Re-fetch CLOB ask price (prevents stale execution)
5. Recalculate edge with fresh ask
6. Validate edge >= MIN_EDGE
7. Validate ENTRY_MIN <= ask <= ENTRY_MAX
```

### Phase 4: Order Execution

```
1. Calculate Kelly bet size
2. Compute shares = size_usd / limit_price
3. Create FOK order via py_clob_client
4. Submit to Polymarket CLOB
5. Parse fill result (matched shares, average price)
6. Record position (coin, side, entry, shares, strike, window)
7. Send Telegram notification
```

### Phase 5: Outcome Resolution

```
When window_start + 900 + 60 seconds have passed:
    1. Get final Binance price
    2. Compare to strike price
    3. Determine if UP or DOWN won
    4. Calculate P&L
    5. Record outcome in predictor (for cold streak tracking)
    6. Send Telegram win/loss notification
    7. Remove from active positions
```

---

## Safety Systems

The bot has 13 independent safety filters. A trade must pass ALL of them:

### Pre-Prediction Filters

| # | Filter | Threshold | Purpose |
|---|--------|-----------|---------|
| 1 | **Warmup** | 75 seconds | Wait for enough data after window opens |
| 2 | **Too Late** | 120 seconds remaining | Don't trade in final 2 minutes (resolution risk) |
| 3 | **Few Ticks** | 30 minimum | Need sufficient EWMA data for reliable volatility |
| 4 | **No Volatility** | EWMA initialized | EWMA must have processed at least 2 different prices |
| 5 | **Sigma Spike** | 3x mean sigma | Abstain during extreme volatility spikes |
| 6 | **Cold Streak** | 45% accuracy over last 8 trades | Pause if recent accuracy drops below threshold |
| 7 | **Cold Start** | roc_60/roc_120/momentum > 0 | Need 2+ minutes of real momentum data before first trade |

### Direction Filters

| # | Filter | Threshold | Purpose |
|---|--------|-----------|---------|
| 8 | **Weak Trend** | abs(trend_score) >= 0.40 | Require strong directional signal in trending markets |
| 9 | **Choppy Abstain** | Special logic | In choppy markets, need stronger signal or mean-reversion |
| 10 | **Direction Lock** | First direction commits | Once a direction is committed in a window, all coins must agree |
| 11 | **Consensus** | Majority vote | If 2+ coins signal, minority direction is blocked |

### Price/Edge Filters

| # | Filter | Threshold | Purpose |
|---|--------|-----------|---------|
| 12 | **Entry Range** | 15c - 68c | Avoid extreme prices (too cheap = unlikely, too expensive = bad risk/reward) |
| 13 | **Minimum Probability** | 75% | Don't trade low-confidence signals |
| 14 | **Minimum Edge** | 5% | Must have meaningful edge over market price |

### Execution Filters

| # | Filter | Purpose |
|---|--------|---------|
| 15 | **Atomic Window Lock** | One trade per coin per window (threading.Lock + set) |
| 16 | **Max Positions** | Max 2 concurrent positions |
| 17 | **Correlation Limit** | Max 2 same-direction trades per window |
| 18 | **Daily Stop-Loss** | Halt if daily losses exceed $15 |
| 19 | **CLOB Re-validation** | Re-fetch ask and recalculate edge at execution time |

---

## Configuration Guide

### Environment Variables (`.env` file)

#### Polymarket Credentials
| Variable | Description |
|----------|-------------|
| `POLYMARKET_PRIVATE_KEY` | Wallet private key for signing orders |
| `POLYMARKET_FUNDER_ADDRESS` | Funder wallet address |
| `POLYMARKET_API_KEY` | CLOB API key |
| `POLYMARKET_API_SECRET` | CLOB API secret |
| `POLYMARKET_PASSPHRASE` | CLOB API passphrase |
| `POLYMARKET_CHAIN_ID` | Polygon chain ID (default: 137) |
| `POLYMARKET_SIGNATURE_TYPE` | Signature type (default: 1) |

#### Trading Parameters
| Variable | Default | Description |
|----------|---------|-------------|
| `DRY_RUN` | `true` | Set to `false` for live trading |
| `BANKROLL_BALANCE` | `90` | Current bankroll in USDC (auto-updated from CLOB) |
| `ENTRY_MIN` | `0.15` | Minimum ask price to consider (15c) |
| `ENTRY_MAX` | `0.68` | Maximum ask price to consider (68c) |
| `MIN_EDGE_THRESHOLD` | `0.05` | Minimum edge required (5%) |
| `MIN_WIN_PROB` | `0.75` | Minimum win probability required (75%) |
| `MIN_DISTANCE_PCT` | `0.0008` | Minimum price distance from strike |
| `WARMUP_SEC` | `75` | Seconds to wait after window opens |

#### Kelly Criterion Sizing
| Variable | Default | Description |
|----------|---------|-------------|
| `USE_KELLY_SIZING` | `true` | Enable Kelly Criterion bet sizing |
| `KELLY_FRACTION` | `0.25` | Fraction of full Kelly to use (quarter Kelly) |
| `KELLY_MIN_BET` | `2.00` | Minimum bet size in USDC |
| `KELLY_MAX_BET` | `6.00` | Maximum bet size in USDC |

#### Schedule
| Variable | Default | Description |
|----------|---------|-------------|
| `SKIP_NIGHT_HOURS` | `true` | Enable trading hour restrictions |
| `NIGHT_START_HOUR` | `22` | UTC hour to stop trading (5pm Lima) |
| `NIGHT_END_HOUR` | `14` | UTC hour to start trading (9am Lima) |

#### Risk Management
| Variable | Default | Description |
|----------|---------|-------------|
| `DAILY_LOSS_LIMIT` | `15` | Maximum daily loss before halting (USDC) |
| `USE_DAILY_STOP_LOSS` | `true` | Enable daily stop-loss |
| `MAX_SINGLE_TRADE` | `10` | Maximum single trade size (USDC) |

#### Infrastructure
| Variable | Default | Description |
|----------|---------|-------------|
| `USE_TOR` | `true` | Route CLOB orders through Tor proxy |
| `USE_BINANCE_US` | `false` | Use Binance US API (vs global) |
| `AGGRESSIVE_FOK` | `true` | Use FOK orders (vs GTC) |
| `LOG_LEVEL` | `INFO` | Logging verbosity |
| `TELEGRAM_BOT_TOKEN` | - | Telegram bot token for notifications |
| `TELEGRAM_CHAT_ID` | - | Telegram chat ID for notifications |

---

## Trading Schedule

### Active Trading Hours
- **Trading:** 2:00 PM - 5:00 PM Lima time (same as ET), Monday through Friday
- **Scanning:** 24/7 (the bot collects price data continuously even outside trading hours)
- **Weekends:** No trading (Saturday and Sunday fully blocked)

### Why This Schedule
- **2:00 PM - 3:00 PM:** Proven sweet spot with 80-86% historical win rate. US market hours, high liquidity, trending behavior.
- **3:00 PM - 4:00 PM:** Moderate performance (50-67%). Market can become choppy.
- **4:00 PM - 5:00 PM:** Variable. Extended to capture additional opportunities but with declining edge.
- **Before 2:00 PM:** Morning markets are choppy and unprofitable. The bot scans but does not trade.
- **After 5:00 PM:** Low liquidity, unreliable signals.

### Data Collection Importance
The bot must run continuously (not just during trading hours) because:
1. EWMA volatility needs continuous tick data to be accurate
2. Momentum analyzers need price history to calculate rates of change
3. The ChopDetector needs direction history across windows
4. A bot started at 2pm with no prior data will have poor signals for the first 15-30 minutes

**Best practice:** Start the bot before 9 AM and let it run 24/7. It will only trade during the configured window.

---

## Performance Data

### Historical Results (April 2026)

| Date | Trades | Wins | Losses | Win Rate | Net P&L | Notes |
|------|--------|------|--------|----------|---------|-------|
| Apr 6 | 15 | 10 | 5 | 67% | +$21 | First full day with V12 |
| Apr 7 | 14 | 8 | 6 | 57% | -$2 | ChopDetector added mid-day |
| Apr 8 | 15 | 13 | 2 | 87% | +$45 | Best day - uninterrupted run |
| Apr 9 | 16+ | 4 | 12 | 25% | -$40 | Disaster - 6 mid-session restarts broke state |
| Apr 10 | 21 | 14 | 7 | 67% | +$17 | Recovery - uninterrupted run |

### Win Rate by Time Window (April 8-10 combined)

| Time (Lima/ET) | Win Rate | Notes |
|----------------|----------|-------|
| 2:00 PM - 3:00 PM | 86% | Strongest window - trending markets |
| 3:00 PM - 4:00 PM | 50-67% | Variable - can be choppy |
| 4:00 PM - 5:00 PM | 67% | Decent but volatile |

### Key Lesson
The bot performs dramatically better when running uninterrupted. April 8 (13-2, 87%) vs April 9 (4-12, 25%) had identical code -- the only difference was 6 mid-session restarts on April 9 that destroyed accumulated momentum/volatility data.

**Rule: Never restart or deploy changes during trading hours (2pm-5pm).**

---

## Deployment

### EC2 Instance
- **Host:** `ubuntu@44.192.17.18`
- **Path:** `/home/ubuntu/v3-bot/`
- **Python:** 3.x with pip dependencies
- **Key:** `polymarket-key.pem`

### Starting the Bot
```bash
cd /home/ubuntu/v3-bot
PYTHONUNBUFFERED=1 nohup python3 -u run_bot.py >> /tmp/v12_run.log 2>&1 & disown
```

### Stopping the Bot
```bash
pkill -9 -f 'python3.*run_bot'
```

### Checking Status
```bash
# Is it running?
ps aux | grep run_bot | grep -v grep

# Latest logs
tail -30 /home/ubuntu/v3-bot/logs/bot_$(date +%Y-%m-%d).log

# Recent trades
grep FILLED /home/ubuntu/v3-bot/logs/bot_$(date +%Y-%m-%d).log

# Win/Loss record
grep -E 'WIN|LOSS' /home/ubuntu/v3-bot/logs/bot_$(date +%Y-%m-%d).log
```

### Log Files
- **Persistent daily logs:** `~/v3-bot/logs/bot_YYYY-MM-DD.log` (rotates at 50MB, 14-day retention)
- **Console output:** `/tmp/v12_run.log` (append mode, survives restarts)
- **Runtime log:** `~/v3-bot/v3_bot.log` (rotates at 10MB, 3-day retention)

### GitHub Repository
- **Repo:** `https://github.com/leoquin26/faithbotpolymarket.git`
- **main branch:** Production-tested code
- **demo branch:** Testing branch for new features (currently synced with main)

### Dependencies
- `py_clob_client` - Polymarket CLOB API client
- `httpx` - HTTP client (orderbook reads, Telegram)
- `websocket-client` - Binance WebSocket
- `python-dotenv` - Environment variable loading
- `loguru` - Structured logging

### File Structure
```
/home/ubuntu/v3-bot/
    run_bot.py              # Main entry point and orchestrator
    predictor.py            # V12 prediction engine
    config.py               # Configuration with env var loading
    order_manager.py        # Order execution and Kelly sizing
    market_data.py          # Binance + Polymarket data fetching
    binance_ws.py           # WebSocket + REST price feed
    telegram_notifier.py    # Telegram notifications
    force_tor.py            # Tor proxy management
    morning_predictor.py    # Morning strategy (disabled)
    .env                    # Environment variables (credentials, params)
    chop_state.json         # ChopDetector persistence
    outcomes.json           # Trade outcome history
    morning_dir_state.json  # Morning direction history
    data/
        traded_windows.json # Window dedup persistence
    logs/
        bot_YYYY-MM-DD.log  # Daily persistent logs
```
