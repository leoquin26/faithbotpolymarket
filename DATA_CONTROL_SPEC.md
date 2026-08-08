# QUANTUM DATA CONTROL — build spec (next session)

Owner's goal: one dashboard over ALL collected data — inventory, metrics, graphs —
positioned so the datasets themselves become a sellable asset later.

## Assets already collecting (the product)
| dataset | rows (Aug 8) | growth | content |
|---|---|---|---|
| hourly_research.csv | 211k+ | ~12k/day | 30s bid/ask snapshots, 4 coins, 1h windows + winners |
| late_book.jsonl | 113k+ | ~8k/day | 1Hz top-2 book depth, 15m windows, both tokens |
| clean_bot_research.csv | 12k+ | paused w/ 15m | per-window features + outcomes (the 15m lab) |
| wallet_trades.csv | 2.02M | one-shot | full participant census, 6,754 wallets, cash-flow P&L |
| hour_bot_state.json | cycles | live | audited maker ledger (cycle1 PASSED n=40 +25.21) |

## Build plan
1. `data_control.py` (FastAPI, port 8097, same auth pattern as quantum_dash):
   - /api/inventory — per-dataset: rows, size, first/last ts, growth rate, gaps
   - /api/hourly/metrics — calibration curve, fav ROI by t_left/band/hour (the validated suite)
   - /api/ledger — hour_bot cycles, equity, meter stats
   - nightly cron: append-day + rerun validation suite + Telegram digest
     ("edge holding / drifting / proposal — pending owner word"; NOTHING auto-deploys)
2. `quantum_ui/data.html` — the control room: inventory cards w/ sparklines,
   calibration + ROI charts (reuse quantum_ui chart code), census explorer,
   export buttons (CSV slices) — the future "data storefront" skeleton.
3. Cloudflare tunnel route alongside the desk; same credentials.

## Rules carried over (constitution)
- Analyst PROPOSES; pre-registered gates + owner DISPOSE. No self-deploying changes.
- All new claims: chronological train/test + z >= bar before any live config touch.
- Layer-3 selection model (GBM on snapshots) only after ~3 more weeks of data.
