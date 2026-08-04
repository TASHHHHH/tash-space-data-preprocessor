from __future__ import annotations

import importlib.util
import os
import platform
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Mapping

from tsdp2.config import default_aria2_path, load_settings, settings_file_path


PASS = "pass"
WARNING = "warning"
FAIL = "fail"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    detail: str
    required: bool = False


@dataclass(frozen=True)
class InstallationReport:
    checked_at: str
    results: tuple[CheckResult, ...]

    @property
    def ready(self) -> bool:
        return not any(item.required and item.status == FAIL for item in self.results)

    @property
    def warning_count(self) -> int:
        return sum(item.status == WARNING for item in self.results)

    @property
    def failure_count(self) -> int:
        return sum(item.status == FAIL for item in self.results)

    def to_text(self) -> str:
        heading = "READY" if self.ready else "NEEDS ATTENTION"
        lines = [
            "Tash Space Data Preprocessor installation report",
            f"Checked: {self.checked_at}",
            f"Overall: {heading}",
            "",
        ]
        symbols = {PASS: "OK", WARNING: "WARN", FAIL: "FAIL"}
        for item in self.results:
            requirement = "required" if item.required else "optional"
            lines.append(
                f"[{symbols.get(item.status, item.status.upper())}] "
                f"{item.name} ({requirement}) - {item.detail}"
            )
        return "\n".join(lines)


def _distribution_version(distribution: str) -> str:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return ""


def _module_check(
    label: str,
    module: str,
    *,
    distribution: str | None = None,
    required: bool,
    missing_detail: str,
) -> CheckResult:
    try:
        present = importlib.util.find_spec(module) is not None
    except (ImportError, AttributeError, ValueError):
        present = False
    if not present:
        return CheckResult(
            label,
            FAIL if required else WARNING,
            missing_detail,
            required=required,
        )
    version = _distribution_version(distribution or module)
    detail = f"Available{f' ({version})' if version else ''}"
    return CheckResult(label, PASS, detail, required=required)


def _nearest_existing_folder(path: Path) -> Path:
    candidate = path.expanduser()
    if candidate.suffix and not candidate.is_dir():
        candidate = candidate.parent
    while not candidate.exists() and candidate.parent != candidate:
        candidate = candidate.parent
    return candidate


def _folder_check(label: str, raw_path: str, *, scratch: bool = False) -> CheckResult:
    text = str(raw_path or "").strip()
    if not text:
        return CheckResult(label, WARNING, "No folder selected yet.")

    requested = Path(text).expanduser()
    existing = _nearest_existing_folder(requested)
    if not existing.exists():
        return CheckResult(label, FAIL, f"No usable parent exists for {requested}.", True)
    if not existing.is_dir():
        return CheckResult(label, FAIL, f"{existing} is not a folder.", True)

    temp_path = None
    cleanup_note = ""
    try:
        descriptor, raw_temp = tempfile.mkstemp(prefix=".tsdp-write-check-", dir=existing)
        os.close(descriptor)
        temp_path = Path(raw_temp)
    except OSError as exc:
        return CheckResult(
            label,
            FAIL,
            f"Cannot write through {existing}: {type(exc).__name__}.",
            True,
        )
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError as exc:
                cleanup_note = f" Temporary probe cleanup failed: {type(exc).__name__}."

    try:
        free_gb = shutil.disk_usage(existing).free / (1024 ** 3)
    except OSError:
        free_gb = 0.0
    suffix = cleanup_note
    status = PASS
    if scratch and free_gb < 30.0:
        status = WARNING
        suffix = " Large mosaics may require substantially more free space."
    state = "exists" if requested.exists() else "will be created when needed"
    return CheckResult(
        label,
        status,
        f"{requested} ({state}); {free_gb:.1f} GB free.{suffix}",
    )


def _aria2_check(settings: Mapping[str, object]) -> CheckResult:
    configured = str(settings.get("aria2_path", "") or "").strip()
    candidate = configured or default_aria2_path(dict(settings))
    resolved = ""
    if candidate:
        expanded = Path(candidate.strip('"')).expanduser()
        if expanded.is_file():
            resolved = str(expanded)
        else:
            resolved = shutil.which(candidate) or shutil.which(expanded.name) or ""
    if resolved:
        return CheckResult("Download engine", PASS, f"aria2c found at {resolved}.")
    return CheckResult(
        "Download engine",
        WARNING,
        "aria2c was not found. FITS processing works, but managed downloads need aria2c.",
    )


def _settings_storage_check() -> CheckResult:
    path = settings_file_path()
    parent = _nearest_existing_folder(path.parent)
    if not parent.exists():
        return CheckResult(
            "Settings storage",
            FAIL,
            f"No usable parent exists for {path}.",
            True,
        )
    return _folder_check("Settings storage", str(path.parent))


def check_installation(
    settings: Mapping[str, object] | None = None,
) -> InstallationReport:
    """Run fast, offline checks without importing the astronomy stack."""
    values = dict(settings) if settings is not None else load_settings()
    results: list[CheckResult] = []

    python_ok = sys.version_info >= (3, 10)
    runtime_kind = "packaged application" if getattr(sys, "frozen", False) else "Python"
    results.append(
        CheckResult(
            "Runtime",
            PASS if python_ok else FAIL,
            f"{runtime_kind} {platform.python_version()} ({platform.architecture()[0]}).",
            required=True,
        )
    )
    results.append(
        _module_check(
            "Tk user interface",
            "tkinter",
            required=True,
            missing_detail="Tkinter is unavailable; the desktop interface cannot start.",
        )
    )
    for label, module, distribution in (
        ("NumPy", "numpy", "numpy"),
        ("SciPy", "scipy", "scipy"),
        ("Astropy", "astropy", "astropy"),
        ("Reproject", "reproject", "reproject"),
    ):
        results.append(
            _module_check(
                label,
                module,
                distribution=distribution,
                required=True,
                missing_detail=f"{label} is required for FITS processing.",
            )
        )
    for label, module, distribution, detail in (
        ("Image previews", "PIL", "Pillow", "Pillow is missing; image previews are disabled."),
        ("Modern theme", "sv_ttk", "sv-ttk", "sv-ttk is missing; the fallback theme is used."),
        ("Secure credentials", "keyring", "keyring", "Keyring is missing; Telegram tokens cannot be stored securely."),
        ("MAST search", "astroquery", "astroquery", "Astroquery is missing; MAST search is disabled."),
        ("Resource monitor", "psutil", "psutil", "psutil is missing; Windows fallbacks will be used."),
    ):
        results.append(
            _module_check(
                label,
                module,
                distribution=distribution,
                required=False,
                missing_detail=detail,
            )
        )

    results.append(_aria2_check(values))
    results.append(_settings_storage_check())

    folder_specs = (
        ("Download folder", "download_dir", False),
        ("Work folder", "work_dir", False),
        ("Mosaic output", "mosaic_output_dir", False),
        ("Mosaic scratch", "mosaic_scratch_dir", True),
        ("Alignment output", "align_output_dir", False),
        ("Recovery output", "recovery_output_dir", False),
    )
    for label, key, scratch in folder_specs:
        results.append(_folder_check(label, str(values.get(key, "") or ""), scratch=scratch))

    return InstallationReport(
        checked_at=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        results=tuple(results),
    )


__all__ = [
    "CheckResult",
    "InstallationReport",
    "PASS",
    "WARNING",
    "FAIL",
    "check_installation",
]