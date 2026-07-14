# CleanBot Changelog

Every change to CleanBot gets: (1) a `VERSION` bump in `clean_bot.py`, (2) an
entry here, (3) a git tag `cleanbot-vX.Y.Z`, (4) a push to GitHub. The running
version is logged on startup and shown in Telegram + the dashboard, so you always
know exactly what's live. Roll back with `git checkout cleanbot-vX.Y.Z`.

Versioning: **MAJOR.MINOR.PATCH** — MAJOR = strategy change, MINOR = new
feature/knob, PATCH = fix/tuning.

---

## v1.58.1 — 2026-07-14 — dir-vote flip-only + visible ask-band skips
**Tag:** `cleanbot-v1.58.1` · **Branch:** `yirok-cleanbot-grok` · **Status:** deploy

Window-coverage audit: **not missing 15m windows**; quiet was filters + silent
returns after dir-vote. Fixes:

1. **Multi-signal dir vote only on `lead=flip`** (no more grow+roc-fight spam / dead-ends).
2. **Reverse-underway on grow:** still can re-point via roc (`revAsDir`); if corrected ask
   bad → fall back to late and trade when late ask is in band.
3. **Log once per window:** `[LATE SKIP] … ask_out_of_band after dir-fallback …` so we never
   look "blind" when both sides are outside 55–70¢.
4. Throttle `[LATE DIR]` / fallback logs to **one per coin/window**.

### Rollback
```bash
git checkout cleanbot-v1.58.0 -- clean_bot.py
# or env: CLEAN_LATE_DIR_VOTE=off
```

## v1.58.0 — 2026-07-14 — late direction vote (fix side, do not cut frequency)
**Tag:** `cleanbot-v1.58.0` · **Branch:** `yirok-cleanbot-grok` · **Status:** deploy

Owner: flip losses were **wrong direction**, not “too many bets.” Do **not** block
flip / red-EV / reverse setups — **re-detect direction** and still trade.

1. **`CLEAN_LATE_DIR_VOTE=on`** — on `lead=flip` (or reverse-underway), multi-signal vote:
   late drift + **early snap** (heavier on flip) + denser CL **roc** + BTC + soft flow.
2. **`CLEAN_LATE_REV_AS_DIR=on`** — reverse-underway no longer default-SKIPs; it **points
   the bet with roc** when the corrected side ask is still in 55–70c band.
3. **Fallback (no overblock):** if corrected side ask is out of band → keep late side.
4. **`CLEAN_LATE_DIR_MIN_ASK=0.45`** — only for vote-corrected opposite side (underdog often
   the right token on bad flips); normal late band still 55–70c for uncorrected joins.
5. **Roc reliability:** lookback fallback 60→45→30 via `_roc_strict` (span ≥55% of window).
6. Logs: `[LATE DIR] … late=DOWN→UP …` and ENTER tag `[dirfix …]`.

### Rollback
```bash
git checkout cleanbot-v1.57.0 -- clean_bot.py
# or env:
CLEAN_LATE_DIR_VOTE=off
CLEAN_LATE_REV_AS_DIR=off   # restores v1.52 skip-on-reverse
```

## v1.57.0 — 2026-07-14 — denser Chainlink path for roc (timing upgrade; Tor kept for CLOB)
**Tag:** `cleanbot-v1.57.0` · **Branch:** `yirok-cleanbot-grok` · **Status:** live

Highest-value timing fix from speed audit: RTDS alone ~7 ticks/120s → reverse-underway
often `roc60=n/a`. **Not** an HFT race; densify settlement-family path samples.

1. **Start `chainlink_onchain` densify poller** (default **1s** Polygon RPC, **no Tor** —
   RPCs already bypass proxy). CLOB/orders still use Tor/proxy for geo-block.
2. **`chainlink_ws.get_ticks` merges RTDS + on-chain** history (`CHAINLINK_MERGE_ONCHAIN=on`).
3. On-chain tick store every **1s** (was 5s dedupe); larger buffers.
4. Heartbeat **`[CL-TICKS] ETH=n/120s SOL=…`** — expect denser counts after warm-up.
5. Reverse-underway logs **n_ticks=** when firing/failing open.

**Tor/proxy unchanged** for Polymarket CLOB (IP restriction). Do not route Polygon RPC via Tor.

## v1.56.0 — 2026-07-13 — mild de-overblock: ask 0.68, flip 3bps, one FOK retry
**Tag:** `cleanbot-v1.56.0` · **Branch:** `yirok-cleanbot-grok` · **Label:** `deoverblock-v156`  
**Status:** live audition (more volume, same quality core)

Overblock audit (log + research): ~7.6 skips/enter; thin-flip@5bps and max_ask=0.66 cut
+EV rows; FOK fail ~21% of enters (book empty). Package:

1. **`CLEAN_LATE_MAX_ASK=0.68`** (was 0.66) — middle step toward 0.70 research sweet spot.
2. **`CLEAN_LATE_FLIP_MIN_BPS=3`** (was 5) — fewer false skips; still blocks ultra-thin flips.
3. **`CLEAN_LATE_FOK_RETRY=on`** — one retry at **refreshed** ask after FOK kill/unfilled
   (`[LATE FOK RETRY]` / `[FILLED TAKER RETRY]`). Still no resting maker.

**KEPT (do not loosen):** early-require, CL-only spot, fade skip, reverse-underway, EV-gated
min size, late max USD.

### Rollback if anything goes wrong
```bash
# code
git checkout cleanbot-v1.55.0 -- clean_bot.py market_data.py chainlink_ws.py
# or full tag
git checkout cleanbot-v1.55.0

# env (safe known-good knobs)
CLEAN_LATE_MAX_ASK=0.66
CLEAN_LATE_FLIP_MIN_BPS=5
CLEAN_LATE_FOK_RETRY=off
```
Then scp + watchdog restart. Env backup on EC2: `.env.bak_v156`.

See also: `ROLLBACK_v1.56.md`

