# CYCLE LAW — pre-registered, owner-approved 2026-08-11 (~00:55 UTC Aug 12)

## T3 v2 — protective mid-cycle change (2026-09-02 ~16:50 UTC, owner: "no pares, corrige")
T3 after 5 live settles: 1W/4L −$5.64 (chain $62.03). The $0 paper twin took the SAME
five decisions and lost the same (−$4.38) → the model, not execution. All fills landed in
10-12s (instant = informed seller, the mechanism mm_replay measured on 5.05M snapshots).
The clock's n=9 bar was too low (+0.377 was noise; seat record now 6W/8L) — process
error, recorded. Owner chose to continue and improve rather than halt.
CHANGE (removes bets only, never adds): T3 takes a signal ONLY on the side aligned with
the hour's drift (spot vs candle open). Evidence: aligned favourites +4.6% (n=2,643,
z=+3.3) vs opposed −8.0% (n=118, test −20.8%); on T3's own 14-bet seat record (paper+live)
aligned = 4W/2L, opposed = 2W/6L. Stops unchanged (n=40 / −$12, $6.36 of runway left).
late_shadow stays UNFILTERED at $0 as the control for the opposed side.

## T3 LIVE AMENDMENT — THE CLOCK spoke (2026-09-02 06:05 UTC)
Clock (registered 2026-09-01): filtered BTC paper ledger, px 20-85c, fair 10-90c,
EV/$ >= +0.03 -> live. Reading at 06:05 UTC: **n=9, 5W/4L, +$4.11 fake, EV/$ +0.377**
(`clock_exec.log`). GO branch. Honesty: n=9 is tiny; this is a micro-audition of the
LATE-digital seat (`late_shadow` logic, unchanged) with real money, not a proven edge.
T3 config (`t3_live.py`, seat `T3-late-digital-btc`): BTC only, last 10 min of the 1h
window (T 30-600s), P(UP)=Φ(ln(S/S0)/(σ√τ)) σ=180s realized, maker px=min(bid+1c,ask-1c),
fire if fair-px >= 4c, px in [0.20,0.85], fair in (0.10,0.90), spread <= 4c, ONE order
per hour, 5 shares (exchange min), GTC, hold to settlement, unfilled cancels at T-30s.
STOPS: n=40 settles or net <= -$12 -> halt forever. Banking law on a pass (50%).
Auto-launch stays REVOKED: the clock authorized it, a human started it.
`late_shadow` keeps running at $0 as the paper twin for fill-realism comparison.
mm_shadow (15m paper) stays OFF until T3 resolves — one live hypothesis at a time.


Registered at cycle 4, n=7 (4W/3L, net −$5.38), BEFORE any cycle-4 verdict
exists. Git commit timestamp = proof of pre-registration.

## AMENDMENT LATE SHADOW CUT (2026-09-01 ~05:50 UTC) — still $0, clock is set
Paper n=23 11W/12L +$8.10 EV/$ +0.325. Last two rests ETH LOSS −0.57 then
−1.41. ETH paper EV/$ +0.11 and falling. Three rests at px=0c (WS book
does not clip pennies). BTC px∈[0.20,0.85] counterfactual: n=7 6W/1L
+$7.20 fake — too small to launch; large enough to stop measuring junk.

Mid-stream (same SEAT_ID, ledger kept): BTC only, maker px [0.20, 0.85],
fair ∈ (0.10, 0.90). No live dollars. Decision clock **2026-09-02 06:00 UTC**:
- filtered BTC 20-85c EV/$ ≥ +0.03 → T3 live 5 shares, stop −$12, one/hour
- else kill `late_shadow.py` like T2. No third wait.

## AMENDMENT LATE SHADOW (2026-08-30) — $0, live stays dead
T2 halt 2026-08-26: n=11 5W/6L −$14.34, chain $69.38. T2 shadow after halt
still ROI ~−21% n=21. Do NOT relaunch T2. t1_live remains `done`.

New measurement only: `late_shadow.py` seat `LATE-digital-10m`.
  Last 10 minutes of the 1H window (τ ∈ [30s, 600s]).
  Fair P(UP)=Φ(ln(S/S0)/(σ√τ)) from Binance ticks (WS/REST) vs maker px.
  Fire if model−px ≥ 4c, spread≤4c, BTC+ETH, one bet/hour, $0, hold settle.
  Hypothesis from 2026 literature: edge is late conversion of spot→binary,
  not 40–50m favourite hold. If this does not print EV>0 on unseen hours,
  it is refused like T2. No live dollars. No share scale.

