from __future__ import annotations

from tsdp2._deps import *
from tsdp2.logging_config import LOGGER
from tsdp2.astronomy.fits_io import load_2d_and_header

THUMB_SIZE = 200          # max thumbnail dimension in pixels


STRETCH_MODES = ["linear", "sqrt", "log", "asinh", "histeq"]


def _downsample_for_display(data: np.ndarray, max_px: int) -> np.ndarray:
    """Stride-sample a big array down to ~2x the display size before any
    stretching maths.  A 4096x4096 frame becomes ~400x400 for a 200px
    thumbnail: ~100x fewer pixels to stretch, visually identical."""
    h, w = data.shape
    target = max_px * 2
    step = max(1, int(max(h, w) / target))
    return data[::step, ::step] if step > 1 else data


def apply_stretch(data: np.ndarray, mode: str = "asinh",
                  plo: float = 0.5, phi: float = 99.5,
                  nan_value: int | None = 0) -> np.ndarray:
    """Return uint8 (H, W) array ready for display.

    If the input contains NaN no-coverage pixels, render them as black by
    default. This prevents Telegram/preview PNGs from showing fake grey sky.
    """
    invalid = ~np.isfinite(data)
    finite = data[np.isfinite(data)]
    if finite.size == 0:
        return np.zeros((64, 64), dtype=np.uint8)
    # Percentiles on a random sample — identical result visually, much faster
    if finite.size > 500_000:
        finite = np.random.default_rng(0).choice(finite, 500_000, replace=False)

    lo = float(np.percentile(finite, plo))
    hi = float(np.percentile(finite, phi))
    if hi <= lo:
        hi = lo + 1.0

    d = np.clip(data, lo, hi)
    d = np.where(np.isfinite(d), d, lo)

    if mode == "sqrt":
        top = max(hi - lo, 1e-12)
        d = np.sqrt((d - lo) / top)

    elif mode == "log":
        d = d - lo + 1.0
        top = max(hi - lo + 1.0, 1e-12)
        d = np.log10(np.clip(d, 1e-12, None)) / max(np.log10(top), 1e-12)

    elif mode == "asinh":
        d = d - lo
        top = hi - lo
        softening = max(top * 0.05, 1e-12)
        d = np.arcsinh(d / softening) / max(np.arcsinh(top / softening), 1e-12)

    elif mode == "histeq":
        n_bins = 1024
        hist, edges = np.histogram(finite, bins=n_bins)
        cdf = np.cumsum(hist).astype(np.float64)
        cdf /= cdf[-1]
        centers = 0.5 * (edges[:-1] + edges[1:])
        d = np.interp(d.ravel(), centers, cdf).reshape(d.shape)

    else:  # linear
        d = (d - lo) / max(hi - lo, 1e-12)

    normed = np.clip(d, 0.0, 1.0)
    arr = (normed * 255).astype(np.uint8)
    if nan_value is not None and invalid.shape == arr.shape and invalid.any():
        arr[invalid] = int(max(0, min(255, nan_value)))
    return arr


def make_thumbnail(data: np.ndarray, mode: str, max_px: int = THUMB_SIZE):
    """Stretch data and return a PIL Image scaled to fit max_px × max_px."""
    if not HAVE_PIL:
        return None
    small = _downsample_for_display(data, max_px)
    arr = apply_stretch(small, mode)
    arr = arr[::-1, :]           # FITS is bottom-up
    img = Image.fromarray(arr, mode="L")
    w, h = img.size
    scale = min(max_px / max(w, 1), max_px / max(h, 1), 1.0)
    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    return img.resize((nw, nh), Image.LANCZOS)


_THUMB_CACHE: "dict[tuple, object]" = {}


_THUMB_CACHE_MAX = 48


_THUMB_LOCK = threading.Lock()


def load_thumbnail_cached(path: str, mode: str, max_px: int = THUMB_SIZE):
    """Load + stretch a FITS thumbnail with caching.
    Returns (pil_image, (h, w)) — re-stretching an already-seen file is free."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0
    key = (path, mtime, mode, max_px)
    with _THUMB_LOCK:
        hit = _THUMB_CACHE.get(key)
    if hit is not None:
        return hit
    data, _ = load_2d_and_header(path)
    img = make_thumbnail(data, mode, max_px=max_px)
    result = (img, data.shape)
    with _THUMB_LOCK:
        if len(_THUMB_CACHE) >= _THUMB_CACHE_MAX:
            _THUMB_CACHE.pop(next(iter(_THUMB_CACHE)))
        _THUMB_CACHE[key] = result
    return result

__all__ = [name for name in globals() if not name.startswith('__')]