## v1.55.0 — 2026-07-11 — higher-quality settlement data (Chainlink-first everything)
**Tag:** `cleanbot-v1.55.0` · **Branch:** `yirok-cleanbot-grok` · **Status:** live

Data-source clean-up so direction/metrics match Polymarket settlement (Chainlink):

1. **Late direction: Chainlink spot only** — no Binance fallback (`CLEAN_LATE_REQUIRE_CL_SPOT=on`).
2. **Reverse-underway: CL ticks only** by default (`CLEAN_LATE_ROC_CL_ONLY=on`); logs sparse-tick fail-open.
3. **Research purity tags:** `strike_source`, `spot_source`, `roc_source`, `sigma_source`, `feed_ok`
   (CSV schema rotated to `*.pre_v155` once). Prefer CL for research roc/sigma.
4. **`chainlink_ws.get_realized_vol`** + larger tick buffer (5400) for denser CL history.
5. **`market_data.spot_source`** field on MarketInfo.
6. **Early snaps on disk** (`data/late_early_snaps.json`) so require_early survives restarts.
7. Late enter logs **BEwr / wins-needed-per-loss** geometry.

## v1.54.0 — 2026-07-11 — stop losses wiping wins (geometry + thin-flip + true min size)
**Tag:** `cleanbot-v1.54.0` · **Branch:** `yirok-cleanbot-grok` · **Status:** live

Today live (post v1.50–1.53): ~2W/2L but net **−$5** because favorite geometry:
- SOL UP @70¢ ×7 win **+$2.00**
- ETH DOWN @66¢ ×6 loss **−$4.05**
- ETH UP @62¢ ×5 loss **−$3.18** (lead=**flip**, only +3.0 bps)

Fixes:
1. **True MIN size** when late EV not green: exact `CLEAN_SHARES` (was still × SOL cmult 1.5 → 7sh).
2. **`CLEAN_LATE_MAX_ASK=0.66`** (was 0.70) — better win/loss $ ratio; still in verified band.
3. **`CLEAN_LATE_MAX_USD=3.50`** hard notional cap per late fill.
4. **`CLEAN_LATE_FLIP_MIN_BPS=5`** — skip early→late flips with |drift| < 5 bps (thin wire bait).

## v1.53.0 — 2026-07-11 — join-quality + EV-gated compound (direction research)
**Tag:** `cleanbot-v1.53.0` · **Branch:** `yirok-cleanbot-grok` · **Status:** live

Deep research (7094 research rows): direction is NOT random (late 55–70 OOS EV +0.10) but
join *quality* varies hard. Shipped frequency-preserving fixes:

1. **`CLEAN_LATE_REQUIRE_EARLY=on`** — skip late if no early research snapshot (that bucket
   was edge **−7.5 / EV −0.075**). Fail-closed after restarts that miss early capture.
2. **`CLEAN_LATE_COINS=SOL,ETH`** — drop BTC late (EV ≈ 0 in-band). SOL/ETH carry the edge.
3. **`CLEAN_COMPOUND_MIN_EV=0` @ n≥15** — size-up (Kelly) only when rolling late EV/$ > 0;
   otherwise **flat 5sh min**. Stops compounding into a red live meter.
4. **`CLEAN_LATE_GROW_MULT=1.25`** — soft size boost when early→late lead **grew** (+21 pts
   shadow); not a hard skip of flips (still +14).
5. Richer `[LATE ENTER]` diagnostics: `lead=grow|fade|flip`, `size=CMPD|MIN`, `lateEV=`, `roc60=`.

Keeps v1.50 FOK + v1.52 reverse-underway + fade skip.

## v1.52.0 — 2026-07-11 — skip reverse-underway + faster gamma resolve
**Tag:** `cleanbot-v1.52.0` · **Branch:** `yirok-cleanbot-grok` · **Status:** live

Triggered by first post-v1.51 live late: **ETH DOWN @66c x6** (11:12 Lima, drift −5.5bps,
T=178s, FOK filled) → wire **UP** last seconds. Forensics:
- Direction at signal was **correct** (price below strike). Not a sign bug.
- Early→late lead did **not** shrink (−5.2 → −5.5bps) so skip-fading correctly allowed it.
- Soft near-money leads still +EV in shadow; the hole is **momentum already fighting the lead**.
- Research: late 55–70 with roc60 **opposing** n=25 WR 64% edge **−0.2** (toxic) vs keep n=165
  WR 77% edge +11.6; OOS keep still +11.3. Frequency cost small.

Fixes:
1. **`CLEAN_LATE_ROC_OPPOSE=on`** (default): skip late entry when last 60s settlement-feed ROC
   opposes the lead by ≥2bps (`[LATE SKIP] … reverse-underway`). Fail-open if no ticks.
2. **`gamma_winner`**: if `closed=true` is empty but open market already shows ≥0.99, resolve
   (this loss sat filled with Up=0.995 while closed=true returned []).

## v1.51.0 — 2026-07-11 — recovery sizing: compound + SOL tilt ($46→$100 goal)
**Tag:** `cleanbot-v1.51.0` · **Branch:** `yirok-cleanbot-grok` · **Status:** live

Frequency-preserving path to rebuild bankroll (no new skip-filters):
1. **Late uses compound Kelly** via `_late_size_shares` (was hard-coded flat 5sh even when
   `CLEAN_COMPOUND=on`). Stakes scale with bankroll; floor remains exchange min 5sh.
2. **Per-coin size tilt** `CLEAN_LATE_COIN_MULT` (default `SOL=1.5,ETH=1.0,BTC=0.5`) — research
   late 55-70c: SOL EV/$ +0.135, ETH +0.085, BTC ~0. Still trades all coins; re-ranks dollars
   toward SOL. Cap re-applied at `CLEAN_MAX_BET_PCT` after tilt.
3. **Target milestone** `CLEAN_TARGET_BANKROLL=100` — logs/Telegram when crossed.

Live env intent: `CLEAN_COMPOUND=on`, `CLEAN_KELLY_FRAC=0.08`, `CLEAN_MAX_BET_PCT=0.12`,
`CLEAN_LATE_COIN_MULT=SOL=1.5,ETH=1.0,BTC=0.5`. Keeps v1.50 FOK taker + fee-honest EV.

