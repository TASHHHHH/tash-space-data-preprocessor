from __future__ import annotations

from pathlib import Path

import numpy as np
from astropy.io import fits

from tsdp2.astronomy.alignment import align_crop_many
from tsdp2.astronomy.coverage import create_wcs_coverage_map
from tsdp2.astronomy.mosaicking import mosaic_filter_group
from tsdp2.astronomy.normalization import (
    _build_common_overlap_mask,
    _sampled_normalization_scale,
    normalize_to_reference,
)
from tsdp2.output_paths import JobPaths


def test_normalization_calculation():
    reference = np.full((12, 12), 10.0, dtype=np.float32)
    moving = np.full((12, 12), 2.0, dtype=np.float32)
    mask = np.ones((12, 12), dtype=bool)
    normalized, scale = normalize_to_reference(moving, reference, mask, mode="median")
    assert scale == 5.0
    np.testing.assert_allclose(normalized, reference)
    assert _sampled_normalization_scale(moving, reference, mode="median") == 5.0


def test_common_overlap_crop_mask():
    reference = np.ones((5, 5), dtype=np.float32)
    moving = np.ones((5, 5), dtype=np.float32)
    moving[:, :2] = np.nan
    mask = _build_common_overlap_mask(
        reference,
        {"moving": moving},
        {"moving": np.isfinite(moving).astype(np.float32)},
    )
    assert not mask[:, :2].any()
    assert mask[:, 2:].all()


def test_alignment_synthetic_outputs_wcs_masks_and_values(tmp_path, fits_factory):
    reference_data = np.arange(256, dtype=np.float32).reshape(16, 16)
    moving_data = reference_data * 2.0
    reference = fits_factory("reference.fits", data=reference_data, FILTER="F200W")
    moving = fits_factory("moving.fits", data=moving_data, FILTER="F444W")
    paths = JobPaths.create(tmp_path / "outputs", "alignment")
    out_folder, saved, log_path = align_crop_many(
        str(reference),
        "F200W",
        [{"path": str(moving), "name": "F444W"}],
        paths,
        normalize_enabled=True,
        normalize_mode="median",
        psf_match_enabled=False,
        generate_diagnostics=True,
    )
    assert Path(out_folder).is_relative_to(paths.root)
    assert Path(log_path).parent == paths.root / "metadata" / "logs"
    assert len(saved) == 2
    arrays = {}
    headers = {}
    for name, output, coverage in saved:
        path = Path(output)
        assert path.is_relative_to(paths.root / "final" / "normalized")
        arrays[name] = fits.getdata(path)
        headers[name] = fits.getheader(path)
        assert coverage == 100.0
    np.testing.assert_allclose(arrays["F200W"], reference_data)
    np.testing.assert_allclose(arrays["F444W"], reference_data)
    assert headers["F200W"]["ALNMODE"] == "COMMON"
    assert (paths.root / "diagnostics" / "masks" / "common_overlap_mask.fits").exists()
    assert not any(paths.root.glob("final/**/*.png"))


def test_alignment_cancellation_leaves_no_final_output(tmp_path, fits_factory):
    reference = fits_factory("reference.fits", FILTER="F200W")
    moving = fits_factory("moving.fits", FILTER="F444W")
    paths = JobPaths.create(tmp_path / "outputs", "cancelled")
    try:
        align_crop_many(
            str(reference),
            "ref",
            [{"path": str(moving), "name": "moving"}],
            paths,
            False,
            "median",
            False,
            cancel_cb=lambda: True,
        )
    except RuntimeError as exc:
        assert "Cancelled" in str(exc)
    else:
        raise AssertionError("cancellation did not stop alignment")
    assert not paths.final.exists()


def test_synthetic_mosaic_and_quality_maps_are_organized(tmp_path, fits_factory):
    rng = np.random.default_rng(42)
    first = fits_factory("tile_a.fits", data=rng.normal(size=(16, 16)), FILTER="F200W")
    second = fits_factory("tile_b.fits", data=rng.normal(size=(16, 16)), FILTER="F200W")
    paths = JobPaths.create(tmp_path / "outputs", "mosaic")
    output = Path(
        mosaic_filter_group(
            [str(first), str(second)],
            paths,
            "F200W",
            combine="median",
            n_workers=1,
            save_coverage_maps=True,
        )
    )
    assert output.parent == paths.root / "final" / "mosaics" / "F200W"
    data = fits.getdata(output)
    header = fits.getheader(output)
    assert data.ndim == 2 and data.size > 0
    assert header["FILTER"] == "F200W"
    assert list((paths.root / "diagnostics" / "coverage_maps").glob("*.fits"))
    assert list((paths.root / "diagnostics" / "weight_maps").glob("*.fits"))


def test_coverage_preview_and_report_are_separated(tmp_path, fits_factory):
    tile = fits_factory("tile.fits", data=np.arange(256).reshape(16, 16), FILTER="F200W")
    paths = JobPaths.create(tmp_path / "outputs", "coverage")
    report = create_wcs_coverage_map(
        {"F200W": [str(tile)]}, paths, sky_background=False
    )
    assert Path(report["png_path"]).parent == paths.root / "diagnostics" / "coverage_maps"
    assert Path(report["txt_path"]).parent == paths.root / "metadata" / "reports"
