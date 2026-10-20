from __future__ import annotations

from tsdp2._deps import *
from tsdp2.logging_config import LOGGER
from tsdp2.astronomy.coverage import _sample_valid_pixel_mask_on_preview
from tsdp2.astronomy.fits_io import _mosaic_input_display_name, _parse_mosaic_hdu_ref
from tsdp2.astronomy.regions import _crop_wcs_to_sky_region, _downscale_celestial_wcs, _mosaic_filter_scale
from tsdp2.recovery.manifests import _recovery_header_inputs
from tsdp2.utilities.memory import _available_memory_bytes, _format_gib, _mosaic_working_bytes, _mosaic_memory_budget

_MOSAIC_MAX_CANVAS_PX = 2_000_000_000


_MOSAIC_CLUSTER_GAP_FACTOR = 1.6


def _tile_sky_center_radius(data, hdr):
    """Return (SkyCoord center, radius_deg) for one tile, or (None, 0)."""
    try:
        w = WCS(hdr, naxis=2)
        ny, nx = int(data.shape[-2]), int(data.shape[-1])
        # Astropy calc_footprint axes are (NAXIS1, NAXIS2) == (width, height).
        fp = w.calc_footprint(axes=(nx, ny))
        if fp is None or not np.all(np.isfinite(fp)):
            return None, 0.0
        from astropy.coordinates import SkyCoord as _SC
        import astropy.units as u
        corners = _SC(ra=fp[:, 0] * u.deg, dec=fp[:, 1] * u.deg)
        # Use the first corner as an anchor to average small offsets safely
        center = _SC(ra=np.mean(fp[:, 0]) * u.deg, dec=np.mean(fp[:, 1]) * u.deg)
        radius = float(np.max(center.separation(corners).deg))
        return center, radius
    except EXPECTED_OPERATION_ERRORS:
        return None, 0.0


def _cluster_tiles_by_sky(inputs, names):
    """Split tiles into sky-overlap clusters. Returns (kept_inputs, kept_names,
    dropped_report). Tiles whose footprints are near/overlapping stay together;
    tiles pointing elsewhere on the sky are excluded from the largest cluster.

    This prevents find_optimal_celestial_wcs from building a canvas that spans
    unrelated pointings — the cause of wcslib 'Memory allocation failed'."""
    n = len(inputs)
    if n <= 1:
        return list(inputs), list(names), []

    geo = []   # (center, radius) or (None, 0)
    for (data, hdr) in inputs:
        geo.append(_tile_sky_center_radius(data, hdr))

    # Union-find style greedy clustering
    cluster_id = list(range(n))

    def find(i):
        while cluster_id[i] != i:
            cluster_id[i] = cluster_id[cluster_id[i]]
            i = cluster_id[i]
        return i

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            cluster_id[rb] = ra

    for i in range(n):
        ci, ri = geo[i]
        if ci is None:
            continue
        for j in range(i + 1, n):
            cj, rj = geo[j]
            if cj is None:
                continue
            sep = float(ci.separation(cj).deg)
            if sep <= (ri + rj) * _MOSAIC_CLUSTER_GAP_FACTOR:
                union(i, j)

    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    # Largest cluster wins; ties resolved by first appearance
    best = max(groups.values(), key=lambda idxs: (len(idxs), -idxs[0]))
    kept = sorted(best)
    dropped_report = []
    if len(kept) < n:
        bc, _ = geo[kept[0]]
        for i in range(n):
            if i in kept:
                continue
            ci, _ri = geo[i]
            sep_txt = "unknown offset"
            if bc is not None and ci is not None:
                sep = float(bc.separation(ci).deg)
                sep_txt = (f"{sep * 60:.1f} arcmin away"
                           if sep < 1.5 else f"{sep:.2f} deg away")
            dropped_report.append((names[i], sep_txt))

    return ([inputs[i] for i in kept],
            [names[i] for i in kept],
            dropped_report)


def _friendly_wcs_memory_error(filter_code, n_tiles, exc):
    return RuntimeError(
        f"Group '{filter_code}': the combined sky area of the {n_tiles} tile(s) "
        f"is too large to mosaic — WCS grid allocation failed.\n\n"
        f"This usually means the group mixes observations of DIFFERENT sky "
        f"positions (separate targets or visits). Open the Mosaic tab, check "
        f"the group, and remove tiles that do not belong together.\n\n"
        f"Original error: {exc}"
    )


