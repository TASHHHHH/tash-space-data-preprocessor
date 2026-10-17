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


def _mosaic_memory_budget(available=None):
    """Spend at most 60% of free RAM, retaining up to 3 GiB for other apps."""
    if available is None:
        available = _available_memory_bytes()
    if available is None:
        return None
    reserve = min(3 * 1024 ** 3, int(available * 0.5))
    return max(0, min(int(available * 0.60), available - reserve))


def _mosaic_working_bytes(n_tiles, shape_out, combine):
    pixels = int(shape_out[0]) * int(shape_out[1])
    n_tiles = max(1, int(n_tiles))
    # Output tiles plus blend workspaces/EDT, or the median stack and its copy.
    per_pixel = (4 * n_tiles + 40 if combine == "feather" else 12 * n_tiles + 8)
    return pixels * per_pixel + 64 * 1024 ** 2


def _mosaic_disk_mode_needed(n_tiles, shape_out, combine):
    """Return (use_disk_mode, reason). Median/feather mosaics need special
    handling because stacking many full-size reprojected tiles explodes RAM.
    """
    try:
        h, w = int(shape_out[0]), int(shape_out[1])
    except EXPECTED_OPERATION_ERRORS:
        return False, "unknown shape"
    pixels = h * w
    estimated_stack = _mosaic_working_bytes(n_tiles, shape_out, combine)
    avail = _available_memory_bytes()
    budget = _mosaic_memory_budget(avail)
    if avail is None and combine in ("median", "feather") and (pixels >= 40_000_000 or estimated_stack >= 1.2 * (1024 ** 3)):
        return True, f"large {combine} mosaic: {n_tiles} tile(s), output {w}×{h}, working set would be {_format_gib(estimated_stack)}"
    if budget is not None and estimated_stack > budget:
        return True, f"not enough safe RAM for in-memory {combine}: working set {_format_gib(estimated_stack)}, available {_format_gib(avail)}"
    return False, f"estimated working set {_format_gib(estimated_stack)} fits available memory budget"


def _safe_mosaic_workers(requested_workers, n_tiles, shape_out):
    """Clamp reproject workers so one large mosaic cannot launch 13 parallel
    1+ GiB allocations and murder the process.
    """
    cpu_limit = max(1, (os.cpu_count() or 2) - 2)
    requested = max(1, min(_safe_int(requested_workers, default=1, lo=1), max(1, int(n_tiles)), cpu_limit))
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
    # Reprojection is blockwise: float32 output/footprint plus a bounded WCS
    # workspace, rather than three full-size float64 coordinate grids per worker.
    per_worker = pixels * 4 + 64 * 1024 ** 2
    retained_tiles = pixels * 4 * max(1, int(n_tiles))
    safe = max(1, int((_mosaic_memory_budget(avail) - retained_tiles) // max(1, per_worker)))
    return max(1, min(requested, safe))


def _safe_mosaic_block_workers(requested_workers, tile_workers, n_tiles, shape_out):
    """Use spare threads within large tiles without multiplying the RAM budget."""
    pixels = int(shape_out[0]) * int(shape_out[1])
    if pixels < 4_000_000:
        return 1
    available = _available_memory_bytes()
    if available is None:
        return 1
    tile_workers = max(1, int(tile_workers))
    requested = min(max(1, int(requested_workers)), max(1, (os.cpu_count() or 2) - 2))
    retained = pixels * 4 * (max(1, int(n_tiles)) + tile_workers)
    spare = max(0, _mosaic_memory_budget(available) - retained)
    by_ram = int(spare // (64 * 1024 ** 2 * tile_workers))
    return max(1, min(4, requested // tile_workers, by_ram))


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

    # Keep a conservative resident allowance for two memmaps and blockwise WCS
    # work, while leaving CPU threads and free RAM for the rest of the desktop.
    per_worker = max(640 * 1024 ** 2, int(pixels * 4 * 3.5))
    by_ram = max(1, int((_mosaic_memory_budget(available) * 0.8) // max(1, per_worker)))
    by_cpu = max(1, (os.cpu_count() or 2) - 2)
    return max(1, min(requested, by_ram, by_cpu))
__all__ = [name for name in globals() if not name.startswith('__')]
