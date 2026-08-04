from __future__ import annotations

from tsdp2._deps import *
from tsdp2.logging_config import LOGGER
from tsdp2.config import _safe_int

def _available_memory_bytes():
    """Best-effort available RAM estimate. Returns None if unknown."""
    try:
        import psutil  # optional
        return int(psutil.virtual_memory().available)
    except EXPECTED_OPERATION_ERRORS:
        LOGGER.debug("Best-effort operation failed", exc_info=True)
    if os.name == "nt":
        try:
            import ctypes

            class _MEMORYSTATUSEX(ctypes.Structure):
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

            status = _MEMORYSTATUSEX()
            status.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.ullAvailPhys)
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
    try:
        if hasattr(os, "sysconf"):
            pages = os.sysconf("SC_AVPHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            return int(pages * page_size)
    except EXPECTED_OPERATION_ERRORS:
        LOGGER.debug("Best-effort operation failed", exc_info=True)
    return None


def _format_gib(nbytes):
    try:
        return f"{float(nbytes) / (1024 ** 3):.2f} GiB"
    except EXPECTED_OPERATION_ERRORS:
        return "unknown"


def _mosaic_disk_mode_needed(n_tiles, shape_out, combine):
    """Return (use_disk_mode, reason). Median/feather mosaics need special
    handling because stacking many full-size reprojected tiles explodes RAM.
    """
    try:
        h, w = int(shape_out[0]), int(shape_out[1])
    except EXPECTED_OPERATION_ERRORS:
        return False, "unknown shape"
    pixels = h * w
    one_f32 = pixels * 4
    # Old median path roughly needs: n tiles + stack + temporary float64 footprint.
    # For the user's case: 13 * 11418 * 14168 is huge. Disk mode avoids keeping all
    # reprojected tiles in RAM.
    estimated_stack = one_f32 * max(1, int(n_tiles))
    avail = _available_memory_bytes()
    if combine in ("median", "feather") and (pixels >= 40_000_000 or estimated_stack >= 1.2 * (1024 ** 3)):
        return True, f"large {combine} mosaic: {n_tiles} tile(s), output {w}×{h}, stack would be {_format_gib(estimated_stack)}"
    if avail is not None and combine in ("median", "feather") and estimated_stack > avail * 0.35:
        return True, f"not enough safe RAM for in-memory {combine}: stack {_format_gib(estimated_stack)}, available {_format_gib(avail)}"
    return False, "small enough for in-memory combine"


def _safe_mosaic_workers(requested_workers, n_tiles, shape_out):
    """Clamp reproject workers so one large mosaic cannot launch 13 parallel
    1+ GiB allocations and murder the process.
    """
    requested = max(1, min(_safe_int(requested_workers, default=1, lo=1), max(1, int(n_tiles))))
    try:
        h, w = int(shape_out[0]), int(shape_out[1])
        pixels = h * w
    except EXPECTED_OPERATION_ERRORS:
        return requested
    avail = _available_memory_bytes()
    if avail is None:
        # Conservative fallback for large outputs.
        if pixels >= 40_000_000:
            return 1
        if pixels >= 15_000_000:
            return min(requested, 2)
        return requested
    # reproject can allocate output + footprint + temporaries. Treat one worker as
    # roughly 3 float64 images. This deliberately overestimates to stay alive.
    per_worker = pixels * 8 * 3
    safe = max(1, int((avail * 0.45) // max(1, per_worker)))
    return max(1, min(requested, safe))


def _safe_disk_turbo_workers(requested_workers, n_tiles, shape_out):
    """Choose disk-backed reprojection parallelism from current resources.

    The user-selected worker count is an upper bound, not a fixed preset. RAM,
    CPU threads, output geometry, and the number of tiles determine the safe
    value for each run.
    """
    requested = max(
        1,
        min(
            _safe_int(requested_workers, default=1, lo=1),
            max(1, int(n_tiles)),
        ),
    )
    try:
        pixels = int(shape_out[0]) * int(shape_out[1])
    except EXPECTED_OPERATION_ERRORS:
        return min(requested, max(1, (os.cpu_count() or 2) // 2))

    available = _available_memory_bytes()
    if available is None:
        if pixels >= 40_000_000:
            return 1
        return min(requested, max(1, (os.cpu_count() or 2) // 3))

    # Two float32 memmaps plus coordinate/interpolation working space. Only
    # 32% of currently available RAM may be committed to concurrent workers.
    per_worker = max(640 * 1024 ** 2, int(pixels * 4 * 3.5))
    by_ram = max(1, int((available * 0.32) // max(1, per_worker)))
    by_cpu = max(1, (os.cpu_count() or 2) // 2)
    return max(1, min(requested, by_ram, by_cpu))
__all__ = [name for name in globals() if not name.startswith('__')]
