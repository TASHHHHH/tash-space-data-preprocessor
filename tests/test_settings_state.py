from __future__ import annotations

import sys
import time

import pytest


@pytest.mark.gui
def test_workflow_defaults_round_trip_through_settings_file(tmp_path, monkeypatch):
    from tkinter import TclError

    from tsdp2 import config
    from tsdp2.ui import settings_system
    from tsdp2.ui.main_window import App
    from tsdp2.utilities.system_profile import SystemProfile

    monkeypatch.setattr(config, "_SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(config, "_credential_get", lambda: "")
    monkeypatch.setattr(config, "_credential_set", lambda _token: True)
    tested_profile = SystemProfile(
        cpu_name="Test workstation",
        logical_cpus=24,
        ram_gb=48.0,
        disk_path=str(tmp_path),
        disk_free_gb=250.0,
        disk_write_mbps=750.0,
        disk_read_mbps=1200.0,
        gpu_name="Test GPU",
        operating_system="Test OS",
    )
    monkeypatch.setattr(
        settings_system,
        "inspect_system",
        lambda *_args, **_kwargs: tested_profile,
    )
    monkeypatch.setattr(settings_system.messagebox, "showinfo", lambda *_a, **_k: None)

    real_stdout, real_stderr = sys.stdout, sys.stderr
    first = second = None
    try:
        try:
            first = App(show_onboarding=False)
        except TclError as exc:
            pytest.skip(f"Tk display unavailable: {exc}")
        first.withdraw()

        first.settings_tab._start_system_test()
        deadline = time.monotonic() + 2.0
        while first.settings_tab._system_test_running and time.monotonic() < deadline:
            first.update()
            time.sleep(0.01)
        assert first.settings_tab._system_test_running is False
        assert first.settings_tab._last_system_profile == tested_profile
        assert first.settings_tab._last_system_recommendation.concurrent_downloads > 3
        first.settings_tab._apply_system_recommendation()
        assert first.downloader_tab.concurrent_limit.get() > 3

        first.settings_tab.startup_tab.set("Mosaic")
        first.downloader_tab.connections.set(9)
        first.downloader_tab.concurrent_limit.set(4)
        first.downloader_tab.work_dir.set(str(tmp_path / "work"))
        first.downloader_tab.user_agent.set("TSDP-Test/1.0")
        first.downloader_tab.pipeline_mode.set("align")
        first.recovery_tab.output_dir.set(str(tmp_path / "recovery"))
        first.recovery_tab.scale_var.set("0.65")
        first.mosaic_tab.combine_fn.set("mean")
        first.mosaic_tab.downscale_global.set("0.5")
        first.mosaic_tab.retain_intermediates.set(True)
        first.aligner_tab.normalize_enabled.set(True)
        first.aligner_tab.normalize_mode.set("p95")
        first.aligner_tab.generate_diagnostics.set(False)

        assert first.settings_tab._save_application_settings()
        first._on_close()
        first = None

        second = App(show_onboarding=False)
        second.withdraw()
        assert second.settings_tab.startup_tab.get() == "Mosaic"
        assert second.downloader_tab.connections.get() == 9
        assert second.downloader_tab.concurrent_limit.get() == 4
        assert second.downloader_tab.work_dir.get() == str(tmp_path / "work")
        assert second.downloader_tab.user_agent.get() == "TSDP-Test/1.0"
        assert second.downloader_tab.pipeline_mode.get() == "align"
        assert second.recovery_tab.output_dir.get() == str(tmp_path / "recovery")
        assert second.recovery_tab.scale_var.get() == "0.65"
        assert second.settings_tab.recovery is second.recovery_tab
        assert second.mosaic_tab.combine_fn.get() == "mean"
        assert second.mosaic_tab.downscale_global.get() == "0.5"
        assert second.mosaic_tab.retain_intermediates.get() is True
        assert second.aligner_tab.normalize_enabled.get() is True
        assert second.aligner_tab.normalize_mode.get() == "p95"
        assert second.aligner_tab.generate_diagnostics.get() is False
    finally:
        sys.stdout, sys.stderr = real_stdout, real_stderr
        if first is not None:
            first._on_close()
        if second is not None:
            second._on_close()