## v1.50.0 — 2026-07-11 — true late TAKER (FOK) + fee-honest EV + partial-fill tracking
**Tag:** `cleanbot-v1.50.0` · **Branch:** `yirok-cleanbot-grok` (Grok / yirok CleanBot line) · **Status:** live audition

Audit fixes (no new signal / no new filter):
1. **Late taker is now real FOK.** v1.46 priced at the ask but still posted **GTC** — if the ask
   walked, the order could rest as a bid and re-introduce maker adverse selection. v1.50 posts
   `OrderType.FOK` at the signal ask: fill now or `[LATE MISS]` and retry next scan while still
   in the late band. Maker/GTC path remains behind `CLEAN_LATE_TAKER=off`.
2. **Taker buy fee in settlement + EV/$.** Crypto fee `0.07*p*(1-p)` per share is stored on the
   fill and subtracted from resolve PnL; stake for EV/$ includes the fee. Makers stay fee=0.
3. **Partial GTC fills stay tracked.** `check_orders` no longer drops an oid on first partial
   match (that left residual size untracked). Updates the position as matched grows; cancel-race
   re-verify unchanged.

Logs to watch: `[LATE ENTER] … TAKER/FOK`, `[FILLED TAKER]`, `[LATE MISS]`, `[FILLED-PARTIAL]`.

## v1.49.0 — 2026-07-10 — mid-window shadow capture (the never-measured 3.5-9min zone)
**Tag:** `cleanbot-v1.49.0` · **Status:** ✅ live · shadow measurement only, zero trading change

Owner: "focus more on late entry — measure and bet more." Late live frequency is already at the
max of its VERIFIED territory (band/floor/corr/coins all data-bounded), so the honest expansion
is measurement: NEW `phase='mid'` research snapshots at t_rem 210-540s — a zone with ZERO
existing rows. If fresh mid-window leads carry the late-style edge, that's a second late-type
engine (~doubles candidate flow). Verifier gate at n≥80 as always. ALSO this morning: **EARLY
ENGINE RETIRED** at n=34/40, EV −0.177 (mathematically unable to reach −0.03 even winning out —
verdict executed early, mate-is-forced). Live trading = LATE engine only (07-24 Lima, taker).

## v1.48.0 — 2026-07-10 — session study: late engine sleeps the Lima night (00-07)
**Tag:** `cleanbot-v1.48.0` · **Status:** ✅ live

Owner asked for the best hours after watching 3 straight days of overnight bleed + daytime
recovery ($45→$59 solo this morning). Session-block study (Lima time, BOTH-HALVES stability
check, both live universes): **EARLY-Z: all four blocks positive incl. NIGHT +2.9 (stable) —
early keeps 24h, cutting it would be overblocking. LATE: NIGHT 00-07 = +0.9pts (zero, and
taker fees make it net-negative) vs MORNING +6.6 / AFTERNOON +14.3 / EVENING +12.8.** So the
overnight killer was the late engine specifically. Fix: `CLEAN_LATE_NIGHT_OFF=on` — the late
engine skips 00:00-07:00 Lima (~26% of its volume at ~zero EV; removing zero-EV volume ≠
overblocking, same logic as the z<1 noise cut). Best sessions confirmed: EVENING 18-24 and
MORNING 07-12. Hour filters remain REJECTED for the early engine (old OOS failure stands).

> **Jul 9 14:01 env addendum (owner-directed):** `CLEAN_DAILY_STOP` 10 → **999** (daily stop
> disabled) + day counter reset — owner: "reset the stop loss, let the bot trade, I don't care
> about stop loss." Remaining brakes: 3-loss breaker cooldown + the engines' own n≥40 verdicts.

## v1.47.0 — 2026-07-09 — SELF-GOVERNANCE: the engine executes its own verdicts
**Tag:** `cleanbot-v1.47.0` · **Status:** ✅ live · owner: "I care about the engine — make it work"

The pre-registered 40-trade verdicts stop being advisory notes on the [TRACK] lines and become
ACTIONS the bot takes itself: per engine at n≥40 — EV/$ ≤ −0.03 → `[VERDICT:tag] ENGINE RETIRED`
(engine_off latches in state, owner reset required, Telegram alert); EV/$ ≥ +0.03 → scales its
own size x1→x2→x3 (measurement window restarts each step so the next verdict judges the new
size). engine_mult/engine_off persisted; enforced at all three entry paths. This is the
"dynamic bot that knows how to trade without being told" — measurement → judgment → action,
closed loop, no operator in the middle.

## v1.46.0 — 2026-07-09 — late engine goes TAKER (owner diagnosed the maker-fill reversal trap)
**Tag:** `cleanbot-v1.46.0` · **Status:** ✅ live

Owner: "we catch the GTC during the reversal — that's why it got filled." Measured: **live late
maker fills n=25: 52% WR vs 63c avg = −11pts** — versus the +12-16pt shadow edge, which was
measured AT THE ASK (taker). The maker order I used to save 1c only fills when someone dumps our
side = precisely during reversals; the execution choice inverted the verified edge's sample.
Fix: `CLEAN_LATE_TAKER=on` (default) — cross the spread at signal time, pay ~1c + ~1.6c taker
fee, receive the fill composition the edge was verified on. v1.45 A-S shading remains only for
the maker fallback path. This supersedes shading as the late engine's adverse-selection answer.

## v1.45.1 — 2026-07-09 — fix: engine tag dropped on fill (late/voldiv scored as 'early')
**Tag:** `cleanbot-v1.45.1` · **Status:** ✅ live · scoreboard-integrity fix

Owner's ETH DOWN review caught it: the 09:56 LATE loss printed `[TRACK:early]`. Cause:
`check_orders()` (and the FILLED-RACE path) rebuilt the position dict without the order's
late/voldiv flags, so every LIVE late/voldiv fill was attributed to the early scoreboard —
corrupting both engines' 40-trade verdicts. Fix: carry the tags through both fill paths.
ALSO ANALYZED (no change): should the late engine get the deep-storm brake? NO — late 55-70c
in storm regimes (gate < −6) historically runs +9.1pts (n=27) vs +4.5 healthy; the late edge is
storm-resistant (only ~2-3min left = little time for the chop to reverse a late lead). Today's
late loss = its normal ~25-30% loss rate, not a design flaw.

