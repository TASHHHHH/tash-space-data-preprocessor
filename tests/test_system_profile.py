from __future__ import annotations

from tsdp2.utilities.system_profile import (
    SystemProfile,
    inspect_system,
    recommend_settings,
)


def _profile(**overrides):
    values = {
        "cpu_name": "Test CPU",
        "logical_cpus": 8,
        "ram_gb": 16.0,
        "disk_path": "scratch",
        "disk_free_gb": 80.0,
        "disk_write_mbps": 300.0,
        "disk_read_mbps": 500.0,
        "gpu_name": "Not detected",
        "operating_system": "Test OS",
    }
    values.update(overrides)
    return SystemProfile(**values)


def test_constrained_system_gets_safe_recommendation():
    recommendation = recommend_settings(
        _profile(logical_cpus=4, ram_gb=4.0, disk_free_gb=12.0)
    )

    assert recommendation.tier == "Conservative"
    assert recommendation.pipeline_workers <= 3
    assert recommendation.concurrent_downloads == 2
    assert recommendation.full_resolution is False
    assert recommendation.retain_intermediates is False


def test_high_end_system_is_not_capped_at_three_parallel_downloads():
    recommendation = recommend_settings(
        _profile(
            logical_cpus=32,
            ram_gb=64.0,
            disk_free_gb=500.0,
            disk_write_mbps=900.0,
        )
    )

    assert recommendation.tier == "High performance"
    assert recommendation.pipeline_workers > 4
    assert recommendation.mosaic_workers > 4
    assert recommendation.concurrent_downloads > 3
    assert recommendation.full_resolution is True


def test_inspection_without_benchmark_is_side_effect_free(tmp_path):
    profile = inspect_system(tmp_path, run_disk_benchmark=False)

    assert profile.logical_cpus >= 1
    assert profile.ram_gb >= 0.0
    assert profile.disk_path == str(tmp_path)
    assert profile.disk_write_mbps == 0.0
    assert profile.disk_read_mbps == 0.0
    assert list(tmp_path.iterdir()) == []