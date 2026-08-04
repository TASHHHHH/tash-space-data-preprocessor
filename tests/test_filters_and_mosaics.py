from __future__ import annotations

from tsdp2.astronomy.mosaic_geometry import _mosaic_cache_key
from tsdp2.astronomy.mosaic_inputs import group_fits_by_filter
from tsdp2.astronomy.reference import filter_code_to_microns, parse_filter_code


def test_filter_code_parsing_and_wavelength_sorting():
    assert parse_filter_code("JWST MIRI F770W") == "F770W"
    codes = ["F770W", "F200W", "F444W"]
    assert sorted(codes, key=filter_code_to_microns) == ["F200W", "F444W", "F770W"]


def test_mosaic_grouping_uses_fits_filter(fits_factory):
    first = fits_factory("first.fits", FILTER="F200W")
    second = fits_factory("second.fits", FILTER="F444W")
    groups = group_fits_by_filter([str(second), str(first)])
    assert set(groups) == {"F200W", "F444W"}
    assert groups["F200W"] == [str(first)]


def test_mosaic_cache_key_is_stable_and_input_sensitive(fits_factory, simple_wcs):
    first = fits_factory("first.fits", FILTER="F200W")
    key_a, signatures_a = _mosaic_cache_key(
        [str(first)], "F200W", (8, 8), simple_wcs, "median", 1.0
    )
    key_b, signatures_b = _mosaic_cache_key(
        [str(first)], "F200W", (8, 8), simple_wcs, "median", 1.0
    )
    key_c, _ = _mosaic_cache_key(
        [str(first)], "F200W", (8, 8), simple_wcs, "mean", 1.0
    )
    assert key_a == key_b
    assert signatures_a == signatures_b
    assert key_a != key_c
