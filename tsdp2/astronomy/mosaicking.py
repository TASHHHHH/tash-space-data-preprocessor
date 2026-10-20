from __future__ import annotations

from tsdp2._deps import *
from tsdp2.logging_config import LOGGER
from tsdp2.astronomy.coverage import _has_valid_celestial_wcs, _load_mosaic_2d_and_header
from tsdp2.astronomy.mosaic_geometry import _MOSAIC_MAX_CANVAS_PX, _cluster_tiles_by_sky, _friendly_wcs_memory_error, _mosaic_cache_key
from tsdp2.astronomy.mosaic_inputs import _feather_mosaic, _sky_match_tiles
from tsdp2.astronomy.mosaic_reprojection import _estimate_overlap_plane_corrections, _nanmean_memmap, _nanmedian_memmap, _reproject_tile_to_memmap, _write_mosaic_quality_maps
from tsdp2.astronomy.reference import filter_code_to_microns
from tsdp2.astronomy.regions import _crop_wcs_to_sky_region, _downscale_celestial_wcs, _normalise_mosaic_downscale
from tsdp2.output_paths import JobPaths, safe_filename
from tsdp2.recovery.manifests import _RECOVERY_MANIFEST_NAME, _stream_float32_to_fits, _update_recovery_manifest
from tsdp2.utilities.memory import _mosaic_disk_mode_needed, _safe_disk_turbo_workers, _safe_mosaic_workers
from tsdp2.astronomy.mosaic_reprojection import _mosaic_roundtrip_required
from tsdp2.utilities.memory import _safe_mosaic_block_workers