> **Jul 9 09:27 env addendum:** `CLEAN_SIG_MIN_EDGE` −2 → **−6** (owner-approved). Measured on the
> live z≥1.0 universe: stand-down windows still carry +2.3pts edge (z-bar supersedes the gate's
> old job); softening reclaims ~13% more early trades (~+3pt slice) while keeping the deep-decay
> (<−6, +1.5pts ≈ noise) crash brake. Per the owner's standing no-overblocking rule.

## v1.45.0 — 2026-07-09 — Avellaneda-Stoikov maker shading on the late engine (own-fill-calibrated)
**Tag:** `cleanbot-v1.45.0` · **Status:** ✅ live (owner directed) · execution-layer change, late engine only

Own-fill audit first (n=226 maker fills joined to window sigma): fill edge decays monotonically
with adverse-selection exposure σ√t_rem — LOW +5.6pts / MID +0.9 / HIGH +0.4 (terciles at
13.4/30.2bps). Exactly the A-S prediction: the more the price can move while our order rests, the
worse the fills we receive. Fix (A-S-style): `_late_entry` maker offset now scales with exposure —
base 1c + 1c per `CLEAN_LATE_SHADE_BPS`(15bps) of σ√t_rem, capped +3c (env `CLEAN_LATE_SHADE=on`).
Tradeoff: fewer fills, better-priced fills. Early engine untouched (test purity). Note: this
changes late-engine execution mid-audition — its [TRACK:late] verdict now measures the shaded
version. RS/YZ σ upgrade + meta-labeling sizer were tested offline the same day and REJECTED
(see memory/scripts); A-S was the one queue item that validated on our own data.

## v1.44.0 — 2026-07-09 — the disciplined reset: verified-only engines + per-engine scoreboards
**Tag:** `cleanbot-v1.44.0` · **Status:** ✅ live · owner delegated control; pre-registered test begins

Config: **VOLDIV OFF** (failed live audition: claimed ~70%, realized ~40% across 15 trades — the
market is the better calculator; stays shadow-loggable). **EARLY back ON in verified form only**:
z-bar (OOS z=+1.77 PASS), SIG gate −2, max_ask 0.70, `CLEAN_COMPOUND=off` → flat 5-share min-size.
**LATE stays ON** (best live performer). Code: per-engine `[TRACK:early|late|voldiv]` rolling
meters (WR + EV/$ per engine, tagged through recent_ev/day_results; old tuples backfilled
'mixed'), midnight `[SCORE]` scoreboard per engine, and the PRE-REGISTERED verdicts printed on
every meter line: at n≥40 per engine → EV/$ ≥ +0.03 SCALE-UP | −0.03..+0.03 keep min-size |
≤ −0.03 OFF permanently. No mid-test strategy changes, no same-day signal deploys (the kappa/
VOLDIV rushes cost real money — rule binds the operator too). Book at start of test: ~$66.

## v1.43.2 — 2026-07-09 — VOLDIV live forensic: kill the kappa term + book-sanity check
**Tag:** `cleanbot-v1.43.2` · **Status:** ✅ live · correcting two deploy errors found in live forensics