## AMENDMENT T2 CYCLE (2026-08-24 ~15:20 UTC) — cycle 1 passed, apply the audit
T1 live verdict 2026-08-24 05:01 UTC: n=40, 31W/9L, net **+$8.11**, EV/$ +0.056
(pass bar +0.03). z=+0.62 — BELOW the |z|≥1 scale gate. Chain equity $83.08
(launch $74.72). BTC +$15.86 (n=23); ETH −$7.75 (n=17), and post-67c-filter
ETH was −$11.50 (n=15). Cheap px<67c −$3.80.

Therefore cycle 2 is lawful as a **passed-cycle continuation**, with:
- BANKING LAW: withdraw **$4.06** (50% of +$8.11) from the trading wallet.
  Agent cannot move USDC; owner must send it out. Banked money never rides.
- **Do NOT scale** (z<1; cycle 3 died after 5→8→10). SHARES stay **5**.
- **ETH out.** Seat T2 = BTC only, ask [0.67, 0.85], |drift|≥0.20, 1 bet/hour,
  first look. SEAT_ID=`T2-btc-67-85-dmin20`. Fresh unseen cutoff 2026-08-24
  16:00 UTC. Cycle-1 ledger archived inside t1_live_state.json.
- Stops unchanged: n=40 or net≤−$12. Gate MIRAGE still kills.
- Auto-launch remains REVOKED; this start is the owner "apply your recs".

## AMENDMENT T1 TIGHTEN (2026-08-20 ~01:55 UTC) — mid-audition, after 65c dying
Live n=8 7W/1L net +$7.40 then BTC DOWN @ 65c x5 (ask 66c, |drift|=0.10) marked
to ~0 before settle (same family as the only prior live loss: 66c ask 67c
d=0.18, −$3.30). Lucky 59c/60c wins do not excuse the band. Change in
`qualify.py` (shadow + live import it):
  MIN_ASK 0.60 → **0.67**  (65c ask-66 and 66c ask-67 out)
  D_MIN   0.00 → **0.20**  (weak-chop drift out)
  5 shares, stop −$12, n=40, 1 coin: unchanged.
  Open 65c position is left to settle — not cancelled mid-hour.
  SEAT_ID unchanged (tightening, not a new ledger). Bets 1-8 + the 65c
  open predate this filter.

## AMENDMENT T1 LIVE (2026-08-19 ~15:25 UTC) — OWNER DIRECTED
Owner instruction: "enciende live vamos a probar". Gate at launch is NOT
green (FILLING, collector n=5 ROI −16%, shadow n=6 +$0.18 fake). This
violates GATE-FIRST on purpose; the owner overrode it. Config:
  T1 seat (`qualify.py`), 5 shares (exchange min), ONE coin/hour, first
  look, GTC maker, hold to settlement. STOP_NET=−$12, verdict n=40.
  Kill switch: gate MIRAGE → halt. Shadow keeps running at $0.
  hour_bot + micro_bot stay retired (killed so they cannot double-fire).
HONESTY: worst case this audition is −$12 (equity ~$62.72). Impatience
tuition of cycles 5–6 is the prior of this launch. Auto-launch remains
REVOKED for any future seat.

## AMENDMENT T1 SHADOW (2026-08-19 ~03:30 UTC) — measure only, $0
Post-mortem of the 55-65c ALIGNED collapse: that band is structurally chop
(a 58c favourite at 40-50min means the hour has not decided). The surviving
slice on the same CSV is T1: favourite ask [0.60, 0.85], 40-50min half-open,
spread ≤3c, mid ≥55c, drift-aligned, BTC/ETH/SOL, ONE coin/hour (tightest
spread), first snapshot in the bucket (do not wait for price to fall in).
Definition: `research_brain/qualify.py` (SEAT_ID=`T1-fav-60-85-40-50-align`).

HONESTY: T1 was cut AFTER seeing 55-65 die. It is a new fork, not a
pre-registered cell. Therefore:
- Shadow + gate run T1 at $0 from cutoff 2026-08-19 04:00 UTC (T1_CUTOFF=
  1787109600). Old 55-65 / 75-85 shadow ledger is archived, not mixed.
- Gate unit = clustered UTC hours, not coin-rows. OPEN: n_hours ≥ 24 AND
  ROI ≥ +8%. MIRAGE: n_hours ≥ 24 AND ROI ≤ 0. ARMED = two consecutive
  OPEN reads. Auto-launch stays REVOKED (2026-08-14 hysteresis). ARMED
  only means the owner MAY consider a $20 micro-audition.
