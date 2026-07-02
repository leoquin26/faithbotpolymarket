# CleanBot Changelog

Every change to CleanBot gets: (1) a `VERSION` bump in `clean_bot.py`, (2) an
entry here, (3) a git tag `cleanbot-vX.Y.Z`, (4) a push to GitHub. The running
version is logged on startup and shown in Telegram + the dashboard, so you always
know exactly what's live. Roll back with `git checkout cleanbot-vX.Y.Z`.

Versioning: **MAJOR.MINOR.PATCH** — MAJOR = strategy change, MINOR = new
feature/knob, PATCH = fix/tuning.

---

## v1.30.1 — 2026-07-02 — owner-caught confound: relax entry cutoff to age ≤240s (keep 180-240s)
**Tag:** `cleanbot-v1.30.1` · **Status:** ✅ live · correction to v1.30.0

Owner challenged v1.30.0 ("doesn't waiting longer ruin the trades that built our win streaks?").
Re-ran the timing audit on POST-TREND-GUARD fills only — and the challenge was right in part: the
full-history "late loses" sample was CONTAMINATED (the v1.21 era FORCED all entries into age
150-300s and included the counter-trend disaster, whose losses are already fixed by the guard but
land in the late buckets by construction). Clean-era data: age 0-180s = 75% vs 63% BE (n=28);
age 180-240s = 75% (n=4, ambiguous — condemned only by polluted data); age 240s+ = 50% (n=2) AND
bad in every era historically. FIX: min_t 720→660 (entries age 60-240s) — restore the ambiguous
180-240s zone, keep only the universally-bad 240s+ tail cut. Re-audit when clean-era n≥30 in the
180-240s bucket; tighten again only if IT (not history) says so.

## v1.30.0 — 2026-07-02 — no stale entries: cut the age-180s+ tail (live data: below break-even)
**Tag:** `cleanbot-v1.30.0` · **Status:** ✅ live · from the 70c ETH UP loss post-mortem (`_timing_audit.py`)

