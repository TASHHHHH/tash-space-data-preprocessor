from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlparse

import numpy as np
import pytest
from astropy.io import fits
from astropy.wcs import WCS
from PIL import Image

from tsdp2.astronomy import coverage, sky_background
from tsdp2.astronomy.coverage_geometry import build_coverage_mosaic_geometries, coverage_output_dimensions
from tsdp2.astronomy.mosaicking import mosaic_filter_group
from tsdp2.astronomy.regions import _rotated_rectangle_corners


@pytest.fixture
def groups(fits_factory):
    rng = np.random.default_rng(442)
    return {"G": [str(fits_factory(f"g_{i}.fits", data=rng.normal(10, 1, (96, 128)), FILTER="g")) for i in range(2)]}


def test_offline_preview_keeps_sky_selection_wcs(tmp_path, groups, monkeypatch):
    monkeypatch.setattr(coverage, "_coverage_fetch_sky_background", lambda *a, **k: None)
    report = coverage.create_wcs_coverage_map(groups, str(tmp_path))
    assert report["background_online"] is False
    assert report["background_survey"] == "Offline WCS grid"
    assert WCS(fits.Header.fromstring(report["preview_wcs_header"], sep="\n")).has_celestial
    assert report["preview_width"] == 1200
    assert report["preview_height"] == 850
    assert len(report["tiles"]) == 2
    assert all(tile["polygon"] for tile in report["tiles"])


def test_skyview_failures_fall_back_without_losing_selection(tmp_path, groups, monkeypatch):
    calls = []
    def unavailable(*args, **kwargs):
        calls.append(kwargs["survey"])
        raise OSError("HTTP 503 unavailable")
    monkeypatch.setattr(sky_background, "fetch_skyview_fits", unavailable)
    report = coverage.create_wcs_coverage_map(groups, str(tmp_path))
    assert calls == ["DSS2 Red", "DSS2 Blue"]
    assert report["preview_wcs_header"] and not report["background_online"]


def test_https_cutout_uses_bounded_direct_fits_request(simple_wcs, monkeypatch):
    sky_background.fetch_skyview_fits.cache_clear()
    payload = BytesIO()
    fits.PrimaryHDU(np.ones((6, 8), dtype="float32"), header=simple_wcs.to_header()).writeto(payload)
    captured = []
    def open_request(request, timeout):
        captured.append((request.full_url, timeout))
        return BytesIO(payload.getvalue())
    monkeypatch.setattr(sky_background, "urlopen", open_request)
    array, wcs = sky_background.fetch_skyview_fits(83, -5, 0.1, 0.075, 8, 6)
    assert array.shape == (6, 8) and wcs.has_celestial
    url, timeout = captured[0]
    assert url.startswith("https://skyview.gsfc.nasa.gov/") and timeout == 12
    query = parse_qs(urlparse(url).query)
    assert query["Return"] == ["FITS"] and query["Pixels"] == ["8,6"]
    assert query["Survey"] == ["DSS2 Red"]
    sky_background.fetch_skyview_fits(83, -5, 0.1, 0.075, 8, 6)
    assert len(captured) == 1
    sky_background.fetch_skyview_fits.cache_clear()


@pytest.mark.parametrize("bad_response", [b"<html>Service unavailable</html>", b"x" * (1024 * 1024 + 1)], ids=["html", "oversized"])
def test_cutout_rejects_html_and_excess_data(monkeypatch, bad_response):
    sky_background.fetch_skyview_fits.cache_clear()
    monkeypatch.setattr(sky_background, "urlopen", lambda *a, **k: BytesIO(bad_response))
    with pytest.raises(ValueError):
        sky_background.fetch_skyview_fits(83, -5, 0.1, 0.1, 8, 6)


def test_overlapping_frames_do_not_make_ten_percent_fill_opaque(monkeypatch):
    mask = np.full((20, 30), 255, dtype="uint8")
    monkeypatch.setattr(coverage, "_sample_valid_pixel_mask_on_preview", lambda *a, **k: (mask, 100.0))
    record = {"path": "fake.fits", "group": "G", "preview_polygon": [(0, 0), (29, 0), (29, 19), (0, 19)]}
    outputs = []
    for count in (1, 100):
        image = Image.new("RGBA", (30, 20), (100, 100, 100, 255))
        used, counts, union, overlap = coverage._draw_sampled_hole_coverage(image, None, [dict(record) for _ in range(count)], {"G": []}, {"G": (255, 0, 0, 38)}, 30, 20)
        assert used and counts.max() == count
        outputs.append(np.array(image))
    np.testing.assert_array_equal(*outputs)


