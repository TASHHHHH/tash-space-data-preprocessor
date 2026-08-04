from __future__ import annotations

from tsdp2._deps import *
from tsdp2.logging_config import LOGGER
from tsdp2.output_paths import JobPaths, safe_filename
from tsdp2.recovery.manifests import _stream_float32_to_fits

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
    """Solve additive low-order background planes from tile overlaps.

    Each returned (a, bx, by) plane is added to its tile on the common output
    grid. Sampling is sparse, so this is cheap even for gigapixel mosaics.
    """
    shape = tuple(int(x) for x in shape_out)
    h, w = shape
    n = len(tile_paths)
    if n <= 1:
        return [(0.0, 0.0, 0.0)] * n
    # Small draft/seam-model grids need dense samples. Full mosaics retain the
    # historical stride floor, which also keeps anchor selection deterministic.
    stride_floor = 1 if dense else 8
    step = max(stride_floor, int(np.ceil(np.sqrt((h * w) / 600000.0))))
    ys = np.arange(0, h, step, dtype=np.int64)
    xs = np.arange(0, w, step, dtype=np.int64)
    yn = (2.0 * ys / max(h - 1, 1) - 1.0).astype(np.float32)
    xn = (2.0 * xs / max(w - 1, 1) - 1.0).astype(np.float32)
    xx, yy = np.meshgrid(xn, yn)
    min_pair_samples = max(24, min(300, int(xx.size * 0.02)))
    samples = []
    coverage = []
    for path in tile_paths:
        mm = np.memmap(path, dtype="float32", mode="r", shape=shape)
        sample = np.asarray(mm[::step, ::step], dtype=np.float32).copy()
        samples.append(sample)
        coverage.append(int(np.isfinite(sample).sum()))
        del mm

    pair_rows = []
    pair_rhs = []
    adjacency = {i: set() for i in range(n)}
    nearby_pair_count = 0
    # In sampled coordinates, compare edges separated by at most roughly forty
    # output pixels.  This bridges narrow detector gaps without treating remote
    # tiles as neighbours.
    nearby_gap = max(2, int(np.ceil(40.0 / max(step, 1))))
    for i in range(n):
        for j in range(i + 1, n):
            good = np.isfinite(samples[i]) & np.isfinite(samples[j])
            count = int(good.sum())
            edge_only = False
            if count >= min_pair_samples:
                diff = (samples[i][good] - samples[j][good]).astype(np.float64)
                gx = xx[good].astype(np.float64)
                gy = yy[good].astype(np.float64)
            elif nearby_edges:
                finite_i = np.isfinite(samples[i])
                finite_j = np.isfinite(samples[j])
                edge_diff = []
                edge_x = []
                edge_y = []

                if np.any(finite_i) and np.any(finite_j):
                    dist_j, idx_j = distance_transform_edt(
                        ~finite_j, return_distances=True, return_indices=True)
                    near_i = finite_i & ~finite_j & (dist_j <= nearby_gap)
                    if np.any(near_i):
                        iy, ix = np.nonzero(near_i)
                        jy, jx = idx_j[0, iy, ix], idx_j[1, iy, ix]
                        edge_diff.append((samples[i][iy, ix] -
                                          samples[j][jy, jx]).astype(np.float64))
                        edge_x.append(((xx[iy, ix] + xx[jy, jx]) * 0.5).astype(np.float64))
                        edge_y.append(((yy[iy, ix] + yy[jy, jx]) * 0.5).astype(np.float64))

                    dist_i, idx_i = distance_transform_edt(
                        ~finite_i, return_distances=True, return_indices=True)
                    near_j = finite_j & ~finite_i & (dist_i <= nearby_gap)
                    if np.any(near_j):
                        jy, jx = np.nonzero(near_j)
                        iy, ix = idx_i[0, jy, jx], idx_i[1, jy, jx]
                        edge_diff.append((samples[i][iy, ix] -
                                          samples[j][jy, jx]).astype(np.float64))
                        edge_x.append(((xx[iy, ix] + xx[jy, jx]) * 0.5).astype(np.float64))
                        edge_y.append(((yy[iy, ix] + yy[jy, jx]) * 0.5).astype(np.float64))

                edge_min_samples = max(24, min(120, min_pair_samples // 3))
                edge_count = sum(int(values.size) for values in edge_diff)
                if edge_count < edge_min_samples:
                    continue
                diff = np.concatenate(edge_diff)
                gx = np.concatenate(edge_x)
                gy = np.concatenate(edge_y)
                count = int(diff.size)
                edge_only = True
            else:
                continue
            if diff.size > 200000:
                stride = max(1, diff.size // 200000)
                diff, gx, gy = diff[::stride], gx[::stride], gy[::stride]
            if edge_only:
                # One narrow shared border cannot constrain a safe 2-D plane.
                # Use a robust scalar sky offset; this removes rectangular level
                # steps without inventing a large gradient across the detector.
                centre = float(np.median(diff))
                mad = float(np.median(np.abs(diff - centre)))
                sigma = max(1e-8, 1.4826 * mad)
                keep = np.abs(diff - centre) <= 3.5 * sigma
                if int(keep.sum()) >= max(20, count // 4):
                    centre = float(np.median(diff[keep]))
                coeff = np.asarray((centre, 0.0, 0.0), dtype=np.float64)
                nearby_pair_count += 1
            else:
                design = np.column_stack((np.ones(diff.size), gx, gy))
                coeff = np.linalg.lstsq(design, diff, rcond=None)[0]
                residual = diff - design @ coeff
                med = float(np.median(residual))
                mad = float(np.median(np.abs(residual - med)))
                sigma = max(1e-8, 1.4826 * mad)
                keep = np.abs(residual - med) <= 4.0 * sigma
                min_refit = max(20, min(200, count // 2))
                if int(keep.sum()) >= min_refit:
                    coeff = np.linalg.lstsq(design[keep], diff[keep], rcond=None)[0]
            row = np.zeros(n, dtype=np.float64)
            row[i], row[j] = 1.0, -1.0
            weight = min(8.0, max(1.0, np.sqrt(count / 1000.0)))
            if edge_only:
                weight *= 0.75
            pair_rows.append(row * weight)
            pair_rhs.append((-coeff) * weight)
            adjacency[i].add(j)
            adjacency[j].add(i)

    if not pair_rows:
        print(f"[Mosaic] {filter_code}: no usable tile overlaps/nearby edges for seam matching", flush=True)
        return [(0.0, 0.0, 0.0)] * n

    if nearby_pair_count:
        print(f"[Mosaic] {filter_code}: matched {nearby_pair_count} nearby "
              "detector-edge pair(s) without overlap", flush=True)

    # Anchor one well-covered tile in every connected overlap component.
    seen = set()
    for start in range(n):
        if start in seen:
            continue
        stack = [start]
        component = []
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            component.append(node)
            stack.extend(adjacency[node] - seen)
        anchor = max(component, key=lambda idx: coverage[idx])
        row = np.zeros(n, dtype=np.float64)
        row[anchor] = 10.0
        pair_rows.append(row)
        pair_rhs.append(np.zeros(3, dtype=np.float64))

    matrix = np.vstack(pair_rows)
    rhs = np.vstack(pair_rhs)
    solved = np.linalg.lstsq(matrix, rhs, rcond=None)[0]
    corrections = [tuple(float(v) for v in solved[i]) for i in range(n)]
    for i, (a, bx, by) in enumerate(corrections):
        print(f"[Mosaic] {filter_code}: seam plane tile {i + 1}: "
              f"offset={a:+.5g}, x={bx:+.5g}, y={by:+.5g}", flush=True)
    return corrections


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
