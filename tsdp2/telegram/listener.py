from __future__ import annotations

from tsdp2._deps import *
from tsdp2.logging_config import LOGGER
from tsdp2.telegram.client import TelegramNotifier

def _tg_poll_loop(self):
    """Telegram listener with support for messages and inline button callbacks."""
    offset = 0
    try:
        tok0 = str(getattr(self, "_tg_runtime_token", "")).strip()
        cid0 = str(getattr(self, "_tg_runtime_chat_id", "")).strip()
        if tok0 and cid0:
            tg0 = TelegramNotifier(tok0, cid0)
            data0 = tg0._post_json("getUpdates", {"timeout": 0, "limit": 100}, timeout=8)
            for upd in data0.get("result", []):
                offset = max(offset, int(upd.get("update_id", 0)) + 1)
    except EXPECTED_OPERATION_ERRORS:
        offset = 0

    while True:
        try:
            if getattr(self, "_tg_listener_stop", False):
                return
            if not bool(getattr(self, "_tg_runtime_enabled", False)):
                time.sleep(2.0); continue
            tok = str(getattr(self, "_tg_runtime_token", "")).strip()
            cid = str(getattr(self, "_tg_runtime_chat_id", "")).strip()
            if not tok or not cid:
                time.sleep(2.0); continue

            tg = TelegramNotifier(tok, cid)
            payload = {"timeout": 20, "limit": 20}
            if offset:
                payload["offset"] = offset
            try:
                data = tg._post_json("getUpdates", payload, timeout=25)
            except EXPECTED_OPERATION_ERRORS:
                time.sleep(3.0); continue

            for upd in data.get("result", []):
                offset = max(offset, int(upd.get("update_id", 0)) + 1)

                cb = upd.get("callback_query") or {}
                if cb:
                    msg = cb.get("message") or {}
                    chat = msg.get("chat") or {}
                    if str(chat.get("id")) != str(cid):
                        continue
                    try:
                        tg.answer_callback(cb.get("id"), "OK")
                    except EXPECTED_OPERATION_ERRORS:
                        LOGGER.debug("Best-effort operation failed", exc_info=True)
                    data_text = str(cb.get("data") or "")
                    try:
                        self._tg_command_queue.put((data_text, tg))
                    except EXPECTED_OPERATION_ERRORS as exc:
                        # Never run the handler on this listener thread: it may
                        # touch Tk objects, which is only safe on the main loop.
                        LOGGER.warning("Telegram callback dropped: %s", exc,
                                       exc_info=True)
                    continue

                msg = upd.get("message") or upd.get("edited_message") or {}
                chat = msg.get("chat") or {}
                if str(chat.get("id")) != str(cid):
                    continue
                doc = msg.get("document") or {}
                if doc:
                    fname = str(doc.get("file_name", "") or "")
                    fsize = int(doc.get("file_size", 0) or 0)
                    if fname.lower().endswith(".csv") and 0 < fsize <= 8 * 1024 * 1024:
                        try:
                            self._tg_command_queue.put(
                                (f"__tgcsv__:{doc.get('file_id', '')}|{fname}", tg))
                        except EXPECTED_OPERATION_ERRORS as exc:
                            LOGGER.warning("Telegram CSV document dropped: %s",
                                           exc, exc_info=True)
                    else:
                        tg.send("I only accept .csv documents up to 8 MB.")
                    continue
                try:
                    self._tg_command_queue.put((msg.get("text", ""), tg))
                except EXPECTED_OPERATION_ERRORS as exc:
                    LOGGER.warning("Telegram message dropped: %s", exc,
                                   exc_info=True)
        except EXPECTED_OPERATION_ERRORS as exc:
            try:
                LOGGER.warning("Telegram command listener failed: %s", exc,
                               exc_info=True)
            except EXPECTED_OPERATION_ERRORS:
                LOGGER.debug("Best-effort operation failed", exc_info=True)
            time.sleep(3.0)


def poll_command_queue(controller) -> None:
    """Dispatch queued bot commands on Tk's owning thread."""
    try:
        while True:
            try:
                raw_text, notifier = controller._tg_command_queue.get_nowait()
            except queue.Empty:
                break
            try:
                from tsdp2.telegram.commands import _tg_handle_command
                _tg_handle_command(controller, raw_text, notifier)
            except (OSError, ValueError, TypeError, KeyError, RuntimeError, tk.TclError) as exc:
                notifier.send(f"⚠️ Command failed:\n<pre>{str(exc)[:1200]}</pre>")
                LOGGER.warning("Telegram command failed: %s", exc, exc_info=True)
    finally:
        try:
            controller.after(120, controller._tg_poll_command_queue)
        except tk.TclError:
            LOGGER.debug("Telegram command polling stopped with the Tk window")


def ensure_listener(controller) -> None:
    """Start one daemon listener and schedule the next lifecycle check."""
    try:
        thread = controller._tg_listener_thread
        configured = (
            controller.tg_enabled.get()
            and controller.tg_token.get().strip()
            and controller.tg_chat_id.get().strip()
        )
        if configured and (thread is None or not thread.is_alive()):
            controller._tg_listener_stop = False
            thread = threading.Thread(target=_tg_poll_loop, args=(controller,), daemon=True)
            controller._tg_listener_thread = thread
            thread.start()
    except (AttributeError, RuntimeError, tk.TclError) as exc:
        LOGGER.warning("Telegram listener failed to start: %s", exc, exc_info=True)
    try:
        controller.after(5000, controller._tg_ensure_listener)
    except tk.TclError:
        LOGGER.debug("Telegram listener lifecycle stopped with the Tk window")


__all__ = [name for name in globals() if not name.startswith('__')]