13 live VOLDIV trades: model claimed avg 70%, realized 38% (5W/8L, −$11.45; P(≤5|p=.70)≈1% — NOT
luck). Three causes identified: (1) **the v1.43 kappa momentum term amplified sparse-tick noise
into false extremes** (0.97/0.81/0.81 claims all LOST; my own test showed it worsened Brier
0.225→0.243 and I deployed anyway) → `CLEAN_VOLDIV_KAPPA=0` (pure vol-pricing restored).
(2) **overnight wide/stale books faked edges** (all 13 trades were 20:46–01:16 thin tape; asks
summing ≫1.0 make the \"market price\" garbage) → NEW book-sanity: require both asks and
lead_ask+dog_ask ≤ 1.06. (3) maker adverse selection (backtest fills at signal ask; live fills
only when the market comes to us) — noted, structural, mitigated by (2). Book at fix: $68.92.

## v1.43.1 — 2026-07-08 — kill-switch LATCH fix (fired 21:39, silently re-armed on ledger bounce)
**Tag:** `cleanbot-v1.43.1` · **Status:** ✅ live · integrity fix, disclosed to owner

Live logs caught it: ledger dipped to $79.60 at 21:36 (two VOLDIV losses while a winning position
was still open) → [KILL-SWITCH] fired 21:39/21:43/21:48 — but the v1.39 check was a bare
`bankroll <= floor` comparison, so when the pending win settled and the ledger bounced above $80,
the condition went false and trading silently RESUMED (21:48:29 VOLDIV ENTER, which won). Fix:
`self.killed` latch — once fired it persists in state and blocks all entries until the owner
explicitly resets ("killed": false in clean_bot_state.json + restart, or change the floor). NOT
retro-latched for the 21:39 transient (ledger-vs-chain dip while a win was pending; owner decides
whether it counts — book recovered to $82.75). VOLDIV live so far: 3W/2L, losses small (−$2.35/
−$2.60 vs old −$7s), the 97%-model SOL@79c win = the engine working as designed.

## v1.43.0 — 2026-07-08 — literature upgrades: vol-normalized early bar + momentum term in VOLDIV
**Tag:** `cleanbot-v1.43.0` · **Status:** ✅ live · both changes verified on our data before deploy

From the owner-requested research review (intraday TSM/momentum-reversal literature). (1) EARLY
BAR REDEFINED in the correct unit: `CLEAN_Z_BAR=1.0` — enter only when the move is significant vs
current volatility, zscore=|dist|/(σ√elapsed) ≥ 1.0, replacing the raw-bps bar (falls back to it
if σ unavailable). **OOS-verified: z≥1.0 windows carry the whole edge (+4.3pts vs price, z=+1.77
PASS, stable IS≈OOS) while sub-1.0 raw-bps windows are noise (−1.6 OOS).** ~61% of prior volume
kept. Early path remains PAUSED (SIG=999) — this readies it, doesn't re-enable it. Flow-
confirmation idea from the same literature was tested and REFUTED on our data (confirm≈oppose).
(2) VOLDIV fair-value model gains a momentum-persistence term: p=Phi((|dist|+κ·μ·t_rem)/(σ√t_rem)),
μ = recent 5-min drift rate projected on the lead, `CLEAN_VOLDIV_KAPPA=0.25`. **Tested κ∈{0,.25,.5,1}:
κ=.25 lifts OOS EV +$0.037→+$0.048/$, z +0.90→+1.50, +40% signals; κ=1 worse (momentum ~quarter-
persists).** Still audition-grade (<1.64) at min-size behind the same kill-floor.

## v1.42.0 — 2026-07-08 — Strategy #4 LIVE: vol-divergence engine (pricing edge) replaces early drift
**Tag:** `cleanbot-v1.42.0` · **Status:** ✅ live at midnight reset · min-size AUDITION, kill-floor $80

Owner: "implement the strategy now and go live now." From the Jul 8 accuracy audit
(`_accuracy_audit.py`): betting where the market price diverges ≥5% from the vol-priced
probability of the lead holding — p = Phi(drift/(σ√t_left)) — is the only signal that stayed
positive OOS (**EV +$0.044/$, z=+1.07, AUDITION grade below the 1.64 proof bar** — owner accepted
the risk explicitly; ~109 raw signals/day, avg price ~51c, bets BOTH leader and underdog so
win/loss payoffs are near-symmetric, unlike favorites). NEW `_vol_div_entry()`: chainlink-strike
only, t_rem 60-840s, ask band 25-90c, min-size (5sh), corr-sibling/opposite guards, shared
daily-stop/breaker/kill-floor, same GTC maker path. `CLEAN_VOLDIV=on`, threshold
`CLEAN_VOLDIV_MIN=0.05`, coins ETH/SOL/BTC. **Early drift path PAUSED again** (SIG=999): it was
the $7-compounding bleeder (Jul 8: 2W/4L −$7.5 vs late audition +$1.2 at min-size) — the
vol-divergence engine replaces it as primary. Late engine stays. Book $82.80 vs kill-floor $80:
the test survives ~1-2 losses max before the pre-committed stop fires — stated to the owner.

## v1.41.0 — 2026-07-07 — PRODUCT phase 1: capture Binance book-imbalance (microstructure signal)
**Tag:** `cleanbot-v1.41.0` · **Status:** ✅ live · shadow data-enrichment, zero trading-logic change

Owner pivoted to building a real product: improve predictions by enriching the DATA (our features
were starved — flow60 only 37% populated, book depth 0%). Predictions can't beat the market on
crude price-only features; the edge, if any, is in microstructure that leads the settlement price.
Phase 1: NEW Binance `@bookTicker` subscription in `binance_ws.py` (added alongside @aggTrade, fully
isolated in `_on_message` — early-return branch, own try/except, cannot disturb the tick/flow path).
`get_book_imbalance(coin)` = top-of-book (bid_qty−ask_qty)/(bid_qty+ask_qty) ∈ [−1,+1] — resting
bid-vs-ask SIZE pressure, a leading directional signal. Logged as new `book_imb` research column.
bookTicker updates on every book change (high-freq, reliable even when trades are sparse). This is
DATA CAPTURE only — no change to entry logic. Roadmap: (1) this, build ~1-2wk dataset → (2) model
book_imb + flow + roc walk-forward vs the MARKET price benchmark → (3) deploy only if it beats the
market OOS → (4) refactor to clean product. Next enrichment candidates: fix flow60/roc60 coverage,
add funding/OI/liquidation features.

## v1.40.0 — 2026-07-07 — live accuracy+EV meter + widen the cap (bet constantly, measure it)
**Tag:** `cleanbot-v1.40.0` · **Status:** ✅ live · owner's "bet constant, measure accuracy, compound" philosophy

Owner: stop capping/gating — bet constantly, measure our real accuracy/EV, and even a small
constant edge compounds. Two changes: (1) NEW `[TRACK]` log after every resolution — rolling
WR + **realized EV/$ (net PnL per $ staked)** over the last up-to-100 trades, flagged
COMPOUNDING vs break-even/bleeding. EV/$ is the honest compounding rate (WR alone lies at favorite
prices). Persisted across restarts (`recent_ev` in state). This is the instrument that tells us —
live, not in theory — whether constant-betting is net-positive. (2) `CLEAN_MAX_ASK` 0.70→0.85 so
it bets the fuller favorite range (data: 70-80c +0.032/$, 80-95c +0.028/$ are positive) instead of
capping. Signal-health gate stays (verified) but the marginal caps loosen. The meter now arbitrates:
if [TRACK] EV/$ holds positive over 50-100 trades, the constant-bet approach works and we scale
size; if it sits at/below zero, we have the honest verdict in real time.

## v1.39.1 — 2026-07-07 — late-window: add the correlation guard (owner caught "3 all DOWN all lose")
**Tag:** `cleanbot-v1.39.1` · **Status:** ✅ live · risk-concentration fix

Owner spotted the bot betting BTC+ETH+SOL all DOWN in the SAME window, all losing together. Root
cause: `_late_entry` had NO correlation guard (the early `scan` path has full corr control since
v1.15/v1.31, but it was never added to the late path built in v1.38). The 3 coins move ~0.85
together, so 3 same-direction late legs in one window = ONE 3x directional bet — a single wrong
call loses all three (~$10) at once. Fix: reuse `_corr_sibling`/`_corr_opposite` in `_late_entry`
— skip a late leg if a correlated coin is already bet the same direction this window, or opposite
a held sibling (divergence coinflip). Now at most one late leg per window/direction. NOT a
direction-detection failure (signal is 65-71% right); it was risk concentration. Reduces late
frequency slightly but removes the correlated-cluster loss.

## v1.39.0 — 2026-07-07 — late-window done right (settlement-feed fading filter) + hard kill-switch
**Tag:** `cleanbot-v1.39.0` · **Status:** ✅ deployed, late_live OFF pending the $100 deposit

The fix for v1.38.2's broken roc60 filter (data present only ~30%). NEW `late_skip_fading`
measures the leader's trajectory from the window's OWN early→late Chainlink snapshots (drift_pct,
96% coverage — reliable) instead of sparse Binance ticks. Skips FADING leaders (favorite still
ahead but its lead SHRANK, same direction — the weak 68% bucket); keeps growing + reversed leads.
Verified on real data: whole band 76.5% WR / EV +0.167 → **skip-fading 81.5% / EV +0.244, and it
HOLDS RECENT (last-30%: WR 80.0%, EV +0.202, z=+1.27)** — not an in-sample artifact. ~10 SOL/XRP
setups/day. Old roc60 `late_mom_agree` deprecated (default off). ALSO: `CLEAN_KILL_FLOOR` — a hard
pre-committed drawdown floor; below it ALL trading stops permanently until the owner resets (the
deposit-test kill-switch; e.g. 70 on a $100 book = max −$30). This is the version the $100 deposit
funds: late-window SOL/XRP, skip-fading, min-size (~3% at $100), early path still paused, kill-
switch armed. GO sequence: owner deposits → set CLEAN_KILL_FLOOR=70, CLEAN_LATE_LIVE=on, restart.

## v1.38.2 — 2026-07-07 — late-window: skip FADING leaders (owner caught it; verified OOS)
**Tag:** `cleanbot-v1.38.2` · **Status:** ✅ live · quality+frequency win

Owner flagged an `XRP UP` entry "where clearly the direction was down." Correct catch: the late
strategy bets the LEADER (spot vs strike) to hold, and this one was a *fading* leader (spot above
strike but 60s momentum falling). Split of late 55-70c by momentum-vs-leader: **WITH momentum →
84.1% WR, OOS EV +0.335 (z=+2.65); AGAINST (fading) → 72.5% all but OOS EV −0.031 (negative)**.
So fading-leader bets are the negative-OOS half. Fix: `CLEAN_LATE_MOM_AGREE` (default on) — only
enter when 60s `_roc` agrees with the leader direction; fail-open if tick data missing. Keeps the
strong half (momentum-aligned, ~7.2 SOL/XRP tradeable/day — still > the old 4.8) and drops exactly
the trade the owner objected to. Net vs original: MORE trades AND higher quality. Still min-size,
SOL/XRP, 55-70c, shares daily-stop/breaker; early path paused.

## v1.38.1 — 2026-07-07 — late-window fix: drop the drift floor (deployed filter ≠ verified edge)
**Tag:** `cleanbot-v1.38.1` · **Status:** ✅ live · frequency fix, verified OOS

Owner flagged "almost no trades since 9pm." Root cause: `_late_entry()` copied the EARLY path's
`drift≥5bps` floor, but the late "momentum-into-close" edge is **drift-INDEPENDENT** and the
verification (n=80, z=+2.50) was measured with NO drift filter. Split proves it: late 55-70c
**drift<5bps → OOS z=+1.53, EV +0.159 (n=67, STRONGER)** vs drift≥5 → OOS z=+0.66 (n=28, weaker).
So the floor discarded ~2/3 of the verified windows — the biggest, best slice. Fix: new
`CLEAN_LATE_DRIFT_BPS` (default **0** = no floor); the 55-70c ask band already selects "modest
favorite," and a small late lead holds ~78% regardless of drift magnitude (the edge is the
favorite being underpriced with time nearly out, not momentum). Impact: tradeable SOL/XRP late
windows **4.8/day → 15.5/day (3.2×)**. Still SOL/XRP-only, min-size, 55-70c, shares daily-stop/
breaker; early drift path still paused. Overnight will stay quiet regardless — 97% of overnight
late windows are one-sided-book or favorite >70c (strong-trend hours); the edge lives in active/
choppy hours. Separately noted (not changed): late maker fills race the 90s-to-close cancel on
thin books (XRP no-fill 22:42 Jul6) — candidate T≥135 entry-timing tweak still open.

