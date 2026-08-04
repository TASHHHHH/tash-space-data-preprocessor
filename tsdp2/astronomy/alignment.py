from __future__ import annotations

from tsdp2._deps import *
from tsdp2.logging_config import LOGGER
from tsdp2.astronomy.coverage import _load_mosaic_2d_and_header
from tsdp2.astronomy.fits_io import bbox_from_mask, crop_with_wcs, load_2d_and_header
from tsdp2.astronomy.normalization import _build_common_overlap_mask, _finite_fraction_blockwise, _has_reference_overlap, _sampled_normalization_scale, _scale_finite_inplace, _write_uint8_mask_fits, normalize_to_reference, psf_match_to_worst
from tsdp2.astronomy.reference import write_log
from tsdp2.output_paths import JobPaths, safe_filename

def _align_reproject_to_memmaps(name, data, hdr, ref_hdr, shape_out, tmp_dir, progress_cb=None, cancel_cb=None):
    """Blockwise reproject for the standalone/pipeline aligner.

    The aligner used to call reproject_interp() on the full reference grid at
    once. Large HST skycell frames can make wcslib allocate a huge coordinate
    array and fail with wcsp2s() Memory allocation failed. This writes the
    reprojected image and footprint to disk-backed float32 memmaps and asks
    reproject to process the WCS transform in small blocks.
    """
    os.makedirs(tmp_dir, exist_ok=True)
    shape = tuple(int(x) for x in shape_out)
    h, w = shape
    safe = safe_filename(str(name))
    arr_path = os.path.join(tmp_dir, f"align_{safe}_data.f32")
    fp_path = os.path.join(tmp_dir, f"align_{safe}_footprint.f32")
    arr_mm = np.memmap(arr_path, dtype="float32", mode="w+", shape=shape)
    fp_mm = np.memmap(fp_path, dtype="float32", mode="w+", shape=shape)

    block_attempts = [(512, 512), (256, 256), (128, 128)]
    last_exc = None
    used_block = None
    try:
        for block_size in block_attempts:
            if cancel_cb and cancel_cb():
                raise RuntimeError("Cancelled by user.")
            try:
                if progress_cb:
                    progress_cb(0, 1, f"Reprojecting {name} blockwise ({block_size[0]}×{block_size[1]})")
                reproject_interp(
                    (data, hdr), ref_hdr, shape_out=shape,
                    output_array=arr_mm,
                    output_footprint=fp_mm,
                    block_size=block_size,
                    parallel=False,
                    roundtrip_coords=False,
                )
                used_block = block_size
                break
            except TypeError as exc:
                # Installed reproject is too old for block_size or roundtrip_coords.
                last_exc = exc
                break
            except MemoryError as exc:
                last_exc = exc
                continue
            except EXPECTED_OPERATION_ERRORS as exc:
                if "memory" in str(exc).lower() or "wcsp2s" in str(exc).lower():
                    last_exc = exc
                    continue
                raise

        if used_block is None:
            # Fallback for old reproject. It can still fail on huge grids, but the
            # error message now tells the user the real fix.
            try:
                if progress_cb:
                    progress_cb(0, 1, f"Reprojecting {name} without blockwise fallback")
                arr, fp = reproject_interp((data, hdr), ref_hdr, shape_out=shape)
                arr_mm[:] = np.asarray(arr, dtype=np.float32)
                fp_mm[:] = np.asarray(fp, dtype=np.float32)
                del arr, fp
            except EXPECTED_OPERATION_ERRORS as exc:
                raise RuntimeError(
                    f"Aligner blockwise reprojection failed for {name}. "
                    f"Update reproject/astropy, or reduce alignment size. "
                    f"Try: pip install -U reproject astropy. Original error: {last_exc or exc}"
                ) from exc

        # Keep invalid/no-footprint pixels as NaN so grey corners cannot become
        # part of the common overlap or final aligned FITS.
        chunk = 512
        for y0 in range(0, h, chunk):
            y1 = min(h, y0 + chunk)
            block = arr_mm[y0:y1]
            fp_block = fp_mm[y0:y1]
            block[fp_block <= 0.5] = np.nan
            arr_mm[y0:y1] = block
        arr_mm.flush()
        fp_mm.flush()
    finally:
        try:
            del arr_mm
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
        try:
            del fp_mm
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
    return arr_path, fp_path