@pytest.mark.parametrize("scale", [1.0, 0.5, 0.25])
@pytest.mark.parametrize("angle", [0, 27])
def test_selected_dimensions_match_written_mosaic(tmp_path, groups, scale, angle):
    geometries = build_coverage_mosaic_geometries(groups)
    native = geometries["G"]["wcs"]
    pixels = _rotated_rectangle_corners(64.3, 48.7, 60.4, 40.8, angle)
    ra, dec = native.all_pix2world(pixels[:, 0], pixels[:, 1], 0)
    region = {"world_corners": np.column_stack((ra, dec)).tolist(), "rotation_deg": angle}
    expected = coverage_output_dimensions(geometries, scale, region=region)["G"]
    output = mosaic_filter_group(groups["G"], str(tmp_path / f"output_{scale}_{angle}"), "G", combine="mean", downscale=scale, sky_region=region)
    shape = fits.getdata(output).shape
    assert (expected["height"], expected["width"]) == shape


def test_filter_scale_overrides_and_nonoverlap(groups):
    geometries = build_coverage_mosaic_geometries(groups)
    full = coverage_output_dimensions(geometries, 1)["G"]
    half = coverage_output_dimensions(geometries, 0.25, {"G": "0.5"})["G"]
    assert half["width"] == round(full["width"] * 0.5)
    outside = {"world_corners": [[100, 10], [101, 10], [101, 11], [100, 11]]}
    assert coverage_output_dimensions(geometries, region=outside)["G"]["error"]


def test_header_geometry_ignores_invalid_files(groups, tmp_path):
    broken = tmp_path / "broken.fits"
    broken.write_bytes(b"not a FITS")
    geometries = build_coverage_mosaic_geometries({"G": groups["G"] + [str(broken)]})
    assert "G" in geometries


@pytest.mark.gui
def test_gui_live_dimensions_scale_rotation_and_apply(tmp_path, groups, monkeypatch):
    import tkinter as tk
    from tsdp2.ui.coverage_window import CoverageWindow
    monkeypatch.setattr(CoverageWindow, "_render", Mock())
    root = tk.Tk()
    root.withdraw()
    scale_callback, area_callback = Mock(), Mock()
    window = None
    try:
        window = CoverageWindow(root, groups, ["G"], str(tmp_path), on_region=area_callback, on_scale=scale_callback)
        report = coverage.create_wcs_coverage_map(groups, str(tmp_path), sky_background=False)
        report["mosaic_geometries"] = build_coverage_mosaic_geometries(groups)
        window._apply_report(report, "All filters")
        window.state["selection"] = {"cx": 600.0, "cy": 425.0, "width": 280.0, "height": 180.0, "angle_deg": 0.0}
        window._draw_selection()
        original = window.output_dimensions["G"]
        assert "Selected output" in window.output_var.get()
        assert "px" in window.output_var.get()
        assert str(window.use_area_btn.cget("state")) == "normal"
        window.scale_var.set("0.5")
        scale_callback.assert_called_with(0.5)
        scaled = window.output_dimensions["G"]
        assert scaled["width"] < original["width"]
        window._set_rotation(30)
        assert window.output_dimensions["G"]["width"] > 0
        window._use_selection()
        assert area_callback.call_args.args[0]["output_scale"] == 0.5
        window.scale_var.set("invalid")
        assert str(window.use_area_btn.cget("state")) == "disabled"
        window.scale_var.set("0.25")
        assert str(window.use_area_btn.cget("state")) == "normal"
        window._clear_selection()
        assert "Full footprint" in window.output_var.get()
        root.update_idletasks()
    finally:
        if window is not None:
            window._close()
        root.destroy()


@pytest.mark.gui
def test_background_worker_posts_results_on_tk_thread(tmp_path, groups, monkeypatch):
    import threading
    import time
    import tkinter as tk
    from tsdp2.ui import coverage_rendering
    from tsdp2.ui.coverage_window import CoverageWindow
    main_thread = threading.get_ident()
    received = []
    worker_threads = []
    def render(*args, **kwargs):
        worker_threads.append(threading.get_ident())
        kwargs["sky_background"] = False
        return coverage.create_wcs_coverage_map(*args, **kwargs)
    monkeypatch.setattr(coverage_rendering, "create_wcs_coverage_map", render)
    root = tk.Tk()
    root.withdraw()
    window = None
    try:
        window = CoverageWindow(root, groups, ["G"], str(tmp_path), on_report=lambda report: received.append(threading.get_ident()))
        deadline = time.monotonic() + 6
        while not received and time.monotonic() < deadline:
            root.update()
            time.sleep(0.01)
        assert received == [main_thread]
        assert worker_threads and worker_threads[0] != main_thread
        assert window.geometry_cache and window.state["report"]["preview_wcs_header"]
        window._drag_start(SimpleNamespace(x=400, y=300))
        window._drag_motion(SimpleNamespace(x=800, y=500, state=0))
        assert "Selected output" in window.output_var.get()
        window._drag_end(SimpleNamespace(x=800, y=500))
        assert str(window.use_area_btn.cget("state")) == "normal"
    finally:
        if window is not None:
            window._close()
        root.destroy()
