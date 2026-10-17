from __future__ import annotations

from tsdp2._deps import *
from tsdp2.logging_config import LOGGER
from tsdp2.astronomy.fits_io import _iter_requested_image_hdus, _parse_mosaic_hdu_ref, load_2d_and_header

def try_get_header_value(hdr, keys):
    for k in keys:
        if k in hdr and hdr[k] not in (None, ""):
            return str(hdr[k]).strip()
    return ""


FILTER_RE = re.compile(r"(F\d{3,4}[A-Z]|N\d{3,4})", re.IGNORECASE)
OPTICAL_FILTER_RE = re.compile(
    r"(?:^|[^A-Z0-9])(?P<band>HA|HALPHA|OIII|SII|VR|[UGRIZYBVL])(?:$|[^A-Z0-9])",
    re.IGNORECASE,
)
FILTER_HEADER_KEYS = ("FILTER", "FILTER1", "FILTER2", "PUPIL", "BAND", "FILTNAM", "FILTERID")
_EMPTY_FILTERS = {"", "NONE", "NULL", "N/A", "UNKNOWN", "CLEAR", "CLEAR1L", "CLEAR2L", "OPEN"}
# Approximate band centres used for reference/PSF ordering, not photometric calibration.
_OPTICAL_MICRONS = {"U": 0.36, "B": 0.44, "G": 0.48, "V": 0.55,
                    "L": 0.55, "VR": 0.62, "R": 0.64, "I": 0.78,
                    "Z": 0.92, "Y": 1.0, "HA": 0.6563, "OIII": 0.5007, "SII": 0.6724}


def parse_filter_code(text: str):
    if not text:
        return ""
    text = str(text).strip().upper()
    m = FILTER_RE.search(text)
    if m:
        return m.group(1)
    # A band must be a complete token: g.fits and field_g_01.fits are valid,
    # while target.fits and mosaic.fits must not become R or I.
    m = OPTICAL_FILTER_RE.search(text)
    if not m:
        return ""
    band = m.group("band")
    return "HA" if band == "HALPHA" else band


def filter_from_header(header):
    """Prefer recognised bands; retain custom FILTER names instead of losing them."""
    for key in FILTER_HEADER_KEYS:
        code = parse_filter_code(header.get(key, ""))
        if code:
            return code
    for key in ("FILTER", "FILTER1", "FILTER2", "BAND", "FILTNAM", "FILTERID"):
        value = str(header.get(key, "")).strip().upper()
        if value not in _EMPTY_FILTERS and not value.startswith("CLEAR"):
            return value
    return ""


def filter_code_to_microns(code: str):
    if not code:
        return None
    code = code.upper()
    if code in _OPTICAL_MICRONS:
        return _OPTICAL_MICRONS[code]
    prefix = code.upper()[:1]
    if prefix not in ("F", "N"):
        return None
    digits = re.findall(r"\d+", code)
    if not digits:
        return None
    val = int(digits[0])
    if prefix == "N":
        # DECam narrowband names encode nanometres (N501, N662, N673).
        return val / 1000.0
    return val / 100.0


def suggest_name_from_fits(path: str):
    base = os.path.basename(path)
    try:
        _, hdr = load_2d_and_header(path)
    except EXPECTED_OPERATION_ERRORS:
        code = parse_filter_code(base)
        return code or os.path.splitext(base)[0]

    instr = try_get_header_value(hdr, ["INSTRUME", "INSTRUMENT"]).lower()

    code = (
        filter_from_header(hdr) or
        parse_filter_code(base)
    )

    if not code:
        return os.path.splitext(base)[0]

    if "nircam" in instr:
        return f"nircam_{code}"
    if "miri" in instr:
        return f"miri_{code}"
    if "nirspec" in instr:
        return f"nirspec_{code}"
    if "niriss" in instr:
        return f"niriss_{code}"
    return code


def get_fits_shape(path: str):
    """Return (height, width) from FITS header without loading pixel data."""
    try:
        real_path, requested_hdu, _label = _parse_mosaic_hdu_ref(path)
        with fits.open(real_path, memmap=True) as hdul:
            for hdu in _iter_requested_image_hdus(hdul, requested_hdu):
                hdr   = hdu.header
                naxis = hdr.get("NAXIS", 0)
                if naxis >= 2:
                    n1 = hdr.get("NAXIS1")
                    n2 = hdr.get("NAXIS2")
                    if n1 and n2:
                        return int(n2), int(n1)   # (H, W)
    except EXPECTED_OPERATION_ERRORS:
        LOGGER.debug("Best-effort operation failed", exc_info=True)
    return None, None