- Original-seat RE-ARM (55-85c / 30-55m, two weeks ≥ +4%) is separately
  MET as of 2026-08-19 and remains available; we are NOT launching it in
  this amendment. One live hypothesis at a time, and T1 is still $0.
- Neural nets / logistic as a live sizer: refused. Walk-forward Brier did
  not beat the ask.

## 1. THE FLOOR RULE
Cycle 4 is the last cycle funded on faith.

- Cycle 4 PASSES (EV/$ ≥ +0.03 at n=40) → cycle 5 proceeds at 8 shares
  (no scale-up without standalone z ≥ +1, per the constitution).
- Cycle 4 FAILS the bar or hits the −$20 stop → the engine RETIRES unless
  the pooled ~160-settle lifetime record shows z ≥ +1. No cycle 5 bought
  with hope.

Consequence: worst case for the whole project is ~$84 equity — still above
the $77.84 launch. The project can no longer end net negative.

## 2. THE BANKING LAW
From the next PASSING cycle onward: 50% of each passed cycle's realized
profit is withdrawn from the trading wallet at cycle close. Banked money
never rides again. The bot's verdict Telegram includes the exact amount.

Rationale: the audit machinery protects against a bad engine; banking
protects a good run from staying fully at risk. Both protections are now
law.

## 3. STANDING PRE-REGISTERED TRIGGERS (unchanged)
- At cycle-4 verdict: if the pooled LIVE 70c+ subgroup is negative,
  MAX_ASK drops to 0.75. (At registration it is POSITIVE: n=13, +0.091.)
- Verdicts are z-gated (|z| ≥ 1 both directions); emergency brake z-free.

## 4. MID-CYCLE CHANGE DISCLOSURE (honesty ledger)
Cycle 4 config at launch (n=0): 8 shares, stop −$20, MIN_MID 0.55 wide-book
guard, fill_s instrumentation.
At n=7 (this document): entry tie-break changed from code-order-first to
TIGHTEST-SPREAD-first among qualifying coins, with [PICK] logging of the
counterfactual. Direction: protective/selective (tight books were the only
split-consistent positive slice, ≤2c: +2.7% ROI both halves; wide books
produced the fake-favourite losses). Bets 1-7 predate this tie-break; the
cycle-4 verdict must note it.

## AMENDMENT (2026-08-13 ~17:25 UTC, owner-approved): exchange minimum forced 5 shares
Polymarket rejected 3-share orders ("Size (3) lower than the minimum: 5"). Owner chose:
SHARES=5, STOP_NET=-$12 (3-loss headroom). CONSEQUENCE STATED PLAINLY: worst case is now
~$71.70 vs the $77.84 launch line — the original "cannot end below launch" floor is
AMENDED to "cannot end more than ~$6 below launch" (chain truth $83.71 at amendment).
All other terms unchanged: n=40 verdict, unseen-data kill switch, banking law on a pass.

## AMENDMENT 2 (2026-08-13 ~18:20 UTC, owner-directed 3x): MULTI-COIN LIVE
Owner instruction "let it work live" (asked three times, risks disclosed twice):
micro_bot now rests on EVERY qualifying coin per window (the shadow's full bet-set,
real money) instead of one-bet-at-a-time. The -$12 total stop is UNCHANGED and caps
all positions combined — a single correlated reversal window (~$8-12 across 3 coins)
can spend most of it at once; the audition can therefore end in as few as 2 bad
windows. Verdict n=40, kill switch, banking law: unchanged. Gate control data is
population-based (collector), so live conversion does not degrade Friday's verdict.

## CYCLE 6 KILLED BY GATE + HYSTERESIS AMENDMENT (2026-08-14 ~19:10 UTC)
90 minutes after the lawful launch, the rolling gate re-read flipped MIRAGE
(n=67, -2.9%) and the kill switch halted cycle 6 at n=1, -$3.80. The full
unseen trajectory (-3.6% @41 -> +3.2% @63 -> -2.9% @67) is the verdict: the
75-85c edge is ~ZERO wearing noise. High-band chapter total: -$12.87.
HYSTERESIS (fixes my gate-design flaw — a zero-threshold switch whipsaws):
exit stays roi<=0, but RE-ENTRY now requires roi >= +4% at n>=60 on a FRESH
cutoff, sustained across two consecutive 6h reads. The 2026-08-13 standing
"launch the moment the gate opens" authorization is REVOKED — superseded by
this bar. Shadow + gate keep measuring for $0. Chain equity $74.72.

