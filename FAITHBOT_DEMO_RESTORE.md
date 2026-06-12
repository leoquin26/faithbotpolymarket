# FaithBot Demo Restore (20260603_142746)

## Source
- https://github.com/leoquin26/faithbotpolymarket/tree/demo
- Profitable core: BS + 70% trend blend, ChopDetector, no regime invert

## Restored files
predictor.py, run_bot.py, morning_strategy.py, morning_predictor.py, order_manager.py

## Kept from v3-bot (speed)
- polymarket_ws.py + set_subscriptions() per scan
- order_manager WS-first get_clob_book
- bybit_ws failover (_multi_price)
- force_tor.py, .env (unchanged)

## Removed / disabled
- regime_aware/ -> regime_aware.disabled_20260603_142746
- exhaustion_detector loop (not in demo run_bot)
- All Jun-3 flip/invert/cheap-trap patches

## Restart
pkill -f 'python3 -u run_bot.py'
cd ~/v3-bot && nohup python3 -u run_bot.py >> logs/bot_$(date +%Y-%m-%d).log 2>&1 &
