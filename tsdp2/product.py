"""Lightweight product identity shared by the GUI, tests, and packager."""

from __future__ import annotations

import os


APP_TITLE = "TSDP"
APP_NAME = "Tash Space Data Preprocessor"
APP_SUBTITLE = APP_NAME
APP_VERSION = "3.2.2"
APP_UPDATE_URL = os.environ.get("TSDP_UPDATE_URL", "").strip()
DEFAULT_ARIA2 = "aria2c.exe"


__all__ = [
    "APP_TITLE",
    "APP_NAME",
    "APP_SUBTITLE",
    "APP_VERSION",
    "APP_UPDATE_URL",
    "DEFAULT_ARIA2",
]