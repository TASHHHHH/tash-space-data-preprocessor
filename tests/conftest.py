from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits
from astropy.wcs import WCS


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def simple_wcs() -> WCS:
    wcs = WCS(naxis=2)
    wcs.wcs.crpix = [4.5, 4.5]
    wcs.wcs.cdelt = np.array([-1.0 / 3600.0, 1.0 / 3600.0])
    wcs.wcs.crval = [83.0, -5.0]
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    return wcs


@pytest.fixture
def fits_factory(tmp_path, simple_wcs):
    def create(name: str, data=None, **header_values) -> Path:
        array = np.asarray(
            np.arange(64, dtype=np.float32).reshape(8, 8) if data is None else data,
            dtype=np.float32,
        )
        header = simple_wcs.to_header()
        for key, value in header_values.items():
            header[key] = value
        path = tmp_path / name
        fits.PrimaryHDU(array, header=header).writeto(path)
        return path

    return create
