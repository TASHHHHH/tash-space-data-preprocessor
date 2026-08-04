from __future__ import annotations

from tsdp2._deps import *
from tsdp2.logging_config import LOGGER
from tsdp2.astronomy.reference import filter_code_to_microns, parse_filter_code

def normalize_to_reference(arr, ref_arr, mask, mode="median"):
    a = arr[mask & np.isfinite(arr)]
    r = ref_arr[mask & np.isfinite(ref_arr)]
    if a.size < 100 or r.size < 100:
        return arr, 1.0

    if mode == "median":
        av = float(np.median(a))
        rv = float(np.median(r))
    elif mode == "p95":
        av = float(np.percentile(a, 95))
        rv = float(np.percentile(r, 95))
    elif mode == "background":
        av = float(np.percentile(a, 20))
        rv = float(np.percentile(r, 20))
    else:
        av = float(np.median(a))
        rv = float(np.median(r))

    if not np.isfinite(av) or not np.isfinite(rv) or abs(av) < 1e-12:
        return arr, 1.0

    s = rv / av
    return arr * s, s


def _sampled_normalization_scale(arr, ref_arr, footprint=None, mode="median",
                                 max_samples=1_000_000):
    """Estimate a normalization scale without materializing a huge overlap.

    Full-reference-canvas alignment can place a small Hubble footprint inside a
    very large Euclid grid.  Boolean-indexing that entire grid creates multiple
    large temporary arrays.  Regularly striding the canvas keeps the estimate
    bounded while still sampling the complete partial footprint.
    """
    h, w = ref_arr.shape
    step = max(1, int(np.ceil(np.sqrt((h * w) / max(1, int(max_samples))))))
    a = np.asarray(arr[::step, ::step])
    r = np.asarray(ref_arr[::step, ::step])
    valid = np.isfinite(a) & np.isfinite(r)
    if footprint is not None:
        valid &= np.asarray(footprint[::step, ::step]) > 0
    a = a[valid]
    r = r[valid]
    if a.size < 100 or r.size < 100:
        return 1.0

    if mode == "p95":
        av = float(np.percentile(a, 95))
        rv = float(np.percentile(r, 95))
    elif mode == "background":
        av = float(np.percentile(a, 20))
        rv = float(np.percentile(r, 20))
    else:
        av = float(np.median(a))
        rv = float(np.median(r))

    if not np.isfinite(av) or not np.isfinite(rv) or abs(av) < 1e-12:
        return 1.0
    return float(rv / av)


def _scale_finite_inplace(arr, scale, chunk_rows=512):
    """Apply a scalar to finite pixels in bounded row blocks."""
    if not np.isfinite(scale) or abs(float(scale) - 1.0) < 1e-12:
        return
    h = int(arr.shape[0])
    for y0 in range(0, h, int(chunk_rows)):
        y1 = min(h, y0 + int(chunk_rows))
        block = np.asarray(arr[y0:y1], dtype=np.float32).copy()
        good = np.isfinite(block)
        block[good] *= np.float32(scale)
        arr[y0:y1] = block
    flush = getattr(arr, "flush", None)
    if callable(flush):
        flush()


def _finite_fraction_blockwise(arr, chunk_rows=512):
    """Return finite-pixel fraction without allocating a full-size mask."""
    finite = 0
    total = int(arr.shape[0]) * int(arr.shape[1])
    if total <= 0:
        return 0.0
    for y0 in range(0, int(arr.shape[0]), int(chunk_rows)):
        y1 = min(int(arr.shape[0]), y0 + int(chunk_rows))
        finite += int(np.count_nonzero(np.isfinite(arr[y0:y1])))
    return float(finite / total)


def _build_common_overlap_mask(ref_data, reproj_data, footprints,
                               chunk_rows=512):
    """Build the all-layers overlap mask while bounding temporary memory."""
    mask = np.empty(ref_data.shape, dtype=bool)
    h = int(ref_data.shape[0])
    for y0 in range(0, h, int(chunk_rows)):
        y1 = min(h, y0 + int(chunk_rows))
        block = np.isfinite(ref_data[y0:y1])
        for nm, arr in reproj_data.items():
            block &= np.isfinite(arr[y0:y1])
            block &= np.asarray(footprints[nm][y0:y1]) > 0
        mask[y0:y1] = block
    return mask


def _has_reference_overlap(ref_data, arr, footprint, chunk_rows=512):
    """True when a partial layer contains at least one valid reference pixel."""
    h = int(ref_data.shape[0])
    for y0 in range(0, h, int(chunk_rows)):
        y1 = min(h, y0 + int(chunk_rows))
        if np.any(np.isfinite(ref_data[y0:y1])
                  & np.isfinite(arr[y0:y1])
                  & (np.asarray(footprint[y0:y1]) > 0)):
            return True
    return False


def _write_uint8_mask_fits(path, source, header, positive=False, finite=False,
                           finite_source=None, chunk_rows=512):
    """Write a large uint8 mask through a temporary disk-backed array."""
    raw_path = os.fspath(path) + ".mask-writing.u8"
    mm = None
    try:
        mm = np.memmap(raw_path, dtype=np.uint8, mode="w+", shape=source.shape)
        h = int(source.shape[0])
        for y0 in range(0, h, int(chunk_rows)):
            y1 = min(h, y0 + int(chunk_rows))
            block = np.asarray(source[y0:y1])
            if finite:
                block = np.isfinite(block)
            elif positive:
                block = block > 0
            if finite_source is not None:
                block = np.asarray(block, dtype=bool)
                block &= np.isfinite(finite_source[y0:y1])
            mm[y0:y1] = np.asarray(block, dtype=np.uint8)
        mm.flush()
        fits.PrimaryHDU(mm, header=header).writeto(path, overwrite=True)
    finally:
        try:
            mmap_obj = getattr(mm, "_mmap", None)
            if mmap_obj is not None:
                mmap_obj.close()
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
        try:
            del mm
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
        try:
            if os.path.exists(raw_path):
                os.remove(raw_path)
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)


def psf_match_to_worst(images_by_name, names_in_order):
    wl = {}
    for nm in names_in_order:
        code = parse_filter_code(nm)
        wl[nm] = filter_code_to_microns(code) if code else None

    known = [(nm, wl[nm]) for nm in names_in_order if wl[nm] is not None]
    if len(known) < 2:
        return images_by_name, ["PSF match skipped: could not parse wavelengths from names."]

    lam_min = min(v for _, v in known)
    lam_max = max(v for _, v in known)

    base_fwhm_min_pix = 2.0
    fwhm_target = base_fwhm_min_pix * (lam_max / lam_min)
    sig_target = fwhm_target / 2.355

    notes = [f"PSF match: lam_min={lam_min:.2f}um lam_max={lam_max:.2f}um target_fwhm={fwhm_target:.2f}px"]

    out = {}
    for nm in names_in_order:
        img = images_by_name[nm]
        mic = wl.get(nm, None)
        if mic is None:
            out[nm] = img
            notes.append(f"PSF match: {nm} skipped (no wavelength parsed).")
            continue

        fwhm_cur = base_fwhm_min_pix * (mic / lam_min)
        sig_cur = fwhm_cur / 2.355
        sig_add_sq = sig_target * sig_target - sig_cur * sig_cur
        if sig_add_sq <= 1e-6:
            out[nm] = img
            continue

        sig_add = float(np.sqrt(sig_add_sq))
        out[nm] = gaussian_filter(img, sigma=sig_add)
        notes.append(f"PSF match: {nm} convolved sigma_add={sig_add:.3f}px")

    return out, notes

__all__ = [name for name in globals() if not name.startswith('__')]
