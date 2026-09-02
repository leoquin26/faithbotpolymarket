# AGENT STATUS — read this first

## UPDATE 2026-09-02 17:30 UTC (Claude)
- T3 live: 6 settles 1W/5L net −$8.19 (chain $59.48). v2 (aligned-only) deployed 16:44 UTC;
  its first bet (UP@51c fair 58c, filled in 5s) lost. **$3.81 from the −$12 stop = one loss.**
  Owner instruction: do not halt; let the stop rule. Paper twin lost the same 6 → the seat.
- `mm_shadow.py` STARTED 17:26 UTC ($0 paper, BTC-15m defensive maker, flat at T-300s) to
  measure minutes 1-10 fill toxicity and real reward Q-share. State `mm_shadow_state.json`,
  log `mm_shadow.log`, Telegram 🧱. Not in the watchdog. Memory on box ~330 MB free — watch.
- Three experiments today, all negative, in `research_brain/results/`: defensive-maker lab
  (last 5 min unquotable), async complete-set replay (−8..−10%), walk-forward LightGBM
  (market beats the model, taker −2..−4%). Next real lead = NEW INFORMATION collector
  (trade prints WS + sub-second Binance lead), not new math.

## UPDATE 2026-09-02 06:10 UTC (Claude) — THE CLOCK SAID GO; T3 IS LIVE
- **Clock verdict 06:05 UTC:** filtered BTC paper n=9 5W/4L +$4.11 fake, EV/$ +0.377 → GO.
- **`t3_live.py` LIVE since 06:06:41 UTC** (seat `T3-late-digital-btc`): late_shadow's Φ
  decision with real money, BTC only, last 10 min, 5sh, one/hour, stops n=40 or −$12.
  State `t3_live_state.json`, log `t3_live.log`, Telegram `⏱ T3`. Watchdog restarts it.
  Do NOT scale, do NOT add coins, do NOT touch before its stop or verdict.
- `late_shadow.py` keeps running at $0 (paper twin). `t1_live.py` runs but is HALTED
  (AUDIT COMPLETE, inert). One-shot clock cron removed.
- **Incident 09-01 22:11→09-02 04:14 UTC:** box frozen (research load on 1.9 GB RAM);
  owner rebooted via CLI; everything restored with `_ie_restore_after_reboot.sh`.
  ~6h capture gap in both tapes. RULE: heavy research runs LOCALLY, never on the box.
  Owner's Lightsail "Ubuntu-1" (54.194.22.159) is a DIFFERENT machine — ignore it.
- Tunnels (rotate on restart): desk 8096 = URL in `cf_notify_run.log`; data 8097 =
  `cf_data.log`; cleanbot 8095 = `cf_clean.log`.
- 15m research: `M15_REVIVAL_RESEARCH.md` (maker-only verdict; last 5 min unquotable;
  BTC-15m liquidity rewards $7,500/day live). `mm_shadow.py` (paper defensive maker)
  is built but OFF until T3 resolves — one hypothesis at a time.
- Next research lead (from x.com/RetroValix Aug 24 article): profitable 1h/4h bots are
  ASYNCHRONOUS COMPLETE-SET ACCUMULATORS (buy Up cheap, later Down cheap, combined VWAP
  $0.91-0.97) — testable on `hourly_research.csv` (pulled locally). Not built yet.

---
**Written 2026-09-01 ~20:25 UTC by Grok (previous session).**  
Owner: Leo. Bankroll is real personal money. Spanish-speaking.  
Stale on purpose after **2026-09-02 06:00 UTC** — re-read Ireland, do not trust this file blindly.

**Español (10s):** Live OFF, ~$69.38. T2 muerto. Único experimento: `late_shadow.py` a $0, BTC 20–85c, reloj **2 sep 06:00 UTC**. 15m medido otra vez: Φ taker y scalp 99c son −EV. No enciendas CleanBot. No relances T1/T2. No hay tercera espera.

---

## 0. What you are allowed to do

| Do | Do not |
|---|---|
| Read Ireland logs/state | Place live orders / flip `CLEAN_*_LIVE=on` |
| Paper / replay / research | Relaunch `t1_live` or T2 |
| At **2026-09-02 06:00 UTC** execute the clock below | Invent a cycle-3 seat because paper looks green |
| Touch 15m only as **$0 lab** after the 1H clock | Resurrect early / late@195s / voldiv / hiband / fav@9min |
| Keep one live *hypothesis* (paper counts) | Two live betting engines. Print `.env` / `*.pem` |

