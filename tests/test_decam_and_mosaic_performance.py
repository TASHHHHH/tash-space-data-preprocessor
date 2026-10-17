from __future__ import annotations

import sys

import numpy as np
import pytest
from astropy.io import fits
from reproject import reproject_interp
from scipy.ndimage import distance_transform_edt

from tsdp2.astronomy.reference import parse_filter_code, filter_from_header, filter_code_to_microns
from tsdp2.astronomy.mosaic_inputs import group_fits_by_filter, expand_mosaic_input_paths, _feather_mosaic
from tsdp2.astronomy.mosaicking import _combine_in_memory_mosaic, mosaic_filter_group
from tsdp2.astronomy.coverage import wcs_footprint_from_header_only
from tsdp2.utilities import memory


@pytest.mark.parametrize("text, expected", [
    ("g.fits", "G"), ("r.fit", "R"), ("i.fts", "I"), ("z.fits.fz", "Z"),
    ("Y.fits.gz", "Y"), ("u.fits", "U"), ("field_g_001.fits", "G"),
    ("g DECam SDSS c0001 4720.0 1520.0", "G"),
    ("r DECam SDSS c0002 6415.0 1480.0", "R"),
    ("N673 DECam", "N673"), ("JWST NIRCAM F200W", "F200W"),
    ("Halpha.fits", "HA"), ("OIII.fits", "OIII"), ("SII.fits", "SII"),
    ("target.fits", ""), ("mosaic.fits", ""), ("grism.fits", ""),
    ("CLEAR", ""), ("UNKNOWN", ""),
])
def test_optical_filter_tokens(text, expected):
    assert parse_filter_code(text) == expected


def test_optical_wavelength_sorting():
    assert sorted(["Y", "R", "G", "I", "Z", "U"], key=filter_code_to_microns) == ["U", "G", "R", "I", "Z", "Y"]
    assert filter_code_to_microns("N673") == 0.673
    assert filter_code_to_microns("F200W") == 2.0


def test_header_filters_take_precedence_and_custom_names_survive(fits_factory):
    header_r = fits_factory("g.fits", FILTER="r DECam SDSS c0002")
    band_g = fits_factory("random.fits", BAND="g")
    custom = fits_factory("mystery.fits", FILTER="NB_CUSTOM_42")
    assert group_fits_by_filter([str(header_r), str(band_g), str(custom)]) == {
        "R": [str(header_r)], "G": [str(band_g)], "NB_CUSTOM_42": [str(custom)]}
    assert filter_from_header({"FILTER1": "CLEAR1L", "FILTER2": "F606W"}) == "F606W"
    assert filter_from_header({"FILTER": "UNKNOWN", "BAND": "i"}) == "I"
    assert wcs_footprint_from_header_only(str(band_g))["filter"] == "G"


def test_filename_bands_and_unknowns_remain_separate(fits_factory):
    paths = [str(fits_factory(name)) for name in ("g.fits", "field_g_2.fits", "r.fits", "target.fits")]
    groups = group_fits_by_filter(paths, number_unknowns=True)
    assert groups["G"] == paths[:2]
    assert groups["R"] == [paths[2]]
    assert groups["UNKNOWN 1"] == [paths[3]]


def test_decam_multi_ccd_primary_and_extension_filters(tmp_path, simple_wcs):
    path = tmp_path / "exposure.fits"
    primary = fits.PrimaryHDU()
    primary.header["FILTER"] = "g DECam SDSS c0001 4720.0 1520.0"
    hdus = [primary]
    for name in ("N1", "N2"):
        hdus.append(fits.ImageHDU(np.ones((8, 8), dtype="float32"), header=simple_wcs.to_header(), name=name))
    fits.HDUList(hdus).writeto(path)
    refs, reports, errors = expand_mosaic_input_paths([str(path)])
    assert not errors and len(refs) == 2
    assert group_fits_by_filter(refs) == {"G": refs}
    with fits.open(path, mode="update") as opened:
        opened[2].header["FILTER"] = "r"
    assert group_fits_by_filter(refs) == {"G": [refs[0]], "R": [refs[1]]}


