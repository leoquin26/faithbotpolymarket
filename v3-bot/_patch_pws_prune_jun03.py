#!/usr/bin/env python3
"""Fix POLY-WS subscribing to hundreds of stale window token IDs."""
from pathlib import Path
import shutil
from datetime import datetime

PWS = Path("/home/ubuntu/v3-bot/polymarket_ws.py")
RUN = Path("/home/ubuntu/v3-bot/run_bot.py")
OM = Path("/home/ubuntu/v3-bot/order_manager.py")
ts = datetime.now().strftime("%Y%m%d_%H%M%S")

OLD_SUB = """    def subscribe(self, token_ids: Iterable[str]) -> None:
        \"\"\"Add token_ids to the subscription set; (re)sends the subscribe
        message if connected.\"\"\"
        new = set(token_ids) - self._subscribed
        if not new:
            return
        self._subscribed.update(new)
        self._send_subscribe(list(new))"""

NEW_SUB = """    def subscribe(self, token_ids: Iterable[str]) -> None:
        \"\"\"Add token_ids to the subscription set; (re)sends the subscribe
        message if connected. Prefer set_subscriptions() each scan.\"\"\"
        new = set(token_ids) - self._subscribed
        if not new:
            return
        self._subscribed.update(new)
        self._send_subscribe(list(new), label="new")

    def set_subscriptions(self, token_ids: Iterable[str]) -> None:
        \"\"\"Replace active subs with exactly these tokens (current window only).
        Prevents unbounded growth across 15m windows (was 400+ tokens/day).\"\"\"
        new_set = {t for t in token_ids if t}
        if new_set == self._subscribed:
            return
        dropped = len(self._subscribed - new_set)
        self._subscribed = new_set
        # Drop stale book cache for old windows
        if dropped:
            with self._lock:
                for tid in list(self._books.keys()):
                    if tid not in new_set:
                        self._books.pop(tid, None)
        if self._subscribed and self._ws:
            self._send_subscribe(list(self._subscribed), label="active")"""

OLD_SEND = """    def _send_subscribe(self, token_ids: List[str]) -> None:
        if not self._ws:
            return
        try:
            msg = {
                "type": "Market",
                "assets_ids": list(token_ids),
            }
            self._ws.send(json.dumps(msg))
            logger.info(f"[POLY-WS] subscribed to {len(token_ids)} tokens")
        except Exception as e:
            logger.debug(f"[POLY-WS] send subscribe failed: {e}")"""

OLD_OPEN = """        if self._subscribed:
            self._send_subscribe(list(self._subscribed))"""

NEW_OPEN = """        if self._subscribed:
            self._send_subscribe(list(self._subscribed), label="reconnect")"""

NEW_SEND = """    def _send_subscribe(self, token_ids: List[str], label: str = "") -> None:
        if not self._ws:
            return
        try:
            msg = {
                "type": "Market",
                "assets_ids": list(token_ids),
            }
            self._ws.send(json.dumps(msg))
            _tag = f" ({label})" if label else ""
            logger.info(
                f"[POLY-WS] subscribed to {len(token_ids)} tokens{_tag} "
                f"[active={len(self._subscribed)}]"
            )
        except Exception as e:
            logger.debug(f"[POLY-WS] send subscribe failed: {e}")"""

OLD_RUN = """                if _batch_ids:
                    _pws_mod.subscribe(_batch_ids)"""

NEW_RUN = """                if _batch_ids:
                    _pws_mod.set_subscriptions(_batch_ids)"""

OLD_OM = """                _pws.subscribe([token_id])  # idempotent"""

NEW_OM = """                # WS subs refreshed once per scan in run_bot (set_subscriptions)"""


def patch(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        if label in text and "set_subscriptions" in text:
            print(f"  {label}: already patched")
            return
        raise SystemExit(f"  {label}: anchor not found")
    shutil.copy2(path, path.with_suffix(path.suffix + f".bak_{ts}"))
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print(f"  {label}: OK")


OLD_CONV = '''def subscribe(token_ids: Iterable[str]) -> None:
    """Convenience: subscribe to a list of token_ids."""
    if not _ENABLED:
        return
    get_singleton().subscribe(token_ids)


def is_connected() -> bool:'''

NEW_CONV = '''def subscribe(token_ids: Iterable[str]) -> None:
    """Convenience: subscribe to a list of token_ids."""
    if not _ENABLED:
        return
    get_singleton().subscribe(token_ids)


def set_subscriptions(token_ids: Iterable[str]) -> None:
    """Convenience: replace subs with current-window token_ids only."""
    if not _ENABLED:
        return
    get_singleton().set_subscriptions(token_ids)


def is_connected() -> bool:'''


def main() -> None:
    print("Patching POLY-WS subscription leak...")
    patch(PWS, OLD_SUB, NEW_SUB, "polymarket_ws subscribe")
    patch(PWS, OLD_SEND, NEW_SEND, "polymarket_ws send")
    patch(PWS, OLD_OPEN, NEW_OPEN, "polymarket_ws on_open")
    patch(PWS, OLD_CONV, NEW_CONV, "polymarket_ws conv")
    patch(RUN, OLD_RUN, NEW_RUN, "run_bot batch")
    patch(OM, OLD_OM, NEW_OM, "order_manager")
    print("Done.")


if __name__ == "__main__":
    main()
