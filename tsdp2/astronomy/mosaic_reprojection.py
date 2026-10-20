from __future__ import annotations

from tsdp2._deps import *
from tsdp2.logging_config import LOGGER
from tsdp2.output_paths import JobPaths, safe_filename
from tsdp2.recovery.manifests import _stream_float32_to_fits


def _mosaic_roundtrip_required(header, output_wcs, input_shape, output_shape):
    """Retain inverse checks except for nearby, undistorted small TAN fields.

    Both transforms are unambiguous within these small tangent-plane domains.
    Distorted, wide-field and other projections retain reproject's default checks.
    """
    try:
        input_wcs = WCS(header, naxis=2)
        worlds = []
        for wcs, shape in ((input_wcs, input_shape), (output_wcs, output_shape)):
            if (wcs.has_distortion or wcs.wcs.get_pv() or
                    tuple(wcs.wcs.ctype) != ("RA---TAN", "DEC--TAN")):
                return True
            corners = np.asarray(wcs.calc_footprint(axes=(int(shape[1]), int(shape[0]))))
            if not np.isfinite(corners).all():
                return True
            worlds.extend(corners)
        # Every corner must be within five degrees of the input tangent point.
        # This also handles fields crossing RA=0 without a spurious wide span.
        coords = np.deg2rad(np.asarray(worlds))
        ra, dec = np.deg2rad(input_wcs.wcs.crval)
        cosine = (np.sin(dec) * np.sin(coords[:, 1]) +
                  np.cos(dec) * np.cos(coords[:, 1]) * np.cos(coords[:, 0] - ra))
        return not bool(np.all(cosine > np.cos(np.deg2rad(5.0))))
    except EXPECTED_OPERATION_ERRORS:
        return True


def _reproject_tile_to_memmap(idx, data, hdr, wcs_out, shape_out, tmp_dir, filter_code, progress_cb=None, total_tiles=1):
    """Reproject one tile into a float32 disk-backed memmap and mask invalid
    footprint pixels to NaN.

    Important: passing output_array/output_footprint alone is NOT enough for
    huge HST mosaics. reproject/wcslib can still build the full output pixel
    coordinate grid internally and die with wcsp2s() Memory allocation failed.
    For large canvases this uses reproject's block_size option so WCS math is
    done in small chunks instead of one 16000x20000 monster allocation.
    """
    os.makedirs(tmp_dir, exist_ok=True)
    shape = tuple(int(x) for x in shape_out)
    h, w = shape
    arr_path = os.path.join(tmp_dir, f"{safe_filename(filter_code)}_tile_{idx:03d}.f32")
    fp_path = os.path.join(tmp_dir, f"{safe_filename(filter_code)}_tile_{idx:03d}_fp.f32")
    arr_mm = np.memmap(arr_path, dtype="float32", mode="w+", shape=shape)
    fp_mm = np.memmap(fp_path, dtype="float32", mode="w+", shape=shape)

    if progress_cb:
        progress_cb(idx, max(1, total_tiles * 3 + 2),
                    f"Reprojecting {filter_code} tile {idx + 1}/{total_tiles} in blockwise memory-safe mode…")

    # Block size is deliberately conservative. 512x512 keeps wcslib coordinate
    # arrays tiny while still avoiding death-by-overhead. If that still fails,
    # retry at 256x256 before falling back.
    block_attempts = [(512, 512), (256, 256), (128, 128)]
    try:
        last_exc = None
        used_block = None
        for block_size in block_attempts:
            try:
                if progress_cb:
                    progress_cb(idx, max(1, total_tiles * 3 + 2),
                                f"Reprojecting {filter_code} tile {idx + 1}/{total_tiles} block {block_size[0]}×{block_size[1]}…")
                # roundtrip_coords=False avoids another heavy WCS roundtrip grid.
                # For preview/art mosaics this is a sane tradeoff and prevents
                # wcslib from allocating a full-frame coordinate transform.
                reproject_interp(
                    (data, hdr), wcs_out, shape_out=shape,
                    output_array=arr_mm,
                    output_footprint=fp_mm,
                    block_size=block_size,
                    parallel=False,
                    roundtrip_coords=False,
                )
                used_block = block_size
                break
            except TypeError as exc:
                # Old reproject without block_size/parallel/roundtrip kwargs.
                last_exc = exc
                break
            except MemoryError as exc:
                last_exc = exc
                continue
            except EXPECTED_OPERATION_ERRORS as exc:
                # wcslib reports memory failures as plain Exception in some builds.
                if "memory" in str(exc).lower() or "wcsp2s" in str(exc).lower():
                    last_exc = exc
                    continue
                raise

        if used_block is None:
            # Older reproject fallback. This may still need more RAM, but it is
            # the only option if the installed version lacks block_size support.
            try:
                arr, fp = reproject_interp((data, hdr), wcs_out, shape_out=shape)
                arr_mm[:] = np.asarray(arr, dtype=np.float32)
                fp_mm[:] = np.asarray(fp, dtype=np.float32)
                del arr, fp
            except EXPECTED_OPERATION_ERRORS as exc:
                raise RuntimeError(
                    f"Blockwise reprojection failed for {filter_code} tile {idx + 1}/{total_tiles}. "
                    f"Your installed reproject may be too old for block_size, or the output grid is still too large. "
                    f"Try: pip install -U reproject astropy. Original error: {last_exc or exc}"
                ) from exc

        # Mask invalid pixels in row chunks so we do not create another full-size
        # temporary with np.where(fp > 0.5, arr, np.nan).
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
            del fp_mm
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
        try:
            os.remove(fp_path)
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
        try:
            del arr_mm
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
    return arr_path