def _combine_disk_backed_mosaic(combine, crop_bounds, downscale, filter_code, inputs, n_tiles, out_dir, out_path, paths, progress_cb, resume_cache, scratch_dir, shape_out, sky_region, turbo_fullres, wcs_out, workers):
    scratch_root = os.path.abspath(os.fspath(scratch_dir or out_dir))
    os.makedirs(scratch_root, exist_ok=True)
    cache_key, input_signatures = _mosaic_cache_key(paths, filter_code, shape_out, wcs_out, combine, downscale, sky_region=sky_region)
    session_suffix = cache_key[:16] if resume_cache else str(int(time.time()))
    tmp_dir = os.path.join(scratch_root, f'_cache_mosaic_{safe_filename(filter_code)}_{session_suffix}')
    os.makedirs(tmp_dir, exist_ok=True)
    old_manifest = {}
    manifest_path = os.path.join(tmp_dir, _RECOVERY_MANIFEST_NAME)
    if resume_cache and os.path.isfile(manifest_path):
        try:
            with open(manifest_path, 'r', encoding='utf-8') as fh:
                candidate_manifest = json.load(fh)
            if candidate_manifest.get('cache_key') == cache_key:
                old_manifest = candidate_manifest
        except EXPECTED_OPERATION_ERRORS:
            old_manifest = {}
    manifest_header = wcs_out.to_header()
    manifest_header['FILTER'] = filter_code
    completed_tiles = {int(x) for x in old_manifest.get('completed_tiles') or [] if str(x).lstrip('-').isdigit()}
    _update_recovery_manifest(tmp_dir, manifest_version=2, kind='mosaic_float32', stage='reprojecting', cache_key=cache_key, resumable=bool(resume_cache), filter_code=filter_code, combine=combine, downscale=float(downscale), turbo_fullres=bool(turbo_fullres), turbo_workers=int(workers), shape_out=[int(shape_out[0]), int(shape_out[1])], dtype='float32-native', input_paths=[os.fspath(p) for p in paths], input_signatures=input_signatures, completed_tiles=sorted(completed_tiles), sky_region=sky_region, crop_bounds=list(crop_bounds) if crop_bounds else None, output_path=os.path.abspath(out_path), wcs_header=manifest_header.tostring(sep='\n', endcard=True, padding=False))
    tile_mm_paths = []
    expected_raw_bytes = int(np.prod(shape_out)) * 4
    pending = []
    tile_mm_paths = [None] * n_tiles
    for i, (data, hdr) in enumerate(inputs):
        arr_path = os.path.join(tmp_dir, f'{safe_filename(filter_code)}_tile_{i:03d}.f32')
        reusable = resume_cache and i in completed_tiles and os.path.isfile(arr_path) and (os.path.getsize(arr_path) == expected_raw_bytes)
        if reusable:
            print(f'[Mosaic] {filter_code}: resume cache reused tile {i + 1}/{n_tiles}', flush=True)
            if progress_cb:
                progress_cb(i + 1, max(1, n_tiles * 3 + 2), f'Resume cache: reused {filter_code} tile {i + 1}/{n_tiles}')
            tile_mm_paths[i] = arr_path
        else:
            pending.append((i, data, hdr))

    def _run_disk_tile(job):
        i, data, hdr = job
        return (i, _reproject_tile_to_memmap(i, data, hdr, wcs_out, tuple(shape_out), tmp_dir, filter_code, progress_cb=progress_cb, total_tiles=n_tiles))

    def _record_completed(i, arr_path):
        tile_mm_paths[i] = arr_path
        completed_tiles.add(i)
        _update_recovery_manifest(tmp_dir, stage='reprojecting', turbo_fullres=bool(turbo_fullres), turbo_workers=int(workers), completed_tiles=sorted(completed_tiles))
    if workers > 1 and len(pending) > 1:
        retry_jobs = []
        if progress_cb:
            progress_cb(len(completed_tiles), max(1, n_tiles * 3 + 2), f'Reprojecting up to {workers} {filter_code} tiles in parallel…')
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_run_disk_tile, job): job for job in pending}
            for future in as_completed(futures):
                job = futures[future]
                try:
                    idx, arr_path = future.result()
                    _record_completed(idx, arr_path)
                except EXPECTED_OPERATION_ERRORS as exc:
                    retry_jobs.append((job, exc))
        for job, first_exc in retry_jobs:
            idx = int(job[0])
            partial_path = os.path.join(tmp_dir, f'{safe_filename(filter_code)}_tile_{idx:03d}.f32')
            try:
                if os.path.isfile(partial_path):
                    os.remove(partial_path)
            except EXPECTED_OPERATION_ERRORS:
                LOGGER.debug("Best-effort operation failed", exc_info=True)
            print(f'[Mosaic] {filter_code}: parallel tile {idx + 1} failed ({first_exc}); retrying alone in safe mode', flush=True)
            if progress_cb:
                progress_cb(len(completed_tiles), max(1, n_tiles * 3 + 2), f'Retrying {filter_code} tile {idx + 1}/{n_tiles} in safe mode…')
            idx_done, arr_path = _run_disk_tile(job)
            _record_completed(idx_done, arr_path)
    else:
        for job in pending:
            idx, arr_path = _run_disk_tile(job)
            _record_completed(idx, arr_path)
    if any((path is None for path in tile_mm_paths)):
        raise RuntimeError(f'{filter_code}: one or more tile reprojections did not complete.')
    final_array_name = str(old_manifest.get('final_array') or '')
    final_array_path = os.path.join(tmp_dir, final_array_name) if final_array_name else ''
    reuse_combined = resume_cache and old_manifest.get('stage') in ('combined', 'writing_fits', 'writing_maps') and final_array_path and os.path.isfile(final_array_path) and (os.path.getsize(final_array_path) == expected_raw_bytes)
    if reuse_combined:
        print(f'[Mosaic] {filter_code}: resume cache reused completed combine', flush=True)
        mosaic = np.memmap(final_array_path, dtype='float32', mode='r+', shape=tuple(shape_out))
    else:
        if progress_cb:
            progress_cb(n_tiles * 2, n_tiles * 3 + 2, f'Matching overlap backgrounds for {filter_code}...')
        corrections = _estimate_overlap_plane_corrections(tile_mm_paths, tuple(shape_out), filter_code=filter_code, nearby_edges=True)
        if combine in ('mean', 'average', 'feather'):
            if combine == 'feather':
                print(f'[Mosaic] {filter_code}: memory-safe seam blend enabled (overlap plane matching + streaming mean)', flush=True)
            mosaic = _nanmean_memmap(tile_mm_paths, tuple(shape_out), progress_cb=progress_cb, filter_code=filter_code, corrections=corrections)
        else:
            mosaic = _nanmedian_memmap(tile_mm_paths, tuple(shape_out), progress_cb=progress_cb, filter_code=filter_code, corrections=corrections)
        _update_recovery_manifest(tmp_dir, stage='combined', final_array=os.path.basename(os.fspath(getattr(mosaic, 'filename', '') or '')), raw_bytes=expected_raw_bytes)
    return (mosaic, tmp_dir, tile_mm_paths)


