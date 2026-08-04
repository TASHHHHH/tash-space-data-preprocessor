from __future__ import annotations

from tsdp2._deps import *
from tsdp2.logging_config import LOGGER
from tsdp2.output_paths import JobPaths
from tsdp2.astronomy.fits_io import _PRIMARY_META_KEYS, _iter_requested_image_hdus, _mosaic_input_display_name, _parse_mosaic_hdu_ref, load_2d_and_header
from tsdp2.astronomy.previews import apply_stretch
from tsdp2.astronomy.reference import parse_filter_code

def _has_valid_celestial_wcs(data, hdr):
    """Return True if hdr has a 2D celestial WCS with a finite sky footprint."""
    try:
        w = WCS(hdr, naxis=2)
        if not w.has_celestial:
            return False
        h_px, w_px = data.shape[-2], data.shape[-1]
        # Astropy WCS axes are (NAXIS1, NAXIS2) == (width, height).
        # Passing (height, width) gives wrong footprints for non-square FITS.
        fp = w.calc_footprint(axes=(w_px, h_px))
        return fp is not None and bool(np.all(np.isfinite(fp)))
    except EXPECTED_OPERATION_ERRORS:
        return False


def wcs_footprint_from_header_only(path: str):
    """Return a WCS-only footprint record for a FITS file without loading pixels.

    This is intentionally header-only. It reads NAXIS1/NAXIS2 + celestial WCS
    from the first usable image HDU and computes the sky footprint corners.
    """
    try:
        real_path, requested_hdu, _label = _parse_mosaic_hdu_ref(path)
        with fits.open(real_path, memmap=True, lazy_load_hdus=True) as hdul:
            primary_hdr = hdul[0].header.copy() if hdul else fits.Header()
            for hdu in _iter_requested_image_hdus(hdul, requested_hdu):
                hdr = hdu.header.copy()
                if int(hdr.get("NAXIS", 0) or 0) < 2:
                    continue
                nx = int(hdr.get("NAXIS1", 0) or 0)
                ny = int(hdr.get("NAXIS2", 0) or 0)
                if nx <= 0 or ny <= 0:
                    continue
                for k in _PRIMARY_META_KEYS:
                    if k in primary_hdr and k not in hdr:
                        try:
                            hdr[k] = primary_hdr[k]
                        except EXPECTED_OPERATION_ERRORS:
                            LOGGER.debug("Best-effort operation failed", exc_info=True)
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore")
                        w = WCS(hdr, naxis=2)
                        if not w.has_celestial:
                            continue
                        fp = w.calc_footprint(axes=(nx, ny))
                    fp = np.asarray(fp, dtype=float)
                    if fp.shape != (4, 2) or not np.all(np.isfinite(fp)):
                        continue
                    filt = (
                        parse_filter_code(str(hdr.get("FILTER", ""))) or
                        parse_filter_code(str(hdr.get("FILTER1", ""))) or
                        parse_filter_code(str(hdr.get("FILTER2", ""))) or
                        parse_filter_code(str(hdr.get("PUPIL", ""))) or
                        parse_filter_code(os.path.basename(real_path)) or
                        "UNKNOWN"
                    )
                    return {
                        "path": path,
                        "filename": _mosaic_input_display_name(path),
                        "filter": filt,
                        "shape": (ny, nx),
                        "footprint": fp,
                    }
                except EXPECTED_OPERATION_ERRORS:
                    continue
    except EXPECTED_OPERATION_ERRORS:
        return None
    return None


def _normalise_ra_for_coverage(records):
    """Return copied records with RA unwrapped when the field crosses RA=0."""
    if not records:
        return []
    ras = np.array([pt[0] for rec in records for pt in rec["footprint"]], dtype=float)
    if ras.size and np.nanmax(ras) - np.nanmin(ras) > 180.0:
        out = []
        for rec in records:
            rec2 = dict(rec)
            fp = np.array(rec["footprint"], dtype=float, copy=True)
            fp[:, 0] = np.where(fp[:, 0] < 180.0, fp[:, 0] + 360.0, fp[:, 0])
            rec2["footprint_plot"] = fp
            out.append(rec2)
        return out
    out = []
    for rec in records:
        rec2 = dict(rec)
        rec2["footprint_plot"] = np.array(rec["footprint"], dtype=float, copy=True)
        out.append(rec2)
    return out