Post-mortem of the 13:49 ETH UP @70c loss (entered at window age 280s, top of band, settled the
other way) tested TWO hypotheses on all 297 joined live fills (ENTER→result with true entry T):
**H1 CONFIRMED — entry age decays monotonically:** age 0-90s = 66% WR (+2 vs break-even),
90-180s = 68% (+3), **180-240s = 59% (−6)**, **240-320s = 56% (−10)**. Worst slice late+cheap:
51% vs 62% BE (z=−1.8). Buying a 3+ minute-old move = buying exhaustion before mean-reversion.
**H2 REJECTED — "whipsaw chase" (bet vs the window's first drift) shows NO live deficit**
(66% vs 63% BE, n=32) — no first-direction lock will be built. Price-alone buckets: noise.
FIX: `CLEAN_MIN_T` 600→720 — entries now only at window age 60-180s (≥12min to settlement).
Removes ~30% of trades that were measurably LOSING, so accuracy AND total EV rise together.
Note: supersedes the v1.21 "600-750s-left = 81%" proxy analysis (scan-time t_left, n=48,
IS-only) — this is true entry-time data over 297 real fills; live beats proxy.

## Dashboard v2.0 — 2026-07-02 — real-time monitoring console (cleanbot_dash.py rewrite)
**Tag:** `dash-v2.0.0` · **Status:** ✅ live on :8095 · bot code untouched

Complete rewrite so the owner can self-monitor everything. Backend: incremental log tailing
(parses only appended bytes — cheap 3-4s polling on a multi-MB log), chain-truth equity from
RECONCILED/SYNC lines, active-stop detection (profit-lock/daily/breaker), heartbeat staleness →
LIVE/STALE/DOWN status, live open-position status (strike cache + live Binance price → current
drift + WINNING/LOSING + countdown), shadow-coin verifier gate computed from the research CSV
(n/80, WR vs break-even, z, EV, PASS/FAIL), guard-activity counts, UP/DOWN + per-coin splits,
7-day history, incremental /api/logs?since=offset. Frontend: dark console UI, status pill +
alert banners (bot down / stop active), equity chart with Today/3D/All ranges, flash-on-new-
trade, colorized/filterable/searchable live log (Trades/Guards/System/Errors + search + pause),
last-30 outcome dots, last-signal card, live countdowns, tab title shows wallet.

## v1.29.1 — 2026-07-02 — scale the profit-lock trail to the grown book ($6→$10)
**Tag:** `cleanbot-v1.29.1` · **Status:** ✅ live · risk-parameter rescale (owner asked to release+run)

The lock worked as designed this morning (locked +$3.54 real vs day start after a +$12 run gave
back $8.83), but $6 was sized for the ~$25 book. At ~$45-51, two losses resolving in a cluster
(-$3.50 -$3.00) ≈ $6.5 — a NORMAL variance burst inside a verified-+EV streak — so the trail kept
halting the exact frequency the audit unlocked. Trail 6→10 (~20% of the $51 peak): still locks a
genuine reversal-of-day, stops firing on routine 2-loss clusters. Restart releases today's lock
(hwm re-anchors to the live bankroll). Daily loss stop stays $8.

## v1.29.0 — 2026-07-01 — FULL AUDIT: retire the ER chop-bar (verifier-convicted) + fix XRP feed
**Tag:** `cleanbot-v1.29.0` · **Status:** ✅ live · complete-system audit (`_full_audit.py`, 1542 windows)

Owner asked for a complete accuracy/growth audit. Every logged signal re-tested through the OOS
discipline. Findings:
(1) **ER chop-bar RETIRED** (`CLEAN_ER_FILTER=off`): in-band drift≥5 signals in CHOP win **72.7%
    OOS (n=88, z=1.67, EV +0.129)** — passes the gate. The chop bar (16bps) was the #2 volume
    blocker (~95 windows/day) and was blocking +EV trades. The real chop disasters (counter-trend
    dip-shorting, whipsaw re-entries) are guarded by the trend guard + rev-cooldown + $8 stop,
    which all stay.
(2) **Order-flow polarity is INVERTED** (absorption): flow AGREES → 62.7% WR, EV −0.004 (nothing);
    flow OPPOSES → **77.1% WR, EV +0.243** (n=35, below gate). Price rising against net selling =
    absorption = strength. Explains precisely why the v1.26 veto filter lost: it vetoed the 77%
    winners. Keep shadow-logging; candidate signal at n≥80.
(3) **BTC nearly tradeable**: shadow 80% WR OOS, z=1.66, EV +0.210 — but n=30 < 80. Keep shadowing;
    enable when the gate passes. **XRP feed was dead** (BOT_COIN_WHITELIST lacked XRP → no price
    feed → zero shadow rows): whitelist now BTC,ETH,SOL,XRP (feed only; trading stays ETH/SOL).
(4) **UP/DOWN asymmetry**: UP bets 74.0% OOS (z=2.06, PASSES alone); DOWN 69.8% (z=1.16, positive
    but unproven). Both stay (DOWN is +EV; trend guard covers its failure mode); monitor.
(5) **Hour-of-day filters REJECTED**: EU block flipped 75.9% IS → 64.0% OOS (regime-unstable).
(6) **Sizing is floor-bound**: 5-share min ≈ 12.7% of the $25 book ≈ full Kelly (f*=20.7%, half
    =10.4%) — the exchange minimum already bets more than half-Kelly. No sizing lever until ~$45+.
    Log-growth at floor size if the OOS edge holds: ~+1.0%/trade.

## v1.28.1 — 2026-07-01 — remove the entry-timing delay that was choking the widened config
**Tag:** `cleanbot-v1.28.1` · **Status:** ✅ live · frequency fix

Right after v1.28.0 a strong SOL UP (+16.5bps, in-band) skipped as `exposure_or_timing` — the
v1.21 entry_min_age=150 delay only lets the bot enter at window-age 150-300s. That delay was
never in the verified config (which is measured on EARLY entries, t_left>750) and is itself
unvalidated (verifier: OOS n=5, EV<0). It directly suppresses the frequency v1.28.0 was widened
for. FIX: `CLEAN_ENTRY_MIN_AGE` 150→60 (= strike-settle warmup) — enter as soon as the strike
settles, matching the verified edge. Keeps `min_t` (needs ≥10m left) and all guards.

## v1.28.0 — 2026-07-01 — MORE FREQUENCY (verifier-approved): widen band 55-74c, drift bar 5bps
**Tag:** `cleanbot-v1.28.0` · **Status:** ✅ live · the compounding accelerator, OOS-verified

Owner: compounding too slow, need more frequent trades. Ran candidate configs through `_verify.py`
(OOS gate). Result flips the earlier "narrow=better" intuition: the narrow 58-66c/d>=7 band has a
fatter per-trade edge (EV +0.106) but only n=63 OOS → z=1.03, FAILS significance (can't prove it).
The WIDE **55-74c / drift>=5** config has thinner per-trade edge (EV +0.082) but n=208 OOS → z=1.68
→ the ONLY config that PASSES the OOS gate. Compounding math: total EV = edge × frequency →
narrow 0.106×63=6.7 vs wide 0.082×208=17.1 ≈ **2.5× more total profit**. More frequency also gives
the statistical power to trust the edge. FIX: min_ask 0.58→0.55, max_ask 0.66→0.74, drift_bps 7→5
(+ .env); momentum filter OFF (verifier: no OOS edge, only blocked volume). KEEPS all protective
guards (counter-trend, chop/ER, daily stop $8, divergence). ~3× trade frequency at verified +EV.

## v1.27.0 — 2026-07-01 — RECOVERY: counter-trend guard + kill flow filter + tighten stop
**Tag:** `cleanbot-v1.27.0` · **Status:** ✅ live · post-mortem of a −$12.6 day

Root cause (quant post-mortem, trades since v1.26): DOWN bets 5W/6L (45%), UP bets 2W/0L —
ALL 6 losses were DOWN bets shorting dips into a sustained overnight UP-trend. At night the bot
had NO macro-trend guard, so it kept fighting the trend. The v1.26 flow filter made it worse:
it vetoed 5 UP bets (the winning side) while DOWN losers passed. Fixes:
(1) **COUNTER-TREND GUARD** (`CLEAN_TREND_GUARD`, all hours): skip any bet that OPPOSES a strong
    ~30m macro move (|net| ≥ CLEAN_TREND_GUARD_MIN 0.25%). Fires only vs a strong trend, so it
    doesn't over-block chop. Would have blocked all 6 DOWN losses.
(2) **KILL the order-flow filter** (`CLEAN_FLOW_FILTER=off`) — net-harmful, unvalidated; flow60
    keeps shadow-logging for later analysis.
(3) **Tighten daily stop** floor $12→$8 — $12 was 60% of the (now ~$20) bankroll; $8 caps a bad
    day nearer 40% while the book is small.
Daily stop worked (halted at −$12.61). This attacks the actual failure — fighting the trend — not
the win rate.

## v1.26.0 — 2026-07-01 — order-flow filter LIVE (owner-requested test; revert = one toggle)
**Tag:** `cleanbot-v1.26.0` · **Status:** ✅ live · EXPERIMENT (unvalidated — watch & revert)

Owner asked to run the new order-flow signal live now ("if it doesn't work, back to shadow
logs"). Implemented as the SAFEST possible use: a light veto — skip a bet only when 60s
aggressive volume STRONGLY opposes it (`[FLOW SKIP]`): buying ≥ CLEAN_FLOW_MIN (0.4) into a
DOWN bet, or selling into an UP bet — i.e. volume clearly fighting the price move. It never
forces new bets, only vetoes clear conflicts. HONEST RISK: zero validation yet, so the signal's
polarity is a hypothesis (flow agrees with direction, consistent with the momentum thesis); if
live WR/P&L worsens, revert instantly with CLEAN_FLOW_FILTER=off (shadow-logging of flow60
continues regardless). Knobs: CLEAN_FLOW_FILTER (on), CLEAN_FLOW_MIN (0.4). Watch the next ~15-20
trades: if flow-skips would-have-won a lot or WR drops, turn it off.

## v1.25.0 — 2026-07-01 — capture ORDER FLOW (buy/sell volume pressure) — a real-time leading signal
**Tag:** `cleanbot-v1.25.0` · **Status:** ✅ live · new signal, shadow-logged first (zero latency)

The bot's Binance aggTrade WS already streams every trade with size (`q`) + side (`m`) sub-100ms,
but the handler discarded both, keeping only price. Now it captures them (2 extra field reads, NO
added latency) into `_flow_history`, and `binance_ws.get_order_flow(coin, 60)` returns real-time
buy/sell PRESSURE = (buy_vol − sell_vol)/total ∈ [−1,+1]. Volume typically LEADS price, so this is
a faster, genuinely NEW directional signal (everything before was price-based; momentum had ~no
edge). Logged as `flow60` on every research row (incl. BTC/XRP shadow coins). NOT yet used for
betting — validate first (momentum looked obvious and failed). After ~a week: measure WR vs flow
direction; if aggressive-buy windows win UP more (and sell→DOWN), add it as a signal. Reaction
cadence is still the 5s loop (fine for 15m windows; can go event-driven later if flow proves out).

## v1.24.0 — 2026-06-30 — shadow-log BTC + XRP (data only, no bets) to test market expansion
**Tag:** `cleanbot-v1.24.0` · **Status:** ✅ live · zero-risk data gathering

To test doubling volume by adding MARKETS (not loosening filters), we need BTC/XRP edge data
first. New `CLEAN_RESEARCH_COINS` (BTC,XRP) feeds those coins into the existing isolated
`_research_scan` (logs window features + gamma outcome to clean_bot_research.csv) — they are
NEVER passed to scan()/trading, so NO real bets are placed. The trading loop stays ETH/SOL only.
After ~a week we measure BTC/XRP win rate vs break-even; if they clear it, enable them as
tradeable for real volume expansion. Knob: CLEAN_RESEARCH_COINS (empty = ETH/SOL only).

## v1.23.0 — 2026-06-30 — MORE volume on ETH/SOL: drift bar 10→7bps (the band already filters quality)
**Tag:** `cleanbot-v1.23.0` · **Status:** ✅ live · data-driven volume expansion (owner wants more bets)

Within the new 58-66c band, low drifts are STILL profitable: 6-8bps=70% (n=100), 8-10bps=75%
(n=72) vs break-even ~62%. The bot's 10bps bar was SKIPPING the 6-10bps band — good, above-break-
even volume — because the price band (58-66c) already does the quality filtering; demanding a big
drift on top was redundant. FIX: `CLEAN_DRIFT_BPS` 10→7 (.env; code default was already 7).
Roughly DOUBLES eligible ETH/SOL windows at 70-75% WR, all above break-even. Chop is still
protected (ER raises the bar to 16 in chop) and the adaptive bar still raises it when WR dips.
This is "bet more on the coins we have data for," done with evidence — not loosening into the
weak/chop losers. Pairs with v1.22.0 (cheaper band); watch the combined live effect.

## v1.22.0 — 2026-06-30 — THE diagnosis: WR ≈ break-even. Cheaper band (58-66c) to get real edge
**Tag:** `cleanbot-v1.22.0` · **Status:** ✅ live · the root cause of "stuck at $40-50 for a week"

Live week: 154 trades, ~65-69% WR, bankroll FLAT ($45.9→$45.3). Found why: avg win **+$1.72**,
avg loss **−$3.32** (loss ≈ 2× win), so break-even WR = 3.32/(1.72+3.32) = **~66%**. The bot's
~65% WR is right AT break-even → it treads water. A positive WR isn't positive ENOUGH for these
payouts. The lever is to lower break-even by buying cheaper: data bands 58-62c=67%/+7edge,
62-66c=72%/+8edge, but 66-70c=only +4edge (the marginal drag). FIX: max_ask 0.70→**0.66** (+ .env).
Avg entry ~66c→~62c, avg win ~$1.72→~$1.95, break-even ~66%→~62% — turning the same ~69% WR from
+2% over break-even into +7%, ~doubling per-trade EV (~+$0.17→+$0.30) with no added risk. Trades
fewer (drops marginal 66-70c) but each is meaningfully +EV. HONEST: still a thin edge on a small
bankroll — grows slowly, variance rules week-to-week; no tweak makes $45 grow fast without ruin risk.

## v1.21.2 — 2026-06-30 — log ER (regime) per window — groundwork for regime-conditional sizing
**Tag:** `cleanbot-v1.21.2` · **Status:** ✅ live · data-gathering only (no behavior change)

To test "bet smaller in chop, full-size in trends" with evidence (not another guess like active
exit, which lost money selling winners early), we need the regime recorded on every trade.
Added `er` (efficiency ratio: trend vs chop) to `clean_bot_research.csv`. NO behavior change —
it only records a number already computed. After ~a week we can measure WR by ER and decide if
chop trades genuinely lose more, then size down in chop (smaller reversal losses) WITHOUT
over-blocking or exiting. Existing CSV migrated (old rows get empty er; history preserved).

## v1.21.1 — 2026-06-30 — FIX profit-lock fired on a phantom (ledger-inflated) peak
**Tag:** `cleanbot-v1.21.1` · **Status:** ✅ live · caught in the daily review

The profit-lock's high-water mark (`hwm`) was tracking `self.bankroll` in the main loop, but
that's the win/loss LEDGER which creeps ~$0.75/win above chain (the sync only corrects gaps
>$0.75, so small drift accumulates). 2026-06-30: hwm hit $53.38 while the real reconciled peak
was $50.38. After one normal loss (real giveback $3.20), the inflated hwm made it look like
$6.20 — over the $6 trail — so the lock fired and stopped the bot for the day having kept only
+$0.71, when it should still be trading. FIX: `hwm` now updates from CHAIN TRUTH inside
`_sync_bankroll` (from `real` = on-chain USDC + open cost), and the main-loop hwm update is
removed. So the profit-lock peak can't be inflated by ledger drift; it fires only on a real
giveback. With this, today's $3.20 giveback stays under the $6 trail → bot keeps trading; big
real run-ups still lock real profit. (Restart resets hwm to the live $47.18, clearing the bad lock.)

## v1.21.0 — 2026-06-29 — entry timing: wait for the move to establish (test, ~66%→81%)
**Tag:** `cleanbot-v1.21.0` · **Status:** ✅ live · TEST (validate over a few days)

Data review (`_new_angles.py`): entries with 750-900s left (first ~2 min of the window) win
66% (n=1023), but entries with 600-750s left win 81% (n=48). The first-2-min twitch fakes out;
the move is more reliable once it establishes. FIX: new `CLEAN_ENTRY_MIN_AGE` (150s) — the entry
path now waits until ≥150s into the window before betting (was just the 60s strike-settle
warmup). With min_t=600 this lands entries in the proven 600-750s-left zone. ONLY timing changes
— same coins, drift bar, price band, sizing, and all safeguards. Side effect: early signals that
fade in the wait won't re-qualify (those were the noise/losers) so volume dips slightly toward
higher quality; a few fast strong trends may run past the 70c band during the wait and be
skipped. TEST: the 81% is n=48, so watch live before trusting. Knob: CLEAN_ENTRY_MIN_AGE (60 = old
behavior).

## v1.20.0 — 2026-06-29 — block divergent correlated bets (the 55% coinflip)
**Tag:** `cleanbot-v1.20.0` · **Status:** ✅ live · data-driven (full review, 1067 windows)

Full data review (`_full_review.py`) found the clearest robust edge yet: when ETH & SOL are bet
in OPPOSITE directions in the same window, they win only **55% (n=168)** vs **69% (n=884)** when
aligned. ETH/SOL move ~0.85 together, so betting them to decorrelate is a coinflip — and a
coinflip loses at favorite prices (break-even WR = the entry price, 58-74%). This is exactly the
case the owner flagged: 2026-06-29 SOL DOWN (won) + ETH UP (lost) — both actually closed DOWN.
FIX: `_corr_opposite` + `CLEAN_CORR_OPPOSITE_BLOCK` (on) — skip a coin bet OPPOSITE a correlated
leg already held this window (`[CORR DIVERGE]`). Removes a proven money-loser (~16% of legs that
were losing anyway), so it lifts WR without cutting good volume. Also documented from the review:
momentum roc300 shows ~no edge in the full data (agree 67% vs oppose 66%) and cross-coin agree is
weak (65%→68%); the strong-looking 81% full stack is only n=27 (overfit) — the divergent-pair
block is the one large, reliable signal.

## v1.19.0 — 2026-06-29 — FIX THE LEAK: phantom fills on "canceled" orders (where profit vanished)
**Tag:** `cleanbot-v1.19.0` · **Status:** ✅ live · root cause of "wins keep vanishing"

THE answer to "good win rate but no growth / 2 losses wipe 3 wins". Diagnosed from the weekend:
ledger said +$3.65 but the wallet went −$3.45 (a ~$7 gap). Proof — Sunday:
```
06:33:32 [ENTER]  SOL DOWN maker 69c x5 ($3.45)
06:36:31 [CANCEL] unfilled SOL DOWN @ 69c     ← bot thinks: never filled
06:42:24 [SYNC]   $52.89 → $49.44  (−$3.45)   ← wallet drops EXACTLY 5×$0.69
```
The order the bot "canceled as unfilled" had actually FILLED on-chain (a fill-vs-cancel race),
then settled as a loss — never logged as a fill, position, or loss. These phantom fills are
adversely selected (a resting bid fills when price moves against you), so they're almost always
losers, silently eating the real profit underneath a genuine ~80% win rate. ROOT CAUSE: the
cancel path called `client.cancel()`, swallowed any error, and assumed "unfilled" without
re-checking. FIX: after canceling, re-verify `size_matched`; if it filled, TRACK it as a
position (`[FILLED-RACE]`) so it's managed, resolved, and counted — instead of leaking.
Also rejected conviction-weighted sizing this session: the "high-conviction" tier wins 80% vs
79% marginal — no separation, so sizing up = pure leverage (more growth AND more drawdown), not
a fix. The edge is real (flat 8% backtests ~4x); the job is making LIVE match it by plugging
this leak. Analysis: `_conviction_sizing.py`.

## v1.18.0 — 2026-06-29 — FIX profit-lock: it was locking LOSSES and blocking fresh days
**Tag:** `cleanbot-v1.18.0` · **Status:** ✅ live · owner caught it Monday morning

Monday AM the bot refused to trade: `[STOP] PROFIT-LOCK 🔒 (peak $56.29 -> $40.79, kept the
gains)` — but $40.79 was BELOW Friday's $44.24 start, so it kept nothing; it locked a loss and
blocked the new day. Three bugs: (1) `hwm` never reset daily (comment claimed it did) — the
weekend's $56.29 peak carried into Monday and tripped the stop immediately; (2) the peak was
inflated by open-position cost (the $56.29 included a $3.40 open leg); (3) the $15 trail is ~37%
of a $40 bankroll, so it only fires after all profit AND principal are gone, then lied "kept the
gains." FIXES: profit-lock now ARMS only once the day's peak is >= trail_stop ABOVE the day-start
(genuinely green) and fires on giving back trail_stop from peak — so it ALWAYS ends >= day-start
(locks real profit, never a loss); `hwm` resets at day-roll (prior day can't block a fresh day);
trail_stop default 15 -> 6 (sane for this bankroll) + .env; honest message shows locked +$ vs day
start. Knob: CLEAN_TRAIL_STOP.

## v1.17.1 — 2026-06-26 — prune persists immediately (disk/dashboard match memory)
**Tag:** `cleanbot-v1.17.1` · **Status:** ✅ live · follow-up to v1.17.0

v1.17.0's startup prune ran in memory but the state file only rewrote on the next trade,
so the dashboard could still show the old count until then. `_prune_positions()` now calls
`_save()` right after removing stale rows, so disk + dashboard reflect the pruned set at once.

## v1.17.0 — 2026-06-26 — honest accounting: reconcile bankroll to chain after every resolve + prune
**Tag:** `cleanbot-v1.17.0` · **Status:** ✅ live · trust fix

The logged bankroll/day-net ran ABOVE the real wallet: the win/loss ledger credits a win
immediately but only reconciled to the chain every 40 scans, so between syncs the number
overshot (e.g. logged $52.24 while the wallet held $44.24). That made wins look like they
vanished — they were partly never real, then the next sync clawed them back. Owner caught it.
Root: ledger drifts above chain on proxy fills/fees (here +$1.76 vs chain over a session).
Also found 127 resolved positions in the state file (102 >24h stale) bloating it. FIXES:
(1) after every REAL resolution batch, `_sync_bankroll()` immediately → the logged
`[RECONCILED] bankroll $X (chain truth)` line and the dashboard now show the wallet number,
not the optimistic running one; (2) honest `session net = reconciled_bankroll −
day_start_bankroll` (anchored at the chain-reconciled session/day start), replacing the
drifting wins−losses as the number to trust; (3) `_prune_positions()` drops resolved
positions older than `CLEAN_POSITION_KEEP_H` (48h) on startup + after each resolve. No change
to trading logic — purely making the numbers truthful. Knob: CLEAN_POSITION_KEEP_H.

## v1.16.1 — 2026-06-26 — reversal cooldown only ARMS in chop (keep trading in trends)
**Tag:** `cleanbot-v1.16.1` · **Status:** ✅ live · refinement (don't over-block)

Guardrail against the cooldown turning the bot into a sit-and-wait machine: the v1.16.0
reversal cooldown now only arms when the efficiency ratio says the regime is CHOPPY
(er < CLEAN_ER_TREND). In a trend a candle flip is usually just a pullback to buy and real
reversals are rare, so the cooldown never fires there — zero impact on trending-regime
trading. It only waits out whipsaws in the chop where they actually trap us. Context: the
bot already trades ~16x/day and the dominant skip reason is "no move" (weak_drift), not the
filters — this keeps it that way while still dodging the chop traps.

## v1.16.0 — 2026-06-26 — reversal cooldown: stop chasing the whipsaw that traps us
**Tag:** `cleanbot-v1.16.0` · **Status:** ✅ live · from a live loss post-mortem

SOL DOWN @69c (2026-06-26 17:31) was entered straight into a reversal and lost. Post-mortem
of the log showed the bot's OWN reversal filter fired first, then got overridden 45s later:
```
17:31:10 [DAY-TREND SKIP] SOL DOWN ... trend REVERSING (last candle flipped)
17:31:55 [ENTER]          SOL DOWN -15.8bps 69c
```
Cause: the day-trend gate passes on `trend_ok AND recent_ok`, where `recent_ok` = the FORMING
15m candle still points our way. Early in a window that candle is ~noise, so a sharp 45s
counter-spike (drift -5→-16bps) re-flipped it down, the gate passed, entry fired — then the
spike exhausted and price reversed back up. The flip-then-spike-back-within-a-minute IS the
chop. Tonight's research rows confirm the regime: drift_correct=0 on nearly every recent SOL
window (drift is anti-predictive right now). FIX: hysteresis — when a reversal is flagged
(macro trend intact but forming candle flipped), arm `CLEAN_REV_COOLDOWN` (150s) for that coin;
entries are blocked (`[REV COOLDOWN]`) until it expires, even if a counter-spike un-flips the
candle. Lets the early-window whipsaw settle before committing. Daytime-scoped (night = the
trending regime, handled by the momentum filter). Knob: CLEAN_REV_COOLDOWN (0=off).

## v1.15.1 — 2026-06-26 — FIX: silence the second datetime.utcfromtimestamp() DeprecationWarning
**Tag:** `cleanbot-v1.15.1` · **Status:** ✅ live · cleanup

The model-feature path computed the window hour via deprecated `utcfromtimestamp(ws).hour`.
Swapped to `datetime.fromtimestamp(ws, timezone.utc).hour` — same UTC hour, timezone-aware,
no warning. No behavior change. (Companion to v1.13.3, which fixed the research-log `ts`.)

## v1.15.0 — 2026-06-26 — correlated-pair control: stop doubling ETH+SOL same-dir bets
**Tag:** `cleanbot-v1.15.0` · **Status:** ✅ live · from the on-chain session analysis

Analysis of the 2026-06-26 on-chain history (16 trades, 69% WR, net +$0.49 — flat day on
good accuracy) found a second compounding leak beyond entry price: **correlated double-bets**.
ETH and SOL move together (~0.85), so betting BOTH coins the same direction in the same 15m
window is one 2x bet, not two diversified trades — a wrong call loses both legs at once. The
worst event of the day was 01:46 ETH Down + SOL Down, **both lost = -$7.25 in one window**,
which erased the rest of the night. The 25% open-exposure cap didn't catch it ($7.20 fit
under it). FIX: new `_corr_sibling` check + `CLEAN_CORR_PAIR_FRAC` (default 0.5) — when a coin
already has a live bet in the same window+direction, size the new leg at half so the pair ≈
one normal position (`[CORR HALF]`); if half falls below the 5-share exchange floor (small
bankroll), take ONE leg only and skip the duplicate (`[CORR SKIP]`). Opposite-direction legs
(genuine divergence, e.g. SOL Up + ETH Down) are unaffected. Would have turned the -$7.25
window into ~-$3.62 and the day green. Knob: CLEAN_CORR_PAIR_FRAC (1.0 = off).

## v1.14.0 — 2026-06-26 — COMPOUND FIX: cheaper entries (ask 58-70c) — stop wasting wins
**Tag:** `cleanbot-v1.14.0` · **Status:** ✅ live · data-driven (`_compound_study.py`, 552 windows)

"One loss wipes 3 wins" is the arithmetic of buying expensive favorites: at 68.5c avg
entry (old 61-74c band) a win pays only $0.47/$1 staked, so one loss erases ~2.2 wins.
Studied geometric (Kelly log-) growth, not just WR, across price bands:
  • 58-62c: 75.8% WR, win pays $0.67, one loss = 1.5 wins, +5.8% growth/trade
  • 70-74c: 81% WR but win pays only $0.40, one loss = 2.5 wins, +2.7%
  • 74-80c: 79% WR, win pays $0.32, one loss = 3.1 wins, +0.3% (looks safe, barely grows)
The bot was REFUSING its best-compounding band: floor was 61c, but 58-62c is 76% WR with
the live filters (the cliff is sharp — 50-58c is 54%, 58c+ jumps to 76%). FIX: ask band
0.61-0.74 → **0.58-0.70** (avg entry 68.5c→~63c). EV/trade ~doubles (+0.17→+0.30), one loss
now erases ~1.7 wins not 2.2. Sequential sim (start $46, 8%/bet, real sequence): old 61-74c
→ $111 (2.4x) maxDD 22.5%; new 58-70c → $117 (2.6x) maxDD **15.4%** — more growth AND
shallower drawdowns. Knobs unchanged: CLEAN_MIN_ASK, CLEAN_MAX_ASK. Sizing/cap untouched.

## v1.13.3 — 2026-06-26 — FIX: silence datetime.utcnow() DeprecationWarning in research log
**Tag:** `cleanbot-v1.13.3` · **Status:** ✅ live · cleanup

`utcnow()` is deprecated (removal scheduled). Swapped the research `ts` field to
`datetime.now(timezone.utc).replace(tzinfo=None)` — same naive-UTC ISO string, no
`+00:00` suffix, so `clean_bot_research.csv` format is byte-identical. No behavior change.

## v1.13.2 — 2026-06-26 — FIX: cross-coin confirm now checks the WHOLE market (was blind to ETH)
**Tag:** `cleanbot-v1.13.2` · **Status:** ✅ live · bug fix

The cross-coin confirmation filter (`_market_confirms`, the 84% tier gate) was silently
checking only ONE other coin. `CLEAN_CONFIRM_MARKET` defaulted to `BTC,SOL`, and the loop
skips the bet's own coin (`if p == coin: continue`) — so a **SOL** bet only ever checked
**BTC** and never saw **ETH**. That let the losing SOL UP @73c through: BTC was up but ETH
was down (a divergent, reversal-prone market) — exactly the "whole market is DOWN but the
bot bet UP" case the owner flagged. FIX: default `CLEAN_CONFIRM_MARKET=BTC,ETH,SOL` (in code
and `.env`) so every bet is validated against the OTHER two coins (SOL bet → checks BTC+ETH;
ETH bet → checks BTC+SOL). Verified live: with BTC/ETH/SOL all up, `_market_confirms` returns
True for UP and False for DOWN on both coins; the counterfactual (BTC up + ETH down) now nets
votes 0 → not confirmed → the divergent SOL UP is correctly skipped. No logic change — the
`votes > 0` net-agreement rule is unchanged, it now just sees all three coins.

## v1.13.1 — 2026-06-26 — MOMENTUM CONFIRMATION (the data-found edge): skip fading moves
**Tag:** `cleanbot-v1.13.1` · **Status:** ✅ live · quantitative analysis result

Deep analysis of 517 logged windows (`_quant_edge.py`, drift_correct vs every feature)
found the real edge. Directional accuracy: drift 3-7bps=62%, 13-18bps=85%, 30+=86%
(big drifts far more accurate); roc300 SAME dir as drift=69% vs OPP 64%; roc60 SAME=70%
vs OPP 62%. STACKED: |drift|>=10 + momentum same dir = 80% (n=103); + both coins agree =
84% (n=69); while |drift|>=10 with momentum OPPOSITE (fading) = 71% — that's where the
reversal losses live. FIX: `[MOM SKIP]` momentum-confirmation gate in scan — skip when the
CLEAN_MOM_LOOKBACK (300s) Chainlink momentum opposes the drift by > CLEAN_MOM_MIN_BPS (2);
optional CLEAN_MOM_NEED_COIN for the 84% cross-coin tier. This directly attacks the
fading-move reversals that wipe the wins. Stacks with the ER regime detector + adaptive
bar. Knobs: CLEAN_MOM_FILTER, CLEAN_MOM_LOOKBACK, CLEAN_MOM_MIN_BPS, CLEAN_MOM_NEED_COIN.

## v1.13.0 — 2026-06-26 — PROACTIVE regime detector (efficiency ratio): trade trends, sit out chop
**Tag:** `cleanbot-v1.13.0` · **Status:** ✅ live

The edge is trend-following: trends ~84%, chop ~53%. New PROACTIVE detector measures the
Kaufman efficiency ratio (`_efficiency_ratio`: |net move|/total path over the last hour
of 5m candles) BEFORE betting — ~1 = clean trend, ~0 = chop. When ER < CLEAN_ER_TREND
(0.32) the regime is choppy, so the drift bar is raised to CLEAN_ER_CHOP_DRIFT (16bps) —
i.e. in chop we ONLY take the strongest moves (which historically hit ~89%), and in
trends we trade freely. `[REGIME SKIP ... chop(ER=x)]` logged. Stacks with the reactive
adaptive-accuracy bar (`_eff_drift`). This attacks the chop-loss problem at the source —
detecting the regime up front instead of reacting after losses. Also bumped CLEAN_ADAPT_K
(reactive tightening) for sharper survival in poor regimes. Knobs: CLEAN_ER_FILTER,
CLEAN_ER_TREND, CLEAN_ER_CHOP_DRIFT.

## v1.12.1 — 2026-06-25 — tiered Kelly (compound up on recovery) + EV-tuned ask band
**Tag:** `cleanbot-v1.12.1` · **Status:** ✅ live

Owner: "compound more efficiently — each win is small, one loss wipes several." (1) EV-by-
ask audit settled the real drag (config, .env): <=60c=50%WR -$4.66, 75c+=neg-EV -$2.79,
while 69-74c=77%WR +$6.50 is the sweet spot -> tightened band to CLEAN_MIN_ASK 0.61 /
CLEAN_MAX_ASK 0.74 (cut the two losing buckets, keep the two winners; +$7.45 on sample).
(2) TIERED KELLY: `_size_shares` now uses CLEAN_KELLY_BUMP (0.08-0.10) once bankroll >=
CLEAN_KELLY_BUMP_AT ($70), else the conservative CLEAN_KELLY_FRAC (0.06) — so it stays
small while rebuilding and sizes UP as it recovers (5sh@$54 -> ~8sh@$70 -> more as it
grows). max_bet_pct raised 0.10->0.12 so the bump isn't clipped. HONEST: betting favorites
is inherently small-win/big-loss; efficiency = cut negative-EV prices + compound size as
the balance grows, not bigger individual wins (cheaper entries = 50% coin-flips).

## v1.12.0 — 2026-06-25 — ADAPTIVE ACCURACY: learn from every trade, adjust the quality bar
**Tag:** `cleanbot-v1.12.0` · **Status:** ✅ live · owner: "learn from losses, measure + adjust accuracy, don't block"

Instead of blocking, the bot now LEARNS. `_rolling_wr()` measures the win rate over the
last CLEAN_ADAPT_WINDOW (15) resolved trades; `_eff_drift()` raises the drift bar when
that rolling accuracy drops below CLEAN_ADAPT_TARGET (0.60) — +CLEAN_ADAPT_K (35) bps per
point of WR deficit, capped at CLEAN_ADAPT_MAX_DRIFT (20). So when it's losing it takes
ONLY the strongest, historically-highest-WR setups (drift 10-15bps = 89% in the data);
when it's winning it drops back to the base 10bps and trades freely. Self-correcting,
NOT a hard block. `[ADAPT] rolling WR X% -> drift bar Ybps` logged on every resolution
(measure on every window). recent_trades persisted. HONEST NOTE: this improves SELECTIVITY
(concentrate on what's working), not the raw direction call (market is efficiently priced).
Layers under night-only + adaptive breaker + rolling profit-lock.

## v1.11.1 — 2026-06-25 — adaptive regime backoff + rolling profit-lock (the choppy-night fix)
**Tag:** `cleanbot-v1.11.1` · **Status:** ✅ live

Loss review (Jun 24->25 night): the night CHOPPED (58% WR vs the usual 84%) — constant
reversals (22:21 2L, 00:18-00:33 3L). Two real gaps fixed, NO new hard-blocking (owner:
"don't go back to over-blocking / waiting for rare signals"):
(1) ROLLING PROFIT-LOCK: hwm no longer resets at the midnight day-roll (the night session
spans midnight, so the overnight $80 peak protection was being discarded at 00:00). It's
now a rolling peak across the run; reset only on (re)start. Would've stopped ~$65 vs $48.
(2) ADAPTIVE REGIME BACKOFF: the loss-breaker now ESCALATES — each repeat trip pauses
longer (base CLEAN_BREAKER_COOLDOWN 20m × trips, capped CLEAN_BREAKER_MAX 90m) so
persistent chop backs off harder; a CLEAN_BREAKER_RESET_WINS (2) win-streak clears the
escalation (regime recovered) and it trades freely again. It RE-PROBES after every
cooldown — never a permanent block. Regime signal = the bot's own results (regime persists
at session level). Removed the old daytime "block-till-night" hard block (replaced by this
re-probing backoff). Knobs: CLEAN_BREAKER_ESCALATE, CLEAN_BREAKER_MAX, CLEAN_BREAKER_RESET_WINS.

## v1.11.0 — 2026-06-24 — NIGHT-ONLY w/ strong-trend daytime exception (the edge is at night)
**Tag:** `cleanbot-v1.11.0` · **Status:** ✅ live

Data settled it: NIGHT (20-09 Lima) = 84% WR / +$36.52; DAY (09-20) = 51% / -$24.26 —
the day gives back the night's profit (Asia-session trends vs US/EU chop). NIGHT-ONLY
mode (`CLEAN_NIGHT_ONLY=on`): night trades freely (proven winner, UNCHANGED); daytime
only fires on a STRONG macro trend (`CLEAN_DAY_STRONG` 0.25%, vs the old 0.12% filter) +
the last-candle agreement; and after `CLEAN_DAY_LOSS_BLOCK` (2) DAYTIME losses in a row
the bot BLOCKS daytime entirely until night (`[DAY-BLOCK]` + Telegram). day_blocked /
day_loss_streak reset each night + persisted. So: catch strong daytime trends, bail fast
when they fail, sleep through the chop, and work the trending night. Knobs:
CLEAN_NIGHT_ONLY, CLEAN_DAY_STRONG, CLEAN_DAY_LOSS_BLOCK.

## v1.10.2 — 2026-06-24 — PROFIT LOCK: trailing high-water-mark stop (keep the gains)
**Tag:** `cleanbot-v1.10.2` · **Status:** ✅ live

Owner: "we topped $80 overnight, fell to $55 — how do we KEEP the profit?" Added a
trailing high-water-mark stop on the REAL (chain-synced) bankroll: track `self.hwm`
(peak bankroll, persisted, resets to the day's opening balance each day); once
`hwm - bankroll >= CLEAN_TRAIL_STOP` ($15 default) the bot STOPS for the day —
`[STOP] PROFIT-LOCK` + Telegram alert. So an $80 peak halts ~$65 instead of bleeding to
$55. Layered with the existing daily-loss stop + day_net give-back. Env: CLEAN_TRAIL_STOP
($, 0=off). Resumes next day (peak resets) or on restart. Manual ultimate safeguard:
withdraw profit off Polymarket when up — the bot can only STOP to preserve, not withdraw.

## v1.10.1 — 2026-06-24 — trend RESET on reversal + on-chain bankroll sync (honest numbers)
**Tag:** `cleanbot-v1.10.1` · **Status:** ✅ live · OVERNIGHT UNCHANGED

Owner: the daytime trend was "stuck" on the dead overnight trend after the morning
reversal, and the bot's bankroll diverged from the real Polymarket balance. TWO fixes:
(1) TREND RESET: `_macro_trend` now returns (net, last_candle); the daytime filter
requires BOTH the net trend AND the most-recent candle to agree with the drift — so a
reversal (last candle flips) immediately breaks the signal -> `[DAY-TREND SKIP] trend
REVERSING`, and trading only resumes once the NEW trend rebuilds (recent + net realign).
Lookback shortened 45->30min so it adapts faster. This would have skipped the 9:18+
DOWN bets (last candle had flipped UP). (2) BANKROLL SYNC: `_sync_bankroll` reconciles
bankroll to REAL on-chain USDC + open-position cost on startup and every ~40 scans —
the internal win/loss ledger drifts ABOVE the chain (inconsistent proxy fills: chain
realized +$1.24 vs ledger +$12), so the chain balance is now the source of truth for
sizing + the dashboard. NOTE: the $80 peak owner saw WAS real (portfolio mark-to-market
of open winning positions mid-window); the 9am reversal settled them as losses before
they locked — exactly what the give-back stop now guards.

## v1.10.0 — 2026-06-24 — daytime trend filter + give-back stop (keep the overnight wins)
**Tag:** `cleanbot-v1.10.0` · **Status:** ✅ live · OVERNIGHT BEHAVIOR UNCHANGED

Review of Jun 23-24: overnight 78% WR (39W/11L), peak $87.22, because the market
TRENDED (ETH -1.53% into 8am) and the early-drift rode it (DOWN bets 86-87%). At 9am
the trend REVERSED (+0.25%) and 4 DOWN bets lost (-$13) — a turning point. Two adds,
both env-tunable, OVERNIGHT (Lima 20:00-09:00) is UNTOUCHED:
(1) DAYTIME TREND FILTER (`_macro_trend` + `_is_daytime`): only when day_start<=Lima_hr
<day_end (default 9-20) the drift must AGREE with the Binance macro trend over the last
~45min (>=CLEAN_DAY_TREND_MIN 0.12%), else `[DAY-TREND SKIP]`. Keeps the bot trend-
following + skips daytime chop/counter-trend bounces. (Note: a trend filter is late to
sharp turning points like 9am — it guards chop, not tops/bottoms.)
(2) GIVE-BACK STOP (`CLEAN_GIVEBACK` $10): tracks the day's peak P&L; once P&L falls
>=giveback from the peak, stop for the day — locks in winning days (would've saved most
of the $87->$69 give-back). Added to `_stopped()`; resets each day.
Knobs: CLEAN_DAY_TREND, CLEAN_DAY_START/END, CLEAN_DAY_TREND_MIN/LOOKBACK, CLEAN_GIVEBACK.

## v1.9.3 — 2026-06-23 — strike snapshot in the bot loop (get_ticks buffer too short)
**Tag:** `cleanbot-v1.9.3` · **Status:** ✅ live

v1.9.2's get_ticks-from-buffer approach still served Binance because the RTDS tick
buffer is too short/sparse to cover a boundary that opened minutes ago. FIX:
`_snapshot_strikes()` runs every main-loop iteration and, the instant a window opens
(age<45s), caches the live Chainlink `get_price` as that window's strike (get_price is
proven-good — it's the spot feed). Runs in the always-alive bot loop (no fragile
separate process). Combined with the v1.9.2 gate, the bot now reliably trades on the
Chainlink strike or not at all.

## v1.9.2 — 2026-06-23 — FIX direction: robust Chainlink strike IN the bot + never trade Binance strike
**Tag:** `cleanbot-v1.9.2` · **Status:** ✅ live · owner caught it again

Owner: "the direction selector seems broken — 3 of 5 bets reversed." Audit: the
strike_snapshotter.py process had HUNG (last capture window ...260100), so the bot
silently reverted to `binance_kline_open` strikes (cache showed source=binance) while
the spot is Chainlink → the ~10bps cross-feed basis flips near-strike direction = the
reversals. Root flaw: the strike fix lived in a fragile separate process. FIX moves it
INTO the bot: poly_resolution.get_strike now scans the Chainlink RTDS tick buffer
(~330s) for the tick closest to the window boundary (robust at any window_age, not a
20s race) and ONLY caches Chainlink results (never persists a Binance fallback that
would stick). Plus a hard gate in clean_bot.scan: `[STRIKE SKIP]` — refuse to trade
any window whose strike_source isn't chainlink. Snapshotter retired (get_strike is now
self-sufficient). Binance cache entries cleared on deploy.

## v1.9.1 — 2026-06-23 — EXIT v2: ride SOLID winners to the full reward (owner refinement)
**Tag:** `cleanbot-v1.9.1` · **Status:** ✅ live · owner's refinement

v1.9.0's exit was too eager — it bailed on EVERY position the same way (fixed +12c TP
+ always time-exit), so when the direction was genuinely solid it capped a small scalp
instead of riding to the full +$1. Owner: "the take-profit saves us from reversals, but
when we have a solid direction we should wait to the end for the full reward." New
policy in manage_positions() reads the token's own price to tell SOLID from SHAKY:
(1) HOLD — deep ITM near close (bid >= CLEAN_DEEP_ITM 0.85, within CLEAN_EXIT_BEFORE
180s) → ride to settlement for the full $1 (reversal unlikely + settlement is fee-free);
(2) TRAIL — armed after +CLEAN_TRAIL_ARM (0.08), sell if bid drops CLEAN_TRAIL_DELTA
(0.06) off its PEAK (let runners run, exit only when they actually turn); (3) STOP —
hard cut at -CLEAN_TP_STOP (0.20); (4) TIME — near close & NOT deep ITM → bail before
the coin-flip. Tracks per-position peak. Replaces the fixed tp_delta.

