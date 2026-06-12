# Model & Logic Audit (Jun 3, 2026)

## Your ETH UP @ 45–46c loss (14:15 window)

| Field | Value | Problem |
|-------|-------|---------|
| Spot vs strike | **Dist = -0.007%** (below strike) | Should not buy UP |
| Polymarket book | **UP 48c / DOWN 54c** | Book favors DOWN |
| Model trend | +0.59 | Momentum said UP (ROC +7 bps) |
| Regime | REVERTING + Pattern-A | Approved UP anyway |
| User view | Market trending down | Book + spot agreed; model disagreed |

**Root cause:** Model blends **momentum trend** with **Black–Scholes** and ignores book when they conflict. No flip was used on this trade — it was a straight **wrong-direction momentum** bet.

---

## Signal model (predictor.py)

```
1. Binance ticks → EWMA volatility (sigma)
2. Trend score = momentum ROC + dist_pct×200 (distance from strike)
3. combined_prob = 50% trend + 50% BS binary prob
4. direction = UP if combined_prob >= 0.5
5. edge = win_prob - poly_ask
```

**Weaknesses observed today:**
- `sigma` can collapse (1.6e-4, 7e-4) → BS meaningless
- `dist_pct` near 0 → coin flip but trend score still pushes UP/DOWN
- **REVERTING regime** still uses Pattern-A on cheap side without book check
- Calibrated prob 59–62% ≠ actual WR in chop (~50% or worse)

---

## Logic layers (order of execution)

| # | Layer | Can flip direction? | Jun 3 status |
|---|--------|---------------------|--------------|
| 1 | Warmup / late / ticks | No | OK |
| 2 | Near-strike | No (skip) | Bypassed early in window |
| 3 | Strike conflict | No | Was broken (fixed); threshold too tight |
| 4 | **Book conflict** | No | **NEW** — block UP if DOWN ask ≥ UP+6c |
| 5 | Trend / weak trend | No | Can block weak |
| 6 | Cheap trap / expensive | No | Trap at 58c opposite |
| 7 | **Regime invert** | **YES** | **DISABLED** |
| 8 | **Reversion invert** | **YES** | **DISABLED** |
| 9 | **Exhaust FLIP** | **YES** | **DISABLED → SKIP** |
| 10 | Flip guard | No (blocks weak flips) | Kept |
| 11 | Exhaust ABSTAIN | No | Sometimes overridden |
| 12 | Morning P3 / PM | No | Prob gates |

---

## Changes deployed (no-flip + trending)

| Setting | Value |
|---------|--------|
| `REGIME_INVERT_ENABLED` | off |
| `REGIME_STRONG_TREND_INVERT` | off |
| `REVERSION_RISK` INVERT branch | disabled in code |
| `EXHAUST FLIP` | skip trade |
| `TRENDING_ONLY_MARKET` | on — skip CHOPPY label |
| `BOOK_DIRECTION_ENFORCE` | on — 6c gap |
| `STRIKE_DIRECTION_MIN_DIST` | 0.00005 (tighter) |
| REVERTING + invert off | trade **with** trend only at half size |

---

## Today's trade scorecard

| Trade | Result | Main failure |
|-------|--------|--------------|
| BTC DOWN 09:42 | WIN | Aligned with move |
| BTC UP 12:16 | WIN | OK |
| ETH UP 13:15 @ 37c | LOSS | Cheap vs 60c book |
| SOL DOWN 13:45 @ 58c | LOSS | Early no-invert (fixed) |
| ETH UP 14:01 @ 45c | LOSS | Chop + low sigma |
| ETH UP 14:15 @ 46c | LOSS | **Book DOWN 54c, spot below strike** |

---

## What to watch in logs

```
[BOOK CONFLICT] ETH UP: book DOWN=54c > UP=48c — skip
[CHOPPY SKIP] ETH: trending-only mode
[EXHAUST FLIP DISABLED] … — skip
[TRADE AUDIT] … DECISION=SKIP
```

If you still see `[ORDER] ETH UP` while DOWN ask > UP ask + 6c, book gate failed — report immediately.