## v1.38.0 — 2026-07-06 — Strategy #3 goes LIVE: late-window "momentum-into-close" micro-audition
**Tag:** `cleanbot-v1.38.0` · **Status:** ✅ live audition (SOL/XRP, min-size) · early path still paused

Owner directed starting the late-window strategy live now. It cleared its AUDITION gate today:
`_late_verify.py` band 55-70c → **n=80, WR 78.8% vs 65.4% BE, z=+2.50, EV/$ +0.133; OOS(30%)
n=24 z=+1.40 EV/$ +0.136** → verdict AUDITION (small-size live OK). Edge concentrated in
**SOL (23-5, 82%)** and **XRP (22-4, 85%)**; ETH weak (10-6, 63%) → excluded. NEW `_late_entry()`
places ONE minimum-size (5-share) maker per window in the last ~3min (t_rem 60-210s), established
move (drift≥5bps), fav-ask 55-70c, on `CLEAN_LATE_COINS=SOL,XRP`. Routes through the existing
GTC/fill/settlement plumbing (incl. the phantom-fill re-verify). **Deliberately independent of the
early signal-health gate** — it's a distinct edge that trades even while early stands down — but
the loop only calls it when NOT daily-stopped and NOT in breaker cooldown, so it shares those risk
controls. Env: `CLEAN_LATE_LIVE=on`. Early drift path stays PAUSED (`CLEAN_SIG_MIN_EDGE=999`,
proven negative-EV OOS). This is a small-size AUDITION, not a full deploy — DEPLOY gate is OOS
n≥80 (~1wk out); size stays at the 5-share floor until then. Sizing/deposit decisions unchanged.

## v1.37.0 — 2026-07-06 — Monday resume: unpause + retire the DAY-TREND gate (failed verification)
**Tag:** `cleanbot-v1.37.0` · **Status:** ✅ live · env-only behavior change, no code edits

