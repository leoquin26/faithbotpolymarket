# FaithBot Polymarket

Production trading bot for Polymarket 15-minute crypto Up/Down markets (BTC, ETH, SOL).

## Production snapshot (Jun 3, 2026)

**Full configuration, deployment, gates, and session results:**

→ **[PRODUCTION_JUN03_2026.md](./PRODUCTION_JUN03_2026.md)**

Copy `.env.example` → `.env` and set Polymarket + Telegram credentials. Requires **`py_clob_client_v2`**.

## Stack

- V12 Predictor — 70% trend momentum + 30% Black-Scholes / EWMA vol
- Polymarket CLOB V2 (`py_clob_client_v2`)
- Binance + Bybit WebSocket, Polymarket WS order books
- Kelly sizing (capped), strike-direction gate, daily stop-loss
- Position + daily PnL persistence across restarts

## Run

```bash
python3 -u run_bot.py
```

## EC2 live

- `ubuntu@44.192.17.18:/home/ubuntu/v3-bot/` (deploy path may use `v3-bot/` subfolder on server)