## v1.9.0 — 2026-06-23 — ACTIVE EXIT: take-profit + time-exit (stop riding into the settlement reversal)
**Tag:** `cleanbot-v1.9.0` · **Status:** ✅ live · owner's insight

Owner observed the recurring pattern: positions WIN most of the window, then reverse
in the last ~3 minutes and lose. Diagnosis: the drift moves the price our way
mid-window (the token appreciates), but we held the binary to SETTLEMENT, where the
near-money close is a coin flip — so we gave the gain back at the bell. FIX:
`manage_positions()` marks each open position to market every loop and exits early:
(1) TAKE-PROFIT — sell when the token gains >= CLEAN_TP_DELTA (0.12); (2) STOP — cut
if it drops >= CLEAN_TP_STOP (0.20); (3) TIME-EXIT — always sell CLEAN_EXIT_BEFORE
(180s) before close, dodging the last-3-min reversal window. Exit is a marketable FOK
SELL (crosses the bid; 7% taker fee modeled). Closed positions free their exposure
slot; resolve() skips them. Converts the binary settlement lottery into an active
trade that books the favorable move the owner kept seeing.

## STRIKE FIX — 2026-06-23 — precise Chainlink strike snapshotter (owner was right: source corrupted)
**Status:** ✅ live (DRY re-measurement) · the directional-signal corruption

