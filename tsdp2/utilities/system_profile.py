from __future__ import annotations

import ctypes
import logging
import os
import platform
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SystemProfile:
    cpu_name: str
    logical_cpus: int
    ram_gb: float
    disk_path: str
    disk_free_gb: float
    disk_write_mbps: float
    disk_read_mbps: float
    gpu_name: str
    operating_system: str

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, values: dict) -> "SystemProfile":
        return cls(
            cpu_name=str(values.get("cpu_name", "Unknown CPU")),
            logical_cpus=max(1, int(values.get("logical_cpus", 1))),
            ram_gb=max(0.0, float(values.get("ram_gb", 0.0))),
            disk_path=str(values.get("disk_path", "")),
            disk_free_gb=max(0.0, float(values.get("disk_free_gb", 0.0))),
            disk_write_mbps=max(0.0, float(values.get("disk_write_mbps", 0.0))),
            disk_read_mbps=max(0.0, float(values.get("disk_read_mbps", 0.0))),
            gpu_name=str(values.get("gpu_name", "Not detected")),
            operating_system=str(values.get("operating_system", "")),
        )


@dataclass(frozen=True)
class SystemRecommendation:
    tier: str
    pipeline_workers: int
    mosaic_workers: int
    concurrent_downloads: int
    connections: int
    splits: int
    mosaic_scale: str
    draft_scale: str
    full_resolution: bool
    retain_intermediates: bool
    notes: tuple[str, ...]

    def as_dict(self) -> dict:
        payload = asdict(self)
        payload["notes"] = list(self.notes)
        return payload


def _total_ram_gb() -> float:
    try:
        import psutil
    except ImportError:
        psutil = None
    if psutil is not None:
        try:
            return float(psutil.virtual_memory().total) / (1024 ** 3)
        except (AttributeError, OSError, ValueError) as exc:
            LOGGER.debug("psutil memory inspection failed: %s", exc)

    if os.name == "nt":
        class MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(status)
        try:
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return float(status.ullTotalPhys) / (1024 ** 3)
        except (AttributeError, OSError):
            return 0.0

    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return float(pages * page_size) / (1024 ** 3)
    except (AttributeError, OSError, ValueError):
        return 0.0


def _cpu_name() -> str:
    return (
        platform.processor().strip()
        or os.environ.get("PROCESSOR_IDENTIFIER", "").strip()
        or "Unknown CPU"
    )


def _gpu_name() -> str:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return "Not detected"
    try:
        result = subprocess.run(
            [
                executable,
                "--query-gpu=name,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
        first_line = (result.stdout or "").strip().splitlines()
        if first_line:
            name, _, memory = first_line[0].partition(",")
            suffix = f" ({memory.strip()} MB)" if memory.strip() else ""
            return f"{name.strip()}{suffix}"
    except (OSError, subprocess.SubprocessError):
        return "Not detected"
    return "Not detected"


def _benchmark_disk(target: Path, size_mb: int = 32) -> tuple[float, float]:
    target.mkdir(parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(prefix="tsdp-system-test-", dir=target)
    test_path = Path(raw_path)
    block = bytes(1024 * 1024)
    try:
        started = time.perf_counter()
        with os.fdopen(descriptor, "wb", buffering=0) as handle:
            for _ in range(size_mb):
                handle.write(block)
            os.fsync(handle.fileno())
        write_seconds = max(0.001, time.perf_counter() - started)

        started = time.perf_counter()
        with test_path.open("rb", buffering=0) as handle:
            while handle.read(len(block)):
                continue
        read_seconds = max(0.001, time.perf_counter() - started)
        return size_mb / write_seconds, size_mb / read_seconds
    finally:
        try:
            test_path.unlink()
        except OSError as exc:
            LOGGER.warning("Could not remove disk benchmark file %s: %s", test_path, exc)


def inspect_system(
    disk_target: str | Path | None = None,
    *,
    run_disk_benchmark: bool = True,
) -> SystemProfile:
    target = Path(disk_target).expanduser() if disk_target else Path(tempfile.gettempdir())
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError:
        target = Path(tempfile.gettempdir())

    try:
        free_gb = shutil.disk_usage(target).free / (1024 ** 3)
    except OSError:
        free_gb = 0.0

    write_speed = read_speed = 0.0
    if run_disk_benchmark:
        try:
            write_speed, read_speed = _benchmark_disk(target)
        except OSError:
            write_speed = read_speed = 0.0

    return SystemProfile(
        cpu_name=_cpu_name(),
        logical_cpus=max(1, os.cpu_count() or 1),
        ram_gb=_total_ram_gb(),
        disk_path=str(target),
        disk_free_gb=free_gb,
        disk_write_mbps=write_speed,
        disk_read_mbps=read_speed,
        gpu_name=_gpu_name(),
        operating_system=platform.platform(),
    )


def recommend_settings(profile: SystemProfile) -> SystemRecommendation:
    cpu = max(1, profile.logical_cpus)
    ram = profile.ram_gb
    free = profile.disk_free_gb
    write_speed = profile.disk_write_mbps
    notes: list[str] = []

    constrained = ram < 8.0 or free < 30.0
    high_end = (
        ram >= 24.0
        and cpu >= 12
        and free >= 100.0
        and (write_speed == 0.0 or write_speed >= 200.0)
    )

    if constrained:
        tier = "Conservative"
        pipeline_workers = min(3, max(1, cpu // 2))
        mosaic_workers = min(2, max(1, cpu // 3))
        concurrent = 2
        connections = splits = 8
        scale = "0.5"
        draft_scale = "0.06"
        full_resolution = False
        notes.append("Reduced memory and scratch-disk pressure.")
    elif high_end:
        tier = "High performance"
        pipeline_workers = min(16, max(4, cpu - 2))
        mosaic_workers = min(12, max(4, cpu // 2))
        concurrent = min(16, max(5, cpu // 3))
        connections = splits = 16
        scale = "1.0"
        draft_scale = "0.10"
        full_resolution = True
        notes.append("Full-resolution, parallel processing is appropriate.")
    else:
        tier = "Balanced"
        pipeline_workers = min(8, max(2, cpu - 1))
        mosaic_workers = min(6, max(2, cpu // 2))
        concurrent = min(8, max(3, cpu // 4))
        connections = splits = 16
        scale = "1.0" if ram >= 12.0 and free >= 50.0 else "0.75"
        draft_scale = "0.08"
        full_resolution = ram >= 12.0 and free >= 50.0
        notes.append("Balanced for interactive use and background processing.")

    if write_speed and write_speed < 120.0:
        mosaic_workers = min(mosaic_workers, 2)
        notes.append("Mosaic workers were limited because scratch storage tested slowly.")
    if free < 50.0:
        notes.append("Free scratch space is low; intermediate retention is disabled.")
    if profile.gpu_name == "Not detected":
        notes.append("No NVIDIA GPU was detected; current FITS workflows remain CPU-based.")

    return SystemRecommendation(
        tier=tier,
        pipeline_workers=pipeline_workers,
        mosaic_workers=mosaic_workers,
        concurrent_downloads=concurrent,
        connections=connections,
        splits=splits,
        mosaic_scale=scale,
        draft_scale=draft_scale,
        full_resolution=full_resolution,
        retain_intermediates=False,
        notes=tuple(notes),
    )


__all__ = [
    "SystemProfile",
    "SystemRecommendation",
    "inspect_system",
    "recommend_settings",
]