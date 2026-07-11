# Yirok / Grok session — 2026-07-11

**Purpose of this file:** handoff for tomorrow. Everything we shipped, where it lives, what to check.

---

## Branch & git (Grok line)

| Item | Value |
|------|--------|
| Branch | `yirok-cleanbot-grok` |
| Meaning | **yirok** + **cleanbot** + **grok** — Grok’s CleanBot line (not `cleanbot-main`) |
| Primary remote | `faithbot` → https://github.com/leoquin26/faithbotpolymarket |
| Also pushed | `origin` → Randomforest |
| Tags | `cleanbot-v1.50.0`, `cleanbot-v1.51.0` |
| Commits | `7fb3c5b` (v1.50), `6380c10` (v1.51) |
| PR (optional) | https://github.com/leoquin26/faithbotpolymarket/pull/new/yirok-cleanbot-grok |

`cleanbot-main` was **not** merged. Production history branch stays separate until you choose to PR/merge.

---

## What is live on EC2 (as of session end)

| Item | Value |
|------|--------|
| Host | `ubuntu@54.162.216.46` · dir `~/v3-bot` · key `polymarket-key.pem` |
| Version | **CleanBot v1.51.0** |
| Mode | LIVE |
| Bankroll (session) | **~$46.45** |
| Goal | **$100** (`CLEAN_TARGET_BANKROLL=100` → Telegram `[TARGET HIT]`) |
| Early engine | **OFF** (`engine_off.early=true`) — do not resurrect |
| Voldiv | OFF |
| Late engine | **ON** · coins SOL,ETH,BTC · **FOK taker** · sleep **00:00–07:00 Lima** |
| Compound | **ON** · 8% Kelly · bump 10% after $70 · max bet 12% · max open 35% |
| Coin size tilt | **SOL=1.5, ETH=1.0, BTC=0.5** |
| Daily stop | 999 (effectively off) · kill floor 0 |
| Env backup | `~/v3-bot/.env.bak_v151_recovery` |

### Banner to expect on restart
```
CleanBot v1.51.0 | LIVE | COMPOUND 8%/bet
late=ON[SOL,ETH,BTC] FOK cmult=BTC=0.5,ETH=1,SOL=1.5
bankroll $… target $100
```

---

## Code changes shipped

### v1.50.0 — execution honesty
1. Late **true FOK** at the ask (was GTC-at-ask → could rest as maker).
2. Taker **buy fee** in resolve PnL + EV/$ stake.
3. **Partial GTC** fills stay tracked until full/cancel.

Logs: `[LATE ENTER] … TAKER/FOK`, `[FILLED TAKER]`, `[LATE MISS]`, `[FILLED-PARTIAL]`.

### v1.51.0 — recovery sizing ($46 → $100)
1. Late uses **`_late_size_shares`** (compound actually applied; was flat 5sh).
2. **`CLEAN_LATE_COIN_MULT`** — more $ on SOL, less on BTC, still all coins.
3. **`CLEAN_TARGET_BANKROLL=100`** milestone alert.

No new skip-filters. No mid-window trading (shadow mid still negative).

---

## Research snapshot (why we sized this way)

Late phase, fav ask 55–70¢, settled (EC2 research CSV, Jul 11 scan):

| Slice | n | WR | Edge vs BE | EV/$ |
|-------|---|-----|------------|------|
| All late | 189 | 75.7% | +10.4 pts | +0.104 |
| OOS 30% | 57 | 77.2% | +12.1 | +0.121 |
| SOL | 69 | — | — | **+0.135** |
| ETH | 50 | — | — | +0.085 |
| BTC | 35 | — | — | **~+0.007** |
| Mid 55–70 | 40 | 60% | **−4.8** | −0.048 |

Live late meter before session was **n=24, EV/$ ~+0.004** (flat) — gap vs shadow is mostly **execution**, not “need more filters.”

---

## Tomorrow morning checklist

1. **SSH / process**
   ```bash
   ssh -i polymarket-key.pem ubuntu@54.162.216.46
   cd ~/v3-bot
   ps -eo pid,lstart,cmd | grep '[p]ython3 -u clean_bot.py'
   # expect exactly 1 python3 clean_bot
   grep 'CleanBot v' clean_bot.log | tail -3   # should show v1.51.0
   ```

2. **After ~07:00 Lima** (late wakes)
   - Count `[FILLED TAKER]` vs `[LATE MISS]`
   - Note SOL `cmult=1.50` vs BTC `0.50` on `[LATE ENTER]`
   - Bankroll vs $100; day net

3. **Health**
   ```bash
   python3 -c "import json; s=json.load(open('clean_bot_state.json')); print(s.get('bankroll'), s.get('version'), s.get('engine_off'), s.get('mode'))"
   # TRACK:late lines for EV/$
   grep '\[TRACK:late\]' clean_bot.log | tail -5
   ```

4. **If something is wrong**
   - Rollback code: `git checkout cleanbot-v1.49.0 -- clean_bot.py` (or v1.50) then scp + restart dance
   - Env: restore `.env.bak_v151_recovery` if recovery knobs misbehave
   - Watchdog: `touch .watchdog_pause` → kill by **PID only** → start → `rm .watchdog_pause`

5. **Do not**
   - Resurrect early engine without new OOS proof
   - Turn on mid trading while mid shadow is negative
   - Add flow/mom/day-trend hard blocks (owner rule: no overblocking)

---

## Files touched this session

| File | Role |
|------|------|
| `clean_bot.py` | v1.50 + v1.51 logic |
| `CHANGELOG.md` | Version history |
| `YIROK_SESSION_2026-07-11.md` | This handoff |
| `_patch_env_recovery.py` | Idempotent .env recovery knobs (on EC2 too) |
| `_edge_scan_freq.py` | One-off research scan script |

---

## Strategy in one sentence

**Same late signals, honest FOK fills + fees, put more dollars on SOL, compound 8% toward $100 — no new blocks.**