Re-audit found the directional signal wasn't broken — its **reference price was**.
Polymarket settles 15m crypto on the **Chainlink BTC/USD data stream** (confirmed in
market description), strike = Chainlink price at the window boundary. The bot only
captured Chainlink if it read within 20s of the boundary, else fell back to Binance:
audit showed **43% of recent strikes were `binance_kline_open` (wrong feed, ~10bps
off)** → drift measured against a corrupted reference → near-money bets flip → signal
looks like 50%. FIX: `strike_snapshotter.py` — a fast loop that captures the exact
Chainlink boundary tick (within ~0.8s) for BTC/ETH/SOL every window and pre-populates
`data/strike_cache.json`, so `get_strike` never falls back to Binance. Now
re-measuring the true directional WR (DRY, no risk) with correct strikes. Also added
`arb_monitor.py` (fee-aware arbitrage scanner; found 7% taker fee = crypto_fees_v2).

## v1.8.6 — 2026-06-22 — FIX: exposure cap < minimum bet (was blocking ALL trades)
**Tag:** `cleanbot-v1.8.6` · **Status:** ✅ live · the real "no trades" cause

The [WATCH] log (v1.8.5) immediately exposed it: a clean signal (SOL UP +7.9bps,
ask 67¢) was skipped as `exposure_or_timing`. Root cause: `max_open_pct` (25%) ×
bankroll ($8.75) = $2.19 cap, but the **minimum bet is 5 shares ≈ $2.75–3.30** >
$2.19 → the exposure check rejected EVERY trade. The minimum possible bet exceeded
the exposure cap on the shrunken bankroll → mathematically zero trades, regardless
of drift/ask. No drift tuning could ever fix this. Raised `CLEAN_MAX_OPEN_PCT`
0.25→0.70 so a single 5-share bet fits. (On a small account the $6 daily stop is the
real risk control, not the % exposure cap.) THIS is why it wasn't betting.

