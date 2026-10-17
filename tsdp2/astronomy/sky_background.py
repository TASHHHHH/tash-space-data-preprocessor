"""Small, bounded HTTPS FITS cutouts without importing astroquery's VO stack."""
from __future__ import annotations

from io import BytesIO
from functools import lru_cache
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

from tsdp2.product import APP_VERSION


@lru_cache(maxsize=4)
def fetch_skyview_fits(ra, dec, width_deg, height_deg, width_px, height_px,
                      survey="DSS2 Red", timeout=12):
    """Use SkyView's documented batch interface, with a fixed response limit."""
    width_px, height_px = int(width_px), int(height_px)
    if not (1 <= width_px <= 1600 and 1 <= height_px <= 1600):
        raise ValueError("Sky preview dimensions must be between 1 and 1600 pixels.")
    parameters = {
        "Position": f"{float(ra) % 360:.8f},{float(dec):.8f}",
        "Survey": survey, "Coordinates": "J2000", "Projection": "Tan",
        "Pixels": f"{width_px},{height_px}",
        "Size": f"{float(width_deg):.8f},{float(height_deg):.8f}", "Return": "FITS",
    }
    request = Request(
        "https://skyview.gsfc.nasa.gov/current/cgi/runquery.pl?" + urlencode(parameters),
        headers={"User-Agent": f"TSDP/{APP_VERSION}"},
    )
    limit = max(1024 * 1024, width_px * height_px * 16 + 65536)
    with urlopen(request, timeout=timeout) as response:
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("SkyView returned an unexpectedly large cutout.")
    if not raw.startswith(b"SIMPLE"):
        raise ValueError("SkyView did not return a FITS cutout.")
    with fits.open(BytesIO(raw), memmap=False) as hdul:
        image = hdul[0]
        if image.data is None or image.data.shape != (height_px, width_px):
            raise ValueError("SkyView returned an unexpected image shape.")
        data = np.array(image.data, dtype=np.float32, copy=True)
        wcs = WCS(image.header, naxis=2)
    if not wcs.has_celestial or not np.isfinite(data).any():
        raise ValueError("SkyView cutout has no usable sky image/WCS.")
    return data, wcs
