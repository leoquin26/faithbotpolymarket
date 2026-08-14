# CYCLE LAW — pre-registered, owner-approved 2026-08-11 (~00:55 UTC Aug 12)

Registered at cycle 4, n=7 (4W/3L, net −$5.38), BEFORE any cycle-4 verdict
exists. Git commit timestamp = proof of pre-registration.

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
