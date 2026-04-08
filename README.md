# FaithBot Polymarket

Production trading bot for Polymarket binary options (15-minute crypto Up/Down markets).

## Stack
- Black-Scholes + EWMA Volatility + Trend Momentum (V12 Predictor)
- Binance WebSocket + REST for real-time price data
- Polymarket CLOB API for order execution
- Kelly Criterion bet sizing
- ChopDetector for market regime detection
- Telegram notifications