def test_streaming_feather_matches_stack_and_preserves_inputs():
    rng = np.random.default_rng(77)
    tiles = [rng.normal(size=(61, 73)).astype("float32") for _ in range(5)]
    for index, tile in enumerate(tiles):
        tile[:index+2, :] = np.nan
        tile[:, :2] = np.nan
    copies = [tile.copy() for tile in tiles]
    weights = [distance_transform_edt(np.isfinite(tile).astype("float32")).astype("float32") for tile in tiles]
    values = [np.where(np.isfinite(tile), tile, 0).astype("float32") for tile in tiles]
    denominator = np.stack(weights).sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        expected = np.where(denominator > 0, (np.stack(values)*np.stack(weights)).sum(axis=0)/denominator, np.nan)
    np.testing.assert_allclose(_feather_mosaic(tiles), expected, rtol=2e-6, atol=2e-7, equal_nan=True)
    for before, after in zip(copies, tiles):
        np.testing.assert_array_equal(before, after)


@pytest.mark.parametrize("block_workers", [1, 2])
def test_blockwise_reprojection_matches_original_with_shift_and_holes(simple_wcs, block_workers):
    data = np.arange(600*700, dtype="float32").reshape(600, 700)
    data[50:70, 90:110] = np.nan
    shifted = simple_wcs.deepcopy()
    shifted.wcs.crpix += [0.37, -1.2]
    expected, footprint = reproject_interp((data, shifted), simple_wcs, shape_out=data.shape)
    expected = np.where(footprint > 0.5, expected, np.nan).astype("float32")
    actual, _ = _combine_in_memory_mosaic("median", None, "G", None, [(data, shifted.to_header())], 1, None, data.shape, simple_wcs, 1, block_workers=block_workers)
    np.testing.assert_allclose(actual, expected, rtol=2e-6, atol=1e-4, equal_nan=True)


@pytest.mark.parametrize("available, expected_disk", [(1024**3, True), (32*1024**3, False)])
def test_memory_plan_uses_free_ram_including_blend_workspaces(monkeypatch, available, expected_disk):
    monkeypatch.setattr(memory, "_available_memory_bytes", lambda: available)
    assert memory._mosaic_disk_mode_needed(4, (6033, 6042), "feather")[0] is expected_disk
    assert memory._mosaic_memory_budget(available) <= available * 0.6


def test_workers_use_extra_resources_but_leave_cpu_and_ram(monkeypatch):
    monkeypatch.setattr(memory, "_available_memory_bytes", lambda: 9*1024**3)
    monkeypatch.setattr(memory.os, "cpu_count", lambda: 24)
    assert memory._safe_mosaic_workers(22, 8, (2000, 2000)) == 8
    assert memory._safe_disk_turbo_workers(22, 8, (2000, 2000)) >= 4
    assert memory._safe_mosaic_workers(100, 100, (10, 10)) <= 22
    assert memory._safe_mosaic_block_workers(22, 2, 2, (6033, 6042)) == 4
    assert memory._safe_mosaic_block_workers(2, 2, 2, (6033, 6042)) == 1
    monkeypatch.setattr(memory, "_available_memory_bytes", lambda: 512*1024**2)
    assert memory._safe_mosaic_workers(22, 8, (6033, 6042)) == 1
    assert memory._safe_disk_turbo_workers(22, 8, (6033, 6042)) == 1