def _combine_in_memory_mosaic(combine, data, filter_code, hdr, inputs, n_tiles, progress_cb, shape_out, wcs_out, workers, block_workers=1):
    tiles = [None] * n_tiles
    done_count = [0]

    def _reproject_one(args):
        idx, data, hdr = args
        arr = np.empty(shape_out, dtype=np.float32)
        fp = np.empty(shape_out, dtype=np.float32)
        reproject_interp(
            (data, hdr), wcs_out, shape_out=shape_out,
            output_array=arr, output_footprint=fp,
            block_size=(512, 512), parallel=block_workers if block_workers > 1 else False,
            roundtrip_coords=_mosaic_roundtrip_required(hdr, wcs_out, data.shape, shape_out),
        )
        arr[fp <= 0.5] = np.nan
        return idx, arr
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_reproject_one, (i, d, h)): i for i, (d, h) in enumerate(inputs)}
        for fut in as_completed(futs):
            idx, tile = fut.result()
            tiles[idx] = tile
            done_count[0] += 1
            if progress_cb:
                progress_cb(n_tiles + done_count[0], n_tiles * 2 + 2, f'Reprojected {filter_code} tile {done_count[0]}/{n_tiles}…')
    if progress_cb:
        progress_cb(n_tiles * 2 + 1, n_tiles * 2 + 2, f'Robust overlap normalization of {n_tiles} tile(s)…')
    tiles = _sky_match_tiles(tiles, inplace=True)
    if combine == 'feather':
        mosaic = _feather_mosaic(tiles)
    elif combine in ('mean', 'average'):
        total = np.zeros(shape_out, dtype=np.float32)
        count = np.zeros(shape_out, dtype=np.uint32)
        for tile in tiles:
            valid = np.isfinite(tile)
            np.add(total, tile, out=total, where=valid)
            count += valid
        mosaic = np.full(shape_out, np.nan, dtype=np.float32)
        np.divide(total, count, out=mosaic, where=count > 0)
    else:
        if progress_cb:
            progress_cb(n_tiles * 2 + 1, n_tiles * 2 + 2, f'nanmedian across {n_tiles} tile(s)…')
        with warnings.catch_warnings():
            warnings.filterwarnings('ignore', 'All-NaN slice', RuntimeWarning)
            mosaic = np.nanmedian(np.stack(tiles, axis=0), axis=0).astype(np.float32, copy=False)
    return (mosaic, tiles)


def _combine_coadd_mosaic(combine, filter_code, inputs, n_tiles, progress_cb, shape_out, wcs_out, workers):
    if progress_cb:
        progress_cb(n_tiles + 1, n_tiles + 2, f'Coadding {filter_code} ({combine}, workers={workers})…')
    try:
        mosaic, _fp = reproject_and_coadd(inputs, wcs_out, shape_out=shape_out, reproject_function=reproject_interp, combine_function=combine, block_sizes=[(512, 512)] * len(inputs), parallel=workers if workers > 1 else False)
    except TypeError:
        mosaic, _fp = reproject_and_coadd(inputs, wcs_out, shape_out=shape_out, reproject_function=reproject_interp, combine_function=combine)
    mosaic = np.asarray(mosaic, dtype=np.float32)
    return (mosaic, _fp)


