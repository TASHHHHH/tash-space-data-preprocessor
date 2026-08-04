from __future__ import annotations

import html as _html
import json as _json
import os
import shutil
import sys
import threading
from pathlib import Path
from typing import Any, Mapping

from tsdp2.product import DEFAULT_ARIA2


EXPECTED_OPERATION_ERRORS = (
    OSError,
    ValueError,
    TypeError,
    LookupError,
    AttributeError,
    RuntimeError,
    ImportError,
)
from tsdp2.logging_config import LOGGER, _register_secret

_SETTINGS_FILE = Path.home() / ".jwst_suite_config.json"


_SETTINGS_LOCK = threading.RLock()


_TELEGRAM_TOKEN_ENV = "JWST_SUITE_TELEGRAM_TOKEN"


_KEYRING_SERVICE = "TSDP"


_KEYRING_USERNAME = "telegram-bot-token"


_SECRET_STORAGE_WARNING = ""


def settings_file_path() -> Path:
    """Return the user-specific JSON settings path."""
    return _SETTINGS_FILE


def _set_secret_storage_warning(message: str) -> None:
    global _SECRET_STORAGE_WARNING
    _SECRET_STORAGE_WARNING = str(message or "")
    if _SECRET_STORAGE_WARNING:
        LOGGER.warning(_SECRET_STORAGE_WARNING)


def telegram_secret_storage_warning() -> str:
    """Return the latest credential-storage warning, if any."""
    return _SECRET_STORAGE_WARNING


def _keyring_module():
    """Return the optional keyring module, or ``None`` when unavailable."""
    try:
        import keyring
        return keyring
    except ImportError:
        return None


def _credential_get() -> str:
    keyring = _keyring_module()
    if not keyring:
        return ""
    try:
        return str(keyring.get_password(_KEYRING_SERVICE, _KEYRING_USERNAME) or "")
    except EXPECTED_OPERATION_ERRORS as exc:
        _set_secret_storage_warning(
            "Secure credential storage is unavailable; the Telegram token "
            f"could not be read ({type(exc).__name__}).")
        return ""


def _credential_set(token: str) -> bool:
    keyring = _keyring_module()
    if not keyring:
        _set_secret_storage_warning(
            "The optional 'keyring' package is not installed. The Telegram "
            "token cannot be stored securely; set JWST_SUITE_TELEGRAM_TOKEN "
            "or install keyring.")
        return False
    try:
        if token:
            keyring.set_password(_KEYRING_SERVICE, _KEYRING_USERNAME, token)
        else:
            try:
                keyring.delete_password(_KEYRING_SERVICE, _KEYRING_USERNAME)
            except EXPECTED_OPERATION_ERRORS:
                # Deleting a credential that does not exist is harmless and
                # backend-specific keyring exceptions are not standardized.
                LOGGER.debug("Best-effort operation failed", exc_info=True)
        return True
    except EXPECTED_OPERATION_ERRORS as exc:
        _set_secret_storage_warning(
            "Secure credential storage is unavailable; the Telegram token "
            f"was not stored ({type(exc).__name__}).")
        return False


def _read_settings_file() -> dict[str, Any]:
    try:
        with _SETTINGS_FILE.open(encoding="utf-8") as handle:
            payload = _json.load(handle)
        if not isinstance(payload, dict):
            raise TypeError("settings root must be a JSON object")
        return payload
    except FileNotFoundError:
        return {}
    except (_json.JSONDecodeError, OSError, TypeError) as exc:
        LOGGER.warning("Settings could not be loaded from %s: %s",
                       _SETTINGS_FILE, exc)
        return {}


