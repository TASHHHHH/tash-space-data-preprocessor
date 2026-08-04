from __future__ import annotations

import numpy as np

from tsdp2.astronomy.alignment import _wcs_same_pixel_grid
from tsdp2.astronomy.fits_io import (
    _make_mosaic_hdu_ref,
    _parse_mosaic_hdu_ref,
    load_2d_and_header,
)


def test_fits_hdu_reference_round_trip(tmp_path):
    path = tmp_path / "data with spaces.fits"
    encoded = _make_mosaic_hdu_ref(str(path), 3, "SCI λ")
    decoded_path, index, label = _parse_mosaic_hdu_ref(encoded)
    assert decoded_path == str(path)
    assert index == 3
    assert label == "SCI λ"


def test_plain_fits_reference_defaults_to_primary(tmp_path):
    path = tmp_path / "plain.fits"
    decoded_path, index, label = _parse_mosaic_hdu_ref(str(path))
    assert (decoded_path, index, label) == (str(path), None, "")


def test_load_2d_preserves_shape_and_header(fits_factory):
    path = fits_factory("image.fits", FILTER="F200W")
    data, header = load_2d_and_header(str(path), preserve_nan=True)
    assert data.shape == (8, 8)
    assert data.dtype == np.float32
    assert header["FILTER"] == "F200W"


def test_wcs_equal_grid_and_shift_detection(simple_wcs):
    header = simple_wcs.to_header()
    same, error, _reason = _wcs_same_pixel_grid(header, header.copy(), (8, 8), (8, 8))
    assert same is True
    assert error <= 1e-8

    shifted = header.copy()
    shifted["CRPIX1"] += 1.0
    same, error, _reason = _wcs_same_pixel_grid(header, shifted, (8, 8), (8, 8))
    assert same is False
    assert error > 0.05
