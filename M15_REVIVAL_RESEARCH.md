# 15m REVIVAL RESEARCH — deep dive on our tape + how the profitable bots operate
**2026-09-01 ~21:40 UTC. Owner asked: find a way to revive the 15m bot.**
Verdict up front: **the 15m bot can be revived only as a MARKET MAKER, never as a
directional bot — and only as a $0 paper lab first.** Our own 5M-snapshot tape
proves naive two-sided quoting loses; the public record shows the survivors are
maker-volume machines earning 1-4% on rebates + rewards + skewed quotes. The
one thing that is genuinely new since our 15m era died: **Polymarket now pays
$7,500/day in liquidity rewards on the BTC-15m market alone** (live config,
start 2026-09-01). That is the only reason to reopen this door.

---

## 1. What killed the old 15m bot (re-measured today on our tape, 43,941 rows)

| seat (clean_bot_research.csv, taker) | n | WR | EV/$ after fee |
|---|---|---|---|
| Φ fair-value taker, edge ≥ 8c | 16,411 | 31% | **−0.080** |
| favourite ask ≥ 50c, edge ≥ 8c | 3,042 | 67.5% | **−0.018** |
| longshots < 50c | 13,536 | 23% | **−0.118** |
| last-60s scalp 90-99c (late_book, 4,753 rests) | — | 94.8% | **−0.009** |
| scalp > 97c | 1,734 | 97.3% | **−0.014** |