## GATE RULING + CYCLE 6 (2026-08-14 ~16:55 UTC, owner-directed launch)
The gate RULED GREEN on schedule: n=63 unseen opportunities, ROI +3.2%, win 81%
(recovered from -3.6% at n=41; ex-XRP slice +1.6% n=72; shadow n=91 +$5.82).
Under the GATE-FIRST LAW, live capital is authorized. CYCLE 6 config:
  BTC/ETH/SOL only (XRP: discovery cells MIRAGE-graded, owned cycle 5's whole
  loss), ONE bet per window (tightest book — cycle 5's drawdown was entirely
  correlated multi-coin windows), 5sh, stop -$12, verdict n=40, rolling gate
  kill switch (roi<=0 at any 6h re-read -> self-halt), banking law on a pass.
HONESTY CLAUSE: the confirmed edge is thin (+1.5..+3.2%); even if real, this
cycle is ~coin-flip to clear the +0.03 bar. Cycle 5 archived in micro state.

## THE GATE-FIRST LAW (2026-08-14 ~06:50 UTC — born from cycle 5's $12 lesson)
No live dollars on ANY seat, ever again, until its unseen-data gate reads GREEN at
n >= 60. The shadow bot + gate test any candidate seat for $0 in ~48h; cycle 5 paid
~$12 to learn what that instrument would have reported free (gate: +22% at n=22 ->
-3.6% at n=41 — the winner's curse washing out in real time, caught in 36 hours).
Impatience now has a measured price. The hunt stays free until the data says pay.

## MICRO-AUDITION v2 — pre-registered 2026-08-13 ~16:30 UTC (before confirmation data existed)
seat_scan found the 75-85c favourite band alive (five adjacent cells z+2.0..+3.1,
green last-14d) in data through 2026-08-13 16:00 UTC. Confirmation MUST come from
windows after that horizon (CUTOFF=1786636800). Gate (confirm_gate.py, 6h cron):
  seat = fav ask 0.75-0.85, spread <= 3c, entry 20-50min, maker px, hold
  GATE OPEN : n >= 60 post-cutoff opportunities AND ROI > 0
              -> micro-audition: 3 shares, stop -$10, verdict n=40, same
                 constitution (z-gated verdicts, one bet at a time, banking law
                 applies to any pass)
  MIRAGE    : n >= 60 AND ROI <= 0 -> stand down; weekly sweep continues
shadow_bot.py runs the seat live at $0 from 2026-08-13 16:00 UTC (Telegram 🕶).
Owner's standing instruction ("start from now", 2026-08-13): launch is authorized
the moment the gate opens — no further approval needed, stops are the guardrail.

## OUTCOME (2026-08-12, ~15:00 UTC) — LAW EXECUTED, ENGINE RETIRED
Cycle 4 verdict: n=40, 23W/17L, net −$15.43 (near-identical to cycle 3).
Pooled lifetime: 158 settles, 100W/58L (63%), +$11.13, EV/$ +0.015, z=+0.50.
Floor rule applied: z < +1 → NO CYCLE 5. Project closes NET POSITIVE
($77.84 → ~$89 ledger).

EPILOGUE FINDING (edge_watch, population sim on 268k rows): the seat's edge
decayed MARKET-WIDE, not just for us — weekly population ROI: +6.2% (launch
week, cycles 1-2 passed) → +2.6% → −1.1% → +0.9% (cycles 3-4 failed). The
edge was real, was captured while it lived, and was competed away. The
meter tracked reality with ~1 week of lag. Nothing was broken; nothing is
fixable; the market moved.

STANDING WATCH: edge_watch.py runs Sundays 15:00 UTC (cron), Telegram
digest. RE-ARM RULE (pre-registered): population ROI ≥ +4% two consecutive
weeks → owner may consider a $20 micro-audition. Until then: spend nothing.

## 5. WHAT THE DATA REFUSED (do not relitigate without NEW large-sample evidence)
Hour-of-day filters (24-bucket noise farm, no split-consistent block).
Coin drops (SOL n=8; population says all three fine). 65-70c sub-band cut
(n=14). Slow-fill cutoff (n=15). Adverse-selection repricing (REFUTED on
121 matched fills: instant fills EV +0.078).
