# Rollback card — CleanBot v1.56.0 (`deoverblock-v156`)

**Branch:** `yirok-cleanbot-grok`  
**Forward tag:** `cleanbot-v1.56.0`  
**Safe previous tag:** `cleanbot-v1.55.0`  
**EC2 env backup:** `~/v3-bot/.env.bak_v156`

## What v1.56 changed
| Knob | v1.55 (safe) | v1.56 (live) |
|------|----------------|--------------|
| `CLEAN_LATE_MAX_ASK` | `0.66` | **`0.68`** |
| `CLEAN_LATE_FLIP_MIN_BPS` | `5` | **`3`** |
| `CLEAN_LATE_FOK_RETRY` | n/a (off) | **`on`** (1 retry) |

Everything else (early-require, CL-only, fade, reverse-underway, compound min EV) unchanged.

## When to rollback
- Late EV/$ turns clearly negative again over 2+ days  
- Losses at 67–68¢ wipe gains (geometry)  
- FOK retry causes double-fills or weird errors (should not — still FOK only)  
- Owner wants less volume / more caution  

## Rollback steps (EC2)
```bash
cd ~/v3-bot
touch .watchdog_pause

# restore code from known-good tag (from Windows project, then scp) OR:
# on machine with git:
# git checkout cleanbot-v1.55.0 -- clean_bot.py

# env knobs only (fast path — no code swap needed for knobs):
# set CLEAN_LATE_MAX_ASK=0.66
# set CLEAN_LATE_FLIP_MIN_BPS=5
# set CLEAN_LATE_FOK_RETRY=off
# or: cp .env.bak_v155 .env   /   edit from .env.bak_v156

# kill by PID only, then:
# nohup python3 -u clean_bot.py >> clean_bot.log 2>&1 < /dev/null &
rm -f .watchdog_pause
```

## Git restore (local Windows → scp)
```bash
git checkout cleanbot-v1.55.0 -- clean_bot.py
# scp clean_bot.py to EC2, restart as above
```

## Success criteria for keeping v1.56 (3–5 days)
- Fills/day up vs v1.55 quiet days  
- Late EV/$ still ≥ 0 (ideally ≥ +0.02)  
- FOK fail rate down (retries convert some misses)  
- Bankroll not bleeding  
