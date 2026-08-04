from __future__ import annotations

from types import SimpleNamespace

import tsdp2.ui.downloader_tab as downloader_module
from tsdp2.telegram.commands import _global_batch_counts, _tg_batch_counts
from tsdp2.telegram.parsing import parse_command
from tsdp2.ui.downloader_tab import DownloaderTab


class Variable:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def row(status, *, job_id=1, global_id=2, running=False):
    worker = SimpleNamespace(is_alive=lambda: running) if running else None
    return SimpleNamespace(
        status_var=Variable(status),
        url_var=Variable("https://example.invalid/file.fits"),
        name_var=Variable("file.fits"),
        worker=worker,
        _tg_job_id=job_id,
        _post_download_batch_id=global_id,
        last_file_path="",
    )


def test_command_parser_is_case_insensitive_and_bot_suffix_aware():
    parsed = parse_command(" /GIF@MyTSDPBot latest ")
    assert parsed.name == "gif"
    assert parsed.argument == "latest"
    assert parse_command("   ").name == ""


def test_batch_completion_counts_terminal_and_running_rows():
    rows = [row("Done", running=False), row("Error", running=False), row("Running", running=True)]
    controller = SimpleNamespace(rows=rows, running_workers={"x": rows[-1]})
    tg_batch = {"job_id": 1, "rows": rows, "expected_count": 3}
    global_batch = {"id": 2, "rows": rows, "expected": 3}
    tg_counts = _tg_batch_counts(controller, tg_batch)
    global_counts = _global_batch_counts(controller, global_batch)
    assert tg_counts["terminal"] == 2
    assert tg_counts["running_workers"] == 1
    assert global_counts["terminal"] == 2
    assert global_counts["running"] == 1


def test_premature_pipeline_start_is_blocked(monkeypatch):
    calls = []
    fake = SimpleNamespace(
        _tg_active_batch={"pipeline_started": False, "expected_count": 2, "rows": []},
        _start_pipeline_for_core=lambda source: calls.append(source),
    )
    monkeypatch.setattr(
        downloader_module,
        "_tg_batch_counts",
        lambda _self, _batch: {"terminal": 1, "expected": 2},
    )
    monkeypatch.setattr(downloader_module, "_tg_update_batch_dashboard", lambda *a, **k: None)
    DownloaderTab._start_pipeline_for(fake, "one-file.fits")
    assert calls == []


def test_pipeline_starts_when_no_telegram_barrier():
    calls = []
    fake = SimpleNamespace(
        _tg_active_batch=None,
        _start_pipeline_for_core=lambda source: calls.append(source) or "started",
    )
    assert DownloaderTab._start_pipeline_for(fake, "all-files") == "started"
    assert calls == ["all-files"]


def test_stop_all_marks_batch_cancelled_before_stopping_workers():
    batch = {"pipeline_started": False, "cancelled": False}
    fake = SimpleNamespace(
        _post_download_batch=batch,
        _stop_all_core=lambda: "stopped",
    )
    assert DownloaderTab.stop_all(fake) == "stopped"
    assert batch["cancelled"] is True