Every taker seat is −EV because the **crypto taker fee is 0.07·p·(1−p) per share
= 1.75c at 50c (3.5% of notional, ~7% round trip)**, taker-only, makers pay $0.
A 67% win rate buying at 69c+fee needs ~71%. A 95% scalp at 97c loses on the 5%.
**Faster code cannot fix a negative expectation** (Grok's and my conclusion agree).

## 2. Polymarket's maker economics in 2026 (primary sources, verified live)

- **Taker fees** on 15m since Jan 5 2026, 5m since launch, all crypto markets
  created after Mar 6 2026. Curve `C·0.07·p·(1−p)`, makers $0.
- **Maker Rebates (fill-based)**: 20% of each crypto market's taker fees paid
  daily to makers pro-rata by fee-equivalent of THEIR fills. ≈ **0.35c per share
  filled at 50c** (0.22c at 80c). Min $1/day to pay out.
- **Liquidity Rewards (quote-based, paid even without fills)**: orders ≥
  `rewards_min_size` within `rewards_max_spread` of the size-adjusted midpoint,
  sampled every minute, quadratic score `S=((v−s)/v)²`, two-sided boosted
  (min of both sides), single-sided /3. Orders must rest ≥ 3.5s.
  **Live config today (CLOB /rewards/markets/multi, 2026-09-01):**

  | market | pool/day | per window | min size | max spread |
  |---|---|---|---|---|
  | BTC 5m | $10,000 | ~$35 | 50 sh | 1.5c |
  | **BTC 15m** | **$7,500** | **~$78** | 50 sh | 1.5c |
  | ETH/SOL/XRP/HYPE 15m | $833 | ~$8.7 | 50 sh | 1.5c |
  | BTC 4h | $1,667 | ~$278 | 50 sh | 1.5c |
  | **1h markets (our whole project)** | **none** | — | — | — |

  Allocation `start_date 2026-09-01, end open` — a NEW September program, not
  August's $1M. Verify daily; Polymarket changes it at will.
- **Maker protection**: taker orders are held 50ms (was 250ms) and revalidated
  before matching — a cancel window against snipers.
- **Settlement**: since Aug 7 2026 crypto Up/Down resolve on **Chainlink TWAP
  (60s lookback for 15m)**, streamable free via RTDS. The last-second snapshot
  manipulation ($8.2M extracted by 821 accounts) is dead. The TWAP lags spot in
  the final minute — naive spot-vs-strike probabilities are wrong there.

## 3. How the profitable bots actually operate (public record)

- Latency arbitrage as TAKER is dead (one wallet did $313→$414k pre-fee; the
  0.07 curve was designed to exceed that edge). The Binance-lead signal survives
  only as a **maker quote-skew input**.
- Profit is hyper-concentrated: <1% of wallets take ~half the profits; top
  wallets are makers at 1.4-15% ROI on huge volume. Our own 4M-fill census says
  the same: favourites +$603k, longshots −$495k, winners are maker/hybrid volume.
- Reference code: `warproxxx/poly-maker` (~1.5k★): two-sided quotes around a
  reservation price = fair − inventory skew, half-spread = base + c·σ + c·toxicity,
  quotes kept inside the rewards band, notional caps, daily-loss kill, paper mode.
  README: "can lose money". `terrytrl100/polymarket-automated-mm` (reward
  optimiser): author's honest result **net ≈ $0** — rewards wiped by adverse moves.
- Documented failure modes: adverse selection ("one event erases weeks of
  spread"), taker anything, ops bugs (zombie positions, silent API failures),
  latency for cancels (serious operators sit in eu-west-2, 5-12ms — we are in
  eu-west-1, ~70-100ms: adequate for rewards farming, not for sniping).

## 4. THE NEW MEASUREMENT: two-sided maker replay on our tape (late_book.jsonl)

5.05M snapshots, 1Hz top-2 both outcomes, BTC/ETH/SOL, Jul 23 → Sep 1, 11,487
window-coins. Strategy: join best bid on BOTH outcomes, hold to settlement.
Fill proxy = a later snapshot shows that outcome's best ask ≤ our bid (price
traded through our level). Rebate credited at 0.2·fee(px) per filled share.

**Quoting at 5-3 min left (the only band our tape covers — capture is last ~5.5 min):**

| | windows | double-fill | single | pair cost | EV/$ gross | rebate/$ | **net EV/$** |
|---|---|---|---|---|---|---|---|
| ALL | 10,290 | 66% | 33% | 0.982 | −0.079 | +0.0045 | **−0.075** |
| BTC | 3,496 | 69% | 29% | 0.988 | −0.070 | +0.0044 | **−0.065** |
| ETH | 3,375 | 68% | 30% | 0.985 | −0.075 | +0.0044 | **−0.071** |
| SOL | 3,419 | 60% | 38% | 0.972 | −0.095 | +0.0047 | **−0.090** |

- Double-fills earn the spread: **+0.76c/share** (pair bought at 98.2c → $1).
- **Single fills are pure poison: −20.5c/share on average, and the anatomy is
  brutal — when only one leg fills it loses ~97-99% of the time** (favourite
  alone EV/$ −0.97, n=448; longshot alone −0.995, n=2,915). Reason: in the last
  minutes a bid gets traded THROUGH only when that outcome is dying. The maker
  is the exit liquidity for the losing side, by construction.
- The 20% rebate adds +0.45% — it does not begin to cover a −7.9% gross.
- **Data gap**: our tape cannot measure quoting earlier than ~5.5 min before
  close (n=0 at 10-8 and 13-10 min). Toxicity is almost certainly lower early
  in the window, but WE HAVE NO NUMBER. That is the first thing the lab must
  collect.

## 5. Reward-farm yield estimate (upper bound) from our book depth

Quoting 50 shares both sides at best bid within 1.5c: our per-minute Q-share vs
VISIBLE competition (top-2 levels only — real competition is deeper, so this is
an UPPER bound) came out 15-25% → **$11-19 per BTC-15m window if held the whole
window without being filled**. Reality check: (a) pros stack size beyond top-2,
(b) every fill converts a scoring quote into toxic inventory (section 4), (c)
`tezlee`'s live reward-optimised bot netted ≈ $0. Treat true yield as unknown,
probably a small fraction of this, and **measurable only live-paper**.

## 6. The honest economics for a ~$69 account

- Min qualifying quote = 50 shares/side ≈ **$25/side at 50c → one two-sided
  quote in ONE market uses ~72% of the bankroll.** One toxic single fill = 50
  shares held to settlement = ±$25 swing. Two bad windows = stop-out territory.
- Census-consistent maker ROI 1-4% on volume: $69 × ~20 turns/day ≈ $1,400
  volume → **$0.7-2.8/day** at the best-case ROI, brushing the $1/day rebate
  payout floor. This is an **audition of a mechanism, not income**. If the
  mechanism proves non-toxic it scales linearly with capital; if it is toxic,
  no rebate percentage fixes it.

## 7. What "reviving the 15m bot" honestly means — the $0 lab

Not `clean_bot` flags. A new process, paper first, per GATE-FIRST law:

**`mm_shadow.py` — paper market-maker, BTC-15m only, $0:**
1. From window open to T−120s, quote 50 sh both sides at best bid, only while
   inside 1.5c of the size-adjusted mid (the rewards band).
2. **Reservation price + skew**: fair value from `research_brain/digital.py`
   (Φ on Chainlink TWAP + realized σ — already built for late_shadow); skew both
   quotes toward fair; **pull the quote on the side fair is moving against**
   (our drift-alignment finding, reused as a maker signal, not a bet).
3. **No inventory into the last 2 minutes**: cancel everything at T−120s; any
   filled leg is held to settlement and logged as the toxicity cost.
4. Fills: real trade prints from the CLOB WebSocket (not the price-through proxy).
5. Log per window: minutes qualified, estimated Q-share (live depth), est.
   reward $, est. rebate $, fills with **mark-outs at +30s/+60s and at TWAP
   settlement**, paper PnL.
6. **Gate (pre-registered now)**: ≥ 300 windows AND net EV/$ (spread + rebate +
   est. rewards − toxicity) ≥ +0.03 on unseen data, two consecutive daily reads.
   Then, and only then, a $50 live audition (one market, min size, stop −$12).

Timeline: build ~1 session, run 3-4 days for 300 windows. Costs $0.

## 8. What NOT to do (measured, closed)
Taker anything on 15m. Φ-taker. 99c scalps. late@195s. Reviving any
`CLEAN_*_LIVE` flag. "Faster Python." Quoting the last 5 minutes (section 4).
Running this while the 1H clock (2026-09-02 06:00 UTC) is unresolved — one live
hypothesis at a time (AGENT_STATUS §0).

*Sources: docs.polymarket.com (trading/fees, programs/maker-rebates,
programs/liquidity-rewards, market-data/chainlink-twap, changelog), CLOB
/rewards/markets/multi live 2026-09-01, The Block Jan 6 2026, CoinDesk Aug 7 &
Apr 29 2026, tezlee.substack Dec 2025, github warproxxx/poly-maker, our
late_book.jsonl / clean_bot_research.csv / wallet_trades.csv replays.*