def _release_alignment_memmaps(reproj_data, footprints, temp_files, temp_dir,
                               extra_mappings=()):
    """Close alignment memmaps and remove their scratch files on Windows."""
    for mapping in extra_mappings:
        try:
            mapping.clear()
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
    for arr in list(reproj_data.values()) + list(footprints.values()):
        try:
            mmap_obj = getattr(arr, "_mmap", None)
            if mmap_obj is not None:
                mmap_obj.close()
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
    try:
        reproj_data.clear()
        footprints.clear()
    except EXPECTED_OPERATION_ERRORS:
        LOGGER.debug("Best-effort operation failed", exc_info=True)
    gc.collect()
    for path in list(temp_files or []):
        try:
            os.remove(path)
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
    try:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
    except EXPECTED_OPERATION_ERRORS:
        LOGGER.debug("Best-effort operation failed", exc_info=True)


def _wcs_same_pixel_grid(ref_hdr, mov_hdr, ref_shape, mov_shape, tol_pix=0.05):
    """Return (same, max_error_px, reason) for whether two FITS images already share WCS/grid.

    This is stricter than just matching filter names. It samples the moving-image
    pixel grid, converts moving pixel -> sky -> reference pixel, and checks that
    the returned reference pixel coordinates land on the same pixel coordinates.
    If True, alignment/reprojection can be skipped safely.
    """
    try:
        if ref_shape is None or mov_shape is None:
            return False, None, "missing image shape"
        rh, rw = int(ref_shape[-2]), int(ref_shape[-1])
        mh, mw = int(mov_shape[-2]), int(mov_shape[-1])
        if rh != mh or rw != mw:
            return False, None, f"different image size: ref {rw}x{rh}, moving {mw}x{mh}"
        if rw <= 1 or rh <= 1:
            return False, None, "invalid image size"

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ref_w = WCS(ref_hdr, naxis=2)
            mov_w = WCS(mov_hdr, naxis=2)
        if not getattr(ref_w, "has_celestial", False) or not getattr(mov_w, "has_celestial", False):
            return False, None, "missing celestial WCS"

        # Corners, midpoints, center, and quartile points. This catches most
        # CRPIX/rotation/scale/skew differences without touching the full image.
        xs = np.array([0, rw - 1, 0, rw - 1, (rw - 1) / 2,
                       (rw - 1) / 2, 0, rw - 1,
                       (rw - 1) * 0.25, (rw - 1) * 0.75], dtype=float)
        ys = np.array([0, 0, rh - 1, rh - 1, (rh - 1) / 2,
                       0, (rh - 1) / 2, (rh - 1) / 2,
                       (rh - 1) * 0.25, (rh - 1) * 0.75], dtype=float)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ra, dec = mov_w.all_pix2world(xs, ys, 0)
            xr, yr = ref_w.all_world2pix(ra, dec, 0)
        vals = np.concatenate([np.ravel(xr), np.ravel(yr), np.ravel(ra), np.ravel(dec)])
        if not np.all(np.isfinite(vals)):
            return False, None, "WCS transform produced non-finite coordinates"
        err = np.sqrt((xr - xs) ** 2 + (yr - ys) ** 2)
        max_err = float(np.nanmax(err))
        if max_err <= float(tol_pix):
            return True, max_err, f"same WCS/grid within {max_err:.4f} px"
        return False, max_err, f"WCS/grid differs by up to {max_err:.3f} px"
    except EXPECTED_OPERATION_ERRORS as exc:
        return False, None, f"WCS check failed: {exc}"