def _write_mosaic_products(combine, downscale, filter_code, job_paths, mosaic, n_tiles, out_dir, out_path, progress_cb, save_coverage_maps, scratch_dir, shape_out, sky_region, tile_mm_paths, tiles, tmp_dir, use_disk, wcs_out, workers, coverage_array=None):
    os.makedirs(out_dir, exist_ok=True)
    hdr_out = wcs_out.to_header()
    hdr_out['FILTER'] = filter_code
    hdr_out['BGNORM'] = ('robust-overlap-v1', 'Relative additive overlap normalization')
    hdr_out['HISTORY'] = f'Mosaic {n_tiles} tiles combine={combine} workers={workers} memory_safe={use_disk} turbo={bool(use_disk and workers > 1)} downscale={downscale:g}'
    if sky_region:
        hdr_out['SKYCROP'] = (True, 'Interactive WCS coverage area applied')
        hdr_out['SKYROT'] = (float(sky_region.get('rotation_deg', 0.0) or 0.0), 'Interactive selection rotation deg')
        hdr_out['HISTORY'] = 'Output footprint selected interactively in WCS Coverage'
    mosaic_arr = np.asarray(mosaic, dtype=np.float32)
    if tmp_dir:
        _update_recovery_manifest(tmp_dir, stage='writing_fits')
    if isinstance(mosaic, np.memmap) and getattr(mosaic, 'filename', None):
        _stream_float32_to_fits(os.fspath(mosaic.filename), tuple(shape_out), hdr_out, out_path, progress_cb=lambda done, total, msg: progress_cb(n_tiles * 3 + done, n_tiles * 3 + total, msg) if progress_cb else None)
    else:
        temp_out = out_path + '.recovering'
        fits.PrimaryHDU(mosaic_arr, header=hdr_out).writeto(temp_out, overwrite=True)
        with fits.open(temp_out, memmap=True, lazy_load_hdus=True) as check:
            if tuple(check[0].shape) != tuple(shape_out):
                raise RuntimeError('Temporary mosaic FITS failed shape verification.')
        os.replace(temp_out, out_path)
    if save_coverage_maps:
        if tmp_dir:
            _update_recovery_manifest(tmp_dir, stage='writing_maps')
        quality_work_dir = tmp_dir or os.path.join(os.path.abspath(os.fspath(scratch_dir or out_dir)), f'_quality_{safe_filename(filter_code)}_{int(time.time())}')
        if use_disk and tile_mm_paths:
            quality_contributors = tile_mm_paths
            quality_array = None
        elif tiles:
            quality_contributors = tiles
            quality_array = None
        else:
            quality_contributors = []
            quality_array = coverage_array
        coverage_path, weight_path = _write_mosaic_quality_maps(quality_contributors, shape_out, wcs_out, out_dir, filter_code, quality_work_dir, progress_cb=progress_cb, coverage_array=quality_array, job_paths=job_paths)
        print(f'[Mosaic] quality maps written: {coverage_path}, {weight_path}', flush=True)
        if not tmp_dir:
            try:
                shutil.rmtree(quality_work_dir, ignore_errors=True)
            except EXPECTED_OPERATION_ERRORS:
                LOGGER.debug("Best-effort operation failed", exc_info=True)


