from __future__ import annotations

from tsdp2._deps import *
from tsdp2.logging_config import LOGGER

_PRIMARY_META_KEYS = (
    "FILTER", "FILTER1", "FILTER2", "PUPIL", "BAND", "FILTNAM", "FILTERID",
    "INSTRUME", "DETECTOR", "TELESCOP",
    "EXPTIME", "TARGNAME", "PROPOSID",
    "DATE-OBS", "OBJECT", "OBSTYPE",
)


_MOSAIC_HDU_REF_RE = re.compile(
    r"^(?P<path>.+)::HDU=(?P<index>\d+)(?:::(?P<label>[^\r\n]*))?$",
    re.IGNORECASE,
)


def _parse_mosaic_hdu_ref(path: str):
    """Return (real_path, hdu_index_or_None, label) for a mosaic input."""
    text = os.fspath(path)
    m = _MOSAIC_HDU_REF_RE.match(text)
    if not m:
        return text, None, ""
    return m.group("path"), int(m.group("index")), (m.group("label") or "")


def _make_mosaic_hdu_ref(path: str, hdu_index: int, label: str = ""):
    clean_label = re.sub(r"[\r\n:]", "_", str(label or "").strip())
    suffix = f"::{clean_label}" if clean_label else ""
    return f"{os.fspath(path)}::HDU={int(hdu_index)}{suffix}"


def _mosaic_input_display_name(path: str):
    real_path, hdu_index, label = _parse_mosaic_hdu_ref(path)
    name = os.path.basename(real_path)
    if hdu_index is None:
        return name
    detail = label or f"HDU {hdu_index}"
    return f"{name}  [{detail}]"


def _iter_requested_image_hdus(hdul, hdu_index=None):
    """Yield either a requested HDU or every HDU for legacy single-image use."""
    if hdu_index is not None:
        if hdu_index < 0 or hdu_index >= len(hdul):
            raise RuntimeError(f"FITS HDU index {hdu_index} is out of range (0..{len(hdul)-1}).")
        yield hdul[hdu_index]
        return
    yield from hdul


def load_2d_and_header(path: str, preserve_nan: bool = False):
    """Load the first 2-D image extension and return (data_float32, header).

    For multi-extension FITS (HST drizzle products), the primary HDU carries
    metadata (FILTER, EXPTIME …) but no pixel data.  We merge those keywords
    into the returned image-extension header so everything downstream can find
    them without knowing the file structure.
    """
    real_path, requested_hdu, _label = _parse_mosaic_hdu_ref(path)
    with fits.open(real_path, memmap=True) as hdul:
        primary_hdr = hdul[0].header.copy()

        for hdu in _iter_requested_image_hdus(hdul, requested_hdu):
            if hdu.data is not None and getattr(hdu.data, "ndim", 0) >= 2:
                data   = np.array(hdu.data, dtype=np.float32)
                header = hdu.header.copy()
                # Inject primary-only metadata into the image header
                for k in _PRIMARY_META_KEYS:
                    if k in primary_hdr and k not in header:
                        try:
                            header[k] = primary_hdr[k]
                        except EXPECTED_OPERATION_ERRORS:
                            LOGGER.debug("Best-effort operation failed", exc_info=True)
                break
        else:
            hdu_text = f" HDU {requested_hdu}" if requested_hdu is not None else ""
            raise RuntimeError(f"No image data found in: {real_path}{hdu_text}")

    # Reduce common singleton axes safely, for example JWST arrays shaped
    # (1, H, W). If a genuine cube remains, use the first plane as before.
    try:
        while data.ndim > 2 and 1 in data.shape:
            data = np.squeeze(data)
        while data.ndim > 2:
            data = data[0]
    except EXPECTED_OPERATION_ERRORS:
        if getattr(data, "ndim", 0) > 2:
            data = data[0]

    # Most UI thumbnails historically filled NaN with a median. For mosaic
    # previews/Telegram, that is visually wrong: real no-coverage NaNs become
    # a huge grey rectangle. preserve_nan=True keeps holes/uncovered canvas as
    # NaN so the renderer can show them as black instead of fake sky.
    if not preserve_nan:
        bad = ~np.isfinite(data)
        if bad.any():
            finite = data[np.isfinite(data)]
            fill = float(np.nanmedian(finite)) if finite.size else 0.0
            data[bad] = fill

    return data, header


def bbox_from_mask(mask: np.ndarray):
    rows = np.where(mask.any(axis=1))[0]
    cols = np.where(mask.any(axis=0))[0]
    if rows.size == 0 or cols.size == 0:
        return None
    y0, y1 = int(rows[0]), int(rows[-1]) + 1
    x0, x1 = int(cols[0]), int(cols[-1]) + 1
    return y0, y1, x0, x1


def crop_with_wcs(data: np.ndarray, header, bbox):
    y0, y1, x0, x1 = bbox
    cropped = data[y0:y1, x0:x1]

    w = WCS(header)
    w2 = w.slice((slice(y0, y1), slice(x0, x1)))
    new_header = w2.to_header()

    for k in header:
        if k not in new_header and k not in ("HISTORY", "COMMENT"):
            try:
                new_header[k] = header[k]
            except EXPECTED_OPERATION_ERRORS:
                LOGGER.debug("Best-effort operation failed", exc_info=True)

    return cropped, new_header

__all__ = [name for name in globals() if not name.startswith('__')]