## v1.8.5 — 2026-06-22 — [WATCH] per-window visibility log (see what the bot is doing)
**Tag:** `cleanbot-v1.8.5` · **Status:** ✅ live

Owner couldn't see what's happening (bot only logged heartbeats between trades).
Added a `[WATCH]` line in `_research_scan` — one per real-move window (drift≥3bps):
`[WATCH] ETH UP drift=+7.4bps ask=65c t=659s -> SKIP:weak_drift`. Shows drift, ask,
time-left, and the decision/reason for every window, live in clean_bot.log → visible
in the dashboard 🤖 CleanBot "Live log". No trading-logic change. Dashboard live-log
line count bumped so more history shows.

## v1.8.4 — 2026-06-22 — drop ETH cross-coin confirmation (the last gate blocking trades)
**Tag:** `cleanbot-v1.8.4` · **Status:** ✅ live · exploratory

At drift≥7 the bot STILL didn't trade: ETH signals were blocked by `[NO CONFIRM]`
(cross-coin confirmation) and SOL's qualifying drifts had ask>68¢. The confirmation
was marginal (+0.5pt in tests) and is the active blocker. Dropped it
(`CLEAN_CONFIRM_COINS=`) — fewer gates, more trades, per owner's standing direction.
Now: drift≥7, ask 0.50–0.68, no confirmation, $6 stop + breaker. Goal unchanged:
generate Chainlink-era trades so we can judge edge-vs-efficient-market from real
numbers. Committing to LET IT RUN now — no more knob-tuning until the data is in.

