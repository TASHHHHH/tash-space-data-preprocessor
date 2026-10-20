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
        from tsdp2.astronomy.background_matching import estimate_background_planes, apply_background_plane
        y, x = np.mgrid[:48, :64]
        first = (100 + 0.1*x + 0.05*y).astype(np.float32)
        second = (first + 20 + 2*(2*x/63-1)).astype(np.float32)
        corrections = estimate_background_planes([first, second])
        apply_background_plane(second, corrections[1])
        payload["checks"]["normalization"] = bool(np.allclose(first, second, atol=0.01, rtol=0))
        from tsdp2.astronomy.mosaic_quality import automatically_repair_mosaic, cosmetically_fill_draft
        with tempfile.TemporaryDirectory(prefix='tsdp-repair-smoke-') as raw_temp:
            source_path=Path(raw_temp)/'g.fits'
            target_path=Path(raw_temp)/'mosaic_G.fits'
            image=np.random.default_rng(106).normal(100,1,(64,64)).astype(np.float32)
            header=_test_wcs().to_header()
            header['FILTER']='G'
            fits.writeto(source_path,image,header)
            missing=image.copy()
            missing[30:33,30:33]=np.nan
            fits.writeto(target_path,missing,header)
            repair=automatically_repair_mosaic(target_path,[str(source_path)],'G')
            payload['checks']['automatic_repair']=bool(
                repair['repaired_pixels']==9 and repair['remaining_pixels']==0)
            with fits.open(target_path,mode='update') as hdul:
                hdul[0].header['FASTDRFT']=True
                hdul[0].data[20:22,20:22]=np.nan
            cosmetic=cosmetically_fill_draft(target_path)
            payload['checks']['draft_interpolation']=bool(cosmetic['cosmetic_pixels']==4)
        from tsdp2.astronomy.global_background import estimate_global_background, global_correction_block
        gy,gx=np.mgrid[:96,:128]
        visual=(100+.1*gx+.04*gy+12*(gy>45)+np.random.default_rng(54).normal(0,.3,gx.shape)).astype(np.float32)
        field,_mask,global_report=estimate_global_background(visual)
        flat=visual-global_correction_block(field,visual.shape,global_report['sample_step'],0,len(visual))
        payload['checks']['global_normalization']=bool(np.std(flat)<np.std(visual)*.3 and abs(np.median(flat))<1)
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