Monday review outcome. (1) **Unpause**: `CLEAN_SIG_MIN_EDGE` back to `-2` — the walk-forward-
verified signal-health gate resumes control (trades when rolling edge > −2pts, stands down
otherwise). Weekend evidence says this is exactly the right tool: daily edge was +1.2 (Jul 4),
**−10.4 (Jul 5)**, +0.6 (Jul 6 am) — the regime is trading in and out of health, so a static
pause and a naive full-resume are both wrong; the gate arbitrates window by window.
(2) **DAY-TREND gate retired** (`CLEAN_DAY_TREND=off`): its queued verification FAILED.
Counterfactual join of 223 unique [DAY-TREND SKIP] windows to research outcomes: the skipped
in-band rows would have won **67.6% vs 61.9% BE (+5.7pts, n=71, z=+0.99)** — the gate was
blocking above-break-even trades and never showed harm-prevention. Redundant with the
signal-health gate for regime protection. Same fate as the adaptive drift-bar, same reason.
Kept: max_ask 0.70 (bankroll < $35), daily stop $5, ER deep-chop guard, trend guard.
Gates checked and NOT passed today (keep collecting, no deployment): BTC coin gate z=+0.84
(needs ≥1.64, n=102); late-window 55-70c audition **z=+2.29 but n=62 < 80**; Strategy #2
n=46 settled < 80 (5-8% band still positive ≈ +$0.47/$, ≥8% band still toxic −$0.41/$).

## v1.36.1 — 2026-07-04 — [SIG] heartbeat logging (retro-entry; shipped with Dashboard v3.0)
**Tag:** `cleanbot-v1.36.1` · **Status:** ✅ live

`[SIG] edge=±X.Xpts` logged every 40 scans so the dashboard and post-hoc audits can chart the
rolling signal-health value continuously (previously only visible when the gate fired).

## v1.36.0 — 2026-07-04 — late-window shadow capture: auditioning "momentum-into-close"
**Tag:** `cleanbot-v1.36.0` · **Status:** ✅ live (bot still trade-paused) · shadow data only

From the owner's Novals83/5min-btc-polymarket repo review. Its thesis (enter with ~2min left
after a strong established move; fee curve favors it — taker fee 0.07·p·(1−p) is tiny at extreme
prices) is the INVERSE of our verified early-entry finding, but our "late" cut was measured on
NORMAL drifts; late-after-STRONG-move was flat (72% vs 72% BE, n=29) — unproven, not disproven.
NEW: `_research_scan(coin, phase='late')` snapshots every window a second time in the last
~2-3min (t_rem 60-210s) with full features; `phase` column added (early/late), CSV migrated.
The trade-paused weekend collects this for free. Verifier decides at n≥80 whether late+strong
becomes strategy #3. Also: 50%-of-allocation sizing in that repo = never copy; micro-hedge =
EV-neutral theater at our size.

## v1.35.2 — 2026-07-03 — snapshot XRP strikes (owner spotted per-scan fallback log spam)
**Tag:** `cleanbot-v1.35.2` · **Status:** ✅ live · shadow-data quality + log hygiene