## v1.8.3 — 2026-06-22 — drift 10→7 to GENERATE Chainlink data (was stuck between two skips)
**Tag:** `cleanbot-v1.8.3` · **Status:** ✅ live · exploratory

Live scan diagnostic proved zero trades isn't a bug: the bot is wedged between
`weak_drift` (small drift, cheap ask 50–62¢) and `ask_out_of_zone` (big drift,
expensive ask 70–89¢). On the correct Chainlink feed the ask tracks the drift
tightly — the "big drift + still cheap" window (the old edge) was mostly the ~10bps
Binance basis illusion. So drift≥10 ∧ ask≤68 is a near-empty set.

- **Hypothesis:** the basis flipped *near-money (small-drift)* bets worst, so the
  small drifts we skip may actually WIN on the correct feed (no flip). Untested —
  0 Chainlink trades so far.
- **`CLEAN_DRIFT_BPS` 10→7** to trade the small/cheap-ask windows and finally
  generate Chainlink-era win-rate data, instead of guessing thresholds tuned on the
  wrong (Binance) feed.
- Then LET IT RUN and judge from real numbers: thin edge to keep, or efficient
  market = stop. No more blind tuning until we have Chainlink data.

## v1.8.2 — 2026-06-22 — max-ask 0.62→0.68 (real drifts come priced; was skipping all)
**Tag:** `cleanbot-v1.8.2` · **Status:** ✅ live

Diagnosed "no trades": NOT a calm market — drifts up to 63bps exist, but on the
(correct) Chainlink feed a real drift is already *priced*, so the favored ask is
63–89¢. With `max_ask 0.62` the bot skipped them all (26 `ask_out_of_zone` skips vs
0 entries). Raised `CLEAN_MAX_ASK` 0.62→0.68 to take the moderate drifts (63–68¢) and
get trading on the correct feed + generate Chainlink-era data (we have ~0 chainlink
trades). HONEST: big-drift→high-ask = efficient market; some of the old "cheap entry"
edge was the ~10bps Binance basis illusion, so margins are thinner now (breakeven =
ask). Exploratory — the research logger will tell us what's actually +EV on Chainlink.

## v1.8.1 — 2026-06-22 — drift 12→10 (Chainlink feed runs smaller drifts than Binance)
**Tag:** `cleanbot-v1.8.1` · **Status:** ✅ live

After the v1.8.0 Chainlink switch, the bot went quiet — Chainlink is a smoother feed,
so its drifts run smaller than Binance, and almost nothing cleared the 12bps bar
(observed drifts 3–12bps in a calm window; one 12.7bps blocked by ETH confirm).
The 12bps bar was tuned on *Binance* data and is too high for the (correct) Chainlink
feed. Lowered `CLEAN_DRIFT_BPS` 12→10 to restore volume — now safe because the feed
is correct (smaller drift = real signal, not the old cross-feed noise). Note: all
backtest thresholds are Binance-derived; the research logger is now capturing
Chainlink-era drifts+outcomes to re-tune properly. The one trade before the fix
(10:33, SOL UP +19bps Binance → settled DOWN, −$2.85) was the cross-feed flip itself.