@pytest.mark.parametrize("combine", ["mean", "median", "feather"])
@pytest.mark.parametrize("force_disk", [False, True])
def test_decam_mosaic_outputs_band_and_quality_maps(tmp_path, fits_factory, combine, force_disk, monkeypatch):
    if force_disk:
        import tsdp2.astronomy.mosaicking as mosaicking
        monkeypatch.setattr(mosaicking, "_mosaic_disk_mode_needed", lambda *args: (True, "forced disk regression"))
    rng = np.random.default_rng(13)
    paths = [str(fits_factory(name, data=rng.normal(100, 1, (32, 32)), FILTER="g DECam SDSS c0001"))
             for name in ("visit_1.fits", "visit_2.fits")]
    assert list(group_fits_by_filter(paths)) == ["G"]
    out = mosaic_filter_group(paths, str(tmp_path / combine), "G", combine=combine, n_workers=4, save_coverage_maps=True, turbo_fullres=True)
    assert fits.getheader(out)["FILTER"] == "G"
    assert np.isfinite(fits.getdata(out)).any()
    coverage = fits.getdata(tmp_path / combine / "coverage_G.fits")
    assert np.nanmax(coverage) == 2


@pytest.mark.gui
def test_gui_regroups_decam_files_and_enables_auto_workers(tmp_path, fits_factory, monkeypatch):
    from tkinter import TclError
    from tsdp2 import config
    from tsdp2.ui.main_window import App
    monkeypatch.setattr(config, "_SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(config, "_credential_get", lambda: "")
    monkeypatch.setattr(config, "_credential_set", lambda token: True)
    app = None
    real_stdout, real_stderr = sys.stdout, sys.stderr
    try:
        try:
            app = App(show_onboarding=False)
        except TclError as exc:
            pytest.skip(str(exc))
        tab = app.mosaic_tab
        tab.fits_paths = [str(fits_factory("g.fits")), str(fits_factory("r.fits"))]
        tab.regroup()
        app.update_idletasks()
        assert set(tab.groups) == {"G", "R"}
        assert tab.auto_workers.get() is True
        assert app.settings_tab._application_settings_payload()["mosaic_auto_workers"] is True
    finally:
        if app is not None:
            app._on_close()
        sys.stdout, sys.stderr = real_stdout, real_stderr


@pytest.mark.parametrize("kind, required", [("small", False), ("wide", True), ("other_projection", True), ("distorted", True), ("pv_distortion", True)])
def test_fast_projection_retains_checks_for_unsafe_domains(simple_wcs, kind, required):
    from astropy.wcs import Sip
    from tsdp2.astronomy.mosaic_reprojection import _mosaic_roundtrip_required
    source = simple_wcs.deepcopy()
    target = simple_wcs.deepcopy()
    if kind == "wide":
        source.wcs.cdelt = [-1.0, 1.0]
    elif kind == "other_projection":
        source.wcs.ctype = ["RA---CAR", "DEC--CAR"]
    elif kind == "distorted":
        source.wcs.ctype = ["RA---TAN-SIP", "DEC--TAN-SIP"]
        a = np.zeros((3, 3))
        a[2, 0] = 1e-5
        source.sip = Sip(a, a, None, None, source.wcs.crpix)
    elif kind == "pv_distortion":
        source.wcs.set_pv([(1, 1, 1.0), (1, 4, 1e-5), (2, 1, 1.0)])
    assert _mosaic_roundtrip_required(source.to_header(relax=True), target, (32, 32), (32, 32)) is required


def test_fast_reprojection_handles_ra_wrap(simple_wcs):
    source = simple_wcs.deepcopy()
    source.wcs.crval = [359.999, -5]
    target = source.deepcopy()
    target.wcs.crval[0] = 0.001
    data = np.arange(64*64, dtype="float32").reshape(64, 64)
    expected, footprint = reproject_interp((data, source), target, shape_out=data.shape)
    expected[footprint <= 0.5] = np.nan
    actual, _ = _combine_in_memory_mosaic("median", None, "G", None, [(data, source.to_header())], 1, None, data.shape, target, 1)
    np.testing.assert_allclose(actual, expected.astype("float32"), rtol=2e-6, atol=1e-4, equal_nan=True)