def mosaic_filter_group(paths, out_dir, filter_code, combine='median', progress_cb=None, n_workers=1, downscale=1.0, scratch_dir=None, sky_region=None, save_coverage_maps=False, resume_cache=True, turbo_fullres=False, job_paths: Optional[JobPaths]=None):
    """
    Mosaic all tile paths for one filter. Returns output FITS path.

    Memory-safe behavior:
    - Small mosaics keep the old fast in-memory path.
    - Large median mosaics use sequential disk-backed reprojection + chunked
      median, so a 13-tile 11418×14168 mosaic does not need 10+ GiB RAM.
    - Worker count is automatically clamped for large outputs.
    - Optional Turbo mode runs up to three independent disk-backed tile
      reprojections concurrently and retries failures sequentially.
    """
    if isinstance(out_dir, JobPaths):
        job_paths = out_dir
        out_dir = os.fspath(job_paths.mosaics)
    if not HAVE_MOSAIC:
        raise RuntimeError('reproject.mosaicking not available.\nRun: pip install --upgrade reproject\n\n' + MOSAIC_IMPORT_ERR)
    n_tiles = len(paths)
    inputs = []
    tile_names = []
    for i, p in enumerate(paths):
        if progress_cb:
            progress_cb(i, n_tiles * 2 + 2, f'Loading {filter_code} tile {i + 1}/{n_tiles}')
        data, hdr = _load_mosaic_2d_and_header(p)
        inputs.append((data, hdr))
        tile_names.append(os.path.basename(p))
    if progress_cb:
        progress_cb(n_tiles, n_tiles * 2 + 2, f'Computing optimal WCS for {filter_code}…')
    valid_inputs = []
    valid_names = []
    for i, (data, hdr) in enumerate(inputs):
        if _has_valid_celestial_wcs(data, hdr):
            valid_inputs.append((data, hdr))
            valid_names.append(tile_names[i])
        else:
            print(f'[Mosaic] WARNING: {filter_code} tile {i + 1} ({tile_names[i]}) skipped — invalid or non-celestial WCS', flush=True)
    if not valid_inputs:
        raise RuntimeError(f"Group '{filter_code}': none of the {n_tiles} tile(s) have a valid 2D celestial WCS — cannot mosaic. These files may be weight maps, context images, or catalogs.")
    if len(valid_inputs) < n_tiles:
        print(f'[Mosaic] {filter_code}: using {len(valid_inputs)}/{n_tiles} tiles (others have no valid sky WCS)', flush=True)
    inputs = valid_inputs
    tile_names = valid_names
    n_tiles = len(inputs)
    inputs, tile_names, dropped = _cluster_tiles_by_sky(inputs, tile_names)
    if dropped:
        print(f'[Mosaic] {filter_code}: excluded {len(dropped)} tile(s) that do not overlap the main pointing:', flush=True)
        for nm, sep_txt in dropped:
            print(f'[Mosaic]   - {nm}  ({sep_txt})', flush=True)
        if progress_cb:
            progress_cb(n_tiles, n_tiles * 2 + 2, f'{filter_code}: excluded {len(dropped)} off-target tile(s)')
    n_tiles = len(inputs)
    if n_tiles == 0:
        raise RuntimeError(f"Group '{filter_code}': no sky-coherent tiles remain to mosaic.")
    try:
        wcs_out, shape_out = find_optimal_celestial_wcs(inputs)
    except MemoryError as exc:
        raise _friendly_wcs_memory_error(filter_code, n_tiles, exc)
    except EXPECTED_OPERATION_ERRORS as exc:
        if 'emory allocation' in str(exc) or 'wcss2p' in str(exc):
            raise _friendly_wcs_memory_error(filter_code, n_tiles, exc)
        raise
    downscale = _normalise_mosaic_downscale(downscale, default=1.0)
    if downscale < 0.999999:
        old_h, old_w = (int(shape_out[0]), int(shape_out[1]))
        wcs_out, shape_out = _downscale_celestial_wcs(wcs_out, shape_out, downscale)
        new_h, new_w = (int(shape_out[0]), int(shape_out[1]))
        print(f'[Mosaic] {filter_code}: output scale {downscale:g}x applied ({old_w}x{old_h} → {new_w}x{new_h})', flush=True)
        if progress_cb:
            progress_cb(n_tiles, n_tiles * 2 + 2, f'{filter_code}: output scale {downscale:g}x ({new_w}×{new_h})')
    if sky_region:
        before_h, before_w = (int(shape_out[0]), int(shape_out[1]))
        wcs_out, shape_out, crop_bounds = _crop_wcs_to_sky_region(wcs_out, shape_out, sky_region)
        after_h, after_w = (int(shape_out[0]), int(shape_out[1]))
        print(f'[Mosaic] {filter_code}: interactive sky crop applied ({before_w}x{before_h} -> {after_w}x{after_h})', flush=True)
        if progress_cb:
            progress_cb(n_tiles, n_tiles * 2 + 2, f'{filter_code}: selected sky area {after_w}x{after_h} px')
    else:
        crop_bounds = None
    h_out, w_out = (int(shape_out[0]), int(shape_out[1]))
    if h_out * w_out > _MOSAIC_MAX_CANVAS_PX:
        gpx = h_out * w_out / 1000000000.0
        raise RuntimeError(f"Group '{filter_code}': computed mosaic canvas is {w_out} × {h_out} px ({gpx:.1f} gigapixels) — far beyond anything sensible. The {n_tiles} tile(s) almost certainly cover different sky positions. Check the group in the Mosaic tab and remove tiles that do not belong.")
    use_disk, disk_reason = _mosaic_disk_mode_needed(n_tiles, shape_out, combine)
    workers = _safe_mosaic_workers(n_workers, n_tiles, shape_out)
    if use_disk:
        workers = _safe_disk_turbo_workers(n_workers, n_tiles, shape_out) if turbo_fullres else 1
    block_workers = 1 if use_disk else _safe_mosaic_block_workers(n_workers, workers, n_tiles, shape_out)
    turbo_text = 'turbo' if use_disk and workers > 1 else 'safe'
    print(f'[Mosaic] {filter_code}: {n_tiles} tile(s), output={w_out}x{h_out}, combine={combine}, workers={workers}, memory_safe={use_disk}, disk_mode={turbo_text} ({disk_reason})', flush=True)
    if block_workers > 1:
        print(f'[Mosaic] {filter_code}: {block_workers} block threads per tile, at most {workers * block_workers} processing threads', flush=True)
    if progress_cb and use_disk:
        progress_cb(n_tiles, n_tiles * 3 + 2, f'Parallel memory-safe mosaic: {workers} tile workers for {filter_code} ({w_out}×{h_out})' if workers > 1 else f'Memory-safe mosaic enabled for {filter_code}: {w_out}×{h_out}, {n_tiles} tile(s)')
    mosaic_name = f'mosaic_{safe_filename(filter_code)}.fits'
    out_path = os.fspath(job_paths.final_mosaic_fits(filter_code, mosaic_name) if job_paths is not None else os.path.join(out_dir, mosaic_name))
    mosaic = None
    tmp_dir = None
    tile_mm_paths = None
    tiles = None
    _fp = None
    output_written = False
    try:
        if use_disk and combine in ('median', 'mean', 'average', 'feather'):
            mosaic, tmp_dir, tile_mm_paths = _combine_disk_backed_mosaic(combine, crop_bounds, downscale, filter_code, inputs, n_tiles, out_dir, out_path, paths, progress_cb, resume_cache, scratch_dir, shape_out, sky_region, turbo_fullres, wcs_out, workers)
        elif combine in ('median', 'feather', 'mean', 'average'):
            mosaic, tiles = _combine_in_memory_mosaic(combine, data, filter_code, hdr, inputs, n_tiles, progress_cb, shape_out, wcs_out, workers, block_workers=block_workers)
        else:
            mosaic, _fp = _combine_coadd_mosaic(combine, filter_code, inputs, n_tiles, progress_cb, shape_out, wcs_out, workers)
        _write_mosaic_products(combine, downscale, filter_code, job_paths, mosaic, n_tiles, out_dir, out_path, progress_cb, save_coverage_maps, scratch_dir, shape_out, sky_region, tile_mm_paths, tiles, tmp_dir, use_disk, wcs_out, workers, coverage_array=_fp)
        output_written = True
        if tmp_dir:
            _update_recovery_manifest(tmp_dir, stage='complete')
        print(f'[Mosaic] written: {out_path}', flush=True)
        return out_path
    finally:
        try:
            if isinstance(locals().get('mosaic'), np.memmap):
                mosaic.flush()
                mmap_obj = getattr(mosaic, '_mmap', None)
                if mmap_obj is not None:
                    mmap_obj.close()
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
        try:
            if output_written and tmp_dir and os.path.isdir(tmp_dir):
                shutil.rmtree(tmp_dir, ignore_errors=True)
            elif tmp_dir and os.path.isdir(tmp_dir):
                print(f'[Mosaic] Recovery scratch preserved after failure: {tmp_dir}', flush=True)
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)


def pick_reference_from_mosaic_paths(mosaic_paths):
    """
    Sort filters by wavelength; pick shortest (finest PSF) as reference.
    Returns (ref_code, ref_path, items_list).
    items_list = [{"path": ..., "name": ...}, ...]
    """
    scored = []
    for code, path in mosaic_paths.items():
        wl = filter_code_to_microns(code)
        scored.append((wl if wl is not None else 9999.0, code, path))
    scored.sort()
    if not scored:
        raise ValueError("No mosaic paths provided.")
    _wl, ref_code, ref_path = scored[0]
    items = [{"path": p, "name": safe_filename(c)} for _w, c, p in scored[1:]]
    return ref_code, ref_path, items

__all__ = [name for name in globals() if not name.startswith('__')]
