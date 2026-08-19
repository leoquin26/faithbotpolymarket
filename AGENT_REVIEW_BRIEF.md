# AGENT REVIEW BRIEF — Polymarket 1H Bot Project
**Written 2026-08-18 ~02:45 UTC. Purpose: complete, honest context for an external
agent to review this project and find what we have not. Nothing here is spin;
where we made mistakes they are labeled as mistakes. Numbers are chain/ledger
verified unless marked otherwise.**

---

## 1. WHAT THIS PROJECT IS

Automated trading of **Polymarket hourly crypto Up/Down markets** (BTC/ETH/SOL/XRP;
1-hour windows; binary outcome resolves whether spot closed up or down for the
hour). Infrastructure lives on an Ireland EC2 (`~/v3-bot`), owner-operated,
bankroll is real personal money.

The trade in every engine version: **buy the favourite side as a MAKER**
(rest a GTC bid at `min(bid+1c, ask-1c)`, $0 fees) at a specific time-remaining
window, **hold to settlement**. Never taker, never sell early. One exchange
constraint that shaped everything: **minimum order = 5 shares** (~$3 at 60c).

**Current chain-verified equity: $74.72** (peak $149.09 Aug 10; launch reference
$77.84 Aug 6; total deposited across the whole project's life since June: $215.87
— earlier 15m-market era lost ~$130 before this architecture existed).

## 2. INFRASTRUCTURE (all live unless noted)

| component | file | role |
|---|---|---|
| data collector | `hourly1.py` (EC2) | every ~30s: spot, candle_open, drift_pct, top-of-book both sides, 4 coins → `hourly_research.csv` (~320k rows, ~12k/day, since Jul 18) |
| retired engine 1 | `hour_bot.py` | cycles 1-4 (55-85c band). Retired Aug 12 by floor rule. State archives cycles 1-3 + its final run |
| retired engine 2 | `micro_bot.py` | cycles 5-6 (75-85c band). Gate-halted Aug 14. Has direction-engine-less code; per-coin dicts, `fill_s` latency instrumentation, tightest-spread selection, kill switch reading `gate_state.json` |
| shadow bot | `shadow_bot.py` | LIVE. Same decisions as a real engine, $0 stakes, settles vs chain, Telegram 🕶. Currently running candidate seat: 55-65c fav, drift-ALIGNED, spread ≤3c, entry 40-50min, 4 coins |
| confirmation gate | `confirm_gate.py` (cron */6h) | measures the candidate seat ONLY on windows recorded after a cutoff (currently 2026-08-17 17:00 UTC). Hysteresis: `ARMED` (=live betting lawful) needs ROI ≥ +4% at n≥60 on TWO consecutive reads. MIRAGE at ≤0 |
| weekly sweep | `seat_scan.py` (cron Sun 15:20 UTC) | grid over 146 seats (6 time buckets × 5 bands × FAV/DOG × 5 coin groups), maker px sim, labels CANDIDATE/STRONG/MIRAGE with 70/30 split + last-14d bars |
| old-seat watch | `edge_watch.py` (cron Sun 15:00) | weekly pulse of the original 55-85c seat |
| nightly analyst | `nightly_analyst.py` (cron 05:10 UTC) | 24h/7d seat pulse + inventory → Telegram |
| balance probe | `balance_probe.py` (cron */10min) | chain USDC + open-position cost → `balance.json` (dashboard equity is chain-truth) |
| dashboard | `quantum_dash.py` :8096 + `quantum_ui/index.html` | Quantum Desk: equity (chain), cycle history, animated BRAIN panel, SHADOW TESTER tracker, log console (tails hour/micro/shadow logs) |
| data control | `data_control.py` :8097 + `quantum_ui/data.html` | inventory, calibration curve, seat ROI tables, ledger APIs |
| laws | `CYCLE_LAW.md` | pre-registered rules, amendments, refused-fixes list. Git timestamps = pre-registration proof |

