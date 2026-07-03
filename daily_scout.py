#!/usr/bin/env python3
"""Daily-threshold SCOUT — shadow measurement for Option 2 (DRY RUN, no orders).

Measures whether our Black-Scholes vol model can beat the Polymarket price on the
DAILY "{coin} above $X on [date]" markets — a latency-INDEPENDENT edge that fits
our constraints (no sub-100ms race; the HFT swarm is on 5m/15m). For each
near-the-money strike it logs model P(above) vs the market price and the edge,
then tracks the end-of-day outcome to score model-vs-market (Brier).

Logs to logs/daily_scout.log. Read-only. gamma + binance.us + clob are directly
reachable from the EC2 (no Tor needed for these).
"""
import time, math, json, re, datetime
import httpx
from loguru import logger

COINS = {"bitcoin": "BTCUSDT", "ethereum": "ETHUSDT", "solana": "SOLUSDT"}
TRADE_LO = float(0.05)   # only score strikes the market prices as uncertain
TRADE_HI = float(0.95)
MARGIN = 0.05            # |model - market| edge to flag as a candidate
_h = httpx.Client(timeout=12)


def binance_price(sym):
    for host in ("https://api.binance.us", "https://api.binance.com"):
        try:
            j = _h.get(host + "/api/v3/ticker/price", params={"symbol": sym}).json()
            if "price" in j:
                return float(j["price"])
        except Exception:
            pass
    return None


def hourly_vol(sym):
    """Std-dev of hourly log returns over ~7 days (per-hour vol)."""
    for host in ("https://api.binance.us", "https://api.binance.com"):
        try:
            kl = _h.get(host + "/api/v3/klines", params={"symbol": sym, "interval": "1h", "limit": 168}).json()
            closes = [float(k[4]) for k in kl]
            rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0]
            if len(rets) < 24:
                continue
            mu = sum(rets) / len(rets)
            var = sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)
            return math.sqrt(var)
        except Exception:
            continue
    return None


_dvol_cache = {}


def deribit_iv(coin):
    """Deribit DVOL (30d implied-vol index, annualized %) for BTC/ETH — logged as a
    covariate so a skew/IV-aware model v2 can be tested against flat-HV later
    (per Bloch: digital prices deviate from flat-vol Phi(d2) via the vol skew).
    Cached 10min. None for coins without a DVOL index (SOL)."""
    cur = {"BTCUSDT": "btc_usd", "ETHUSDT": "eth_usd"}.get(coin)
    if not cur:
        return None
    now = time.time()
    c = _dvol_cache.get(cur)
    if c and now - c[0] < 600:
        return c[1]
    iv = None
    try:
        j = _h.get("https://www.deribit.com/api/v2/public/get_volatility_index_data",
                   params={"currency": cur.split("_")[0].upper(),
                           "start_timestamp": int((now - 7200) * 1000),
                           "end_timestamp": int(now * 1000), "resolution": "3600"}).json()
        data = (j.get("result") or {}).get("data") or []
        if data:
            iv = round(float(data[-1][4]), 1)      # last candle close of the DVOL index
    except Exception:
        iv = None
    _dvol_cache[cur] = (now, iv)
    return iv


def phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_prob_above(spot, strike, hv, t_sec):
    """P(spot_T > strike) with zero drift; sig = hourly_vol * sqrt(hours)."""
    if spot <= 0 or strike <= 0 or hv <= 0 or t_sec <= 0:
        return None
    sig = hv * math.sqrt(t_sec / 3600.0)
    if sig <= 0:
        return None
    d2 = (math.log(spot / strike) - 0.5 * sig * sig) / sig
    return phi(d2)


def daily_events():
    """Active '{coin}-above-on-...' events with their markets."""
    out = []
    try:
        evs = _h.get("https://gamma-api.polymarket.com/events",
                     params={"closed": "false", "active": "true", "limit": 300,
                             "order": "volume24hr", "ascending": "false"}).json()
    except Exception as e:
        logger.warning(f"gamma events err {e}")
        return out
    for e in evs:
        slug = (e.get("slug") or "").lower()
        m = re.match(r"(bitcoin|ethereum|solana)-above-on-", slug)
        if m and e.get("markets"):
            out.append((m.group(1), e))
    return out


flagged = {}   # (slug, strike) -> dict(model, market, dir, spot, end_ts)
done = set()
# v2 (Jul 3): CALIBRATION set — record EVERY uncertain strike at first sighting (not just
# |edge|>=5% flags) and grade it at settlement ([CAL] lines). Builds the full model-vs-market
# dataset ~5-10x faster, so the 5-8%-divergence edge (+$0.42/$ on n=32) can reach the n>=80
# verification gate in days instead of weeks.
tracked = {}   # (slug, strike) -> dict(model, market, end_ts) — all uncertain strikes
done_cal = set()
scored = {"n": 0, "model_brier": 0.0, "market_brier": 0.0, "model_better": 0}
t0 = time.time()


