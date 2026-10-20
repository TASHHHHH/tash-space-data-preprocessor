from __future__ import annotations

from tsdp2._deps import *
from tsdp2.logging_config import LOGGER
from tsdp2.astronomy.coverage import _find_science_and_weight_hdus_for_mask, _has_valid_celestial_wcs, _load_mosaic_2d_and_header, _squeeze_2d_array_for_mosaic
from tsdp2.astronomy.fits_io import _mosaic_input_display_name
from tsdp2.astronomy.mosaic_geometry import _cluster_tiles_by_sky, _nearest_existing_folder
from tsdp2.astronomy.mosaic_inputs import _feather_mosaic, group_fits_by_filter
from tsdp2.astronomy.mosaic_reprojection import _estimate_overlap_plane_corrections
from tsdp2.astronomy.reference import parse_filter_code
from tsdp2.astronomy.regions import _crop_wcs_to_sky_region, _downscale_celestial_wcs, _normalise_mosaic_downscale
from tsdp2.output_paths import JobPaths, safe_filename
from tsdp2.recovery.manifests import _copy_file_chunked, _find_internal_nan_boxes, _recovery_header_inputs, _source_overlaps_repair_box, _stream_float32_to_fits
from tsdp2.utilities.memory import _format_gib

def _fast_repair_fits_holes(target_path, source_paths, output_path,
                            filter_override="", progress_cb=None, cancel_cb=None,
                            repair_regions=None, repair_cb=None):
    """Fill only internal NaN islands from original FITS, preserving real data.

    The target is copied once. Original tiles are then reprojected only into
    small rectangles surrounding internal holes, so this is much faster than a
    complete full-frame mosaic rebuild.
    """
    target_path = os.path.abspath(os.fspath(target_path))
    output_path = os.path.abspath(os.fspath(output_path))
    if target_path.lower().endswith(".fz"):
        raise ValueError("Fast repair needs an uncompressed target .fits file.")
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    temp_path = output_path + ".repairing"
    if os.path.exists(temp_path):
        os.remove(temp_path)

    with fits.open(target_path, memmap=True, lazy_load_hdus=True) as target_hdul:
        target_index = next((i for i, hdu in enumerate(target_hdul)
                             if getattr(hdu, "data", None) is not None and
                             getattr(hdu.data, "ndim", 0) >= 2), None)
        if target_index is None:
            raise ValueError("Target FITS contains no 2-D image.")
        target_data = _squeeze_2d_array_for_mosaic(target_hdul[target_index].data)
        target_header = target_hdul[target_index].header.copy()
        if target_data is None:
            raise ValueError("Target FITS image is not two-dimensional.")
        target_wcs = WCS(target_header, naxis=2)
        if not target_wcs.has_celestial:
            raise ValueError("Target FITS has no celestial WCS.")
        target_shape = tuple(int(x) for x in target_data.shape)
        if repair_regions is None:
            boxes = _find_internal_nan_boxes(target_data)
        else:
            boxes = [(max(0,r['box'][0]-32),min(target_shape[0],r['box'][1]+32),
                      max(0,r['box'][2]-32),min(target_shape[1],r['box'][3]+32))
                     for r in repair_regions]
        target_filter = (filter_override.strip() or
                         parse_filter_code(str(target_header.get("FILTER", ""))) or "")
        del target_data
    gc.collect()

    if not boxes:
        raise ValueError("No enclosed NaN/black hole regions were found in the target FITS.")
    groups = group_fits_by_filter(source_paths)
    selected_sources = groups.get(target_filter)
    if selected_sources is None:
        selected_sources = next((paths for code, paths in groups.items()
                                 if code.upper() == target_filter.upper()), None)
    if selected_sources is None and len(groups) == 1:
        target_filter, selected_sources = next(iter(groups.items()))
    if not selected_sources:
        raise ValueError(
            f"No loaded original FITS group matches '{target_filter}'. Available: " +
            ", ".join(sorted(groups)))

    source_geometry = []
    for source in selected_sources:
        try:
            header_inputs,_names=_recovery_header_inputs([source])
            for data_shape,header in header_inputs:
                bounds=None
                try:
                    footprint=WCS(header,naxis=2).calc_footprint(
                        axes=(data_shape.shape[1],data_shape.shape[0]))
                    px,py=target_wcs.all_world2pix(footprint[:,0],footprint[:,1],0)
                    if np.all(np.isfinite(px)) and np.all(np.isfinite(py)):
                        bounds=(float(np.min(py)),float(np.max(py)),float(np.min(px)),float(np.max(px)))
                except EXPECTED_OPERATION_ERRORS:
                    LOGGER.debug('Could not precompute repair source bounds',exc_info=True)
                source_geometry.append((source,data_shape.shape,header,bounds))
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug('Skipped invalid repair source header',exc_info=True)

    _copy_file_chunked(target_path, temp_path, progress_cb=progress_cb, cancel_cb=cancel_cb)
    repaired = 0
    remaining = 0
    total_jobs = max(1, len(boxes) * len(selected_sources))
    jobs_done = 0
    try:
        with fits.open(temp_path, mode="update", memmap=True, lazy_load_hdus=False) as out_hdul:
            out_hdu = out_hdul[target_index]
            out_data = _squeeze_2d_array_for_mosaic(out_hdu.data)
            out_wcs = WCS(out_hdu.header, naxis=2)
            if tuple(out_data.shape) != target_shape:
                raise RuntimeError("Repair copy changed the target image geometry.")

            for box_index, (y0, y1, x0, x1) in enumerate(boxes, start=1):
                if cancel_cb and cancel_cb():
                    raise RuntimeError("Hole repair cancelled by user.")
                block = out_data[y0:y1, x0:x1]
                hole = ~np.isfinite(block)
                if repair_regions is not None:
                    # Only this exact enclosed component; nearby exterior gaps
                    # may also be present in the context rectangle.
                    from scipy.ndimage import label as label_components
                    local_labels,_ = label_components(hole)
                    sy,sx=repair_regions[box_index-1]['seed']
                    component=int(local_labels[sy-y0,sx-x0])
                    hole=local_labels==component if component else np.zeros(hole.shape,dtype=bool)
                    del local_labels
                if not np.any(hole):
                    continue
                bh, bw = block.shape
                acc = np.zeros((bh, bw), dtype=np.float32)
                count = np.zeros((bh, bw), dtype=np.uint16)
                crop_wcs = out_wcs.deepcopy()
                crop_wcs.wcs.crpix = np.asarray(crop_wcs.wcs.crpix, dtype=float) - [x0, y0]

                for source,source_shape,geometry_header,bounds in source_geometry:
                    jobs_done += 1
                    if progress_cb:
                        progress_cb(jobs_done, total_jobs,
                                    f"Repairing hole region {box_index}/{len(boxes)} from original tiles")
                    if cancel_cb and cancel_cb():
                        raise RuntimeError("Hole repair cancelled by user.")
                    if bounds is not None and (bounds[1]<y0 or bounds[0]>=y1 or
                                               bounds[3]<x0 or bounds[2]>=x1):
                        continue
                    if bounds is None and not _source_overlaps_repair_box(
                            geometry_header,source_shape,out_wcs,(y0,y1,x0,x1)):
                        continue
                    hdul = None
                    try:
                        found = _find_science_and_weight_hdus_for_mask(source)
                        if not found or found[0] is None:
                            continue
                        hdul, sci_hdu, wht_hdu, source_header = found
                        source_data = _squeeze_2d_array_for_mosaic(sci_hdu.data)
                        if source_data is None:
                            continue
                        if not _source_overlaps_repair_box(
                                source_header, source_data.shape, out_wcs,
                                (y0, y1, x0, x1)):
                            continue
                        # Match the mosaic loader's weight/no-data/saturation
                        # rules; never recover holes from invalid raw pixels.
                        source_data, source_header = _load_mosaic_2d_and_header(source)
                        arr, footprint = reproject_interp(
                            (source_data, source_header), crop_wcs,
                            shape_out=(bh, bw), block_size=(512, 512),
                            parallel=False, roundtrip_coords=False)
                        good = np.isfinite(arr) & (footprint > 0.5)
                        # Match a local background PLANE to the already-good
                        # mosaic surrounding the hole. A scalar-only offset left
                        # rectangular color/brightness blocks when a DECam tile
                        # carried an x/y gradient.
                        overlap = good & np.isfinite(block)
                        overlap_count = int(np.count_nonzero(overlap))
                        if overlap_count < 16:
                            # A normalized mosaic needs enough surrounding sky
                            # to put the replacement on its existing zero point.
                            continue
                        if overlap_count >= 16:
                            oy, ox = np.nonzero(overlap)
                            if oy.size > 200000:
                                stride = max(1, oy.size // 200000)
                                oy, ox = oy[::stride], ox[::stride]
                            diff = (np.asarray(block[oy, ox], dtype=np.float64) -
                                    np.asarray(arr[oy, ox], dtype=np.float64))
                            if overlap_count >= 512:
                                px = 2.0 * ox.astype(np.float64) / max(bw - 1, 1) - 1.0
                                py = 2.0 * oy.astype(np.float64) / max(bh - 1, 1) - 1.0
                                design = np.column_stack((np.ones(diff.size), px, py))
                                coeff = np.linalg.lstsq(design, diff, rcond=None)[0]
                                residual = diff - design @ coeff
                                med = float(np.median(residual))
                                mad = float(np.median(np.abs(residual - med)))
                                sigma = max(1e-8, 1.4826 * mad)
                                keep = np.abs(residual - med) <= 4.0 * sigma
                                if int(keep.sum()) >= 256:
                                    coeff = np.linalg.lstsq(design[keep], diff[keep], rcond=None)[0]
                                if np.all(np.isfinite(coeff)):
                                    full_x = (2.0 * np.arange(bw, dtype=np.float32) /
                                              max(bw - 1, 1) - 1.0)[None, :]
                                    full_y = (2.0 * np.arange(bh, dtype=np.float32) /
                                              max(bh - 1, 1) - 1.0)[:, None]
                                    arr = (arr + np.float32(coeff[0]) +
                                           np.float32(coeff[1]) * full_x +
                                           np.float32(coeff[2]) * full_y)
                            else:
                                offset = float(np.nanmedian(diff))
                                if np.isfinite(offset):
                                    arr = arr + offset
                        use = good & hole
                        acc[use] += np.asarray(arr[use], dtype=np.float32)
                        count[use] += 1
                    finally:
                        try:
                            del source_data
                        except EXPECTED_OPERATION_ERRORS:
                            LOGGER.debug("Best-effort operation failed", exc_info=True)
                        try:
                            del arr, footprint
                        except EXPECTED_OPERATION_ERRORS:
                            LOGGER.debug("Best-effort operation failed", exc_info=True)
                        try:
                            if hdul is not None:
                                hdul.close()
                        except EXPECTED_OPERATION_ERRORS:
                            LOGGER.debug("Best-effort operation failed", exc_info=True)
                fill = hole & (count > 0)
                if np.any(fill):
                    block[fill] = acc[fill] / count[fill]
                    out_data[y0:y1, x0:x1] = block
                    repaired += int(np.count_nonzero(fill))
                    if repair_cb is not None:
                        repair_cb((y0,y1,x0,x1),fill,count)
                remaining += int(np.count_nonzero(hole & (count == 0)))
                del acc, count, hole, block
                try:
                    out_hdul.flush()
                except EXPECTED_OPERATION_ERRORS:
                    LOGGER.debug("Best-effort operation failed", exc_info=True)
            del out_data
            out_hdu.header["HOLEREP"] = (True, "NaN holes repaired from original FITS")
            out_hdu.header["HISTORY"] = (
                f"Localized hole repair: {repaired} pixels filled from {len(selected_sources)} source tiles")
            out_hdul.flush()
            del out_hdu
        gc.collect()

        with fits.open(temp_path, memmap=True, lazy_load_hdus=True) as check:
            image = check[target_index]
            if tuple(image.shape) != target_shape:
                raise RuntimeError("Repaired FITS failed shape verification.")
            if not WCS(image.header, naxis=2).has_celestial:
                raise RuntimeError("Repaired FITS failed WCS verification.")
            del image
        gc.collect()
        os.replace(temp_path, output_path)
        return {"path": output_path, "repaired": repaired, "remaining": remaining,
                "boxes": len(boxes), "filter": target_filter}
    except EXPECTED_OPERATION_ERRORS:
        # Keep a substantial interrupted repair copy for diagnosis/resume.
        try:
            if os.path.isfile(temp_path) and os.path.getsize(temp_path) < 1024 * 1024:
                os.remove(temp_path)
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
        raise


def _coarse_corrected_tiles(paths, wcs_out, shape_out, filter_code="DRAFT",
                            scratch_dir=None, progress_cb=None, cancel_cb=None,
                            source_masked_sky=False):
    """Reproject sources onto a small grid and robustly normalize backgrounds."""
    shape = tuple(int(x) for x in shape_out)
    tiles = []
    total = max(1, len(paths))
    for i, path in enumerate(paths):
        if cancel_cb and cancel_cb():
            raise RuntimeError("Mosaic cancelled by user.")
        if progress_cb:
            progress_cb(i, total + 2,
                        f"Drafting {filter_code} frame {i + 1}/{total}")
        data, hdr = _load_mosaic_2d_and_header(path)
        try:
            if not _has_valid_celestial_wcs(data, hdr):
                print(f"[Fast Draft] skipped invalid WCS: "
                      f"{_mosaic_input_display_name(path)}", flush=True)
                continue
            try:
                arr, footprint = reproject_interp(
                    (data, hdr), wcs_out, shape_out=shape,
                    block_size=(512, 512), parallel=False,
                    roundtrip_coords=False)
            except TypeError:
                arr, footprint = reproject_interp(
                    (data, hdr), wcs_out, shape_out=shape)
            tile = np.asarray(arr, dtype=np.float32)
            tile[np.asarray(footprint) <= 0.5] = np.nan
            tiles.append(tile)
            del arr, footprint
        finally:
            del data
            gc.collect()

    if not tiles:
        raise RuntimeError(f"Group '{filter_code}' has no usable WCS image frames.")
    from tsdp2.astronomy.background_matching import (
        estimate_background_planes, estimate_source_masked_sky_planes,
        apply_background_plane)
    sky_corrections = (estimate_source_masked_sky_planes(tiles, shape)
                       if source_masked_sky else [(0.0, 0.0, 0.0)] * len(tiles))
    for tile, correction in zip(tiles, sky_corrections):
        apply_background_plane(tile, correction)
    if progress_cb:
        progress_cb(total, total + 2, f"Robust overlap normalization for {filter_code}")
    corrections = estimate_background_planes(tiles, shape, filter_code)
    for tile, correction in zip(tiles, corrections):
        apply_background_plane(tile, correction)
    total_corrections = [tuple(np.asarray(a) + b) for a, b in zip(sky_corrections, corrections)]
    return tiles, total_corrections


def _fill_enclosed_draft_holes(data, max_component_fraction=0.01):
    """Cosmetically fill small, enclosed NaN islands in a Fast Draft.

    A single DECam exposure can contain chip defects / masked grey plateaus that
    have no contributing pixel after reprojection.  In a low-resolution draft
    these appear as solid black polygons even though the surrounding preview is
    otherwise useful.  Fill only components that do not touch the output edge;
    edge-connected NaN remains NaN because it represents genuinely uncovered
    sky.  This is deliberately draft-only and never changes the science mosaic.

    Returns ``(array, filled_pixel_count, filled_component_count)``.
    """
    arr = np.asarray(data, dtype=np.float32)
    invalid = ~np.isfinite(arr)
    if not np.any(invalid) or np.all(invalid):
        return arr, 0, 0

    structure = np.ones((3, 3), dtype=np.uint8)
    labels, component_count = ndi_label(invalid, structure=structure)
    if component_count <= 0:
        return arr, 0, 0

    border_labels = set(np.unique(np.concatenate((
        labels[0, :], labels[-1, :], labels[:, 0], labels[:, -1]
    ))).tolist())
    sizes = np.bincount(labels.ravel(), minlength=component_count + 1)
    max_pixels = max(64, int(arr.size * max(0.0, float(max_component_fraction))))
    selected_labels = [
        label_id for label_id in range(1, component_count + 1)
        if label_id not in border_labels and 0 < int(sizes[label_id]) <= max_pixels
    ]
    if not selected_labels:
        return arr, 0, 0

    fill_mask = np.isin(labels, np.asarray(selected_labels, dtype=labels.dtype))
    # Nearest real pixels keep the rim continuous.  A modest Gaussian field
    # removes Voronoi-looking streaks toward the middle of a larger hole.
    distance, nearest_indices = distance_transform_edt(
        invalid, return_distances=True, return_indices=True)
    nearest = arr[tuple(nearest_indices)]
    seeded = arr.copy()
    seeded[invalid] = nearest[invalid]
    largest = max(int(sizes[label_id]) for label_id in selected_labels)
    sigma = max(1.25, min(8.0, np.sqrt(float(largest)) / 10.0))
    smooth = gaussian_filter(seeded, sigma=sigma, mode="nearest")
    blend = np.clip(distance / max(2.0 * sigma, 1.0), 0.0, 1.0)

    result = arr.copy()
    result[fill_mask] = (
        nearest[fill_mask] * (1.0 - blend[fill_mask]) +
        smooth[fill_mask] * blend[fill_mask]
    ).astype(np.float32, copy=False)
    return result, int(np.count_nonzero(fill_mask)), len(selected_labels)


def _cosmetic_flatten_draft_background(data, strength=0.90):
    """Suppress medium-scale detector illumination blocks in a Fast Draft.

    The nine NOIRLab cutout tiles can contain many already-assembled DECam CCD
    regions. Their internal sky response cannot be fixed by matching the nine
    outer tile borders. Estimate a source-resistant lower-percentile background
    grid, separate its medium-scale component from the broad astronomical field,
    and subtract only that component. This is a cosmetic preview operation; it
    is intentionally never called for the full-resolution science mosaic.

    Returns ``(array, correction_rms, correction_peak)``.
    """
    arr = np.asarray(data, dtype=np.float32)
    finite = np.isfinite(arr)
    if arr.ndim != 2 or min(arr.shape) < 64 or int(np.count_nonzero(finite)) < 2048:
        return arr, 0.0, 0.0

    h, w = arr.shape
    block = max(20, min(64, int(round(min(h, w) / 28.0))))
    gh = max(3, int(np.ceil(h / block)))
    gw = max(3, int(np.ceil(w / block)))
    background = np.full((gh, gw), np.nan, dtype=np.float32)
    for gy in range(gh):
        y0, y1 = gy * block, min(h, (gy + 1) * block)
        for gx in range(gw):
            x0, x1 = gx * block, min(w, (gx + 1) * block)
            patch = arr[y0:y1, x0:x1]
            values = patch[np.isfinite(patch)]
            if values.size >= max(32, int(patch.size * 0.20)):
                # A lower percentile avoids stars and most bright nebulosity.
                background[gy, gx] = np.float32(np.percentile(values, 18.0))

    good_grid = np.isfinite(background)
    if int(np.count_nonzero(good_grid)) < max(9, int(background.size * 0.35)):
        return arr, 0.0, 0.0
    if not np.all(good_grid):
        _dist, nearest = distance_transform_edt(
            ~good_grid, return_distances=True, return_indices=True)
        background[~good_grid] = background[tuple(nearest)][~good_grid]

    local = gaussian_filter(background, sigma=0.75, mode="nearest")
    broad_sigma = max(3.0, min(background.shape) / 5.5)
    broad = gaussian_filter(background, sigma=broad_sigma, mode="nearest")
    detector_field = (local - broad).astype(np.float32, copy=False)

    centre = float(np.median(detector_field))
    residual = detector_field - np.float32(centre)
    mad = float(np.median(np.abs(residual)))
    robust_sigma = max(1e-6, 1.4826 * mad)
    # Prevent one saturated-star halo from driving an extreme correction.
    limit = max(3.5 * robust_sigma,
                float(np.percentile(np.abs(residual), 90.0)))
    detector_field = np.clip(residual, -limit, limit)
    strength = min(max(float(strength), 0.0), 1.0)
    correction_grid = detector_field * np.float32(strength)
    correction = _bilinear_field_block(correction_grid, arr.shape, 0, h)

    result = arr.copy()
    result[finite] = arr[finite] - correction[finite]
    used = correction[finite]
    rms = float(np.sqrt(np.mean(np.square(used, dtype=np.float64)))) if used.size else 0.0
    peak = float(np.max(np.abs(used))) if used.size else 0.0
    return result.astype(np.float32, copy=False), rms, peak


def fast_draft_filter_group(paths, out_dir, filter_code,
                            combine="feather", progress_cb=None,
                            downscale=0.08, scratch_dir=None,
                            sky_region=None, cancel_cb=None,
                            cosmetic_background=True,
                            job_paths: Optional[JobPaths] = None):
    """Build a low-resolution mosaic without retaining full input tiles in RAM."""
    if isinstance(out_dir, JobPaths):
        job_paths = out_dir
        out_dir = os.fspath(job_paths.mosaics)
    if not HAVE_MOSAIC:
        raise RuntimeError("reproject.mosaicking is not available.")
    header_inputs, names = _recovery_header_inputs(paths)
    path_by_input = {id(item[0]): path for item, path in zip(header_inputs, paths)}
    kept_inputs, _kept_names, dropped = _cluster_tiles_by_sky(header_inputs, names)
    kept_paths = [path_by_input[id(item[0])] for item in kept_inputs]
    if not kept_inputs:
        raise RuntimeError(f"Group '{filter_code}' has no sky-coherent frames.")
    for name, reason in dropped:
        print(f"[Fast Draft] excluded {name}: {reason}", flush=True)

    wcs_out, shape_out = find_optimal_celestial_wcs(kept_inputs)
    scale = _normalise_mosaic_downscale(downscale, default=0.08)
    wcs_out, shape_out = _downscale_celestial_wcs(wcs_out, shape_out, scale)
    if sky_region:
        wcs_out, shape_out, _bounds = _crop_wcs_to_sky_region(
            wcs_out, shape_out, sky_region)
    shape_out = tuple(int(x) for x in shape_out)
    tiles, _corrections = _coarse_corrected_tiles(
        kept_paths, wcs_out, shape_out, filter_code=filter_code,
        scratch_dir=scratch_dir, progress_cb=progress_cb,
        cancel_cb=cancel_cb, source_masked_sky=cosmetic_background)
    if cancel_cb and cancel_cb():
        raise RuntimeError("Mosaic cancelled by user.")

    if combine == "median":
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", "All-NaN slice", RuntimeWarning)
            mosaic = np.nanmedian(np.stack(tiles), axis=0).astype(np.float32)
    elif combine in ("mean", "average"):
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", "Mean of empty slice", RuntimeWarning)
            mosaic = np.nanmean(np.stack(tiles), axis=0).astype(np.float32)
    else:
        mosaic = _feather_mosaic(tiles).astype(np.float32, copy=False)

    # Leave missing samples visible for the automatic source-backed audit.
    # Interpolating them here would hide holes from the repair pass.
    draft_filled_pixels = draft_filled_components = 0

    draft_flat_rms = 0.0
    draft_flat_peak = 0.0
    if cosmetic_background:
        # Sky subtraction already happened per detector, before feathering.
        # Do not run the old high-pass mosaic mesh over the galaxy afterwards.
        magnitudes = np.asarray(_corrections, dtype=np.float64)
        draft_flat_rms = float(np.sqrt(np.mean(magnitudes**2)))
        draft_flat_peak = float(np.max(np.abs(magnitudes)))
        print(f"[Fast Draft] {filter_code}: source-masked detector sky normalization enabled", flush=True)

    os.makedirs(out_dir, exist_ok=True)
    mosaic_name = f"mosaic_{safe_filename(filter_code)}.fits"
    out_path = os.fspath(
        job_paths.final_mosaic_fits(filter_code, mosaic_name)
        if job_paths is not None
        else os.path.join(out_dir, mosaic_name))
    hdr = wcs_out.to_header()
    hdr["FILTER"] = filter_code
    hdr["BGNORM"] = ("robust-overlap-v1", "Relative additive overlap normalization")
    hdr["SKYNORM"] = (bool(cosmetic_background), "Source-masked per-detector sky subtraction")
    hdr["FASTDRFT"] = (True, "Low-resolution Fast Draft mosaic")
    hdr["DRFTSCL"] = (float(scale), "Draft width/height scale")
    hdr["DRFILL"] = (bool(draft_filled_pixels),
                     "Cosmetic enclosed NaN fill in draft")
    hdr["DRFILPX"] = (int(draft_filled_pixels),
                      "Cosmetic draft fill pixel count")
    hdr["DRFLAT"] = (bool(cosmetic_background and draft_flat_rms > 0),
                     "Source-masked detector sky normalization")
    hdr["DRFLRMS"] = (float(draft_flat_rms),
                      "RMS of draft plane coefficients")
    hdr.add_history(
        f"TSDP Fast Draft: {len(tiles)} frames, combine={combine}; robust overlap matching")
    if sky_region:
        hdr["SKYCROP"] = (True, "Interactive WCS coverage area applied")
        hdr["SKYROT"] = (float(sky_region.get("rotation_deg", 0.0) or 0.0),
                         "Interactive selection rotation deg")
    temp_out = out_path + ".writing"
    fits.PrimaryHDU(mosaic, header=hdr).writeto(temp_out, overwrite=True)
    os.replace(temp_out, out_path)
    if progress_cb:
        progress_cb(len(paths) + 2, len(paths) + 2,
                    f"Fast draft written: {os.path.basename(out_path)}")
    return out_path


def _bilinear_field_block(field, output_shape, y0, y1):
    """Resize a small float field into one row block without a full-size copy."""
    src = np.asarray(field, dtype=np.float32)
    sh, sw = src.shape
    oh, ow = int(output_shape[0]), int(output_shape[1])
    x = (np.arange(ow, dtype=np.float32) + 0.5) * (sw / max(ow, 1)) - 0.5
    y = (np.arange(y0, y1, dtype=np.float32) + 0.5) * (sh / max(oh, 1)) - 0.5
    x = np.clip(x, 0.0, max(sw - 1, 0))
    y = np.clip(y, 0.0, max(sh - 1, 0))
    x0 = np.floor(x).astype(np.int64)
    y0i = np.floor(y).astype(np.int64)
    x1 = np.minimum(x0 + 1, sw - 1)
    y1i = np.minimum(y0i + 1, sh - 1)
    wx = (x - x0).astype(np.float32)[None, :]
    wy = (y - y0i).astype(np.float32)[:, None]
    top = src[y0i[:, None], x0[None, :]] * (1.0 - wx) + \
          src[y0i[:, None], x1[None, :]] * wx
    bottom = src[y1i[:, None], x0[None, :]] * (1.0 - wx) + \
             src[y1i[:, None], x1[None, :]] * wx
    return (top * (1.0 - wy) + bottom * wy).astype(np.float32, copy=False)


def _estimate_streaming_background_correction(data, strength=1.0):
    """Compatibility wrapper for the full-field, source-masked sky model."""
    from tsdp2.astronomy.global_background import estimate_global_background
    field,_mask,report=estimate_global_background(data)
    field *= np.float32(strength)
    return field,report['correction_rms']*strength,report['correction_peak']*strength


def write_background_clean_fits_copy(input_path, output_path, strength=1.0,
                                     progress_cb=None, cancel_cb=None):
    """Write a WCS-preserving, cosmetically background-cleaned FITS copy.

    The source file is never modified. The copy and correction are streamed in
    bounded blocks so a 4+ GB mosaic does not need to fit in RAM.
    """
    strength=float(strength)
    if not np.isfinite(strength) or not 0 <= strength <= 1:
        raise ValueError("Global normalization strength must be between 0 and 1.")
    input_path = os.path.abspath(os.fspath(input_path))
    output_path = os.path.abspath(os.fspath(output_path))
    if input_path == output_path:
        raise ValueError("Choose a different output filename; the original stays untouched.")
    if not os.path.isfile(input_path):
        raise FileNotFoundError(input_path)
    output_dir = os.path.dirname(output_path) or "."
    os.makedirs(output_dir, exist_ok=True)
    source_size = int(os.path.getsize(input_path))
    free = int(shutil.disk_usage(output_dir).free)
    if free < int(source_size * 1.08):
        raise RuntimeError(
            f"The cleaned copy needs about {_format_gib(int(source_size * 1.08))}; "
            f"only {_format_gib(free)} is free in the output folder.")

    found = _find_science_and_weight_hdus_for_mask(input_path)
    if not found or found[0] is None:
        raise RuntimeError("The selected FITS has no readable 2-D science image.")
    source_hdul, source_hdu, _weight_hdu, source_header = found
    try:
        source_index = next(
            (idx for idx, candidate in enumerate(source_hdul) if candidate is source_hdu), None)
        source_data = _squeeze_2d_array_for_mosaic(source_hdu.data)
        if source_index is None or source_data is None:
            raise RuntimeError("Could not identify the science image HDU.")
        shape = tuple(int(v) for v in source_data.shape)
        if not WCS(source_header, naxis=2).has_celestial:
            raise RuntimeError("The selected FITS has no celestial WCS to preserve.")
        if progress_cb:
            progress_cb(10, 100, "Sampling the high-resolution detector background")
        from tsdp2.astronomy.global_background import estimate_global_background, global_correction_block
        field, _source_mask, model_report = estimate_global_background(source_data)
        field *= np.float32(strength)
        model_rms=model_report['correction_rms']*strength
        model_peak=model_report['correction_peak']*strength
    finally:
        try:
            del source_data
            del source_hdu
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
        source_hdul.close()
        gc.collect()

    temp_path = output_path + ".writing"
    try:
        copied = 0
        chunk_bytes = 64 * 1024 * 1024
        with open(input_path, "rb") as source_stream, open(temp_path, "wb") as target_stream:
            while True:
                if cancel_cb and cancel_cb():
                    raise RuntimeError("Background cleanup cancelled by user.")
                chunk = source_stream.read(chunk_bytes)
                if not chunk:
                    break
                target_stream.write(chunk)
                copied += len(chunk)
                if progress_cb:
                    progress_cb(10 + int(35 * copied / max(source_size, 1)), 100,
                                "Copying the original FITS safely")

        sum_sq = 0.0
        corrected_count = 0
        actual_peak = 0.0
        with fits.open(temp_path, mode="update", memmap=True, lazy_load_hdus=True) as output_hdul:
            if source_index >= len(output_hdul):
                raise RuntimeError("The copied FITS lost its science HDU.")
            output_hdu = output_hdul[source_index]
            output_data = _squeeze_2d_array_for_mosaic(output_hdu.data)
            if output_data is None or tuple(output_data.shape) != shape:
                raise RuntimeError("The copied FITS science dimensions changed unexpectedly.")
            h, w = shape
            rows = max(1, min(256, int((32 * 1024 * 1024) // max(1, w * 24))))
            for y0 in range(0, h, rows):
                if cancel_cb and cancel_cb():
                    raise RuntimeError("Background cleanup cancelled by user.")
                y1 = min(h, y0 + rows)
                block = np.asarray(output_data[y0:y1], dtype=np.float32)
                correction = global_correction_block(
                    field, shape, model_report['sample_step'], y0, y1)
                finite = np.isfinite(block)
                if np.any(finite):
                    used = correction[finite].astype(np.float64, copy=False)
                    sum_sq += float(np.dot(used, used))
                    corrected_count += int(used.size)
                    actual_peak = max(actual_peak, float(np.max(np.abs(used))))
                    block[finite] -= correction[finite]
                    output_data[y0:y1] = block
                if progress_cb:
                    progress_cb(45 + int(54 * y1 / max(h, 1)), 100,
                                f"Cleaning high-resolution rows {y1:,}/{h:,}")
            exact_rms = float(np.sqrt(sum_sq / max(corrected_count, 1)))
            del block, output_data, output_hdu
            output_hdul.flush(output_verify="silentfix")
        del output_hdul
        # Edit provenance without loading science pixels, so FITS header growth
        # never invalidates a live Windows mmap.
        with fits.open(temp_path, mode='update', memmap=True) as metadata:
            for header in (metadata[0].header, metadata[source_index].header):
                for key in ('CHECKSUM','DATASUM'):
                    header.remove(key, ignore_missing=True)
            header=metadata[source_index].header
            header['BGCLEAN']=(True,'Global visual background equalization')
            header['BGCLMETH']='global-source-masked-v1'
            header['BGCLSTR']=(strength,'Global correction strength')
            header['BGCLRMS']=(exact_rms,'RMS additive correction')
            header['BGCLPEAK']=(float(actual_peak),'Peak additive correction')
            header['BGSRCFR']=(model_report['protected_fraction'],'Source-masked sample fraction')
            header['BGSKY0']=(model_report['target_sky'],'Common visual sky level')
            header.add_history('Full-field source-masked background equalization; additive correction only')
            metadata.flush()
        gc.collect()

        with fits.open(temp_path, memmap=True, lazy_load_hdus=True) as check:
            check_data = _squeeze_2d_array_for_mosaic(check[source_index].data)
            if check_data is None or tuple(check_data.shape) != shape:
                raise RuntimeError("Cleaned FITS failed dimension verification.")
            if not WCS(check[source_index].header, naxis=2).has_celestial:
                raise RuntimeError("Cleaned FITS failed WCS verification.")
            del check_data
        gc.collect()
        os.replace(temp_path, output_path)
        if progress_cb:
            progress_cb(100, 100, "High-resolution background-cleaned FITS complete")
        return {
            "output_path": output_path, "shape": shape, "strength": float(strength),
            "rms": exact_rms, "peak": float(actual_peak),
            "model_rms": float(model_rms), "model_peak": float(model_peak),
            "model": model_report,
        }
    except EXPECTED_OPERATION_ERRORS:
        try:
            if os.path.isfile(temp_path):
                os.remove(temp_path)
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
        raise


def repair_existing_mosaic_seams(mosaic_path, source_paths, output_path,
                                 filter_code="UNKNOWN", strength=1.0,
                                 preview_scale=0.08, scratch_dir=None,
                                 progress_cb=None, cancel_cb=None):
    """Repair tile-level background seams in an existing mosaic.

    Original tiles are reprojected only onto a small copy of the existing
    mosaic WCS. Their overlap planes produce a low-frequency correction field,
    which is then applied to the existing full-resolution mosaic in row blocks.
    High-resolution stars and nebula detail are never rebuilt or resampled.
    """
    if not source_paths:
        raise ValueError("Load the original FITS tiles for this filter first.")
    strength = float(strength)
    if not np.isfinite(strength) or strength < 0.0 or strength > 2.0:
        raise ValueError("Repair strength must be between 0 and 2.")
    requested_scale = _normalise_mosaic_downscale(preview_scale, default=0.08)
    output_path = os.path.abspath(os.fspath(output_path))
    output_dir = os.path.dirname(output_path) or "."
    scratch_root = os.path.abspath(os.fspath(scratch_dir or output_dir))
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(scratch_root, exist_ok=True)

    hdul = None
    work = None
    try:
        found = _find_science_and_weight_hdus_for_mask(mosaic_path)
        if not found or found[0] is None:
            raise RuntimeError("The selected mosaic has no readable 2-D image.")
        hdul, sci_hdu, _wht_hdu, target_header = found
        target = _squeeze_2d_array_for_mosaic(sci_hdu.data)
        if target is None:
            raise RuntimeError("The selected mosaic image is not two-dimensional.")
        shape = tuple(int(x) for x in target.shape)
        h, w = shape
        target_wcs = WCS(target_header, naxis=2)
        if not target_wcs.has_celestial:
            raise RuntimeError("The selected mosaic does not have celestial WCS data.")

        max_dim = max(h, w)
        scale = max(requested_scale, min(1.0, 192.0 / max(max_dim, 1)))
        scale = min(scale, max(0.02, 1600.0 / max(max_dim, 1)))
        scale = max(0.02, min(1.0, scale))
        coarse_wcs, coarse_shape = _downscale_celestial_wcs(
            target_wcs, shape, scale)
        coarse_shape = tuple(int(x) for x in coarse_shape)
        if progress_cb:
            progress_cb(0, 100,
                        f"Building {coarse_shape[1]}x{coarse_shape[0]} seam model")

        corrected_tiles, plane_corrections = _coarse_corrected_tiles(
            source_paths, coarse_wcs, coarse_shape,
            filter_code=f"{filter_code} seam repair",
            scratch_dir=scratch_root, progress_cb=None,
            cancel_cb=cancel_cb)
        if len(corrected_tiles) > 1:
            corrected_low = _feather_mosaic(corrected_tiles).astype(
                np.float32, copy=False)
        else:
            corrected_low = corrected_tiles[0]
        if progress_cb:
            progress_cb(35, 100, "Comparing the repaired tile model with the mosaic")

        try:
            target_low, target_fp = reproject_interp(
                (target, target_header), coarse_wcs,
                shape_out=coarse_shape, block_size=(512, 512),
                parallel=False, roundtrip_coords=False)
        except TypeError:
            target_low, target_fp = reproject_interp(
                (target, target_header), coarse_wcs, shape_out=coarse_shape)
        target_low = np.asarray(target_low, dtype=np.float32)
        valid = (np.isfinite(corrected_low) & np.isfinite(target_low) &
                 (np.asarray(target_fp) > 0.5))
        if int(valid.sum()) < 256:
            raise RuntimeError(
                "The loaded original tiles do not overlap enough of this mosaic.")

        difference = np.zeros(coarse_shape, dtype=np.float32)
        values = (corrected_low[valid] - target_low[valid]).astype(np.float32)
        median = float(np.median(values))
        mad = float(np.median(np.abs(values - median)))
        sigma = max(1e-8, 1.4826 * mad)
        values = np.clip(values, median - 8.0 * sigma, median + 8.0 * sigma)
        difference[valid] = values
        blur_sigma = max(1.0, min(coarse_shape) / 280.0)
        weights = valid.astype(np.float32)
        smooth_num = gaussian_filter(difference, blur_sigma, mode="nearest")
        smooth_den = gaussian_filter(weights, blur_sigma, mode="nearest")
        correction = np.zeros(coarse_shape, dtype=np.float32)
        supported = smooth_den > 0.05
        correction[supported] = smooth_num[supported] / smooth_den[supported]
        correction -= np.float32(np.median(correction[valid]))
        correction *= np.float32(strength)
        correction[~np.isfinite(correction)] = 0.0

        raw_bytes = h * w * 4
        output_free = int(shutil.disk_usage(_nearest_existing_folder(output_dir)).free)
        scratch_free = int(shutil.disk_usage(_nearest_existing_folder(scratch_root)).free)
        same_volume = (os.path.splitdrive(output_dir)[0].lower() ==
                       os.path.splitdrive(scratch_root)[0].lower())
        if same_volume and output_free < int(raw_bytes * 2.25):
            raise RuntimeError(
                f"Seam repair needs about {_format_gib(int(raw_bytes * 2.25))} "
                f"temporarily, but only {_format_gib(output_free)} is free.")
        if not same_volume and (output_free < int(raw_bytes * 1.15) or
                                scratch_free < int(raw_bytes * 1.05)):
            raise RuntimeError("Not enough output or scratch disk space for seam repair.")

        work = tempfile.mkdtemp(prefix="jwst_seam_repair_", dir=scratch_root)
        raw_path = os.path.join(work, "seam_fixed.f32")
        result = np.memmap(raw_path, dtype="float32", mode="w+", shape=shape)
        rows_per_chunk = max(1, min(h, (32 * 1024 * 1024) // max(4, w * 4)))
        for row0 in range(0, h, rows_per_chunk):
            if cancel_cb and cancel_cb():
                raise RuntimeError("Seam repair cancelled by user.")
            row1 = min(h, row0 + rows_per_chunk)
            block = np.asarray(target[row0:row1], dtype=np.float32)
            field = _bilinear_field_block(correction, shape, row0, row1)
            repaired = block.copy()
            finite = np.isfinite(block)
            repaired[finite] = block[finite] + field[finite]
            result[row0:row1] = repaired
            if progress_cb:
                progress_cb(40 + int(45 * row1 / max(h, 1)), 100,
                            f"Applying seam correction rows {row1:,}/{h:,}")
        result.flush()
        del result

        out_header = target_header.copy()
        out_header["SEAMFIX"] = (True, "TSDP existing-mosaic seam repair")
        out_header["SEAMSTR"] = (float(strength), "Seam repair strength")
        out_header["SEAMSCL"] = (float(scale), "Low-resolution seam model scale")
        out_header["SEAMSRC"] = (len(source_paths), "Original source frames used")
        out_header.add_history(
            f"Existing mosaic seam repair used {len(source_paths)} original frames")

        # Release the input FITS before creating another potentially large FITS.
        hdul.close()
        hdul = None
        _stream_float32_to_fits(
            raw_path, shape, out_header, output_path,
            progress_cb=(lambda done, total, msg:
                         progress_cb(85 + int(15 * done / max(total, 1)), 100, msg)
                         if progress_cb else None),
            cancel_cb=cancel_cb, mark_recovery=False,
            history_text="TSDP streamed seam-repaired mosaic to FITS")
        return {
            "output_path": output_path,
            "frames": len(source_paths),
            "model_shape": coarse_shape,
            "scale": float(scale),
            "strength": float(strength),
            "plane_corrections": plane_corrections,
            "correction_min": float(np.nanmin(correction)),
            "correction_max": float(np.nanmax(correction)),
        }
    finally:
        try:
            if hdul is not None:
                hdul.close()
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
        if work:
            shutil.rmtree(work, ignore_errors=True)

__all__ = [name for name in globals() if not name.startswith('__')]