Legacy (inert): `clean_bot.py` (15m era, all engines off), `late_book.jsonl`
(2.6M rows 1Hz 15m book depth), `wallet_trades.csv` (3.3M-row participant census,
6,754 wallets, cash-flow P&L).

## 3. COMPLETE CYCLE HISTORY (the core evidence)

| cycle | engine/config | result | verdict |
|---|---|---|---|
| 1 (Aug 6-8) | hour_bot, 5sh, 55-92c, 30-55min | 29W/10L (74%), **+$25.21**, EV/$ +0.212, z=+1.76 | PASSED (first ever) |
| 2 (Aug 8-10) | 8sh, band trimmed to 55-85c | 26W/14L (65%), **+$16.54**, z=+0.70 | passed money bar |
| 3 (Aug 10-11) | 10sh | 23W/17L (57.5%), **−$15.19** — opened 9-0 (peak $149), then 14W/17L | FAILED |
| 4 (Aug 11-12) | 8sh, +mid-book guard, +fill_s, tightest-spread select | 23W/17L, **−$15.43** (c3's statistical twin) | FAILED → floor rule: engine retired |
| 5 (Aug 13-14) | micro_bot, 75-85c band, 5sh, launched PRE-gate (owner urgency), multi-coin (all qualifying/window) | 30W/11L (73%!), **−$9.07**. XRP alone −$10.45; two correlated multi-coin windows = the whole drawdown | FAILED |
| 6 (Aug 14) | same seat, gate-approved (n=63 unseen +3.2%), single-coin, no XRP | n=1, **−$3.80**; rolling gate re-read flipped MIRAGE 90min post-launch → kill switch self-halt | KILLED |

Pooled cycles 1-4: 158 settles, 63% wins, +$11.13, z=+0.50.
High-band chapter (5-6): −$12.87 total.

**Post-mortem finding that explains cycles 1-4** (from `edge_watch` population sim):
the seat's edge died MARKET-WIDE: weekly population ROI **+6.2% → +2.6% → −1.1% →
+0.9%** across the four weeks. Cycles 1-2 rode a live edge; cycles 3-4 confirmed
its death at our biggest sizes. Same-window decomposition proved our execution ran
~+1.6pp ABOVE the population baseline in BOTH eras (dir agreement 78/79) — the
engine was never broken; the edge left.

## 4. VALIDATED FINDINGS (kept, with receipts)

- **Maker seat works mechanically**: 87-100% fill rates, $0 fees, fill latency
  logged (`fill_s`); adverse selection REFUTED on 121 matched fills (instant
  fills EV +0.078 — as good as slow ones).
- **Wide-book "favourites" are fake**: ask-in-band with mid below band ≈ 0 ROI
  (n=281). Guard: spread ≤3c + mid check (in code since cycle 4/5).
- **Correlated multi-coin windows are the drawdown engine**: cycle 5's entire
  loss came in 2 windows where 3 same-hour bets lost together.
- **Win rate is costume; margin is truth**: every seat's break-even = its price.
  We repeatedly ran 63-77% win rates worth ≈ nothing. Shadow's lifetime at one
  point: n=323, 77% wins, net −$0.00 exactly.
- **DIRECTION/DRIFT ALIGNMENT (Aug 17, biggest single finding)**: favourites
  aligned with intra-hour drift_pct (spot vs candle open) at 40-50min:
  **+4.6% (n=2,643, z=+3.3, tr/te/14d all green)**; OPPOSED favourites:
  **−8.0% (n=118, test −20.8%)**. No engine ever read drift before this.
  Refined seat 55-65c ALIGNED @40-50m: **n=1,071, +7.6%, z=+3.0,
  tr +6.7 / te +9.7 / 14d +9.0** — best candidate ever measured here.

## 5. REFUTED / DEAD (do not re-derive)

Hour-of-day filters (24-bucket noise farm; no split-consistent block). Weekday/
weekend (flattened). Coin drops on small n. 85-92c band (n=141 ≈ 0). 75-85c seat
(gate-measured ≈ 0 on 113 fresh; shadow n=323 net −$0.00). Slow-fill cutoffs
(n=15). Adverse-selection repricing. Taker entries at any timing (ask is
calibrated). 15m markets entirely (four-quadrant live map, all dead). dirVote/
reversal signals (July era). "Quantum math" without data (owner asked; declined).

## 6. THE LAWS AND WHY (CYCLE_LAW.md is authoritative)

Born from measured failures, owner-approved, git-timestamped:
- **Pre-registered audits**: n=40 verdicts, hard stops, EV≥+0.03 pass bar,
  z-gated rulings (|z|≥1) so noise can't kill or scale an engine.
- **Floor rule**: no next cycle without pass or pooled z≥+1 (fired Aug 12).
- **Banking law**: 50% of any passed cycle's profit withdrawn (never yet triggered).
- **GATE-FIRST**: no live dollars on any seat until its gate confirms on UNSEEN
  data (born after cycle 5's $12.87 impatience tuition).
- **Hysteresis**: gate re-entry needs ≥+4% × two consecutive 6h reads (born
  after cycle 6's 90-minute whipsaw kill).
- Amendments log every mid-cycle change honestly (there were several, each
  disclosed at the time in the doc).

## 7. THE CURRENT SITUATION (as of writing)

- All real-money engines halted/retired. Equity $74.72, unchanged 4 days.
- Shadow + gate are testing the best-ever candidate (55-65c ALIGNED @40-50m).
- **It is failing**: unseen sample n=45, **ROI −28%, win 42%** (vs 60% BE);
  shadow on the seat: 79 bets, 44W/35L, −$9.54 fake. MIRAGE ruling expected
  within hours. Would-have-been live loss ≈ −$16 in 8h; actual cost $0.

## 8. THE BIGGEST PROBLEM — THE ONE WE WANT CHALLENGED

**Three consecutive candidates measured beautifully on 30 days of history
(split-consistent, recent-window green) and collapsed within DAYS on truly
fresh data:**
1. 75-85c: discovery +6-10% → fresh gate ≈ 0% (n=113)
2. 55-65c plain: sweep +5.7% (n=1,148, te +7.7, 14d +6.0) → *(superseded by #3 before its own gate finished)*
3. 55-65c ALIGNED: audit +7.6% (n=1,071, 14d +9.0) → fresh −28% (n=45) overnight

Our working hypothesis: **edge half-life in these markets (~days) is shorter
than honest confirmation latency (n≥60 ≈ 1.5-2 days)**, so the gate can
structurally only confirm edges that are already dying, and the sweep's
survivors are increasingly pure selection artifacts (146 cells × weekly
re-runs = heavy garden-of-forking-paths even with split+recency bars).

**Questions for the reviewer (ranked):**
1. **Is the gate itself the trap?** If confirmation latency ≥ edge half-life,
   the system can never lawfully bet a living edge. Alternatives we have NOT
   tried: lower-n/higher-bar gates (e.g. n=25 at ≥+8%?), sequential tests
   (SPRT), bandit-style exploration with tiny bounded budgets per candidate
   (e.g. $5 "exploration tax" concurrent with shadow), regime filters that
   qualify DAYS rather than seats. Is there a statistically honest fast gate?
2. **Is there a data-quality bias inflating ALL historical sims?** Collector
   snapshots are ~30s apart, top-of-book only. Our sims assume maker fill at
   `min(bid+1c, ask−1c)` whenever a qualifying quote existed. Live cycles
   filled 87-100%, and live execution BEAT the population sim (+1.6pp), which
   argues against fill-bias — but the reviewer should re-derive this.
   Raw data schema: `ts,hour_start,coin,spot,candle_open,drift_pct,up_ask,
   up_bid,down_ask,down_bid,t_left,winner` (winner rows have t_left=-1).
3. **Why did the ALIGNED seat invert overnight?** −28% on 45 samples is not
   "no edge" — it's a strongly NEGATIVE read of a slice that measured +9% in
   its freshest prior 14 days. Possibilities: (a) tiny-n variance (45 samples,
   ~60% of the CI still overlaps 0), (b) regime break (BTC chop night), (c)
   the alignment feature itself is regime-dependent (works in trend regimes,
   inverts in chop). Slice the fresh window by realized volatility / hour /
   coin and check.
4. **Is the whole favourite-side approach capped?** Every seat we mined is a
   favourite seat with break-even = price and margins 2-8%. The census
   (wallet_trades.csv, 3.3M rows) showed the profitable population was
   favourite-buyers — but that was June/July. Re-run the census question on
   recent weeks: WHO is making money in these hourly markets NOW, and how?
5. **Layer-3 design**: given fast regime rotation, is a daily-retrained
   walk-forward model (GBM/logistic over drift, book imbalance, vol, hour,
   coin, streak features) viable on ~350 windows/week of labels? Or is the
   label count too small per regime for anything beyond the linear features
   we already tested? Propose an architecture + honest validation protocol.
6. **XRP anomaly**: XRP cells keep printing +14-20% in sweeps (small n,
   MIRAGE-graded); XRP live was our worst coin (−$10.45 in cycle 5). Its
   books are thinner/wider. Is there anything real there or is it pure spread
   noise in the sim?
7. **Are we measuring the right seat at all?** Everything bets WITH the
   favourite. The sweep's DOG cells were all negative — but the sweep only
   tested naked longshots. Conditional longshots (e.g. DOG when drift OPPOSES
   the favourite — the −8% slice inverted) were never tested. Check whether
   the opposed-favourite poison is a *tradeable* dog seat after spread.

## 9. WHY WE ARE "STUCK WAITING" (the honest defense, for context)

Every acceleration attempt is in the ledger: cycle 5 pre-gate launch −$9.07;
cycle 6 whipsaw −$3.80; July's un-gated era −$40. Every wait saved measured
money (the current one: ~$16 in its first 8 hours). The laws are not caution
for its own sake — they are the priced result of 6 weeks of A/B testing
urgency against patience with real money. **But**: the reviewer should treat
"the laws make betting impossible in this regime" as a live hypothesis (see
question 1). If true, the honest conclusions are either a faster-but-still-
honest gate design, or the conclusion that this market's simple seats are
unexploitable at retail latency — and the capital belongs in Layer-3 R&D or
nowhere.

## 10. ASSETS AND HOW TO VERIFY EVERYTHING

- EC2: `ubuntu@34.255.2.158`, code+data in `~/v3-bot` (key: local
  `polymarket-key.pem`, not in git).
- Re-run any analysis: `python3 seat_scan.py`, `python3 confirm_gate.py`,
  `python3 edge_watch.py` on the box; audit scripts in repo
  (`seat_scan.py`, `edge_watch.py`, `confirm_gate.py`, `shadow_bot.py`).
- Ledgers: `hour_bot_state.json` (cycles 1-4 archived inside),
  `micro_bot_state.json` (cycle 5 archived inside, cycle 6 live-final),
  `shadow_state.json` (every shadow entry with book at entry + outcome).
- Dashboards: Quantum Desk :8096, Data Control :8097 (cloudflared quick
  tunnels; URLs rotate on restart — grep `cf_data.log`).
- Git: branch `yirok-cleanbot-grok`, two remotes (origin + faithbot), every
  decision committed with timestamps; `CYCLE_LAW.md` is the law of record.

**The ask: find the thing we can't see. The instrument layer is good, the
discipline is real, the data is rich — and three seats died in ten days.
Either the market is telling us something we've already half-heard (edges
rotate faster than retail confirmation), or there is a flaw in how we
measure/select/confirm that a fresh pair of eyes can catch. Both answers
are valuable. Be adversarial.**