## v1.8.0 — 2026-06-22 — ROOT-CAUSE FIX: strike/spot on Chainlink (settlement feed), not Binance
**Tag:** `cleanbot-v1.8.0` · **Status:** ✅ live · **the real bug**

CleanBot was computing drift entirely on **Binance** (strike = binance_kline_open,
spot = binance_ws) — but **Polymarket settles on Chainlink.** Measured live
cross-feed basis: **BTC +9.0 / ETH +9.9 / SOL +11.3 bps** — i.e. ~10bps, the SAME
size as the 12bps signal. So a "+12bps up" on Binance could be flat-or-DOWN on the
Chainlink feed that actually pays out → near-the-money bets (where the bot lives)
get their direction flipped. This is the documented audit-C1 (Jun 10) failure, and
it's why the rebuild stopped working while the old (Chainlink-aligned) bot won.
Owner called this out repeatedly ("something wrong in the calculations / the
threshold / we won more before") — and was right; I was wrong to blame the regime.

- **Start `chainlink_ws`** on boot (was never started → `get_strike`/`get_market_info`
  always fell back to Binance: 724/724 reads today).
- **Use the Chainlink strike + spot** that `get_market_info` already computes
  (`info.threshold_price` + `info.current_crypto_price`) instead of overriding spot
  with `binance_ws`. Fixed in scan(), `_research_scan`, `_market_confirms`.
- Binance remains the graceful fallback if Chainlink drops. No other logic changed.
- Expected: near-money bets stop getting flipped by the basis → the real edge.

## v1.7.1 — 2026-06-22 — Restore volume (drift 20→12); close-prob engine tested & rejected
**Tag:** `cleanbot-v1.7.1` · **Status:** ✅ live

Owner: don't wait for rare bets — bet a lot on a real close-vs-threshold prediction.
Built + backtested the close-probability engine (`close_prob_test.py`):
`P(close>strike)=Φ(dist/(σ·√min_left))`, enter when confident.

- It gives the volume (43k+ trades) and uses time-left — but **over-predicts**
  (~80% claimed vs ~70% actual; Gaussian misses fat-tailed reversals) and is
  **slightly worse than the plain drift rule** (75% vs 78%). The drift the bot
  already uses *is* the close-vs-threshold prediction; the Φ-formula just adds
  lower-quality volume. **Rejected** as redundant over-refinement.
- **`CLEAN_DRIFT_BPS` 20 → 12** — restore volume (bet a lot, ~80% directional),
  the drift IS the prediction. drift=20 was too restrictive (rare bets).
- Honest: directional edge caps ~70-78% (reversals random); +EV at volume; the
  real constraint is variance on a tiny account ($11.60). Tiny bets + $6 stop.

## v1.7.0 — 2026-06-22 — Clearer-signal entry (drift 10→20bps); strip the noise
**Tag:** `cleanbot-v1.7.0` · **Status:** ✅ live

Back to basics, owner-directed: the hair-trigger 10bps entry was the problem.
Tested on 46k windows (`timing_test.py`): entering on a *clearer* drift sharply
raises win rate — **10bps 78% → 20bps 85% → 25bps 87%** — and cuts the 3-loss
wipeout streaks ~3–4×. The momentum "confirmation" overlay added only **+0.5pt**
(noise), confirming we over-refined and buried the signal.

- **`CLEAN_DRIFT_BPS` 10 → 20.** Enter only on a clear, high-conviction drift.
  Higher WR = lower variance = a small account survives.
- Bonus: drift≥20 *and still cheap* (ask ≤62¢) self-selects the inefficient/overnight
  windows where the market is lagging — the regime the edge actually works in.
- Simpler, not more complex — one clear trigger. Maker, ask-cap, $6 stop unchanged;
  ML model stays benched (shadow) since it added noise live, not signal.
- Note: backtest WRs are binance-directional; live runs lower, but the *relative*
  lift (fewer losses on bigger drifts) transfers.

## v1.6.1 — 2026-06-21 — Entry-timed model retrain + gate→shadow (DRY validation)
**Tag:** `cleanbot-v1.6.1` · **Status:** ✅ live (DRY shadow)

The v1.6.0 DRY run caught the model **miscalibrated live**: it stamped ~0.84 on
every window but delivered 44% (4W/5L) and hit the sim stop. Root cause: trained at
a fixed **minute 5**, but the bot enters at **minute 2–3** → out-of-distribution →
saturated. The DRY shadow did its job — $0 real lost.

- **Retrained on the bot's REAL entry timing** (`build_v3.py`): features at the FIRST
  minute (2–5) the drift crosses 10bps. Entry-minute dist {2:14.8k, 3:5.5k, 4:3.9k,
  5:3.2k} — matches live. Now **calibrated** (0.8→83%, 0.9→91%) but honestly
  **modest discrimination** (AUC 0.57, prob std 0.05) — 2 minutes of data is thin
  signal. Redeployed `drift_model_band.joblib`.
- **Gate → shadow** (`CLEAN_MODEL_GATE=off`): logs `model_prob` vs outcomes without
  gating/stopping, so we validate live calibration cheaply before it touches money.
- Code unchanged except VERSION; model artifact + env only. Banner `model=shadow`.

## v1.6.0 — 2026-06-21 — ML model gate (calibrated P(drift wins)), DRY shadow
**Tag:** `cleanbot-v1.6.0` · **Status:** ✅ live (DRY validation)

Adds a calibrated gradient-boosting model that scores each candidate's probability
of the early-drift winning, trained offline on **46k windows from 1m klines** (see
`_ml_train/`). Honest scope: within the tradeable band (drift≥10bps) the model
lifts WR ~84.8%→90.8% on the top ~47% and flags the weak ~75% windows (modest but
real, calibrated 0.8→86% / 0.9→93%, walk-forward OOS).

- **Shared feature module** `model_features.py` used by BOTH training and the bot →
  parity guaranteed (this fixes a parity break where the bot computed `sigma`
  differently than training).
- `_model_prob(coin, ws)`: fetches binance 1m klines, builds the 8 features, scores
  `drift_model_band.joblib` (sklearn 1.8, isotonic-calibrated). Cached per window.
- Logs `model_prob` on every ENTER + in the research CSV (new column) for live
  calibration validation. **Gate** (`CLEAN_MODEL_GATE`) blocks entries below
  `CLEAN_MODEL_MIN_PROB` (0.80) — running DRY first to validate before live.
- New env: `CLEAN_MODEL_PATH, CLEAN_MODEL_GATE, CLEAN_MODEL_MIN_PROB`. Model scoring
  is wrapped in try/except — never breaks trading. Banner shows `model=gate@0.8`.
- Real-data backtest (419 Polymarket trades): filtering by model prob turned the old
  bot's −$97 into +$136 OOS — the market prices the favored side ~59¢ regardless of
  the model's confidence, so high-prob windows carry real +EV.

## v1.5.3 — 2026-06-20 — Max-ask 0.62 (cut thin-margin 63–66¢) + fav_ask logging fix
**Tag:** `cleanbot-v1.5.3` · **Status:** ✅ live

Owner flagged live losses entering at 60¢+ that immediately reversed. Confirmed:
a high ask means the move is already priced — you buy near exhaustion, and the
breakeven (= entry price) eats the edge.

- **`CLEAN_MAX_ASK` 0.66 → 0.62.** WR-by-entry (n=89): 56–59¢ **77%** / 60–62¢
  **68%** (sweet spot, comfortable margin) vs 63–66¢ **69%** with a **66%
  breakeven** = razor-thin (+3) → first to flip negative in chop. Cut it.
  (≤55¢ is also negative — 44% — but n=18; holding `MIN_ASK` for more data, not
  over-narrowing.)
- **Fix `fav_ask`/`up_ask`/`down_ask` logging:** stored `int(0.64)=0` (truncated
  the fraction) → research ask column was useless. Now `int(round(ask*100))` = cents.
- Confirmation/sizing/breaker/research logic unchanged.

## v1.5.2 — 2026-06-20 — Fix: research CSV header + ENTER mislabeling
**Tag:** `cleanbot-v1.5.2` · **Status:** ✅ live (DRY)

Two research-logger bugs found while reviewing the weekend DRY run:
- **Missing header:** the CSV was pre-created empty, so `new = not exists` was
  False → header never written → the dashboard `DictReader` mis-read every row
  (Research tab stayed blank). Fix: `new = not exists OR getsize == 0`; existing
  CSV back-filled with the header.
- **ENTER mislabeled as SKIP:** `_research_scan` captures a window the first time
  drift ≥ 3bps, which can predate the actual entry → traded windows were logged
  `SKIP` (6 ENTER vs 17 real sim trades). Fix: re-label `decision=ENTER` at
  resolve time from the final `traded` state.
- `drift_correct`, features, and outcomes were always correct — only the header
  and the decision label were wrong.

## v1.5.1 — 2026-06-20 — Fix: research writer crashed (missing `import csv`)
**Tag:** `cleanbot-v1.5.1` · **Status:** ✅ live (DRY)

- **Bug:** `_research_resolve` used `csv.DictWriter` but `csv` was never imported,
  so **every** research write failed silently (`name 'csv' is not defined`) since
  v1.4 → `clean_bot_research.csv` stayed empty → the dashboard 🔬 Research tab had
  no data to show. Trading/sim were unaffected (research is isolated).
- **Fix:** added `import csv`. The CSV now writes one row per resolved window;
  Research tab populates within ~16 min (first window resolution after restart).

## v1.5.0 — 2026-06-20 — Full DRY simulation (paper trading) + dashboard badge
**Tag:** `cleanbot-v1.5.0` · **Status:** ✅ live (DRY)

Run the weekend **risk-free**: DRY mode is now a full paper-trade simulation, not
just a no-op log. Reset to a real-balance seed of **$30** after a choppy-regime
drawdown ($48→~$30).

- **DRY = full lifecycle sim:** `[ENTER]` → `[SIM FILL]` (assume the maker fills)
  → gamma resolve → simulated P&L/bankroll, exactly like live but **no real
  orders**. Positions tagged `sim:true`; `[WIN]/[LOSS]` get a `[SIM]` suffix /
  🧪 Telegram prefix. Gathers weekend data with zero risk.
- **State exposes `mode` (DRY/LIVE) + `version` + `bankroll`** → dashboard shows a
  prominent **🧪 DRY / SIMULATION** banner + sim bankroll on the CleanBot tab
  (`_patch_dash_drysim.py`).
- Reset to **$30** (bot-tracked bankroll had drifted ~$6 above the real balance;
  now sized to reality). `CLEAN_DRY=true`, `CLEAN_START_BANKROLL=30`.
- Trading/sizing/confirmation/research logic unchanged — flip `CLEAN_DRY=false`
  to go live again.

## v1.4.0 — 2026-06-20 — Research data logger + dashboard
**Tag:** `cleanbot-v1.4.0` · **Status:** ✅ live

Capture **everything** for future edge-mining — every real-move window, traded
*or* skipped, with full features + true outcome. Read-only, fully isolated from
the trade path (its own try/except — can never place an order or break trading).

- **`clean_bot_research.csv`** — one row per window with `|drift| >=
  CLEAN_RESEARCH_MIN_BPS` (3): `ts, window_start, coin, dir, drift_pct,
  roc60_bps, roc300_bps, sigma, fav_ask, up_ask, down_ask, btc_drift_pct,
  sol_drift_pct, confirmed, decision (ENTER/SKIP), reason (weak_drift/no_confirm/
  ask_out_of_zone/exposure), t_left, winner, drift_correct`.
- **Captures the windows we SKIP** (with the reason) + the outcome → tells us if a
  gate is leaving money on the table (`drift_correct` on skipped windows).
- Resolved via gamma (Chainlink). New helpers: `_roc`, `_coin_drift`,
  `_research_scan`, `_research_resolve`. Env: `CLEAN_RESEARCH` (on),
  `CLEAN_RESEARCH_MIN_BPS` (3).
- **Dashboard 🔬 Research tab** (`_patch_dash_research.py`): summary (drift-correct
  traded vs skipped), skip-reason "are we over-filtering?" table, recent windows
  with all features + outcome, and the live log. Endpoint `/api/v3/research`.
- Trading logic / sizing / confirmation unchanged.

## v1.3.0 — 2026-06-19 — Cross-coin confirmation for ETH (the follower coin)
**Tag:** `cleanbot-v1.3.0` · **Status:** ✅ live

ETH is a high-beta *follower* of the crypto market — its **solo** drifts are noise
that reverts. The data (clean_bot.log, n=23 ETH): ETH when SOL agrees = **64%**,
ETH solo (SOL flat) = **22%**, ETH vs SOL diverging = **0%**.

- **ETH (and any `CLEAN_CONFIRM_COINS`) only trades when the broader market is
  drifting the same way.** Soft confirmation: each market proxy
  (`CLEAN_CONFIRM_MARKET=BTC,SOL`) that leans the same direction ≥
  `CLEAN_CONFIRM_BPS` (3) votes +1, opposing votes −1 → trade only if net > 0.
  `get_market_info` is used to read BTC/SOL drift (we don't trade BTC).
- Handles divergence correctly: ETH-solo and ETH-vs-market → `[NO CONFIRM]` skip
  (throttled once per window, doesn't lock the window so it can fire if the
  market aligns later). Fail-open if no proxy data (transient).
- **SOL unchanged** (it's the leader/proxy, not confirmed). Compounding (v1.1),
  quality (v1.2) unchanged. New env: `CLEAN_CONFIRM_COINS, CLEAN_CONFIRM_MARKET,
  CLEAN_CONFIRM_BPS`. Banner shows the confirm rule.
- Goal: turn ETH from a ~48% drag into a ~64% contributor by only taking
  market-confirmed ETH (not blocking blindly, not inverting noise). Deploy +
  measure; revisit if confirmed-ETH holds ≥60% over more trades.

## v1.2.0 — 2026-06-19 — Quality tightening + whipsaw breaker
**Tag:** `cleanbot-v1.2.0` · **Status:** ✅ live

Shipped after validating a 4-loss streak — every loss was a *marginal* signal in
a whipsawing (chop) regime. All four would have been skipped by these:

- **Drift floor 7 → 10bps** (`CLEAN_DRIFT_BPS=10`). The 7–10bps band wins only
  54% (coin-flips); ≥10bps wins ~74%. Skipped 3 of the 4 losses.
- **Min-ask 45¢ → 50¢** (`CLEAN_MIN_ASK=0.50`). Don't bet a side the market
  prices below 50¢ (market disagrees with our drift, and it was right). Skipped
  the other 2 losses (SOL UP @44¢, SOL DOWN @46¢).
- **Consecutive-loss breaker** (new): after `CLEAN_LOSS_BREAKER` (3) losses in a
  row, pause `CLEAN_BREAKER_COOLDOWN` (1800s/30min) — protects peak gains during
  choppy regimes the net-based daily stop misses. Counter persisted (restart-safe),
  resets on a win; Telegram 🧊 alert on trip.
- Banner now shows ask-range + breaker config. Compounding (v1.1) unchanged.
- New env: `CLEAN_LOSS_BREAKER, CLEAN_BREAKER_COOLDOWN`; changed `CLEAN_DRIFT_BPS`,
  `CLEAN_MIN_ASK`.

## v1.1.0 — 2026-06-19 — Compounding (bankroll-scaled sizing)
**Tag:** `cleanbot-v1.1.0` · **Status:** ✅ live

- **Bet size now scales with the bankroll** (was fixed 5 shares). Each bet =
  `CLEAN_KELLY_FRAC` of the live bankroll → the account compounds as it wins.
- **Half-Kelly default (6%).** Derived from 51 live trades: 65% WR, avg win
  +$1.95 / avg loss −$2.70, b=0.72 → full Kelly 16.4%; we run 6% (conservative,
  robust if true WR < 65%).
- **Bankroll tracked + persisted** in `clean_bot_state.json`, `+= pnl` each
  resolution. Seeded via `CLEAN_START_BANKROLL` (set to real balance: $48.60).
- **Risk caps:** per-bet ≤ `CLEAN_MAX_BET_PCT` (10%); total open exposure ≤
  `CLEAN_MAX_OPEN_PCT` (25%, limits correlated ETH+SOL stacking); daily stop now
  `CLEAN_STOP_PCT` (15% of bankroll, floor $6) — scales with the account.
- **`VERSION` constant** added — logged on startup, shown in Telegram.
- New env: `CLEAN_COMPOUND, CLEAN_START_BANKROLL, CLEAN_KELLY_FRAC,
  CLEAN_MAX_BET_PCT, CLEAN_MAX_OPEN_PCT, CLEAN_STOP_PCT`.
- Strategy/quality filters **unchanged** from v1.0 (ETH/SOL, early-drift ≥7bps,
  maker, 55–66¢).
- Analysis tool: `deep_analysis.py` (joins drift→fill→outcome; coin×dir, drift,
  entry, EV, Kelly).
- **Performance basis:** 51 trades, 65–67% WR, +$15–22 (account ~$28 → ~$49).
  Edge concentrated in SOL-UP (92%); ETH is marginal; cheap entries (≤62¢) win.

## v1.0.0 — 2026-06-18 — Initial clean rebuild
**Tag:** `cleanbot-v1.0` · **Status:** baseline (pre-compound rollback point)

- New `clean_bot.py` — minimal single-purpose **early-drift** trader, replacing
  the gate-paralysed `run_bot.py` (~10 stacked gates that stopped it trading).
- Edge: first ~5 min of a 15m window (T≥600s), if price drifted ≥7bps from the
  window-open strike, bet that direction. Maker-first (rest 1¢ below ask, 0 fee),
  ask ≤66¢ only, ETH/SOL, fixed 5-share size, $6 net daily stop.
- Reuses proven infra: OrderManager CLOB client, market_data, binance feed,
  Ireland proxy, gamma (Chainlink) resolution. Restart-safe state, dry-run mode.
- Telegram notifications (startup/fill/win/loss/stop). Dashboard 🤖 CleanBot tab.
- Docs: `CLEANBOT.md`. Analyzer: `clean_analysis.py`.
- **First profitable session:** 48 trades, 67% WR, +$22.50 real on-chain.