Laws: `CYCLE_LAW.md` (authoritative). Context dump: `AGENT_REVIEW_BRIEF.md` (2026-08-18, **equity figures in it are stale**). 1H skill: `.agents/skills/hourly-1h-review/SKILL.md`. 15m skill is history.

---

## 1. Live box

`ubuntu@34.255.2.158` `~/v3-bot` (Ireland eu-west-1). Key: local `polymarket-key.pem`.  
`USE_TOR=false`. CLOB matching is **eu-west-2**; Ireland is the close region, not “+100ms vs us-east-1”. `/time` ~70–100ms after dropping Tor.

PowerShell nested quotes break `python -c` over SSH. `scp` a script, then `ssh … bash script`. Watchdog: `touch ~/v3-bot/.watchdog_pause` before kill/restart.

**Processes (2026-09-01 20:20 UTC):**

| PID | process | role |
|---|---|---|
| 3790700 | `late_shadow.py` | **THE experiment.** $0. BTC-only since 05:50 UTC |
| 2428481 | `t1_live.py` | HALTED / `AUDIT COMPLETE`. Do not restart trading |
| 2428482 | `shadow_bot.py` | old T2 shadow, $0, ignore for decisions |
| 80646 | `hourly_capture.py` | 1H collector → `hourly_research.csv` |
| 302991 | `clean_bot.py` | 15m **engines OFF**, scan-only, bankroll mirror $69.38 |

Also: `quantum_dash.py` :8096, `data_control.py` :8097, `arb_monitor.py` (executor **not** running).

---

## 2. Money

- Chain / `clean_bot_state.json` bankroll: **~$69.38**
- T1 live (cycle 1): n=40, 31W/9L, **+$8.11**, EV/$ +0.056, z=+0.62 (no scale)
- T2 live: n=11, 5W/6L, **−$14.34**, halt 2026-08-26. Seat `T2-btc-67-85-dmin20`
- Peak this architecture ~$149 then decay. ~$216 deposited since June; ~$130 lost in the 15m era
- **No new real orders** since T2 halt
- Banking law: 50% of a *passed* cycle is withdraw-only (owner moves USDC). Not triggered for T2. T1 +$8.11 was supposed to bank $4.06 — check with owner, do not assume it happened

---

## 3. The one experiment — `late_shadow.py`

Seat id `LATE-digital-10m` (ledger **not** reset on the 09-01 cut).  
Hypothesis: last 10 min of 1H, `P(UP)=Φ(ln(S/S0)/(σ√τ))` vs maker px, fire if model−px ≥ 4c.

**Code now (deployed 2026-09-01 05:50 UTC, PID 3790700):**

```
COINS = ("BTC",)
MIN_PX, MAX_PX = 0.20, 0.85
FAIR_LO, FAIR_HI = 0.10, 0.90
EDGE_MIN = 0.04
MAX_SPREAD = 0.04
T_LO, T_HI = 30, 600
SHARES = 3          # nominal; live would be 5 (exchange min)
SIGMA_LOOKBACK = 180
# p_up returns None if sigma < 4e-5 (do not saturate to 0/1)
```

State: `~/v3-bot/late_shadow_state.json`  
Log: `~/v3-bot/late_shadow.log`  
Fair: `research_brain/digital.py`  
Telegram tag: `⏱ LATE`

### Paper ledger at 2026-09-01 20:20 UTC

Whole seat (includes ETH, 0c junk, σ-bug): **n=28, 13W/15L, +$7.44 fake, EV/$ +0.236**

| Cut | n | WR | PnL fake | EV/$ |
|---|---|---|---|---|
| all | 28 | 13/15 | +7.44 | +0.236 |
| σ≥4e-5 and px>1c | 24 | 12/12 | +6.66 | +0.227 |
| px≥20c (BTC+ETH) | 17 | 12/5 | +7.41 | +0.259 |
| BTC only | 18 | 9/9 | +6.27 | +0.302 |
| ETH only | 10 | 4/6 | +1.17 | +0.108 |

**Post-cut only** (after 05:50 UTC BTC-only 20–85c): n=5, 2W/3L, **−$0.66 fake**  
(07:50 W +0.60, 12:50 L −0.60, 13:50 L −0.72, 15:51 L −0.66, 19:53 W +0.72).

Known poison in the mixed ledger: first rest fair=1.0 (σ≈2e-8); three px=0c rests (WS book not clipped); ETH last two pre-cut were −0.57 and −1.41.

### THE CLOCK (CYCLE_LAW amendment 2026-09-01)

**2026-09-02 06:00 UTC — binary, no third wait:**

