from __future__ import annotations

from tsdp2.astronomy.mosaic_geometry import _mosaic_preflight_text
from tsdp2.utilities import memory


def test_disk_workers_are_resource_adaptive_not_fixed_at_three(monkeypatch):
    monkeypatch.setattr(memory, "_available_memory_bytes", lambda: 64 * 1024 ** 3)
    monkeypatch.setattr(memory.os, "cpu_count", lambda: 32)

    workers = memory._safe_disk_turbo_workers(
        requested_workers=10,
        n_tiles=20,
        shape_out=(2000, 2000),
    )

    assert workers == 10


def test_preflight_text_exposes_memory_output_and_scratch_estimates():
    gib = 1024 ** 3
    report = {
        "selected_area": False,
        "filters": [],
        "peak_memory": 2 * gib,
        "available_memory": 8 * gib,
        "memory_pressure": False,
        "out_folder": "output",
        "scratch_folder": "scratch",
        "output_free": 100 * gib,
        "scratch_free": 90 * gib,
        "output_needed": 4 * gib,
        "scratch_needed": 12 * gib,
        "same_volume": False,
        "warnings": [],
    }

    text = _mosaic_preflight_text(report)

    assert "Estimated peak working memory: 2.00 GiB" in text
    assert "Output: output" in text
    assert "Scratch: scratch" in text