def _estimate_overlap_plane_corrections(tile_paths, shape_out, filter_code="",
                                        dense=False, nearby_edges=False):
    """Shared robust overlap model for raw memmaps and draft/seam repair.

    Legacy density/nearby-edge keywords remain accepted. Pixels at different
    sky positions do not constrain a relative plane and are never compared.
    """
    from tsdp2.astronomy.background_matching import estimate_background_planes
    return estimate_background_planes(tile_paths, shape_out, filter_code)


def _corrected_memmap_block(mm, y0, y1, shape, correction):
    block = np.asarray(mm[y0:y1], dtype=np.float32)
    a, bx, by = correction if correction is not None else (0.0, 0.0, 0.0)
    if a == 0.0 and bx == 0.0 and by == 0.0:
        return block
    h, w = int(shape[0]), int(shape[1])
    xn = (2.0 * np.arange(w, dtype=np.float32) / max(w - 1, 1) - 1.0)[None, :]
    yn = (2.0 * np.arange(y0, y1, dtype=np.float32) / max(h - 1, 1) - 1.0)[:, None]
    return block + np.float32(a) + np.float32(bx) * xn + np.float32(by) * yn


def _nanmedian_memmap(tile_paths, shape_out, progress_cb=None, filter_code="", corrections=None):
    """Compute nanmedian(tile memmaps) by row chunks, not by stacking full tiles.
    Returns a float32 memmap containing the mosaic.
    """
    shape = tuple(int(x) for x in shape_out)
    h, w = shape
    n = len(tile_paths)
    out_path = os.path.join(os.path.dirname(tile_paths[0]), f"{safe_filename(filter_code)}_median_out.f32")
    out_mm = np.memmap(out_path, dtype="float32", mode="w+", shape=shape)
    mems = [np.memmap(p, dtype="float32", mode="r", shape=shape) for p in tile_paths]
    corrections = corrections or [(0.0, 0.0, 0.0)] * n

    # Keep chunk memory bounded. 64 rows for very wide data, more for smaller.
    target_bytes = 256 * 1024 * 1024
    rows = max(16, min(512, int(target_bytes // max(1, n * w * 4))))
    total_chunks = max(1, (h + rows - 1) // rows)

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", "All-NaN slice", RuntimeWarning)
        for c, y0 in enumerate(range(0, h, rows), start=1):
            y1 = min(h, y0 + rows)
            if progress_cb:
                progress_cb(n * 2 + c, n * 2 + total_chunks + 2,
                            f"Combining {filter_code} chunk {c}/{total_chunks}…")
            stack = np.empty((n, y1 - y0, w), dtype=np.float32)
            for i, mm in enumerate(mems):
                stack[i] = _corrected_memmap_block(mm, y0, y1, shape, corrections[i])
            out_mm[y0:y1] = np.nanmedian(stack, axis=0).astype(np.float32, copy=False)
            del stack
    out_mm.flush()
    return out_mm


def _nanmean_memmap(tile_paths, shape_out, progress_cb=None, filter_code="", corrections=None):
    """Streaming nanmean for disk-backed reprojected tiles."""
    shape = tuple(int(x) for x in shape_out)
    h, w = shape
    n = len(tile_paths)
    out_path = os.path.join(os.path.dirname(tile_paths[0]), f"{safe_filename(filter_code)}_mean_out.f32")
    out_mm = np.memmap(out_path, dtype="float32", mode="w+", shape=shape)
    mems = [np.memmap(p, dtype="float32", mode="r", shape=shape) for p in tile_paths]
    corrections = corrections or [(0.0, 0.0, 0.0)] * n
    rows = max(16, min(512, int((256 * 1024 * 1024) // max(1, n * w * 4))))
    total_chunks = max(1, (h + rows - 1) // rows)
    for c, y0 in enumerate(range(0, h, rows), start=1):
        y1 = min(h, y0 + rows)
        if progress_cb:
            progress_cb(n * 2 + c, n * 2 + total_chunks + 2,
                        f"Averaging {filter_code} chunk {c}/{total_chunks}…")
        acc = np.zeros((y1 - y0, w), dtype=np.float32)
        cnt = np.zeros((y1 - y0, w), dtype=np.uint16)
        for i, mm in enumerate(mems):
            block = _corrected_memmap_block(mm, y0, y1, shape, corrections[i])
            good = np.isfinite(block)
            acc[good] += block[good]
            cnt[good] += 1
        with np.errstate(invalid="ignore", divide="ignore"):
            out = np.where(cnt > 0, acc / np.maximum(cnt, 1), np.nan)
        out_mm[y0:y1] = out.astype(np.float32, copy=False)
        del acc, cnt, out
    out_mm.flush()
    return out_mm


def _write_mosaic_quality_maps(contributors, shape_out, wcs_out, out_dir,
                               filter_code, work_dir, progress_cb=None,
                               coverage_array=None,
                               job_paths: Optional[JobPaths] = None):
    """Write separate coverage-count and relative-weight WCS FITS products."""
    shape = tuple(int(x) for x in shape_out)
    h, w = shape
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    raw_path = os.path.join(work_dir, f"{safe_filename(filter_code)}_coverage_work.f32")
    cov_mm = np.memmap(raw_path, dtype="float32", mode="w+", shape=shape)
    cov_mm[:] = 0.0
    rows = max(16, min(512, int((192 * 1024 * 1024) // max(4, w * 4))))
    sources = list(contributors or [])
    opened = []
    try:
        if coverage_array is not None:
            source_array = np.asarray(coverage_array)
            for y0 in range(0, h, rows):
                y1 = min(h, y0 + rows)
                block = np.asarray(source_array[y0:y1], dtype=np.float32)
                cov_mm[y0:y1] = np.where(np.isfinite(block) & (block > 0), block, 0.0)
        else:
            for source in sources:
                if isinstance(source, (str, os.PathLike)):
                    opened.append(np.memmap(os.fspath(source), dtype="float32",
                                            mode="r", shape=shape))
                else:
                    opened.append(source)
            for y0 in range(0, h, rows):
                y1 = min(h, y0 + rows)
                block_count = np.zeros((y1 - y0, w), dtype=np.float32)
                for source in opened:
                    block_count += np.isfinite(np.asarray(source[y0:y1])).astype(np.float32)
                cov_mm[y0:y1] = block_count
                if progress_cb:
                    progress_cb(y1, h, f"Building {filter_code} coverage map rows {y1}/{h}")
        cov_mm.flush()
        max_coverage = float(np.nanmax(cov_mm)) if cov_mm.size else 0.0

        base_header = wcs_out.to_header()
        base_header["FILTER"] = filter_code
        base_header["COVMAX"] = (max_coverage, "Maximum contributing exposure count")

        coverage_name = f"coverage_{safe_filename(filter_code)}.fits"
        coverage_path = os.fspath(
            job_paths.coverage_map(coverage_name)
            if job_paths is not None
            else os.path.join(out_dir, coverage_name))
        coverage_header = base_header.copy()
        coverage_header["BUNIT"] = "count"
        coverage_header["MAPTYPE"] = "COVERAGE"
        coverage_header.add_history("Per-pixel contributing reprojected tile count/footprint sum")
        _stream_float32_to_fits(raw_path, shape, coverage_header, coverage_path)

        # Reuse the same scratch array to avoid another full-size temporary.
        denom = max(max_coverage, 1.0)
        for y0 in range(0, h, rows):
            y1 = min(h, y0 + rows)
            cov_mm[y0:y1] = np.asarray(cov_mm[y0:y1], dtype=np.float32) / denom
        cov_mm.flush()
        weight_name = f"weight_{safe_filename(filter_code)}.fits"
        weight_path = os.fspath(
            job_paths.weight_map(weight_name)
            if job_paths is not None
            else os.path.join(out_dir, weight_name))
        weight_header = base_header.copy()
        weight_header["BUNIT"] = "relative"
        weight_header["MAPTYPE"] = "WEIGHT"
        weight_header["WHTTYPE"] = "RELATIVE_COVERAGE"
        weight_header.add_history("Relative weight = coverage divided by maximum coverage")
        _stream_float32_to_fits(raw_path, shape, weight_header, weight_path)
        return coverage_path, weight_path
    finally:
        try:
            cov_mm.flush()
            mmap_obj = getattr(cov_mm, "_mmap", None)
            if mmap_obj is not None:
                mmap_obj.close()
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
        for source in opened:
            if isinstance(source, np.memmap):
                try:
                    mmap_obj = getattr(source, "_mmap", None)
                    if mmap_obj is not None:
                        mmap_obj.close()
                except EXPECTED_OPERATION_ERRORS:
                    LOGGER.debug("Best-effort operation failed", exc_info=True)
        try:
            os.remove(raw_path)
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)

__all__ = [name for name in globals() if not name.startswith('__')]
