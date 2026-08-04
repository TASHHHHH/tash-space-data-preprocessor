from __future__ import annotations

from tsdp2._deps import *
from tsdp2.logging_config import LOGGER
from tsdp2.astronomy.fits_io import _iter_requested_image_hdus, _make_mosaic_hdu_ref, _parse_mosaic_hdu_ref
from tsdp2.astronomy.reference import parse_filter_code

_NON_SCIENCE_MOSAIC_EXTNAMES = {
    "WHT", "WEIGHT", "CON", "CTX", "COVERAGE", "DQ", "DQMASK", "MASK",
    "ERR", "ERROR", "VAR", "VAR_POISSON", "VAR_RNOISE", "VAR_FLAT",
}


def _expand_mosaic_mef_file(path: str):
    """Expand a tiled MEF into virtual per-HDU inputs for the Mosaic tab.

    Only files with at least two celestial image HDUs named TILE* are expanded.
    HST/JWST SCI/WHT/CTX products therefore retain their existing behavior.
    No pixel arrays are loaded and no duplicate tile files are written.
    """
    real_path, requested_hdu, _label = _parse_mosaic_hdu_ref(path)
    if requested_hdu is not None:
        return [path], None

    with fits.open(real_path, memmap=True, lazy_load_hdus=True) as hdul:
        image_hdus = []
        for idx, hdu in enumerate(hdul[1:], 1):
            hdr = hdu.header
            if int(hdr.get("NAXIS", 0) or 0) < 2:
                continue
            nx = int(hdr.get("NAXIS1", 0) or 0)
            ny = int(hdr.get("NAXIS2", 0) or 0)
            if nx <= 0 or ny <= 0:
                continue
            extname = str(hdr.get("EXTNAME", f"HDU{idx}")).strip() or f"HDU{idx}"
            if extname.upper() in _NON_SCIENCE_MOSAIC_EXTNAMES:
                continue
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    if not WCS(hdr, naxis=2).has_celestial:
                        continue
            except EXPECTED_OPERATION_ERRORS:
                continue
            extver = str(hdr.get("EXTVER", "")).strip()
            label = f"{extname} {extver}".strip() if extver else extname
            image_hdus.append((idx, label, extname.upper().startswith("TILE")))

    tile_hdus = [(idx, label) for idx, label, is_tile in image_hdus if is_tile]
    if len(tile_hdus) < 2:
        if len(image_hdus) < 2:
            return [path], None
        chosen_hdus = [(idx, label) for idx, label, _is_tile in image_hdus]
        expansion_kind = "frames"
    else:
        chosen_hdus = tile_hdus
        expansion_kind = "tiles"

    refs = [_make_mosaic_hdu_ref(real_path, idx, label)
            for idx, label in chosen_hdus]
    return refs, {
        "source": real_path,
        "count": len(refs),
        "labels": [label for _idx, label in chosen_hdus],
        "kind": expansion_kind,
    }


def expand_mosaic_input_paths(paths):
    """Return (expanded_paths, expansion_reports, errors) for Mosaic-tab input."""
    expanded = []
    reports = []
    errors = []
    for path in paths:
        try:
            refs, report = _expand_mosaic_mef_file(path)
            expanded.extend(refs)
            if report:
                reports.append(report)
        except EXPECTED_OPERATION_ERRORS as exc:
            expanded.append(path)
            errors.append((os.fspath(path), str(exc)))
    return expanded, reports, errors


