from __future__ import annotations

from tsdp2._deps import *
from tsdp2.logging_config import LOGGER, _register_secret
from tsdp2.config import _html_escape, _safe_int, load_settings
from tsdp2.astronomy.fits_io import load_2d_and_header
from tsdp2.astronomy.previews import make_thumbnail

class TelegramNotifier:
    """
    Send messages and photos via the Telegram Bot API.
    No third-party libraries — only stdlib urllib.
    """

    _API = "https://api.telegram.org/bot{token}/{method}"

    def __init__(self, token: str, chat_id: str):
        self.token   = token.strip()
        self.chat_id = chat_id.strip()
        _register_secret(self.token)

    @property
    def ready(self) -> bool:
        return bool(self.token and self.chat_id)

    # ── low-level HTTP ────────────────────────────────────────────────────────

    def _post_json(self, method: str, payload: dict, timeout: int = 12) -> dict:
        import urllib.request
        import urllib.error
        url  = self._API.format(token=self.token, method=method)
        body = _json.dumps(payload).encode()
        req  = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return _json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8", "replace")
            except EXPECTED_OPERATION_ERRORS:
                detail = str(e)
            raise RuntimeError(f"Telegram {method} HTTP {e.code}: {detail}") from e

    def _post_multipart(self, method: str, fields: dict,
                         file_field: str, file_name: str,
                         file_bytes: bytes, file_ct: str = "image/png",
                         timeout: int = 30) -> dict:
        import urllib.request
        import urllib.error
        boundary = b"----JWSTSuiteBnd"

        def part_field(name: str, value: str) -> bytes:
            return (b"--" + boundary + b"\r\n"
                    b'Content-Disposition: form-data; name="'
                    + name.encode() + b'"\r\n\r\n'
                    + value.encode() + b"\r\n")

        safe_file_name = str(file_name or "file").replace('"', "_").replace("\r", "_").replace("\n", "_")
        body = b"".join(part_field(k, v) for k, v in fields.items())
        body += (b"--" + boundary + b"\r\n"
                 b'Content-Disposition: form-data; name="'
                 + file_field.encode()
                 + b'"; filename="' + safe_file_name.encode("utf-8", "replace") + b'"\r\n'
                 b"Content-Type: " + file_ct.encode() + b"\r\n\r\n"
                 + file_bytes + b"\r\n"
                 + b"--" + boundary + b"--\r\n")

        url = self._API.format(token=self.token, method=method)
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type":
                     f"multipart/form-data; boundary={boundary.decode()}"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return _json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8", "replace")
            except EXPECTED_OPERATION_ERRORS:
                detail = str(e)
            raise RuntimeError(f"Telegram {method} HTTP {e.code}: {detail}") from e

    # ── public API ────────────────────────────────────────────────────────────

    def send(self, text: str, silent: bool = False) -> bool:
        """Send a text message. Try HTML first, then fall back to plain text."""
        if not self.ready:
            return False
        try:
            r = self._post_json("sendMessage", {
                "chat_id":              self.chat_id,
                "text":                 str(text)[:4096],
                "parse_mode":           "HTML",
                "disable_notification": silent,
            })
            return bool(r.get("ok"))
        except EXPECTED_OPERATION_ERRORS as exc:
            # Bad HTML in filenames/user text should not kill notifications.
            try:
                plain = re.sub(r"<[^>]*>", "", str(text))
                r = self._post_json("sendMessage", {
                    "chat_id": self.chat_id,
                    "text": plain[:4096],
                    "disable_notification": silent,
                })
                return bool(r.get("ok"))
            except EXPECTED_OPERATION_ERRORS:
                LOGGER.warning("Telegram send failed: %s", exc,
                               exc_info=True)
                return False

    def send_photo(self, png_bytes: bytes, caption: str = "") -> bool:
        """Send a PNG image with optional caption."""
        if not self.ready or not png_bytes:
            return False
        try:
            r = self._post_multipart(
                "sendPhoto",
                fields={"chat_id": self.chat_id,
                        "caption": caption[:1024],
                        "parse_mode": "HTML"},
                file_field="photo",
                file_name="mosaic.png",
                file_bytes=png_bytes,
            )
            return bool(r.get("ok"))
        except EXPECTED_OPERATION_ERRORS as exc:
            try:
                plain = re.sub(r"<[^>]*>", "", str(caption))[:1024]
                r = self._post_multipart(
                    "sendPhoto",
                    fields={"chat_id": self.chat_id, "caption": plain},
                    file_field="photo",
                    file_name="mosaic.png",
                    file_bytes=png_bytes,
                )
                return bool(r.get("ok"))
            except EXPECTED_OPERATION_ERRORS:
                LOGGER.warning("Telegram photo upload failed: %s", exc,
                               exc_info=True)
                return False


    def send_document(self, path: str, caption: str = "") -> bool:
        """Send any local file as a Telegram document.

        Telegram Bot API is unreliable with huge files and reading massive FITS
        into memory can freeze the app, so refuse oversized documents instead
        of silently killing the process. Previews are still sent as photos.
        """
        if not self.ready or not path or not os.path.isfile(path):
            return False
        try:
            max_mb = _safe_int(load_settings().get("tg_document_max_mb", 49), 49, 1, 49)
            size = os.path.getsize(path)
            if size > max_mb * 1024 * 1024:
                self.send(
                    f"⚠️ File too large for Telegram document upload: "
                    f"<code>{_html_escape(os.path.basename(path))}</code> "
                    f"({size/1e6:.1f} MB)."
                )
                return False
            with open(path, "rb") as f:
                data = f.read()
            r = self._post_multipart(
                "sendDocument",
                fields={"chat_id": self.chat_id,
                        "caption": caption[:1024],
                        "parse_mode": "HTML"},
                file_field="document",
                file_name=os.path.basename(path),
                file_bytes=data,
                file_ct="application/octet-stream",
                timeout=120,
            )
            return bool(r.get("ok"))
        except EXPECTED_OPERATION_ERRORS as exc:
            try:
                with open(path, "rb") as f:
                    data = f.read()
                plain = re.sub(r"<[^>]*>", "", str(caption))[:1024]
                r = self._post_multipart(
                    "sendDocument",
                    fields={"chat_id": self.chat_id, "caption": plain},
                    file_field="document",
                    file_name=os.path.basename(path),
                    file_bytes=data,
                    file_ct="application/octet-stream",
                    timeout=120,
                )
                return bool(r.get("ok"))
            except EXPECTED_OPERATION_ERRORS:
                LOGGER.warning("Telegram document upload failed: %s", exc,
                               exc_info=True)
                return False

    def send_dashboard(self, text: str) -> int | None:
        """Send the live dashboard and return its Telegram message_id."""
        if not self.ready:
            return None
        try:
            r = self._post_json("sendMessage", {
                "chat_id": self.chat_id,
                "text": str(text)[:4096],
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            })
            if r.get("ok"):
                return (r.get("result") or {}).get("message_id")
        except EXPECTED_OPERATION_ERRORS as exc:
            LOGGER.warning("Telegram dashboard send failed: %s", exc,
                           exc_info=True)
        return None

    def edit_dashboard(self, message_id: int, text: str) -> bool:
        """Edit an existing Telegram dashboard message in place."""
        if not self.ready or not message_id:
            return False
        try:
            r = self._post_json("editMessageText", {
                "chat_id": self.chat_id,
                "message_id": message_id,
                "text": str(text)[:4096],
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            })
            return bool(r.get("ok"))
        except EXPECTED_OPERATION_ERRORS as exc:
            # Telegram returns an error when the new dashboard text is identical;
            # that is not a real failure. For bad HTML, retry plain text.
            if "message is not modified" in str(exc).lower():
                return True
            try:
                plain = re.sub(r"<[^>]*>", "", str(text))[:4096]
                r = self._post_json("editMessageText", {
                    "chat_id": self.chat_id,
                    "message_id": message_id,
                    "text": plain,
                    "disable_web_page_preview": True,
                })
                return bool(r.get("ok"))
            except EXPECTED_OPERATION_ERRORS:
                LOGGER.warning("Telegram dashboard edit failed: %s", exc,
                               exc_info=True)
                return False

    def get_updates(self, timeout: int = 5) -> list:
        """Return recent updates (messages sent to the bot)."""
        try:
            r = self._post_json("getUpdates",
                                {"timeout": timeout, "limit": 10})
            return r.get("result", [])
        except EXPECTED_OPERATION_ERRORS:
            return []

    @staticmethod
    def fits_to_png(fits_path: str, max_px: int = 900) -> bytes | None:
        """Return a stretched PNG of a FITS file, or None on failure."""
        if not HAVE_PIL:
            return None
        try:
            import io as _io
            data, _ = load_2d_and_header(fits_path, preserve_nan=True)
            img = make_thumbnail(data, "asinh", max_px=max_px)
            if img is None:
                return None
            buf = _io.BytesIO()
            img.convert("RGB").save(buf, format="PNG", optimize=True)
            return buf.getvalue()
        except EXPECTED_OPERATION_ERRORS as exc:
            LOGGER.warning("Telegram thumbnail creation failed: %s", exc,
                           exc_info=True)
            return None