def wcs_alignment_check_paths(reference_path, moving_items, tol_pix=0.05):
    """Check selected aligner inputs and report which files can skip reprojection."""
    try:
        try:
            ref_data, ref_hdr = _load_mosaic_2d_and_header(reference_path)
        except EXPECTED_OPERATION_ERRORS:
            ref_data, ref_hdr = load_2d_and_header(reference_path, preserve_nan=True)
        ref_shape = ref_data.shape
        try:
            del ref_data
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
    except EXPECTED_OPERATION_ERRORS as exc:
        raise RuntimeError(f"Could not read reference WCS: {exc}")

    rows = []
    for it in moving_items:
        p = it.get("path", "")
        nm = it.get("name", os.path.basename(p) or "moving")
        try:
            try:
                mov_data, mov_hdr = _load_mosaic_2d_and_header(p)
            except EXPECTED_OPERATION_ERRORS:
                mov_data, mov_hdr = load_2d_and_header(p, preserve_nan=True)
            same, max_err, reason = _wcs_same_pixel_grid(ref_hdr, mov_hdr, ref_shape, mov_data.shape, tol_pix=tol_pix)
            try:
                del mov_data
            except EXPECTED_OPERATION_ERRORS:
                LOGGER.debug("Best-effort operation failed", exc_info=True)
            rows.append({"name": nm, "path": p, "same": bool(same), "max_err": max_err, "reason": reason})
        except EXPECTED_OPERATION_ERRORS as exc:
            rows.append({"name": nm, "path": p, "same": False, "max_err": None, "reason": f"read/check failed: {exc}"})
    return rows