def _setup_log():
    logger.remove()
    logger.add(lambda m: print(m, end=""), level="INFO")
    logger.add("logs/daily_scout.log", level="INFO", rotation="20 MB")


def settle(now):
    for key in list(flagged):
        f = flagged[key]
        if now < f["end_ts"] + 30 or key in done:
            continue
        spot = binance_price(f["sym"])
        if not spot:
            continue
        above = 1.0 if spot >= f["strike"] else 0.0
        mb = (f["model"] - above) ** 2
        kb = (f["market"] - above) ** 2
        scored["n"] += 1
        scored["model_brier"] += mb
        scored["market_brier"] += kb
        scored["model_better"] += 1 if mb < kb else 0
        done.add(key)
        logger.info(f"[RESULT] {f['coin']} >${f['strike']:.0f} -> spot ${spot:.0f} {'ABOVE' if above else 'below'} "
                    f"| model said {f['model']*100:.0f}% mkt {f['market']*100:.0f}% | "
                    f"model_brier={scored['model_brier']/scored['n']:.4f} mkt_brier={scored['market_brier']/scored['n']:.4f} "
                    f"({scored['model_better']}/{scored['n']} model better)")
    # calibration set: grade EVERY tracked strike (compact [CAL] lines for the verifier)
    for key in list(tracked):
        f = tracked[key]
        if now < f["end_ts"] + 30 or key in done_cal:
            continue
        spot = binance_price(f["sym"])
        if not spot:
            continue
        above = 1 if spot >= f["strike"] else 0
        done_cal.add(key)
        tracked.pop(key, None)
        logger.info(f"[CAL] {f['coin']} >{f['strike']:.0f} out={above} "
                    f"model={f['model']*100:.0f} mkt={f['market']*100:.0f}")


def main():
  _setup_log()
  logger.info("=== DAILY SCOUT (dry-run): BS vol-model vs market on daily 'above $X' threshold markets ===")
  while True:
    now = time.time()
    try:
        for coin, ev in daily_events():
            sym = COINS[coin]
            spot = binance_price(sym)
            hv = hourly_vol(sym)
            end = ev.get("endDate")
            try:
                end_ts = datetime.datetime.fromisoformat(end.replace("Z", "+00:00")).timestamp()
            except Exception:
                continue
            t_sec = end_ts - now
            if not spot or not hv or t_sec <= 60:
                continue
            cand = []
            for mk in ev.get("markets", []):
                try:
                    strike = float(re.sub(r"[^\d.]", "", mk.get("groupItemTitle") or ""))
                    prices = json.loads(mk.get("outcomePrices") or "[]")
                    mkt_yes = float(prices[0]) if prices else None
                except Exception:
                    continue
                if not strike or mkt_yes is None or not (TRADE_LO < mkt_yes < TRADE_HI):
                    continue
                mp = bs_prob_above(spot, strike, hv, t_sec)
                if mp is None:
                    continue
                edge = mp - mkt_yes
                cand.append((strike, mkt_yes, mp, edge))
                key = (ev.get("slug"), strike)
                if key not in tracked and key not in done_cal:   # calibration: every uncertain strike, first sighting
                    tracked[key] = {"coin": coin.upper(), "sym": sym, "strike": strike,
                                    "model": mp, "market": mkt_yes, "end_ts": end_ts}
                if key not in flagged and abs(edge) >= MARGIN:
                    flagged[key] = {"coin": coin.upper(), "sym": sym, "strike": strike,
                                    "model": mp, "market": mkt_yes,
                                    "dir": "YES" if edge > 0 else "NO", "end_ts": end_ts}
                    logger.info(f"[SCOUT] {coin.upper()} >${strike:,.0f} | spot ${spot:,.0f} | "
                                f"model {mp*100:.0f}% vs mkt {mkt_yes*100:.0f}% | edge {edge*100:+.0f}% "
                                f"-> lean {'YES' if edge>0 else 'NO'} | {t_sec/3600:.1f}h left")
            if cand:
                near = min(cand, key=lambda c: abs(c[1] - 0.5))
                iv = deribit_iv(sym)
                ivs = f" iv30={iv:.0f}%" if iv else ""
                logger.info(f"[ATM] {coin.upper()} hv={hv*100:.2f}%/h{ivs} | nearest-50/50 ${near[0]:,.0f}: "
                            f"model {near[2]*100:.0f}% vs mkt {near[1]*100:.0f}% (edge {near[3]*100:+.0f}%)")
        settle(now)
        if int(now) % 1800 < 60:
            br = (f"model {scored['model_brier']/scored['n']:.4f} vs mkt {scored['market_brier']/scored['n']:.4f} "
                  f"({scored['model_better']}/{scored['n']} model better)") if scored["n"] else "no settled strikes yet"
            logger.info(f"[STATUS] {(now-t0)/3600:.1f}h | flagged={len(flagged)} settled={scored['n']} | {br}")
    except Exception as e:
        logger.warning(f"loop err {e}")
    time.sleep(60)


if __name__ == "__main__":
    main()