def send_buttons(self, text: str, keyboard: list, silent: bool = False) -> bool:
    """Send a Telegram message with inline buttons.

    keyboard format: [[{"text": "Previews", "callback_data": "out:previews"}, ...], ...]
    """
    if not self.ready:
        return False
    payload = {
        "chat_id": self.chat_id,
        "text": str(text)[:4096],
        "parse_mode": "HTML",
        "disable_notification": silent,
        "disable_web_page_preview": True,
        "reply_markup": {"inline_keyboard": keyboard or []},
    }
    try:
        r = self._post_json("sendMessage", payload)
        return bool(r.get("ok"))
    except EXPECTED_OPERATION_ERRORS as exc:
        try:
            plain = re.sub(r"<[^>]*>", "", str(text))[:4096]
            payload.pop("parse_mode", None)
            payload["text"] = plain
            r = self._post_json("sendMessage", payload)
            return bool(r.get("ok"))
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.warning("Telegram button send failed: %s", exc,
                           exc_info=True)
            return False

def answer_callback(self, callback_query_id: str, text: str = "") -> bool:
    if not self.ready or not callback_query_id:
        return False
    try:
        payload = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = str(text)[:180]
        r = self._post_json("answerCallbackQuery", payload)
        return bool(r.get("ok"))
    except EXPECTED_OPERATION_ERRORS:
        return False

def send_animation(self, path: str, caption: str = "") -> bool:
    """Send a GIF via sendAnimation so Telegram plays it inline."""
    if not self.ready or not path or not os.path.isfile(path):
        return False
    try:
        size = os.path.getsize(path)
        if size > 49 * 1024 * 1024:
            self.send(f"⚠️ GIF too large for Telegram: {size/1e6:.1f} MB.")
            return False
        with open(path, "rb") as f:
            data = f.read()
        r = self._post_multipart(
            "sendAnimation",
            fields={"chat_id": self.chat_id,
                    "caption": caption[:1024],
                    "parse_mode": "HTML"},
            file_field="animation",
            file_name=os.path.basename(path),
            file_bytes=data,
            file_ct="image/gif",
            timeout=120,
        )
        return bool(r.get("ok"))
    except EXPECTED_OPERATION_ERRORS as exc:
        LOGGER.warning("Telegram animation upload failed: %s", exc,
                       exc_info=True)
        return False

__all__ = [name for name in globals() if not name.startswith('__')]