Owner spotted repeating `[STRIKE] XRP ... mid-window cache miss → Binance kline open` every ~7s.
Cause: `_snapshot_strikes` covered CFG.coins + BTC but never XRP (added as a research coin
v1.24), so every XRP research scan fell back to a Binance-kline strike (small Chainlink basis
taints XRP's drift_correct labels) and logged twice per scan. Chainlink RTDS carries XRP fine —
fix: snapshot loop now includes research_coins. Trading was never affected (XRP never trades;
ETH/SOL snapped correctly; the [STRIKE SKIP] guard hard-blocks non-Chainlink strikes anyway).

## v1.35.1 — 2026-07-03 — small-book geometry: cap ask at 70c while bankroll < ~$35
**Tag:** `cleanbot-v1.35.1` · **Status:** ✅ live · from the owner's (justified) 73c fury

The day's only trade — SOL UP @73c after the deadlock ate 33 setups — lost −$3.65 (18% of the
book). The owner's instinct is mathematically right AT THIS BANKROLL: Kelly geometry ≠ arithmetic
EV. With the 5-share floor forcing ~18%/bet at $20: a 73c entry at the band's measured 77% WR
compounds at **+0.35%/trade (≈zero)** and turns NEGATIVE below 75% WR; a 60c entry compounds
~+1.8%/trade at 70% WR. The verifier passed 55-74c on per-$ EV — valid — but forced-size
geometric growth is a second test that the 70c+ slice FAILS while the book is small.
FIX: max_ask 0.74→0.70 (env + default). RESTORE 0.74 at bankroll ≥ ~$35 (forced fraction ≤10%,
where the full band's geometry is positive again). Day post-mortem: the real thief was the
adaptive-bar deadlock (33 REGIME SKIPs, killed in v1.35.0); morning stand-down (22) was correct
(tape −12pts); DAY-TREND gate blocked 1 setup — queued for weekend verification, not reactively
removed.

## v1.35.0 — 2026-07-03 — retire the adaptive drift-bar (owner caught a DEADLOCK blocking trades)
**Tag:** `cleanbot-v1.35.0` · **Status:** ✅ live · owner: "something is blocking trades, clear moves, no entries"

Owner was right. `[REGIME SKIP] SOL drift=-11.0bps < bar 12bps` — the v1.8 ADAPTIVE bar had
raised the entry requirement 5→12bps because last night's bad-tape losses froze the rolling WR
at ~47%. DEADLOCK: the bar only relaxes when trades WIN, but trades can't happen because of the
bar — so it stayed locked on stale losses while the signal-health gate (10x data, updates while
flat, walk-forward verified z=+2.50) correctly read the recovered tape at +10. Real in-band
11bps directional moves were being refused. The bar is SUPERSEDED by the gate (same principle —
"measure accuracy, adjust" — strictly better instrument), and the OOS-verified config (d≥5,
z=1.68) was validated WITHOUT the penalty. FIX: `CLEAN_ADAPT_K` 35→0 (bar = base 5bps always);
outcome recording + [ADAPT] dashboard logging stay. This was the LAST legacy per-trade
throttle — regime response now lives in exactly one place: the signal-health gate.

## v1.34.0 — 2026-07-03 — HMM regime detector (SHADOW): testing the "quant desk" regime layer
**Tag:** `cleanbot-v1.34.0` · **Status:** ✅ live · zero trading impact — research logging only

Owner shared an HMM regime-detection framework (Horizon marketing post; research/ has the MD +
images). Assessment: the CONCEPT is legitimate quant methodology (regime-switching, Hamilton
1989) and matches our own Jul-3 diagnosis (signal +12→−12pts = regime break); the post's 91-pt
"proof" is a strawman (a Donchian short bleeding through a 7-yr BTC uptrend). We already run
three regime detectors (ER, macro trend guard, signal-health gate — the last walk-forward
VERIFIED z=+2.50). An HMM could still add value: probabilistic (smooth sizing vs binary gates)
and potentially faster flips (return/vol distributions can shift before realized drift-accuracy
does). So: new `hmm_regime.py` — 3-state GaussianHMM (TREND/CHOP/PANIC) on 15m log-returns+vol,
rolling ~9-day fit per coin, refit 6h, posterior cached 2min (hmmlearn 0.3.3, installed
--break-system-packages). Logged per research row as `hmm` = 'T0.62/C0.31/P0.07'. DEPLOY RULE:
it trades ONLY if the verifier shows it beats or adds to ER + signal-health OOS (n≥80, z≥1.64).
CSV migrated (hmm column). No entry-path changes.

## v1.33.0 — 2026-07-03 — SIGNAL-HEALTH gate: trade only when the market-wide signal is winnable
**Tag:** `cleanbot-v1.33.0` · **Status:** ✅ live · full audit of the $40→$20 drawdown (owner demand)

AUDIT VERDICT: nothing in the bot broke — deep-chop guard fired 6×, daily stop halted at −$12.65
(overshoot past $8 = two in-flight positions, structural), accounting honest. What broke is THE
MARKET'S SIGNAL: drift accuracy across ALL logged in-band windows (traded or not) collapsed
Jun27–Jul1 68-76% vs ~64% BE → Jul 2 64% (break-even) → **Jul 3 57% vs 66% BE (−9pts,
ANTI-predictive)**. Today's 8W/10L (44%) is a −3σ event under the verified edge — not luck, a
regime break (July-4th holiday tape and/or edge decay; 2 days can't distinguish). One loss came
at er=1.00 (perfect trend, still reversed) — NO entry filter survives an anti-predictive tape.
FIX: `_signal_health()` — rolling (WR − break-even) over the last CLEAN_SIG_WINDOW (40) resolved
in-band research rows (~10h of ALL windows, ~10x trade-sample power, cached 240s); entries stand
down while edge < CLEAN_SIG_MIN_EDGE (−2pts) with `[SIGNAL-HEALTH]` log. Research logging never
stops, so recovery is detected while flat and trading auto-resumes at FULL frequency — the gate
adds no entry selectivity, it only refuses to play an unwinnable game. Also CLEAN_DAILY_STOP 8→5
while the book is ~$20 (was 40% of book). Deposit decision: HOLD until the signal is back over
break-even. If the signal doesn't recover post-holiday, this is FaithBot-style edge decay and the
honest conversation is strategy-level, not tuning.

## v1.32.0 — 2026-07-03 — DEEP-CHOP guard: the n=375 refinement of the v1.29 ER call
**Tag:** `cleanbot-v1.32.0` · **Status:** ✅ live · overnight bleed post-mortem (owner report)

Overnight Jul 2-3: 11W/8L (58%) vs ~63% BE, wallet $37→~$27. Losses alternated UP/DOWN with
wins — not directional failure (trend guard fine), but CHOP grinding both sides. This is new OOS
data on the v1.29 ER-retirement (which was decided on n=88). Re-test at n=375 REFINES it:
trend (er≥0.32) 65.7% vs 64.2% (thin+); **mid-chop 0.15-0.32 = 73.1% vs 63.9%, z=+1.95 (the
sweet spot — v1.29 was RIGHT to unblock it)**; **DEEP chop er<0.15 = 60.6% vs 64.3% (below
water — tonight's bleed, er 0.04-0.12 on several losses)**. Also re-tested the expensive tail
(68-74c): 76.8% vs 70.6% BE, z=+2.16 — clean; the 70/73c losses were regime, not price.
FIX: new `CLEAN_ER_DEEP` (0.15) — skip entries when er<0.15 (`[DEEP CHOP SKIP]`), a hard skip
only in the statistically-dead zone; mid-chop stays open. The old blunt 16bps bar stays retired.

## v1.31.0 — 2026-07-02 — same-direction ETH+SOL pairs auto-unlock at $55 bankroll
**Tag:** `cleanbot-v1.31.0` · **Status:** ✅ live (dormant until $55) · owner asked to pair-bet for faster compounding

Owner: can we bet ETH and SOL in the same window to compound faster? Data: same-direction legs
are BOTH +EV (aligned 69% WR vs ~64% BE, n=884) — the block was never about EV, it's risk
concentration (~0.85 correlation → a pair = one 2x bet; a paired loss = −$6.50). At $35 that's
18% of book on one bet (~1.7× half-Kelly) and 81% of the $8 daily stop → one bad window ends the
day. At $55+ a pair = ~12% (the bot's own sizing policy) and survivable. FIX: new
`CLEAN_CORR_FULL_AT` (55): when bankroll ≥ $55, same-direction pairs trade BOTH legs full-size;
below it, the existing half/skip logic stands. Auto-unlocks as the book grows — no manual flip.
OPPOSITE-direction pairs stay blocked forever (verified 55% coinflip, n=168). Note: the bigger
compounding unlock is BTC (shadow n=65/80 at 75%+ WR, likely gate-pass within days) — a third
market at full edge, not a correlated double.

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
