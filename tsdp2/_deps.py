"""Shared standard-library and optional dependency imports."""

import gc
import hashlib
import json
import logging
import os
import queue
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid
import warnings
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Protocol
from urllib.parse import parse_qs, quote, unquote, urlparse

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
from reproject import reproject_interp
from scipy.ndimage import (
    binary_dilation,
    binary_propagation,
    distance_transform_edt,
    find_objects,
    gaussian_filter,
    label as ndi_label,
)

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

HAVE_PIL = True
PIL_IMPORT_ERR = ""
try:
    from PIL import Image, ImageTk
except ImportError as exc:
    HAVE_PIL = False
    PIL_IMPORT_ERR = str(exc)
    Image = ImageTk = None

HAVE_MOSAIC = True
MOSAIC_IMPORT_ERR = ""
try:
    from reproject.mosaicking import find_optimal_celestial_wcs, reproject_and_coadd
except ImportError as exc:
    HAVE_MOSAIC = False
    MOSAIC_IMPORT_ERR = str(exc)
    find_optimal_celestial_wcs = reproject_and_coadd = None

try:
    import sv_ttk
    HAVE_SVTTK = True
except ImportError:
    sv_ttk = None
    HAVE_SVTTK = False

from tsdp2.product import *

# Expected failures at file/network/library/UI boundaries.  This deliberately
# excludes arbitrary ``Exception`` so programming errors are not swallowed.
EXPECTED_OPERATION_ERRORS = (
    OSError,
    ValueError,
    TypeError,
    LookupError,
    AttributeError,
    RuntimeError,
    ArithmeticError,
    MemoryError,
    EOFError,
    ImportError,
    subprocess.SubprocessError,
    zipfile.BadZipFile,
    fits.VerifyError,
    tk.TclError,
)

_json = json
_LOG_NAME = "tsdp"
_KNOWN_SECRETS: set[str] = set()
_TELEGRAM_URL_RE = re.compile(
    r"https?://api\.telegram\.org/bot[^/\s]+", re.IGNORECASE
)

__all__ = [name for name in globals() if not name.startswith("__")]