def align_crop_many(reference_path: str, reference_name: str,
                    items: list, out_base_dir: str | JobPaths,
                    normalize_enabled: bool, normalize_mode: str,
                    psf_match_enabled: bool,
                    keep_reference_canvas: bool = False,
                    progress_cb=None, cancel_cb=None,
                    generate_diagnostics: bool = True):
    """Align inputs on the reference WCS using blockwise disk-backed arrays.

    Peak memory is proportional to one image plane plus processing blocks; any
    reprojected full-frame arrays remain float32 memmaps on disk. Passing a
    :class:`JobPaths` instance routes science, diagnostic, temporary, and log
    products into their dedicated categories. String paths retain the legacy
    flat layout for backward compatibility.
    """
    output_kind = "aligned_full_canvas" if keep_reference_canvas else "aligned_cropped"
    job_paths = out_base_dir if isinstance(out_base_dir, JobPaths) else None
    if job_paths is not None:
        science_root = job_paths.normalized if normalize_enabled else job_paths.aligned
        out_dir = os.fspath(science_root)
    else:
        out_dir = os.path.join(os.fspath(out_base_dir), output_kind)
        os.makedirs(out_dir, exist_ok=True)

    log_lines = []
    log_lines.append(f"Run time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    log_lines.append(f"Output folder: {out_dir}")
    log_lines.append(f"Reference path: {reference_path}")
    log_lines.append(f"Reference name: {reference_name}")
    log_lines.append(f"Normalization: {normalize_enabled} mode={normalize_mode}")
    log_lines.append(f"PSF match: {psf_match_enabled}")
    log_lines.append(
        "Alignment canvas: full reference with partial overlays"
        if keep_reference_canvas else
        "Alignment canvas: common-overlap crop")
    log_lines.append("")

    # Use the same coverage-mask-aware loader as mosaic so grey no-data
    # corners do not become valid alignment pixels.
    try:
        ref_data, ref_hdr = _load_mosaic_2d_and_header(reference_path)
    except EXPECTED_OPERATION_ERRORS:
        ref_data, ref_hdr = load_2d_and_header(reference_path, preserve_nan=True)

    total_steps = max(1, len(items) + 5)
    step = 0

    def tick(msg):
        nonlocal step
        step += 1
        if progress_cb:
            progress_cb(step, total_steps, msg)
        if cancel_cb and cancel_cb():
            raise RuntimeError("Cancelled by user.")

    tick("Loaded reference")

    reproj_data = {}
    footprints = {}
    if job_paths is not None:
        align_tmp_dir = os.fspath(
            job_paths.intermediate / "memmaps" /
            f"alignment_{int(time.time())}")
    else:
        align_tmp_dir = os.path.join(
            out_dir, f"_align_tmp_{int(time.time())}")
    _align_tmp_files = []

    for it in items:
        try:
            mov_data, mov_hdr = _load_mosaic_2d_and_header(it["path"])
        except EXPECTED_OPERATION_ERRORS:
            mov_data, mov_hdr = load_2d_and_header(it["path"], preserve_nan=True)

        same_wcs, wcs_err, wcs_reason = _wcs_same_pixel_grid(
            ref_hdr, mov_hdr, ref_data.shape, mov_data.shape, tol_pix=0.05
        )
        if same_wcs:
            tick(f"Skipping reprojection for {it['name']} - WCS/grid already matches reference ({wcs_err:.4f} px)")
            log_lines.append(f"WCS skip {it['name']}: {wcs_reason}")
            reproj_data[it["name"]] = mov_data.astype(np.float32, copy=False)
            footprints[it["name"]] = np.isfinite(reproj_data[it["name"]]).astype(np.float32)
            continue

        tick(f"Reprojecting {it['name']} ({wcs_reason})")
        log_lines.append(f"WCS align {it['name']}: {wcs_reason}")
        arr_path, fp_path = _align_reproject_to_memmaps(
            it["name"], mov_data, mov_hdr, ref_hdr, ref_data.shape,
            align_tmp_dir, progress_cb=progress_cb, cancel_cb=cancel_cb
        )
        reproj_data[it["name"]] = np.memmap(arr_path, dtype="float32", mode="r+", shape=ref_data.shape)
        footprints[it["name"]] = np.memmap(fp_path, dtype="float32", mode="r+", shape=ref_data.shape)
        _align_tmp_files.extend([arr_path, fp_path])
        try:
            del mov_data
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)

    tick("Computing coverage masks")
    if keep_reference_canvas:
        # Every partial layer must touch the Euclid/reference canvas, but the
        # layers do not have to overlap one another.  This permits several
        # independent Hubble pointings on one wide Euclid field.
        for nm, arr in reproj_data.items():
            if not _has_reference_overlap(ref_data, arr, footprints[nm]):
                _release_alignment_memmaps(
                    reproj_data, footprints, _align_tmp_files, align_tmp_dir)
                raise RuntimeError(
                    f"No WCS overlap found between reference and {nm}. "
                    "Check that both FITS files contain valid celestial WCS.")
        mask = _build_common_overlap_mask(ref_data, reproj_data, footprints)
        bbox = (0, int(ref_data.shape[0]), 0, int(ref_data.shape[1]))
        log_lines.append(
            "Full reference canvas retained; moving layers remain NaN outside "
            "their individual WCS footprints.")
    else:
        mask = _build_common_overlap_mask(ref_data, reproj_data, footprints)
        bbox = bbox_from_mask(mask)
        if bbox is None:
            _release_alignment_memmaps(
                reproj_data, footprints, _align_tmp_files, align_tmp_dir)
            raise RuntimeError("No common overlap found. Frames may not overlap.")

    y0, y1, x0, x1 = bbox
    log_lines.append(f"Crop bbox: y0={y0} y1={y1} x0={x0} x1={x1}")
    log_lines.append("")

    ref_crop, new_hdr = crop_with_wcs(ref_data, ref_hdr, bbox)
    try:
        new_hdr["ALNMODE"] = (
            "FULLREF" if keep_reference_canvas else "COMMON",
            "Alignment canvas mode")
        new_hdr["REFCANV"] = (
            bool(keep_reference_canvas),
            "Reference canvas retained at full size")
    except EXPECTED_OPERATION_ERRORS:
        LOGGER.debug("Best-effort operation failed", exc_info=True)

    outputs = {reference_name: ref_crop}
    fp_out = {}

    for nm, arr in reproj_data.items():
        outputs[nm] = arr[y0:y1, x0:x1]
        fp_out[nm] = footprints[nm][y0:y1, x0:x1]

    common_mask_crop = mask[y0:y1, x0:x1]
    if generate_diagnostics:
        tick("Saving masks and footprints")
        common_mask_path = os.fspath(
            job_paths.mask("common_overlap_mask.fits")
            if job_paths is not None
            else os.path.join(out_dir, "common_overlap_mask.fits"))
        _write_uint8_mask_fits(common_mask_path, common_mask_crop, new_hdr)
        log_lines.append(f"Saved: {common_mask_path}")

        ref_fp_name = f"footprint_{safe_filename(reference_name)}.fits"
        ref_fp_path = os.fspath(
            job_paths.footprint(ref_fp_name)
            if job_paths is not None
            else os.path.join(out_dir, ref_fp_name))
        _write_uint8_mask_fits(ref_fp_path, ref_crop, new_hdr, finite=True)
        log_lines.append(f"Saved: {ref_fp_path}")

        for nm, fp in fp_out.items():
            fp_name = f"footprint_{safe_filename(nm)}.fits"
            p = os.fspath(
                job_paths.footprint(fp_name)
                if job_paths is not None
                else os.path.join(out_dir, fp_name))
            _write_uint8_mask_fits(
                p, fp, new_hdr, positive=True, finite_source=outputs[nm])
            log_lines.append(f"Saved: {p}")
    else:
        tick("Diagnostics disabled")

    if normalize_enabled:
        tick("Normalizing to reference")
        ref_arr = outputs[reference_name]
        for nm in list(outputs.keys()):
            if nm == reference_name:
                continue
            if keep_reference_canvas:
                # Each Hubble/partial layer is normalized only where that layer
                # overlaps Euclid, rather than requiring all overlays to share
                # the same patch of sky.
                s = _sampled_normalization_scale(
                    outputs[nm], ref_arr, fp_out[nm], mode=normalize_mode)
                _scale_finite_inplace(outputs[nm], s)
            else:
                nm_mask = common_mask_crop.astype(bool, copy=False)
                out_arr, s = normalize_to_reference(
                    outputs[nm], ref_arr, nm_mask, mode=normalize_mode)
                outputs[nm] = out_arr.astype(np.float32)
            log_lines.append(f"Normalization scale {nm}: {float(s):.6g}")
        log_lines.append("")

    if psf_match_enabled:
        if keep_reference_canvas:
            log_lines.append(
                "PSF match skipped in full-reference mode: blurring the full "
                "Euclid reference would damage the background canvas and the "
                "NaN boundaries of partial overlays.")
        else:
            tick("PSF matching to worst")
            names_order = list(outputs.keys())
            matched, notes = psf_match_to_worst(outputs, names_order)
            outputs = {k: matched[k].astype(np.float32) for k in names_order}
            for n in notes:
                log_lines.append(n)
            log_lines.append("")

    tick("Writing FITS outputs")
    saved = []
    for nm, arr in outputs.items():
        filename = f"{safe_filename(nm)}.fits"
        out_path = os.fspath(
            (job_paths.final_normalized_fits(nm, filename)
             if normalize_enabled else job_paths.final_aligned_fits(nm, filename))
            if job_paths is not None
            else os.path.join(out_dir, filename))
        fits.PrimaryHDU(arr, header=new_hdr).writeto(out_path, overwrite=True)
        cov = float(_finite_fraction_blockwise(arr) * 100.0)
        saved.append((nm, out_path, cov))
        log_lines.append(f"Saved: {out_path}  coverage={cov:.2f}%")

    log_target = (
        job_paths.processing_log("alignment_run.log")
        if job_paths is not None else None)
    log_path = write_log(out_dir, log_lines, log_target)

    # Close/delete temporary alignment memmaps after all FITS have been written.
    _release_alignment_memmaps(
        reproj_data, footprints, _align_tmp_files, align_tmp_dir,
        extra_mappings=(outputs, fp_out))
    return out_dir, saved, log_path

__all__ = [name for name in globals() if not name.startswith('__')]