def _write_settings_file(data: Mapping[str, Any]) -> None:
    """Write settings atomically, including a flush to stable storage."""
    _SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _SETTINGS_FILE.with_suffix(_SETTINGS_FILE.suffix + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            _json.dump(dict(data), handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, _SETTINGS_FILE)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            LOGGER.debug("Could not remove settings temporary file %s",
                         tmp, exc_info=True)


def load_settings() -> dict[str, Any]:
    """Load non-secret settings and inject the Telegram token in memory.

    Token precedence is the environment variable, the OS credential store,
    then a legacy plaintext value. Legacy values are migrated and removed from
    JSON only after secure storage confirms a successful write.
    """
    with _SETTINGS_LOCK:
        data = _read_settings_file()
        legacy_token = str(data.get("tg_token", "") or "").strip()
        env_token = str(os.environ.get(_TELEGRAM_TOKEN_ENV, "") or "").strip()
        stored_token = "" if env_token else _credential_get().strip()
        token = env_token or stored_token or legacy_token
        _register_secret(token)

        if legacy_token and not env_token and not stored_token:
            if _credential_set(legacy_token):
                data.pop("tg_token", None)
                try:
                    _write_settings_file(data)
                    LOGGER.info("Migrated the Telegram token to secure storage")
                except OSError:
                    # Keep the in-memory token and leave the legacy file intact
                    # if its cleanup cannot be committed atomically.
                    LOGGER.exception("Could not save settings after credential migration")
            else:
                data.pop("tg_token", None)
                try:
                    _write_settings_file(data)
                except OSError:
                    LOGGER.exception(
                        "Could not remove the legacy plaintext Telegram token")
                _set_secret_storage_warning(
                    "The legacy plaintext Telegram token was removed. It is "
                    "available for this session only because secure credential "
                    "storage is unavailable; set JWST_SUITE_TELEGRAM_TOKEN or "
                    "install/configure keyring before restarting.")

        if token:
            data["tg_token"] = token
        return data


def save_settings(data: Mapping[str, Any]) -> bool:
    """Atomically save settings without a plaintext Telegram token.

    Returns ``True`` when both settings and any supplied token were persisted
    securely. If no secure backend exists, the token remains session-only and
    a visible warning is made available to the Settings tab.
    """
    clean = dict(data)
    token = str(clean.pop("tg_token", "") or "").strip()
    _register_secret(token)

    secure_ok = True
    if token and not os.environ.get(_TELEGRAM_TOKEN_ENV):
        secure_ok = _credential_set(token)
    elif not token and not os.environ.get(_TELEGRAM_TOKEN_ENV):
        if _credential_get():
            _credential_set("")

    try:
        with _SETTINGS_LOCK:
            _write_settings_file(clean)
        return secure_ok
    except OSError as exc:
        LOGGER.exception("Settings save failed: %s", exc)
        return False


def default_aria2_path(settings: dict | None = None) -> str:
    """Return the user's saved aria2 executable path, or a sane default.

    This is intentionally stored outside the project file in the user's home
    folder, so each computer/user can keep their own aria2c.exe location.
    """
    settings = settings or {}
    saved = str(settings.get("aria2_path", "")).strip()
    if saved:
        return saved

    found = shutil.which(DEFAULT_ARIA2) or shutil.which("aria2c")
    if found:
        return found

    try:
        base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    except EXPECTED_OPERATION_ERRORS:
        base = Path.cwd()
    return str(base / DEFAULT_ARIA2)


def default_download_dir(settings: dict | None = None) -> str:
    """Return the saved download directory, falling back to Downloads."""
    settings = settings or {}
    saved = str(settings.get("download_dir", "")).strip()
    if saved:
        return saved
    return str(Path.home() / "Downloads")


def resolve_executable_path(path_or_name: str) -> str:
    """Resolve an executable set in the GUI.

    Accepts either an absolute path like C:/aria2c/aria2c.exe or a command
    available on PATH like aria2c / aria2c.exe.
    """
    raw = (path_or_name or "").strip().strip('"')
    if not raw:
        return ""
    p = Path(raw)
    if p.exists():
        return str(p)
    found = shutil.which(raw)
    if found:
        return found
    return raw


def path_exists_or_on_path(path_or_name: str) -> bool:
    raw = (path_or_name or "").strip().strip('"')
    if not raw:
        return False
    return Path(raw).exists() or shutil.which(raw) is not None


def _html_escape(value) -> str:
    import html as _html
    return _html.escape(str(value if value is not None else ""), quote=False)


def _safe_int(value, default=1, lo=None, hi=None):
    """Return a bounded int from Tk variables / user text without crashing."""
    try:
        n = int(str(value).strip())
    except EXPECTED_OPERATION_ERRORS:
        n = default
    if lo is not None:
        n = max(lo, n)
    if hi is not None:
        n = min(hi, n)
    return n

__all__ = [name for name in globals() if not name.startswith('__')]