def estimate_pixscale_arcsec(header):
    try:
        w = WCS(header)
        ps = w.proj_plane_pixel_scales()  # degrees/pixel
        if ps is not None and len(ps) >= 2:
            deg_per_pix = float(np.mean(np.abs(ps[:2])))
            return deg_per_pix * 3600.0
    except EXPECTED_OPERATION_ERRORS:
        LOGGER.debug("Best-effort operation failed", exc_info=True)

    if "CDELT1" in header and header["CDELT1"]:
        try:
            return abs(float(header["CDELT1"])) * 3600.0
        except EXPECTED_OPERATION_ERRORS:
            return None
    return None


def robust_detail_score(data):
    finite = data[np.isfinite(data)]
    if finite.size < 1000:
        return 0.0
    lo = np.percentile(finite, 1.0)
    hi = np.percentile(finite, 99.5)
    x = np.clip(data, lo, hi)
    x = np.where(np.isfinite(x), x, np.nanmedian(finite))
    hp = x - gaussian_filter(x, sigma=2.0)
    return float(np.nanstd(hp))


def choose_best_reference(paths):
    rows = []
    for p in paths:
        try:
            d, h = load_2d_and_header(p)
            finite_frac = float(np.isfinite(d).mean())
            detail = robust_detail_score(d)
            ps = estimate_pixscale_arcsec(h)
            ps_term = 0.0 if ps is None or ps <= 0 else (1.0 / ps)
            rows.append((p, finite_frac, detail, ps, ps_term))
        except EXPECTED_OPERATION_ERRORS:
            rows.append((p, 0.0, 0.0, None, 0.0))

    finite_vals = np.array([r[1] for r in rows], dtype=float)
    detail_vals = np.array([r[2] for r in rows], dtype=float)
    ps_vals = np.array([r[4] for r in rows], dtype=float)

    def norm(v):
        mn, mx = float(np.min(v)), float(np.max(v))
        if mx <= mn:
            return np.zeros_like(v)
        return (v - mn) / (mx - mn)

    nf = norm(finite_vals)
    nd = norm(detail_vals)
    npix = norm(ps_vals)

    scores = 0.6 * nd + 0.3 * nf + 0.1 * npix
    best_i = int(np.argmax(scores))
    best_path = rows[best_i][0]

    table = []
    for i, r in enumerate(rows):
        table.append({
            "path": r[0],
            "finite": r[1],
            "detail": r[2],
            "pixscale_arcsec": r[3],
            "score": float(scores[i]),
        })
    return best_path, table


def choose_highest_resolution_reference(paths):
    """Pick the FITS with the largest NAXIS1*NAXIS2 image area."""
    best_path = None
    best_area = -1
    for path in paths:
        try:
            _data, hdr = load_2d_and_header(path)
            nx = int(hdr.get("NAXIS1", 0) or 0)
            ny = int(hdr.get("NAXIS2", 0) or 0)
            if nx <= 0 or ny <= 0:
                # Fallback to actual loaded array shape.
                ny, nx = _data.shape[-2], _data.shape[-1]
            area = int(nx) * int(ny)
            if area > best_area:
                best_area = area
                best_path = path
        except EXPECTED_OPERATION_ERRORS:
            continue
    if not best_path:
        raise RuntimeError("Could not determine image resolution for any FITS file.")
    return best_path


def fits_resolution_text(path):
    """Return a 'W x H' pixel resolution string for a FITS file.

    Header-only via get_fits_shape (memmap, no pixel read). The previous
    version loaded the entire pixel array (hundreds of MB per HST/JWST file)
    just to read two header integers, freezing the UI when called from
    /report or dashboard updates.
    """
    try:
        h, w = get_fits_shape(path)
        if h and w:
            return f"{w} × {h} px"
    except EXPECTED_OPERATION_ERRORS:
        LOGGER.debug("Best-effort operation failed", exc_info=True)
    return "unknown"


def text_progress_bar(pct, width=18):
    pct = max(0, min(100, int(pct or 0)))
    filled = int(round(width * pct / 100))
    return "█" * filled + "░" * (width - filled)


def write_log(out_dir, lines, log_path=None):
    log_path = os.fspath(log_path or os.path.join(out_dir, "run_log.txt"))
    os.makedirs(os.path.dirname(log_path) or os.fspath(out_dir), exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(line.rstrip() + "\n")
    return log_path

__all__ = [name for name in globals() if not name.startswith('__')]
