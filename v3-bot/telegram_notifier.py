"""Lightweight Telegram notifier for V12 bot."""
import os
import threading
import time
from loguru import logger

try:
    import httpx
    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False

_lock = threading.Lock()
_last_sent: dict = {}


def _get_token():
    return os.getenv("TELEGRAM_BOT_TOKEN", "")


def _get_chat():
    return os.getenv("TELEGRAM_CHAT_ID", "")


def _send(text: str, dedup_key: str = "", cooldown: float = 5.0):
    token = _get_token()
    chat = _get_chat()
    if not token or not chat or not _HAS_HTTPX:
        return
    now = time.time()
    if dedup_key:
        with _lock:
            if now - _last_sent.get(dedup_key, 0) < cooldown:
                return
            _last_sent[dedup_key] = now

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    def _do():
        try:
            with httpx.Client(timeout=10.0) as c:
                c.post(url, json={
                    "chat_id": chat,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                })
        except Exception as e:
            logger.debug(f"[TG] Send failed: {e}")

    threading.Thread(target=_do, daemon=True).start()


def notify_fill(coin: str, direction: str, shares: int, price: float, cost: float, edge: float, prob: float):
    _send(
        f"<b>BET PLACED</b>\n"
        f"{coin} {direction}\n"
        f"Entry: {price*100:.0f}c | {shares} shares | ${cost:.2f}\n"
        f"Prob: {prob:.0%} | Edge: {edge*100:.1f}%",
        dedup_key=f"fill-{coin}-{direction}",
    )


def notify_result(coin: str, direction: str, won: bool, cost: float, payout: float = 0):
    if won:
        pnl = payout - cost
        _send(
            f"<b>WIN +${pnl:.2f}</b>\n"
            f"{coin} {direction}\n"
            f"Cost: ${cost:.2f} | Payout: ${payout:.2f}",
            dedup_key=f"result-{coin}",
        )
    else:
        _send(
            f"<b>LOSS -${cost:.2f}</b>\n"
            f"{coin} {direction}\n"
            f"Cost: ${cost:.2f} | Expired worthless",
            dedup_key=f"result-{coin}",
        )


def notify_error(msg: str):
    _send(f"<b>ERROR</b>\n{msg}", dedup_key="error", cooldown=60.0)


def notify_startup():
    _send("<b>BOT STARTED</b>\nV12 engine online, scanning...", dedup_key="startup")


def test():
    token = _get_token()
    chat = _get_chat()
    if not token or not chat:
        logger.warning("[TG] No TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID in .env")
        return False
    if not _HAS_HTTPX:
        logger.warning("[TG] httpx not available")
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        with httpx.Client(timeout=10.0) as c:
            r = c.post(url, json={"chat_id": chat, "text": "Bot connected."})
            ok = r.status_code == 200
            if ok:
                logger.info("[TG] Telegram connected")
            else:
                logger.warning(f"[TG] Telegram test failed: {r.status_code} {r.text[:100]}")
            return ok
    except Exception as e:
        logger.warning(f"[TG] Telegram test error: {e}")
        return False
