from __future__ import annotations

from tsdp2.utilities import installation_check
from tsdp2.utilities.installation_check import (
    FAIL,
    PASS,
    WARNING,
    CheckResult,
    InstallationReport,
    check_installation,
)


def test_report_readiness_and_plain_text():
    report = InstallationReport(
        checked_at="2026-08-04T12:00:00+03:00",
        results=(
            CheckResult("Core", PASS, "Ready.", required=True),
            CheckResult("Optional", WARNING, "Unavailable."),
        ),
    )
    assert report.ready is True
    assert report.warning_count == 1
    assert "Tash Space Data Preprocessor" in report.to_text()

    blocked = InstallationReport(
        checked_at=report.checked_at,
        results=(CheckResult("Core", FAIL, "Missing.", required=True),),
    )
    assert blocked.ready is False
    assert blocked.failure_count == 1


def test_folder_check_removes_its_probe_file(tmp_path):
    result = installation_check._folder_check(
        "Scratch",
        str(tmp_path / "future-scratch"),
        scratch=True,
    )

    assert result.status in {PASS, WARNING}
    assert not list(tmp_path.glob(".tsdp-write-check-*"))


def test_installation_check_uses_supplied_folder_values(tmp_path, monkeypatch):
    settings_path = tmp_path / "config" / "settings.json"
    settings_path.parent.mkdir()
    monkeypatch.setattr(installation_check, "settings_file_path", lambda: settings_path)
    monkeypatch.setattr(
        installation_check,
        "_aria2_check",
        lambda _settings: CheckResult("Download engine", PASS, "Test executable."),
    )
    values = {
        "download_dir": str(tmp_path / "downloads"),
        "work_dir": str(tmp_path / "work"),
        "mosaic_output_dir": str(tmp_path / "mosaic"),
        "mosaic_scratch_dir": str(tmp_path / "scratch"),
        "align_output_dir": str(tmp_path / "align"),
        "recovery_output_dir": str(tmp_path / "recovery"),
    }

    report = check_installation(values)

    names = {item.name for item in report.results}
    assert {"Runtime", "Astropy", "Mosaic scratch", "Recovery output"} <= names
    assert not list(tmp_path.rglob(".tsdp-write-check-*"))