def _coverage_fetch_sky_background(records, width_px=1200, height_px=850, survey_name="DSS2 Red"):
    """Fetch a small sky-image cutout for the WCS coverage preview.

    This does not download a full survey image. It requests one fixed-size
    cutout around the combined footprint bounding box, then overlays the FITS
    footprints on that cutout using the cutout WCS.
    """
    if not HAVE_PIL or not records:
        return None
    try:
        from astroquery.skyview import SkyView
        from astropy.coordinates import SkyCoord
        import astropy.units as u
    except EXPECTED_OPERATION_ERRORS as exc:
        print(f"[WCS coverage] SkyView unavailable: {exc}", flush=True)
        return None

    try:
        plot_records = _normalise_ra_for_coverage(records)
        ras = np.array([pt[0] for rec in plot_records for pt in rec["footprint_plot"]], dtype=float)
        decs = np.array([pt[1] for rec in plot_records for pt in rec["footprint_plot"]], dtype=float)
        if not ras.size or not decs.size:
            return None

        ra_min, ra_max = float(np.nanmin(ras)), float(np.nanmax(ras))
        dec_min, dec_max = float(np.nanmin(decs)), float(np.nanmax(decs))
        ra_center_plot = (ra_min + ra_max) / 2.0
        ra_center = ra_center_plot % 360.0
        dec_center = (dec_min + dec_max) / 2.0

        ra_span = max(ra_max - ra_min, 1e-5)
        dec_span = max(dec_max - dec_min, 1e-5)

        # Padding makes the background look like a finder chart instead of a
        # tight crop. Pixels stay fixed, so this does not explode memory.
        width_deg = max(ra_span * 1.35, 0.03)
        height_deg = max(dec_span * 1.35, 0.03)

        # Avoid huge all-sky style requests by capping the angular cutout.
        width_deg = min(width_deg, 5.0)
        height_deg = min(height_deg, 5.0)

        center = SkyCoord(ra_center * u.deg, dec_center * u.deg, frame="icrs")
        surveys = []
        if survey_name:
            surveys.append(survey_name)
        surveys += ["DSS2 Red", "DSS2 Blue", "DSS", "2MASS-K", "WISE 3.4"]

        seen = set()
        for survey in surveys:
            if survey in seen:
                continue
            seen.add(survey)
            try:
                hdul_list = SkyView.get_images(
                    position=center,
                    survey=[survey],
                    width=width_deg * u.deg,
                    height=height_deg * u.deg,
                    pixels=[int(width_px), int(height_px)],
                    cache=True,
                    show_progress=False,
                )
                if not hdul_list:
                    continue
                hdu = hdul_list[0][0]
                data = np.asarray(hdu.data, dtype=np.float32)
                while data.ndim > 2 and 1 in data.shape:
                    data = np.squeeze(data)
                while data.ndim > 2:
                    data = data[0]
                if data.ndim != 2:
                    continue
                bad = ~np.isfinite(data)
                if bad.any():
                    finite = data[np.isfinite(data)]
                    fill = float(np.nanmedian(finite)) if finite.size else 0.0
                    data[bad] = fill

                arr = apply_stretch(data, mode="asinh", plo=0.5, phi=99.7)
                img = Image.fromarray(arr, mode="L").convert("RGBA")
                w = WCS(hdu.header, naxis=2)
                return {
                    "image": img,
                    "wcs": w,
                    "survey": survey,
                    "center_ra": ra_center,
                    "center_dec": dec_center,
                    "width_deg": width_deg,
                    "height_deg": height_deg,
                }
            except EXPECTED_OPERATION_ERRORS as exc:
                print(f"[WCS coverage] SkyView survey failed ({survey}): {exc}", flush=True)
                continue
    except EXPECTED_OPERATION_ERRORS as exc:
        print(f"[WCS coverage] background fetch failed: {exc}", flush=True)
    return None


def _find_science_and_weight_hdus_for_mask(path: str):
    """Return (open_hdul, science_hdu, weight_hdu, merged_header).

    Pure WCS footprints only know the outer corners. They cannot show detector
    gaps, drizzle holes, bad WHT regions, or NaN islands. This helper is for the
    preview only: it lets us sample a tiny strided mask through FITS memmap
    without loading the full science image.
    """
    real_path, requested_hdu, _label = _parse_mosaic_hdu_ref(path)
    hdul = fits.open(real_path, memmap=True)
    primary_hdr = hdul[0].header.copy() if hdul else fits.Header()
    sci_hdu = None
    wht_hdu = None

    if requested_hdu is not None:
        if requested_hdu < 0 or requested_hdu >= len(hdul):
            hdul.close()
            raise RuntimeError(
                f"FITS HDU index {requested_hdu} is out of range for {real_path}.")
        candidate = hdul[requested_hdu]
        if int(candidate.header.get("NAXIS", 0) or 0) >= 2:
            sci_hdu = candidate

    for hdu in (() if requested_hdu is not None else hdul):
        hdr = hdu.header
        if hdu.data is None or int(hdr.get("NAXIS", 0) or 0) < 2:
            continue
        extname = str(hdr.get("EXTNAME", "")).strip().upper()
        if extname == "SCI":
            sci_hdu = hdu
            break
        if sci_hdu is None:
            sci_hdu = hdu

    for hdu in (() if requested_hdu is not None else hdul):
        hdr = hdu.header
        if hdu.data is None or int(hdr.get("NAXIS", 0) or 0) < 2:
            continue
        extname = str(hdr.get("EXTNAME", "")).strip().upper()
        if extname in ("WHT", "WEIGHT", "CON", "CTX"):
            wht_hdu = hdu
            if extname in ("WHT", "WEIGHT"):
                break

    if sci_hdu is None:
        try:
            hdul.close()
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
        return None, None, None, None

    hdr = sci_hdu.header.copy()
    for k in _PRIMARY_META_KEYS:
        if k in primary_hdr and k not in hdr:
            try:
                hdr[k] = primary_hdr[k]
            except EXPECTED_OPERATION_ERRORS:
                LOGGER.debug("Best-effort operation failed", exc_info=True)
    return hdul, sci_hdu, wht_hdu, hdr


def _squeeze_2d_array_for_mosaic(arr):
    """Return a 2-D view/copy from common FITS image shapes."""
    if arr is None:
        return None
    while getattr(arr, "ndim", 0) > 2 and 1 in arr.shape:
        arr = np.squeeze(arr)
    while getattr(arr, "ndim", 0) > 2:
        arr = arr[0]
    if getattr(arr, "ndim", 0) != 2:
        return None
    return arr