1. Score **filtered BTC, maker px ∈ [0.20, 0.85], fair ∈ (0.10, 0.90)** on the existing ledger + any new rests.  
2. If EV/$ ≥ **+0.03** → T3 live amendment: 5 shares, stop −$12, one/hour, last 10m, BTC only, same bounds. Auto-launch stays REVOKED; this clock *is* the pre-registered go.  
3. Else → kill `late_shadow.py` like T2. Archive state. Do not open a new 1H favourite seat.

Paper EV>0 on the *unfiltered* n=28 does **not** by itself turn live. Maker fill = bid is still unproven in the last 10 minutes.

---

## 4. 15m — measured again 2026-09-01, still dead as a taker

Do **not** set `CLEAN_LATE_LIVE` / `CLEAN_FAV_LIVE` / `CLEAN_VOLDIV`.  
`clean_bot.py` v1.66.1 is scan-only (`engine_off` early/late/hiband/fav True; voldiv gated by `CLEAN_VOLDIV=off`). `CLEANBOT.md` is stale marketing.

Replay (Ireland): `python3 ~/v3-bot/research_brain/m15_phi_replay.py`

| Tape | seat | n | WR | EV/$ after taker fee |
|---|---|---|---|---|
| `clean_bot_research.csv` | Φ taker edge≥8c | 16411 | 31% | **−0.080** |
| same | favourite ask≥50c edge≥8c | 3025 | 67.5% | **−0.017** |
| same | dogs <50c | 13386 | 23% | **−0.118** |
| `late_book.jsonl` last 60s | scalp 90–99c | 4698 | 94.9% | **−0.008** |
| same | scalp >97c | 1722 | 97.3% | **−0.014** |

67% WR loses: you pay ~69c+fee, need ~71%. Last-second 95% WR also loses (the 5% wipe the 1–2c). **Faster Python does not fix −EV.**

Census just re-run on `wallet_trades.csv`: 4.05M fills, 19,150 wallets, $46M notional. Favourites +$603k, longshots −$495k. Winners are 1–4% ROI on huge maker/hybrid volume, not directional 70% WR.

If 1H clock **kills** late_shadow, the only 15m idea not yet killed *on our tape* is two-sided **maker + 20% rebate** at $0 — not Φ taker, not 99c scalp, not late@195s. That is a new process, not a CleanBot flag.

Settlement: 15m = **Chainlink 60s TWAP**. 1H = Binance 1h candle. Do not mix.

---

## 5. Files Claude will need

| path | what |
|---|---|
| `CYCLE_LAW.md` | pre-registered law + 09-01 clock |
| `AGENT_REVIEW_BRIEF.md` | full 1H history through 2026-08-18 |
| `CHANGELOG.md` | 15m verdicts (CleanBot) |
| `late_shadow.py` | current paper engine |
| `research_brain/digital.py` | Φ + σ floor 4e-5 |
| `research_brain/qualify.py` | T1 seat (dead for live) |
| `research_brain/m15_phi_replay.py` | 15m Φ/scalp replay |
| `t1_live.py` / `t1_live_state.json` | halted T2 |
| `_watchdog.sh` | restarts late_shadow if dead |
| `_restart_late.sh` | pause-dance restart |

EC2-only (gitignored): `late_shadow_state.json`, `late_shadow.log`, `clean_bot_state.json`, `clean_bot_research.csv` (~43k), `late_book.jsonl` (~565MB, 1Hz 15m book), `wallet_trades.csv` (~363MB), `hourly_research.csv`.

---

## 6. Session decisions (Grok, 2026-08-30 → 09-01)

- Owner: exclusive adversarial work, then “you decide.” Live stayed OFF after T2.
- Built `late_shadow` $0. First hour: empty look burned the hour; σ-saturation printed fair=100c. Patched.
- Owner asked “qué sigues esperando?” — cut ETH, px 0c, fair 0/1. Set the 09-02 06:00 clock.
- Owner asked for 15m again. Reverse-engineered CleanBot, deep-research, census, Φ replay. Verdict: taker 15m is −EV on our tape. Speed is not the bottleneck.
- Do not start a second live hypothesis while this clock is running.

---

## 7. First commands on Ireland

```bash
ssh -i polymarket-key.pem ubuntu@34.255.2.158
cd ~/v3-bot
date -u
pgrep -af 'python3 -u'
tail -n 40 late_shadow.log
python3 _status_late.py          # if present
python3 research_brain/m15_phi_replay.py   # 15m tape, ~30s
```

At 2026-09-02 06:00 UTC: compute filtered BTC 20–85c EV/$ on `late_shadow_state.json`. Then either write the T3 live amendment and start 5-share live, or `watchdog_pause` + kill `late_shadow.py` and stop. No “one more day.”
