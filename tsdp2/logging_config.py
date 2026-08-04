from __future__ import annotations

import logging
import os
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional


_LOG_NAME = "tsdp"
_KNOWN_SECRETS: set[str] = set()
_TELEGRAM_URL_RE = re.compile(
    r"https?://api\.telegram\.org/bot[^/\s]+",
    re.IGNORECASE,
)


def _register_secret(secret: str) -> None:
    """Register a runtime secret so log records can redact it."""
    value = str(secret or "").strip()
    if value:
        _KNOWN_SECRETS.add(value)


def _redact_sensitive_text(value: object) -> str:
    """Return a log-safe representation of *value*."""
    text = str(value)
    text = _TELEGRAM_URL_RE.sub(
        "https://api.telegram.org/bot<redacted>",
        text,
    )
    for secret in tuple(_KNOWN_SECRETS):
        if secret:
            text = text.replace(secret, "<redacted>")
    return text


class _SensitiveDataFilter(logging.Filter):
    """Remove registered secrets and Telegram API tokens from log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact_sensitive_text(record.getMessage())
        record.args = ()
        return True


def _default_log_directory() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "TSDP" / "logs"
    return Path.home() / ".jwst_suite" / "logs"


def configure_logging(log_dir: Optional[Path] = None) -> logging.Logger:
    """Configure console and rotating-file diagnostics once per process."""
    logger = logging.getLogger(_LOG_NAME)
    if getattr(logger, "_tsdp_configured", False):
        return logger

    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )
    redactor = _SensitiveDataFilter()

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(formatter)
    console.addFilter(redactor)
    logger.addHandler(console)

    try:
        target_dir = Path(log_dir) if log_dir else _default_log_directory()
        target_dir.mkdir(parents=True, exist_ok=True)
        rotating = RotatingFileHandler(
            target_dir / "tsdp.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        rotating.setLevel(logging.DEBUG)
        rotating.setFormatter(formatter)
        rotating.addFilter(redactor)
        logger.addHandler(rotating)
    except OSError as exc:
        logger.warning("File logging unavailable: %s", exc)

    logger._tsdp_configured = True
    return logger


LOGGER = logging.getLogger(_LOG_NAME)


__all__ = [
    "LOGGER",
    "configure_logging",
    "_redact_sensitive_text",
    "_register_secret",
]