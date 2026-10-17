"""Header-only output sizes shared by coverage controls and mosaic geometry."""
from __future__ import annotations

from astropy.io import fits
from astropy.wcs import WCS
from reproject.mosaicking import find_optimal_celestial_wcs

from tsdp2.astronomy.regions import (
    _crop_wcs_to_sky_region, _downscale_celestial_wcs, _mosaic_filter_scale,
)
from tsdp2._deps import EXPECTED_OPERATION_ERRORS
from tsdp2.logging_config import LOGGER


def build_coverage_mosaic_geometries(groups):
    # Lazy imports avoid coverage <-> mosaic_geometry's shared helper cycle.
    from tsdp2.astronomy.mosaic_geometry import _cluster_tiles_by_sky
    from tsdp2.recovery.manifests import _recovery_header_inputs

    geometries = {}
    for code, paths in sorted(groups.items()):
        inputs, names = [], []
        for path in paths:
            try:
                one_input, one_name = _recovery_header_inputs([path])
                inputs.extend(one_input)
                names.extend(one_name)
            except EXPECTED_OPERATION_ERRORS:
                LOGGER.debug("Skipped invalid coverage geometry input %s", path, exc_info=True)
        inputs, _names, _dropped = _cluster_tiles_by_sky(inputs, names)
        if not inputs:
            continue
        wcs, shape = find_optimal_celestial_wcs(inputs)
        geometries[code] = {
            "wcs": wcs,
            "shape": [int(v) for v in shape],
            "wcs_header": wcs.to_header().tostring(sep="\n", endcard=True, padding=False),
        }
    return geometries


def coverage_output_dimensions(geometries, global_scale=1.0, filter_scales=None, region=None):
    """Use the same scale-then-crop operations as the final mosaic writer."""
    results = {}
    for code, geometry in sorted(geometries.items()):
        scale = _mosaic_filter_scale(code, global_scale, filter_scales)
        wcs = geometry.get("wcs")
        if wcs is None:
            wcs = WCS(fits.Header.fromstring(geometry["wcs_header"], sep="\n"), naxis=2)
        wcs, shape = _downscale_celestial_wcs(wcs, geometry["shape"], scale)
        try:
            _wcs, shape, _bounds = _crop_wcs_to_sky_region(wcs, shape, region)
            results[code] = {"width": int(shape[1]), "height": int(shape[0]), "scale": scale}
        except ValueError as exc:
            results[code] = {"width": 0, "height": 0, "scale": scale, "error": str(exc)}
    return results
