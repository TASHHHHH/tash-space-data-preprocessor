"""Small packaged-runtime verification used by the release build."""

from __future__ import annotations

import json
import platform
import tempfile
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
from reproject import reproject_interp

from tsdp2.product import APP_NAME, APP_VERSION


def _test_wcs() -> WCS:
    wcs = WCS(naxis=2)
    wcs.wcs.crpix = [4.5, 4.5]
    wcs.wcs.cdelt = np.array([-0.0001, 0.0001])
    wcs.wcs.crval = [10.0, 20.0]
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    return wcs


def run_release_smoke(output_path: str | Path, *, check_skyview: bool = False) -> int:
    target = Path(output_path)
    payload: dict[str, object] = {
        "product": APP_NAME,
        "version": APP_VERSION,
        "python": platform.python_version(),
        "checks": {},
    }
    try:
        source = np.arange(64, dtype=np.float32).reshape(8, 8)
        with tempfile.TemporaryDirectory(prefix="tsdp-release-smoke-") as raw_temp:
            fits_path = Path(raw_temp) / "smoke.fits"
            fits.PrimaryHDU(source, header=_test_wcs().to_header()).writeto(fits_path)
            with fits.open(fits_path, memmap=False) as hdul:
                restored = np.array(hdul[0].data, copy=True)
            payload["checks"]["fits"] = bool(np.array_equal(source, restored))

        projected, footprint = reproject_interp(
            (source, _test_wcs()),
            _test_wcs(),
            shape_out=source.shape,
        )
        payload["checks"]["reproject"] = bool(
            projected.shape == source.shape
            and footprint.shape == source.shape
            and np.isfinite(projected).all()
        )
        if check_skyview:
            from tsdp2.astronomy.sky_background import fetch_skyview_fits

            sky, sky_wcs = fetch_skyview_fits(83.0, -5.0, 0.1, 0.075, 128, 96)
            payload["checks"]["skyview"] = bool(
                sky.shape == (96, 128) and sky_wcs.has_celestial
                and np.isfinite(sky).any()
            )
        payload["ok"] = all(payload["checks"].values())
    except (OSError, ValueError, TypeError, RuntimeError, ImportError) as exc:
        payload["ok"] = False
        payload["error"] = f"{type(exc).__name__}: {exc}"

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return 0 if payload["ok"] else 1


__all__ = ["run_release_smoke"]