def group_fits_by_filter(fits_paths, number_unknowns=False):
    """Return {filter_code: [path, ...]} by reading FITS FILTER headers.

    Strategy (in order):
    1. Primary header (extension 0) — HST multi-ext FITS store FILTER there
       even though NAXIS=0 (no pixel data).
    2. Any image extension header (NAXIS >= 2).
    3. Filename regex fallback.
    """
    groups = {}
    unknown_sets = {}
    unknown_counter = 0
    for path in fits_paths:
        code = None
        real_path, requested_hdu, _label = _parse_mosaic_hdu_ref(path)
        try:
            with fits.open(real_path, memmap=True, lazy_load_hdus=True) as hdul:
                # ── 1. Primary header first (covers all HST drizzle products) ──
                ph = hdul[0].header
                if requested_hdu is not None:
                    for hdu in _iter_requested_image_hdus(hdul, requested_hdu):
                        hdr = hdu.header
                        for key in ("FILTER", "FILTER1", "FILTER2", "PUPIL"):
                            code = parse_filter_code(str(hdr.get(key, "")))
                            if code:
                                break
                        if code:
                            break
                if not code:
                    for key in ("FILTER", "FILTER1", "FILTER2", "PUPIL"):
                        code = parse_filter_code(str(ph.get(key, "")))
                        if code:
                            break

                # ── 2. Image extension headers as fallback ────────────────────
                if not code and requested_hdu is None:
                    for hdu in _iter_requested_image_hdus(hdul, requested_hdu):
                        hdr = hdu.header
                        if hdr.get("NAXIS", 0) < 2:
                            continue
                        for key in ("FILTER", "FILTER1", "FILTER2", "PUPIL"):
                            code = parse_filter_code(str(hdr.get(key, "")))
                            if code:
                                break
                        if code:
                            break
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)

        # ── 3. Filename fallback ──────────────────────────────────────────────
        if not code:
            code = parse_filter_code(os.path.basename(real_path))
        if not code:
            if number_unknowns:
                label_upper = str(_label or "").strip().upper()
                if requested_hdu is not None and label_upper.startswith("TILE"):
                    identity = (os.path.normcase(os.path.abspath(real_path)), "TILES")
                else:
                    identity = (os.path.normcase(os.path.abspath(real_path)),
                                int(requested_hdu) if requested_hdu is not None else None)
                if identity not in unknown_sets:
                    unknown_counter += 1
                    unknown_sets[identity] = f"UNKNOWN {unknown_counter}"
                code = unknown_sets[identity]
            else:
                code = "UNKNOWN"
        groups.setdefault(code, []).append(path)
    return groups


def _prefer_drc_over_drz(paths):
    """Drop *_drz.fits when a *_drc.fits with the same base name exists.

    HST drizzle pipelines produce both:
      _drz.fits — simple drizzle combine (cosmic rays not removed)
      _drc.fits — drizzle + AstroDrizzle cosmic-ray cleaning  ← keep this one

    Mixing both into a mosaic stacks the same pointing twice.
    """
    drc_bases = set()
    for p in paths:
        bn = os.path.basename(p).lower()
        if bn.endswith("_drc.fits"):
            drc_bases.add(bn[:-9])   # strip "_drc.fits"

    kept, dropped = [], []
    for p in paths:
        bn = os.path.basename(p).lower()
        if bn.endswith("_drz.fits") and bn[:-9] in drc_bases:
            dropped.append(os.path.basename(p))
        else:
            kept.append(p)

    if dropped:
        print(f"[Mosaic] DRZ→DRC dedup: dropped {len(dropped)} _drz file(s) "
              f"(DRC version exists): {', '.join(dropped)}", flush=True)
    return kept


def _sky_match_tiles(tiles):
    """
    Shift each tile so all share the same sigma-clipped sky level as tile 0.
    Works on the reprojected tiles (may contain NaN where there's no data).
    """
    if len(tiles) <= 1:
        return list(tiles)

    def _sky(arr):
        finite = arr[np.isfinite(arr)].ravel()
        if finite.size < 200:
            return 0.0
        med = float(np.median(finite))
        std = float(np.std(finite))
        clipped = finite[np.abs(finite - med) < 3.0 * std]
        return float(np.median(clipped)) if clipped.size > 10 else med

    ref_sky = _sky(tiles[0])
    matched = [tiles[0]]
    for tile in tiles[1:]:
        offset = ref_sky - _sky(tile)
        if abs(offset) > 0:
            print(f"[Mosaic] sky-match: tile offset = {offset:+.4f} e/s", flush=True)
        matched.append(tile + offset)
    return matched


def _feather_mosaic(tiles):
    """
    Distance-weighted feathered blend across all tiles.
    Each tile is weighted by its EDT distance from the nearest NaN edge,
    so coverage centres are trusted more than tile boundaries.
    """
    # weight map: distance from nearest NaN / no-data pixel
    w_list, d_list = [], []
    for tile in tiles:
        valid = np.isfinite(tile).astype(np.float32)
        dist  = distance_transform_edt(valid).astype(np.float32)
        w_list.append(dist)
        # replace NaN with 0 for the weighted sum
        d_list.append(np.where(np.isfinite(tile), tile, 0.0).astype(np.float32))

    w_sum  = np.stack(w_list, axis=0).sum(axis=0)   # (H, W)
    blend  = (np.stack(d_list, axis=0)
              * np.stack(w_list, axis=0)).sum(axis=0)

    with np.errstate(invalid="ignore", divide="ignore"):
        result = np.where(w_sum > 0, blend / w_sum, np.nan)
    return result.astype(np.float32)

__all__ = [name for name in globals() if not name.startswith('__')]