def _detect_flat_border_fill_mask(data: np.ndarray):
    """Detect synthetic no-coverage fill connected to image edges.

    HST/JWST drizzle products sometimes contain finite grey fill in the corners
    instead of NaN. A strict exact-value detector misses this when the fill has
    tiny numerical texture, so this uses robust corner statistics and then keeps
    only edge-connected pixels. That means it can remove the big grey rectangle
    while avoiding isolated real sky/background inside the exposed tile.
    """
    try:
        if data is None or getattr(data, "ndim", 0) != 2:
            return None
        h, w = data.shape
        if h < 32 or w < 32:
            return None
        finite = np.isfinite(data)
        if not finite.any():
            return None

        # Work from corner/edge samples, because the fake fill is normally on
        # the output rectangle edges/corners.
        patch = int(max(16, min(256, min(h, w) // 12)))
        samples = [
            data[:patch, :patch], data[:patch, -patch:],
            data[-patch:, :patch], data[-patch:, -patch:],
            data[:max(4, patch // 4), :], data[-max(4, patch // 4):, :],
            data[:, :max(4, patch // 4)], data[:, -max(4, patch // 4):],
        ]
        vals = np.concatenate([s[np.isfinite(s)].ravel() for s in samples if np.isfinite(s).any()])
        if vals.size < 200:
            return None

        full = data[finite]
        if full.size > 600_000:
            full = full[::max(1, full.size // 600_000)]
        dyn = float(np.nanpercentile(full, 99.5) - np.nanpercentile(full, 0.5))
        dyn = max(dyn, 1e-12)

        # Use a few robust candidate fill levels from border/corner percentiles.
        cand_levels = []
        for q in (10, 25, 50, 75, 90):
            try:
                cand_levels.append(float(np.nanpercentile(vals, q)))
            except EXPECTED_OPERATION_ERRORS:
                LOGGER.debug("Best-effort operation failed", exc_info=True)
        # De-duplicate close levels.
        levels = []
        for v in cand_levels:
            if np.isfinite(v) and all(abs(v - u) > dyn * 0.003 for u in levels):
                levels.append(v)

        best = None
        best_count = 0
        for level in levels:
            # Keep this deliberately tight. The former 1.2%-of-dynamic-range
            # tolerance could follow real bright nebulosity inward from a tile
            # edge and carve black, blocky holes from DECam mosaics.
            tol = max(dyn * 0.001, abs(level) * 1e-5, 1e-8)
            candidate = finite & (np.abs(data - level) <= tol)
            edge_frac = (
                candidate[0, :].mean() + candidate[-1, :].mean() +
                candidate[:, 0].mean() + candidate[:, -1].mean()
            ) / 4.0
            if edge_frac < 0.08:
                continue
            seed = np.zeros(candidate.shape, dtype=bool)
            seed[0, :] = candidate[0, :]
            seed[-1, :] = candidate[-1, :]
            seed[:, 0] = candidate[:, 0]
            seed[:, -1] = candidate[:, -1]
            fill = binary_propagation(seed, mask=candidate)
            count = int(fill.sum())
            if count > best_count:
                best = fill.astype(bool)
                best_count = count

        if best is None:
            return None
        # Ignore tiny detections. Real fake fill is usually a noticeable chunk.
        if best_count < max(256, int(finite.size * 0.002)):
            return None
        return best
    except EXPECTED_OPERATION_ERRORS:
        return None


def _detect_low_texture_border_fill_mask(sample: np.ndarray):
    """Detect edge-connected synthetic grey fill by its missing sky noise.

    Some NOIRLab cutouts store uncovered pixels as a quantized sky plateau:
    every pixel is finite and values are not exactly constant, but local
    texture is ~10-50x lower than real exposed sky. This detector operates on
    a small sampled grid and only activates when the texture distribution is
    strongly bimodal, which protects genuinely smooth astronomical emission.
    """
    try:
        arr = np.asarray(sample, dtype=np.float32)
        if arr.ndim != 2 or min(arr.shape) < 24:
            return None
        finite = np.isfinite(arr)
        if int(np.count_nonzero(finite)) < 500:
            return None
        values = arr[finite]
        lo, hi = np.nanpercentile(values, [1.0, 98.0])
        if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
            return None
        work = np.clip(arr, lo, hi)
        if not finite.all():
            work = work.copy()
            work[~finite] = float(np.nanmedian(values))
        smooth = gaussian_filter(work, 1.2, mode="reflect")
        texture = np.sqrt(np.maximum(
            gaussian_filter((work - smooth) ** 2, 1.5, mode="reflect"), 0.0))
        q05, q30, q50, q70 = np.nanpercentile(texture[finite], [5, 30, 50, 70])
        eps = max(float(q50) * 1e-8, 1e-12)
        strongly_bimodal = (
            float(q05) / max(float(q50), eps) < 0.18 or
            float(q30) / max(float(q70), eps) < 0.18)
        if not strongly_bimodal:
            return None
        threshold = max(float(q05) * 3.0, eps)
        threshold = min(threshold, float(q50) * 0.45)
        candidate = finite & (texture <= threshold)
        seed = np.zeros(candidate.shape, dtype=bool)
        seed[0, :] = candidate[0, :]
        seed[-1, :] = candidate[-1, :]
        seed[:, 0] = candidate[:, 0]
        seed[:, -1] = candidate[:, -1]
        fill = binary_propagation(seed, mask=candidate)
        if int(np.count_nonzero(fill)) < max(64, int(arr.size * 0.005)):
            return None
        # Include the one-sample transition rim between the plateau and real
        # data. At preview sampling resolution this is conservative and stops
        # interpolation from leaking the grey plateau into the mosaic.
        fill = binary_dilation(fill, iterations=1)
        return fill.astype(bool)
    except EXPECTED_OPERATION_ERRORS:
        return None


def _sample_border_fill_geometry(data, max_samples=420):
    """Return (small mask, sampled y, sampled x, reasons) for grey no-data."""
    if data is None or getattr(data, "ndim", 0) != 2:
        return None, None, None, []
    h, w = int(data.shape[0]), int(data.shape[1])
    step = max(1, int(np.ceil(max(h, w) / max(32, int(max_samples)))))
    yy = np.arange(step // 2, h, step, dtype=np.int64)
    xx = np.arange(step // 2, w, step, dtype=np.int64)
    if yy.size < 24 or xx.size < 24:
        yy = np.arange(h, dtype=np.int64)
        xx = np.arange(w, dtype=np.int64)
    sample = np.asarray(data[yy[:, None], xx[None, :]], dtype=np.float32)
    masks = []
    reasons = []
    exact = _detect_flat_border_fill_mask(sample)
    if exact is not None and np.any(exact):
        masks.append(exact)
        reasons.append("flat-border-fill")
    textured = _detect_low_texture_border_fill_mask(sample)
    if textured is not None and np.any(textured):
        masks.append(textured)
        reasons.append("low-texture-grey-fill")
    if not masks:
        return None, yy, xx, []
    combined = np.logical_or.reduce(masks)
    return combined.astype(bool), yy, xx, reasons


def _remove_sampled_fill_from_valid(valid, small_mask, yy, xx, chunk_rows=512):
    """Apply a sampled no-data geometry to a full-size boolean mask in chunks."""
    if small_mask is None or not np.any(small_mask):
        return
    h, w = valid.shape
    if len(yy) == h and len(xx) == w:
        valid &= ~small_mask
        return
    y_step = float(np.median(np.diff(yy))) if len(yy) > 1 else 1.0
    x_step = float(np.median(np.diff(xx))) if len(xx) > 1 else 1.0
    x_lookup = np.clip(
        np.rint((np.arange(w, dtype=np.float64) - float(xx[0])) / x_step).astype(np.int64),
        0, len(xx) - 1)
    for y0 in range(0, h, max(1, int(chunk_rows))):
        y1 = min(h, y0 + max(1, int(chunk_rows)))
        y_lookup = np.clip(
            np.rint((np.arange(y0, y1, dtype=np.float64) - float(yy[0])) / y_step).astype(np.int64),
            0, len(yy) - 1)
        valid[y0:y1] &= ~small_mask[y_lookup[:, None], x_lookup[None, :]]


def _load_mosaic_2d_and_header(path: str):
    """Load mosaic SCI data and mark no-coverage pixels as NaN.

    This is deliberately separate from load_2d_and_header(). The general loader
    fills NaNs with a median so thumbnails/align previews remain easy, but that
    is exactly wrong for mosaics: finite grey no-data corners then contaminate
    overlapping tiles. Mosaic loading keeps no-coverage as NaN using WHT/WEIGHT
    when present, with a conservative flat-border fallback.
    """
    hdul = None
    mask_note = []
    try:
        found = _find_science_and_weight_hdus_for_mask(path)
        if not found or found[0] is None:
            # Fallback, but undo the old median-fill behavior as much as possible
            data, hdr = load_2d_and_header(path)
            return data.astype(np.float32, copy=False), hdr

        hdul, sci_hdu, wht_hdu, hdr = found
        src = _squeeze_2d_array_for_mosaic(sci_hdu.data)
        if src is None:
            raise RuntimeError(f"No usable 2-D science image in {path}")
        data = np.array(src, dtype=np.float32, copy=True)
        valid = np.isfinite(data)

        wdata = None
        if wht_hdu is not None:
            wsrc = _squeeze_2d_array_for_mosaic(wht_hdu.data)
            if wsrc is not None and tuple(wsrc.shape[-2:]) == tuple(data.shape[-2:]):
                wdata = np.asarray(wsrc, dtype=np.float32)
                valid &= np.isfinite(wdata) & (wdata > 0)
                mask_note.append(str(wht_hdu.header.get("EXTNAME", "WHT")).strip() or "WHT")

        if wdata is None:
            # Exact zero often means no exposure in downloaded survey/drizzle products.
            # Only apply it if zero is common on the image border.
            try:
                border = np.concatenate([data[0, :], data[-1, :], data[:, 0], data[:, -1]])
                zero_border_frac = float(np.isfinite(border).sum() and np.mean(border[np.isfinite(border)] == 0))
                if zero_border_frac > 0.08:
                    valid &= (data != 0)
                    mask_note.append("border-zero")
            except EXPECTED_OPERATION_ERRORS:
                LOGGER.debug("Best-effort operation failed", exc_info=True)

        # When there is no authoritative weight plane, inspect a small sampled
        # grid for both exact flat fill and finite low-texture grey plateaus.
        # Apply the sampled geometry to the full mask in row chunks so a 3 GB
        # DECam MEF does not need another full-size temporary array.
        if wdata is None:
            fill_small, fill_yy, fill_xx, fill_reasons = _sample_border_fill_geometry(
                data, max_samples=220)
            if fill_small is not None and np.any(fill_small):
                _remove_sampled_fill_from_valid(valid, fill_small, fill_yy, fill_xx)
                mask_note.extend(fill_reasons)

        removed = int((~valid).sum())
        total = int(valid.size) if hasattr(valid, "size") else 0
        if total and removed:
            data[~valid] = np.nan
            try:
                pct = removed / total * 100.0
                print(f"[Mosaic] mask {os.path.basename(path)}: removed {pct:.2f}% no-coverage pixels"
                      f" ({', '.join(mask_note) if mask_note else 'NaN'})", flush=True)
            except EXPECTED_OPERATION_ERRORS:
                LOGGER.debug("Best-effort operation failed", exc_info=True)
        return data.astype(np.float32, copy=False), hdr
    finally:
        try:
            if hdul is not None:
                hdul.close()
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)


def _sample_valid_pixel_mask_on_preview(path: str, preview_wcs, width_px: int, height_px: int,
                                        max_samples: int = 150, return_stats: bool = False):
    """Return a preview-sized uint8 coverage mask from sampled valid pixels.

    This reveals real chip gaps / no-coverage holes. It samples a small grid
    from the FITS file through memmap and uses the WHT/WEIGHT extension when it
    exists. It does not load the full science map and it does not run mosaic
    reprojection.
    """
    hdul = None
    try:
        found = _find_science_and_weight_hdus_for_mask(path)
        if not found or found[0] is None:
            return (None, 0.0) if return_stats else None
        hdul, sci_hdu, wht_hdu, hdr = found
        data = sci_hdu.data
        if data is None:
            return (None, 0.0) if return_stats else None

        while getattr(data, "ndim", 0) > 2 and 1 in data.shape:
            data = np.squeeze(data)
        while getattr(data, "ndim", 0) > 2:
            data = data[0]
        if getattr(data, "ndim", 0) != 2:
            return (None, 0.0) if return_stats else None

        ny, nx = int(data.shape[-2]), int(data.shape[-1])
        if nx <= 0 or ny <= 0:
            return (None, 0.0) if return_stats else None

        step = max(1, int(np.ceil(max(nx, ny) / max(24, int(max_samples)))))
        yy = np.arange(step // 2, ny, step, dtype=int)
        xx = np.arange(step // 2, nx, step, dtype=int)
        if yy.size == 0 or xx.size == 0:
            return (None, 0.0) if return_stats else None

        sample = np.asarray(data[yy[:, None], xx[None, :]], dtype=np.float32)
        valid = np.isfinite(sample)

        if wht_hdu is not None and getattr(wht_hdu.data, "ndim", 0) >= 2:
            wdata = wht_hdu.data
            while getattr(wdata, "ndim", 0) > 2 and 1 in wdata.shape:
                wdata = np.squeeze(wdata)
            while getattr(wdata, "ndim", 0) > 2:
                wdata = wdata[0]
            if getattr(wdata, "ndim", 0) == 2 and wdata.shape[-2:] == data.shape[-2:]:
                wsample = np.asarray(wdata[yy[:, None], xx[None, :]], dtype=np.float32)
                valid &= np.isfinite(wsample) & (wsample > 0)
        else:
            # Some products use exact 0 as no-coverage. Do not blindly remove
            # zeros, because real sky background can be near zero. Only apply
            # this when zeros are common enough to smell like a mask.
            try:
                zero_frac = float((sample == 0).sum() / max(sample.size, 1))
                if zero_frac > 0.08:
                    valid &= (sample != 0)
            except EXPECTED_OPERATION_ERRORS:
                LOGGER.debug("Best-effort operation failed", exc_info=True)

        # A FITS image can be rectangular while a large part of that rectangle
        # is only synthetic, nearly-flat grey fill. Detect it on the sampled
        # grid as well, so Coverage reports the REAL data fraction rather than
        # treating every finite grey pixel as sky.
        if wht_hdu is None:
            try:
                flat_fill = _detect_flat_border_fill_mask(sample)
                if flat_fill is not None and flat_fill.shape == valid.shape:
                    valid &= ~flat_fill
                texture_fill = _detect_low_texture_border_fill_mask(sample)
                if texture_fill is not None and texture_fill.shape == valid.shape:
                    valid &= ~texture_fill
            except EXPECTED_OPERATION_ERRORS:
                LOGGER.debug("Best-effort operation failed", exc_info=True)

        valid_fraction = float(np.count_nonzero(valid) / max(valid.size, 1) * 100.0)

        if not np.any(valid):
            return (None, valid_fraction) if return_stats else None

        xv, yv = np.meshgrid(xx.astype(float), yy.astype(float))
        xv = xv[valid]
        yv = yv[valid]

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            in_wcs = WCS(hdr, naxis=2)
            if not in_wcs.has_celestial:
                return (None, valid_fraction) if return_stats else None
            ra, dec = in_wcs.all_pix2world(xv, yv, 0)
            px, py = preview_wcs.all_world2pix(ra, dec, 0)

        finite = np.isfinite(px) & np.isfinite(py)
        if not np.any(finite):
            return (None, valid_fraction) if return_stats else None
        px = np.rint(px[finite]).astype(np.int32)
        py = np.rint(py[finite]).astype(np.int32)
        inside = (px >= 0) & (px < width_px) & (py >= 0) & (py < height_px)
        if not np.any(inside):
            return (None, valid_fraction) if return_stats else None

        mask = np.zeros((height_px, width_px), dtype=np.uint8)
        mask[py[inside], px[inside]] = 255

        if HAVE_PIL:
            from PIL import ImageFilter
            spread = max(3, int(round(min(width_px, height_px) / max(80, int(max_samples) * 2))))
            if spread % 2 == 0:
                spread += 1
            spread = min(max(spread, 3), 11)
            mask = np.asarray(Image.fromarray(mask, mode="L").filter(ImageFilter.MaxFilter(spread)), dtype=np.uint8)
        return (mask, valid_fraction) if return_stats else mask
    except EXPECTED_OPERATION_ERRORS as exc:
        print(f"[WCS coverage] sampled mask failed for {os.path.basename(path)}: {exc}", flush=True)
        return (None, 0.0) if return_stats else None
    finally:
        try:
            if hdul is not None:
                hdul.close()
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)


def _rgba_from_mask(mask: np.ndarray, color_rgba):
    r, g, b, a = color_rgba
    out = np.zeros((mask.shape[0], mask.shape[1], 4), dtype=np.uint8)
    out[..., 0] = r
    out[..., 1] = g
    out[..., 2] = b
    out[..., 3] = (mask.astype(np.float32) * (a / 255.0)).clip(0, 255).astype(np.uint8)
    return out


def _draw_sampled_hole_coverage(img, wcs_bg, records, use_groups, color_by_group,
                                width_px, height_px, overlay_opacity=0.10):
    """Overlay sampled valid-pixel masks. Return (used, count, union, overlap)."""
    if not HAVE_PIL:
        return False, None, None, None
    nrec = max(1, len(records))
    max_samples = 210 if len(use_groups) <= 1 else (150 if nrec <= 25 else 105)

    count = np.zeros((height_px, width_px), dtype=np.uint16)
    used = 0
    alpha = int(round(255.0 * min(max(float(overlay_opacity), 0.0), 1.0)))
    from PIL import ImageDraw
    for rec in records:
        code = rec.get("group", rec.get("filter", "UNKNOWN"))
        mask, valid_fraction = _sample_valid_pixel_mask_on_preview(
            rec.get("path", ""), wcs_bg, width_px, height_px,
            max_samples=max_samples, return_stats=True)
        rec["valid_fraction"] = float(valid_fraction)
        if mask is None or not np.any(mask):
            continue
        count += (mask > 0).astype(np.uint16)
        col = color_by_group.get(code, (255, 255, 255, 95))
        polygon_mask_img = Image.new("L", (width_px, height_px), 0)
        pts = rec.get("preview_polygon") or []
        if len(pts) >= 3:
            ImageDraw.Draw(polygon_mask_img).polygon(pts, fill=255)
        polygon_mask = np.asarray(polygon_mask_img, dtype=np.uint8)
        invalid_mask = np.where(
            (polygon_mask > 0) & (mask == 0), 255, 0).astype(np.uint8)
        if np.any(invalid_mask):
            grey = Image.fromarray(
                _rgba_from_mask(invalid_mask, (65, 68, 78, alpha)), mode="RGBA")
            img.alpha_composite(grey)
        overlay = Image.fromarray(
            _rgba_from_mask(mask, (col[0], col[1], col[2], alpha)), mode="RGBA")
        img.alpha_composite(overlay)
        used += 1

    if used <= 0:
        return False, None, None, None
    denom = max(width_px * height_px, 1)
    return True, count, float((count > 0).sum() / denom * 100.0), float((count > 1).sum() / denom * 100.0)


def _draw_wcs_coverage_on_sky_background(records, use_groups, invalid, title, cov_dir,
                                         width_px=1200, height_px=850, sampled_holes=True,
                                         overlay_opacity=0.10,
                                         show_tile_labels=True, output_path=None):
    """Draw WCS footprints over a small SkyView cutout if possible."""
    if not HAVE_PIL:
        return None
    from PIL import ImageDraw

    bg = _coverage_fetch_sky_background(records, width_px=width_px, height_px=height_px)
    if not bg:
        return None

    img = bg["image"].copy().convert("RGBA")
    if img.size != (width_px, height_px):
        img = img.resize((width_px, height_px), Image.LANCZOS)
    draw = ImageDraw.Draw(img, "RGBA")
    wcs_bg = bg["wcs"]

    palette = [
        (34, 211, 238, 38), (239, 68, 68, 38), (34, 197, 94, 38),
        (59, 130, 246, 38), (250, 204, 21, 38), (168, 85, 247, 38),
        (45, 212, 191, 38), (244, 114, 182, 38), (251, 146, 60, 38),
    ]
    edge_palette = [(r, g, b, 235) for r, g, b, _a in palette]
    group_codes = sorted(use_groups.keys())
    color_by_group = {c: palette[i % len(palette)] for i, c in enumerate(group_codes)}
    edge_by_group = {c: edge_palette[i % len(edge_palette)] for i, c in enumerate(group_codes)}

    count = np.zeros((height_px, width_px), dtype=np.uint16)
    good_polys = 0
    sampled_used = False

    # First shade the complete FITS rectangles grey. The coloured overlay is
    # added only where sampled science/weight pixels are genuinely usable.
    # Therefore a half-empty rectangular FITS appears as half colour + half
    # grey instead of one misleading solid rectangle.
    for rec in records:
        fp = np.asarray(rec["footprint"], dtype=float)
        try:
            xs, ys = wcs_bg.all_world2pix(fp[:, 0], fp[:, 1], 0)
        except EXPECTED_OPERATION_ERRORS:
            continue
        if not np.all(np.isfinite(xs)) or not np.all(np.isfinite(ys)):
            continue
        pts = [(int(round(x)), int(round(y))) for x, y in zip(xs, ys)]
        if len(pts) < 3:
            continue
        rec["preview_polygon"] = pts

    # Useful mode: sampled valid-pixel coverage. This shows real holes/chip gaps
    # that a solid WCS footprint rectangle cannot reveal.
    if sampled_holes:
        sampled_used, sampled_count, sampled_union, sampled_overlap = _draw_sampled_hole_coverage(
            img, wcs_bg, records, use_groups, color_by_group, width_px, height_px,
            overlay_opacity=overlay_opacity
        )
        if sampled_used:
            count = sampled_count
            union_pct = sampled_union
            overlap_pct = sampled_overlap

    for rec in records:
        pts = list(rec.get("preview_polygon") or [])
        if len(pts) < 3:
            continue
        code = rec.get("group", rec.get("filter", "UNKNOWN"))
        fill = color_by_group.get(code, (255, 255, 255, 34))
        edge = edge_by_group.get(code, (255, 255, 255, 235))

        if not sampled_used:
            # Fallback: old WCS-only polygon fill. It cannot show interior holes.
            draw.polygon(
                pts, fill=(fill[0], fill[1], fill[2],
                           int(round(255 * min(max(float(overlay_opacity), 0.0), 1.0)))))
            mask = Image.new("L", (width_px, height_px), 0)
            ImageDraw.Draw(mask).polygon(pts, fill=1)
            count += np.asarray(mask, dtype=np.uint16)
        draw.line(pts + [pts[0]], fill=edge, width=3)
        # Label each rectangle with the HDU/tile name and actual usable data
        # percentage. A dark label backing keeps it readable over DSS imagery.
        frac = rec.get("valid_fraction")
        if show_tile_labels and frac is not None:
            cx = int(round(sum(p[0] for p in pts) / len(pts)))
            cy = int(round(sum(p[1] for p in pts) / len(pts)))
            short_name = str(rec.get("filename") or rec.get("filter") or "tile")
            if len(short_name) > 24:
                short_name = short_name[-24:]
            label = f"{short_name}  {float(frac):.0f}% data"
            tw = max(82, 6 * len(label) + 10)
            draw.rectangle([cx - tw // 2, cy - 11, cx + tw // 2, cy + 11],
                           fill=(0, 0, 0, 175))
            draw.text((cx - tw // 2 + 5, cy - 7), label,
                      fill=(255, 255, 255, 255))
        good_polys += 1

    if good_polys == 0:
        return None

    if not sampled_used:
        denom = max(width_px * height_px, 1)
        union_pct = float((count > 0).sum() / denom * 100.0)
        overlap_pct = float((count > 1).sum() / denom * 100.0)

    # Top label strip.
    draw.rectangle([0, 0, width_px, 58], fill=(0, 0, 0, 140))
    draw.text((16, 10), title, fill=(255, 255, 255, 255))
    if sampled_used:
        mode_note = "sampled valid-pixel masks | full science maps not loaded"
    else:
        mode_note = "WCS corner footprints only | interior holes not visible"
    draw.text((16, 31), f"Background cutout: {bg['survey']} | {mode_note}",
              fill=(220, 225, 235, 255))

    # Bottom info strip.
    draw.rectangle([0, height_px - 48, width_px, height_px], fill=(0, 0, 0, 145))
    draw.text((16, height_px - 38),
              f"Footprints: {len(records)} valid, {len(invalid)} invalid | approx union {union_pct:.1f}% | overlap {overlap_pct:.1f}%",
              fill=(255, 255, 255, 255))
    draw.text((16, height_px - 19),
              f"Cutout center RA {bg['center_ra']:.5f} Dec {bg['center_dec']:.5f} | {bg['width_deg']:.3f}° × {bg['height_deg']:.3f}°",
              fill=(220, 225, 235, 255))

    # Legend.
    leg_w = 180
    leg_h = 62 + 22 * max(1, len(group_codes))
    leg_x = width_px - leg_w - 16
    leg_y = 72
    draw.rectangle([leg_x, leg_y, leg_x + leg_w, leg_y + leg_h], fill=(0, 0, 0, 145), outline=(255, 255, 255, 80))
    draw.text((leg_x + 10, leg_y + 8), "Filters", fill=(255, 255, 255, 255))
    draw.text((leg_x + 10, leg_y + 27),
              f"colour = usable data ({overlay_opacity * 100:.0f}%)",
              fill=(220, 225, 235, 255))
    draw.text((leg_x + 10, leg_y + 43), "grey = empty / flat fill", fill=(220, 225, 235, 255))
    for i, code in enumerate(group_codes):
        y = leg_y + 68 + i * 22
        col = color_by_group[code]
        edge = edge_by_group[code]
        draw.rectangle([leg_x + 10, y, leg_x + 25, y + 13], fill=col, outline=edge)
        draw.text((leg_x + 34, y - 2), f"{code} ({len(use_groups[code])})", fill=(255, 255, 255, 255))

    png_path = os.fspath(output_path or os.path.join(cov_dir, f"wcs_sky_coverage_{time.time_ns()}.png"))
    img.convert("RGB").save(png_path, "PNG", optimize=True)
    return {
        "png_path": png_path,
        "union_pct": union_pct,
        "overlap_pct": overlap_pct,
        "background_survey": bg["survey"],
        "background_width_deg": bg["width_deg"],
        "background_height_deg": bg["height_deg"],
        "sampled_holes": sampled_used,
        "tile_labels": bool(show_tile_labels),
        "preview_width": int(width_px),
        "preview_height": int(height_px),
        "preview_wcs_header": wcs_bg.to_header().tostring(
            sep="\n", endcard=True, padding=False),
        "tiles": [
            {
                "path": rec.get("path", ""),
                "filename": rec.get("filename", ""),
                "filter": rec.get("group", rec.get("filter", "UNKNOWN")),
                "valid_fraction": rec.get("valid_fraction"),
                "polygon": rec.get("preview_polygon", []),
            }
            for rec in records
        ],
    }


def create_wcs_coverage_map(groups: dict, out_dir: str | JobPaths, selected_filters=None,
                            title="WCS-only mosaic coverage", sky_background=True,
                            sampled_holes=True, overlay_opacity=0.10,
                            show_tile_labels=True):
    """Create a quick coverage PNG/TXT using only FITS WCS footprints.

    By default this uses lightweight sampled valid-pixel masks, not just WCS
    corner boxes, so chip gaps/drizzle holes can be seen. It samples strided
    pixels via memmap and never loads the full science maps. If sky_background=True,
    a small SkyView finder-chart cutout is fetched and the coverage is drawn over it.
    """
    selected = set(selected_filters or groups.keys())
    use_groups = {k: list(v) for k, v in (groups or {}).items() if k in selected and v}
    if not use_groups:
        raise RuntimeError("No selected filter groups to map.")

    job_paths = out_dir if isinstance(out_dir, JobPaths) else None
    if job_paths is not None:
        out_dir = os.fspath(job_paths.root)
        # Concrete organized paths are allocated through JobPaths below.
        cov_dir = os.fspath(job_paths.root)
    else:
        out_dir = out_dir or str(Path.home() / "JWST_Mosaics")
        cov_dir = os.path.join(out_dir, "wcs_coverage")
    os.makedirs(cov_dir, exist_ok=True)

    records = []
    invalid = []
    for code, paths in sorted(use_groups.items()):
        for p in paths:
            rec = wcs_footprint_from_header_only(p)
            if rec is None:
                invalid.append(p)
                continue
            rec["group"] = code
            records.append(rec)

    if not records:
        raise RuntimeError("No valid celestial WCS footprints found in selected files.")

    plot_records = _normalise_ra_for_coverage(records)
    ras = np.array([pt[0] for rec in plot_records for pt in rec["footprint_plot"]], dtype=float)
    decs = np.array([pt[1] for rec in plot_records for pt in rec["footprint_plot"]], dtype=float)
    ra_min, ra_max = float(np.nanmin(ras)), float(np.nanmax(ras))
    dec_min, dec_max = float(np.nanmin(decs)), float(np.nanmax(decs))
    ra_span = max(ra_max - ra_min, 1e-9)
    dec_span = max(dec_max - dec_min, 1e-9)

    stamp = time.time_ns()
    txt_path = os.fspath(
        job_paths.report(f"wcs_coverage_{stamp}.txt")
        if job_paths is not None
        else os.path.join(cov_dir, f"wcs_coverage_{stamp}.txt"))
    sky_png_path = os.fspath(
        job_paths.coverage_map(f"wcs_sky_coverage_{stamp}.png")
        if job_paths is not None
        else os.path.join(cov_dir, f"wcs_sky_coverage_{stamp}.png"))
    png_path = ""
    union_pct = None
    overlap_pct = None
    background_survey = ""
    background_note = "Plain WCS plot. No sky background was available."

    # First try the requested finder-chart style: small sky cutout plus footprint boxes.
    if HAVE_PIL and sky_background:
        sky = _draw_wcs_coverage_on_sky_background(
            records, use_groups, invalid, title, cov_dir,
            sampled_holes=sampled_holes, overlay_opacity=overlay_opacity,
            show_tile_labels=show_tile_labels, output_path=sky_png_path)
        if sky:
            png_path = sky.get("png_path", "")
            union_pct = sky.get("union_pct")
            overlap_pct = sky.get("overlap_pct")
            background_survey = sky.get("background_survey", "")
            if sky.get("sampled_holes"):
                background_note = (
                    f"Sky background cutout: {background_survey}. "
                    f"Overlay uses sampled valid-pixel masks, so real holes/chip gaps can appear. "
                    f"({sky.get('background_width_deg', 0):.3f} deg x "
                    f"{sky.get('background_height_deg', 0):.3f} deg)."
                )
            else:
                background_note = (
                    f"Sky background cutout: {background_survey}. "
                    f"Overlay uses WCS corner footprints only, so interior holes are not visible. "
                    f"({sky.get('background_width_deg', 0):.3f} deg x "
                    f"{sky.get('background_height_deg', 0):.3f} deg)."
                )

    # Fallback: original plain RA/Dec footprint map.
    if HAVE_PIL and not png_path:
        from PIL import ImageDraw
        W, H = 1200, 850
        margin_l, margin_r, margin_t, margin_b = 80, 210, 60, 80
        plot_w = W - margin_l - margin_r
        plot_h = H - margin_t - margin_b
        img = Image.new("RGBA", (W, H), (255, 255, 255, 255))
        draw = ImageDraw.Draw(img, "RGBA")
        draw.rectangle([margin_l, margin_t, margin_l + plot_w, margin_t + plot_h],
                       outline=(80, 80, 80, 255), fill=(248, 249, 252, 255))

        def xy(ra, dec):
            x = margin_l + (float(ra) - ra_min) / ra_span * plot_w
            y = margin_t + (dec_max - float(dec)) / dec_span * plot_h
            return (int(round(x)), int(round(y)))

        palette = [
            (37, 99, 235, 70), (22, 163, 74, 70), (220, 38, 38, 70),
            (217, 119, 6, 70), (126, 34, 206, 70), (8, 145, 178, 70),
            (219, 39, 119, 70), (71, 85, 105, 70), (101, 163, 13, 70),
        ]
        edge_palette = [(r, g, b, 220) for r, g, b, _a in palette]
        group_codes = sorted(use_groups.keys())
        color_by_group = {c: palette[i % len(palette)] for i, c in enumerate(group_codes)}
        edge_by_group = {c: edge_palette[i % len(edge_palette)] for i, c in enumerate(group_codes)}

        count = np.zeros((H, W), dtype=np.uint16)
        for rec in plot_records:
            pts = [xy(ra, dec) for ra, dec in rec["footprint_plot"]]
            code = rec.get("group", rec.get("filter", "UNKNOWN"))
            fill = color_by_group.get(code, (37, 99, 235, 60))
            edge = edge_by_group.get(code, (37, 99, 235, 220))
            draw.polygon(pts, fill=fill, outline=edge)
            mask = Image.new("L", (W, H), 0)
            ImageDraw.Draw(mask).polygon(pts, fill=1)
            count += np.asarray(mask, dtype=np.uint16)

        plot_mask = np.zeros((H, W), dtype=bool)
        plot_mask[margin_t:margin_t + plot_h + 1, margin_l:margin_l + plot_w + 1] = True
        denom = max(int(plot_mask.sum()), 1)
        union_pct = float(((count > 0) & plot_mask).sum() / denom * 100.0)
        overlap_pct = float(((count > 1) & plot_mask).sum() / denom * 100.0)

        for rec in plot_records:
            pts = [xy(ra, dec) for ra, dec in rec["footprint_plot"]]
            code = rec.get("group", rec.get("filter", "UNKNOWN"))
            draw.line(pts + [pts[0]], fill=edge_by_group.get(code, (37, 99, 235, 230)), width=2)

        draw.text((20, 18), title, fill=(20, 24, 33, 255))
        draw.text((20, 38), "Header/WCS footprint preview only. No pixel reprojection.",
                  fill=(90, 96, 112, 255))
        draw.text((margin_l, H - 58), f"RA span: {ra_span:.5f} deg   Dec span: {dec_span:.5f} deg",
                  fill=(20, 24, 33, 255))
        draw.text((margin_l, H - 36), f"Valid footprints: {len(records)}   Invalid/no WCS: {len(invalid)}   Approx union: {union_pct:.1f}%   overlap: {overlap_pct:.1f}%",
                  fill=(20, 24, 33, 255))

        leg_x = W - margin_r + 20
        leg_y = margin_t
        draw.text((leg_x, leg_y - 24), "Filters", fill=(20, 24, 33, 255))
        for i, code in enumerate(group_codes):
            y = leg_y + i * 24
            col = color_by_group[code]
            draw.rectangle([leg_x, y, leg_x + 16, y + 14], fill=col, outline=edge_by_group[code])
            draw.text((leg_x + 24, y - 1), f"{code} ({len(use_groups[code])})", fill=(20, 24, 33, 255))

        png_path = os.fspath(
            job_paths.coverage_map(f"wcs_coverage_{stamp}.png")
            if job_paths is not None
            else os.path.join(cov_dir, f"wcs_coverage_{stamp}.png"))
        img.convert("RGB").save(png_path, "PNG", optimize=True)

    lines = []
    lines.append(title)
    lines.append("Coverage preview. In real-hole mode it samples valid pixels/weight masks via memmap; it does not load full science maps or run full mosaic reprojection.")
    lines.append(background_note)
    lines.append("")
    lines.append(f"Selected filter groups: {', '.join(sorted(use_groups.keys()))}")
    lines.append(f"Valid WCS footprints: {len(records)}")
    lines.append(f"Invalid/no-WCS files: {len(invalid)}")
    lines.append(f"RA span: {ra_span:.8f} deg")
    lines.append(f"Dec span: {dec_span:.8f} deg")
    if union_pct is not None:
        lines.append(f"Approx projected union inside preview image: {union_pct:.2f}%")
        lines.append(f"Approx projected overlap inside preview image: {overlap_pct:.2f}%")
    lines.append("")
    for code in sorted(use_groups.keys()):
        recs = [r for r in records if r.get("group") == code]
        lines.append(f"{code}: {len(recs)}/{len(use_groups[code])} valid footprint(s)")
        for rec in recs:
            frac = rec.get("valid_fraction")
            if frac is not None:
                lines.append(
                    f"  - {rec.get('filename', os.path.basename(str(rec.get('path', ''))))}: "
                    f"{float(frac):.1f}% usable sampled data")
    if invalid:
        lines.append("")
        lines.append("Invalid/no-WCS files:")
        for p in invalid[:100]:
            lines.append(f"- {p}")
        if len(invalid) > 100:
            lines.append(f"... {len(invalid) - 100} more")

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    result = {
        "png_path": png_path,
        "txt_path": txt_path,
        "summary": "\n".join(lines),
        "valid": len(records),
        "invalid": len(invalid),
        "filters": sorted(use_groups.keys()),
        "union_pct": union_pct,
        "overlap_pct": overlap_pct,
        "background_survey": background_survey,
        "sampled_holes": bool(sky.get("sampled_holes")) if "sky" in locals() and sky else False,
    }
    if "sky" in locals() and sky:
        for key in ("preview_width", "preview_height", "preview_wcs_header", "tiles",
                    "tile_labels"):
            if key in sky:
                result[key] = sky[key]
    return result



__all__ = [name for name in globals() if not name.startswith('__')]