def _mosaic_input_signatures(paths):
    signatures = []
    for path in paths:
        real_path, hdu_index, label = _parse_mosaic_hdu_ref(path)
        try:
            stat = os.stat(real_path)
            size = int(stat.st_size)
            mtime_ns = int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1e9)))
        except EXPECTED_OPERATION_ERRORS:
            size, mtime_ns = -1, -1
        signatures.append({
            "path": os.path.abspath(real_path), "hdu": hdu_index,
            "label": label, "size": size, "mtime_ns": mtime_ns,
        })
    return signatures


def _mosaic_cache_key(paths, filter_code, shape_out, wcs_out, combine, downscale,
                      sky_region=None):
    payload = {
        "inputs": _mosaic_input_signatures(paths),
        "filter": str(filter_code), "shape": [int(x) for x in shape_out],
        "wcs": wcs_out.to_header().tostring(sep="\n", endcard=True, padding=False),
        "combine": str(combine), "downscale": float(downscale),
        "background_method": "robust-overlap-v1",
        "input_mask_method": "decam-saturation-v1",
        "sky_region": sky_region or None,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest(), payload["inputs"]


def _nearest_existing_folder(path):
    candidate = Path(os.path.abspath(os.fspath(path or ".")))
    if candidate.suffix and not candidate.is_dir():
        candidate = candidate.parent
    while not candidate.exists() and candidate.parent != candidate:
        candidate = candidate.parent
    return str(candidate)


def _build_mosaic_preflight(groups, out_dir, scratch_dir, global_scale=1.0,
                            filter_scales=None, save_coverage_maps=True,
                            sky_region=None, extra_clean_copy=False, combine="feather"):
    """Header-only geometry plus sampled real-data and disk-space inspection."""
    filter_scales = dict(filter_scales or {})
    results = []
    warnings_out = []
    total_output = 0
    total_scratch = 0
    peak_memory = 0
    for filter_code, paths in sorted((groups or {}).items()):
        inputs, names = _recovery_header_inputs(paths)
        inputs, names, dropped = _cluster_tiles_by_sky(inputs, names)
        if not inputs:
            warnings_out.append(f"{filter_code}: no usable celestial-WCS tiles")
            continue
        wcs_out, shape_out = find_optimal_celestial_wcs(inputs)
        scale = _mosaic_filter_scale(filter_code, global_scale, filter_scales)
        wcs_out, shape_out = _downscale_celestial_wcs(wcs_out, shape_out, scale)
        if sky_region:
            wcs_out, shape_out, _bounds = _crop_wcs_to_sky_region(
                wcs_out, shape_out, sky_region)
        height, width = int(shape_out[0]), int(shape_out[1])
        pixels = height * width
        # Science plus optional separate coverage and relative-weight products.
        product_count = (3 if save_coverage_maps else 1) + (1 if extra_clean_copy else 0)
        output_bytes = pixels * 4 * product_count
        # Reprojected tiles + combined mosaic + one reusable quality-map scratch.
        scratch_bytes = pixels * 4 * (len(inputs) + 1 + (1 if save_coverage_maps else 0))
        working_memory_bytes = _mosaic_working_bytes(len(inputs), shape_out, combine)
        total_output += output_bytes
        total_scratch += scratch_bytes
        peak_memory = max(peak_memory, working_memory_bytes)

        tile_stats = []
        for path in paths:
            _mask, fraction = _sample_valid_pixel_mask_on_preview(
                path, wcs_out, 32, 32, max_samples=150, return_stats=True)
            fraction = float(fraction)
            tile_stats.append((path, fraction))
            if fraction < 70.0:
                warnings_out.append(
                    f"{filter_code}: {_mosaic_input_display_name(path)} is only "
                    f"{fraction:.1f}% usable data")
        for dropped_name, reason in dropped:
            warnings_out.append(f"{filter_code}: excluded {dropped_name} ({reason})")
        results.append({
            "filter": filter_code, "tiles": len(inputs), "width": width,
            "height": height, "pixels": pixels, "scale": scale,
            "output_bytes": output_bytes, "scratch_bytes": scratch_bytes,
            "working_memory_bytes": working_memory_bytes,
            "tile_stats": tile_stats,
        })

    available_memory = _available_memory_bytes()
    memory_pressure = bool(
        available_memory is not None and peak_memory > _mosaic_memory_budget(available_memory)
    )
    if memory_pressure:
        warnings_out.append(
            f"Estimated working memory is {_format_gib(peak_memory)} while "
            f"{_format_gib(available_memory)} is currently available. "
            "TSDP will use memory-safe disk-backed processing and reduce workers."
        )
    out_folder = _nearest_existing_folder(out_dir)
    scratch_folder = _nearest_existing_folder(scratch_dir or out_dir)
    out_free = int(shutil.disk_usage(out_folder).free)
    scratch_free = int(shutil.disk_usage(scratch_folder).free)
    # Allow headroom for atomic output files and filesystem overhead.
    output_needed = int(total_output * 1.20)
    scratch_needed = int(total_scratch * 1.10)
    same_volume = os.path.splitdrive(out_folder)[0].lower() == os.path.splitdrive(scratch_folder)[0].lower()
    if same_volume:
        combined_needed = output_needed + scratch_needed
        space_ok = out_free >= combined_needed
        if not space_ok:
            warnings_out.append(
                f"Output and scratch share a drive: need about {_format_gib(combined_needed)}, "
                f"only {_format_gib(out_free)} free")
    else:
        space_ok = out_free >= output_needed and scratch_free >= scratch_needed
        if out_free < output_needed:
            warnings_out.append(
                f"Output drive needs about {_format_gib(output_needed)}, "
                f"only {_format_gib(out_free)} free")
        if scratch_free < scratch_needed:
            warnings_out.append(
                f"Scratch drive needs about {_format_gib(scratch_needed)}, "
                f"only {_format_gib(scratch_free)} free")
    return {
        "filters": results, "warnings": warnings_out,
        "total_output": total_output, "total_scratch": total_scratch,
        "output_needed": output_needed, "scratch_needed": scratch_needed,
        "output_free": out_free, "scratch_free": scratch_free,
        "out_folder": out_folder, "scratch_folder": scratch_folder,
        "same_volume": same_volume, "space_ok": bool(space_ok),
        "selected_area": bool(sky_region),
        "peak_memory": peak_memory,
        "available_memory": available_memory,
        "memory_pressure": memory_pressure,
    }


def _mosaic_preflight_text(report):
    lines = ["MOSAIC INSPECTION / PREFLIGHT", ""]
    lines.append("Area: " + ("interactive sky selection" if report.get("selected_area")
                              else "full automatic footprint"))
    lines.append("")
    available_memory = report.get("available_memory")
    lines.extend(
        [
            "MEMORY",
            f"Estimated peak working memory: {_format_gib(report.get('peak_memory', 0))}",
            "Currently available: "
            + (_format_gib(available_memory) if available_memory is not None else "unknown"),
            "Mode: "
            + (
                "memory-safe disk-backed processing with automatic worker reduction"
                if report.get("memory_pressure")
                else "normal automatic memory management"
            ),
            "",
        ]
    )
    for item in report.get("filters") or []:
        lines.append(
            f"{item['filter']}: {item['width']:,} x {item['height']:,} px | "
            f"{item['tiles']} tile(s) | scale {item['scale']:g}x")
        lines.append(
            f"  final products ~{_format_gib(item['output_bytes'])}; "
            f"peak resumable scratch ~{_format_gib(item['scratch_bytes'])}")
        for path, fraction in item.get("tile_stats") or []:
            state = "PARTIAL" if fraction < 95.0 else "good"
            lines.append(
                f"  {state:7s} {fraction:5.1f}% data  {_mosaic_input_display_name(path)}")
    lines += ["", "DISK", f"Output: {report.get('out_folder')} | "
              f"free {_format_gib(report.get('output_free', 0))} | "
              f"needed ~{_format_gib(report.get('output_needed', 0))}",
              f"Scratch: {report.get('scratch_folder')} | "
              f"free {_format_gib(report.get('scratch_free', 0))} | "
              f"needed ~{_format_gib(report.get('scratch_needed', 0))}"]
    if report.get("same_volume"):
        lines.append("Output and scratch are on the same drive; both estimates are combined.")
    warnings_out = report.get("warnings") or []
    lines += ["", "WARNINGS" if warnings_out else "No blocking warnings."]
    lines.extend(f"- {text}" for text in warnings_out)
    return "\n".join(lines)

__all__ = [name for name in globals() if not name.startswith('__')]
