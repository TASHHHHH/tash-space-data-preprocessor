from __future__ import annotations

import sys

import pytest


def test_application_imports_without_starting_gui():
    import tsdp2
    from tsdp2.app import dependency_status
    from tsdp2.ui.main_window import App

    assert tsdp2.__version__
    assert set(dependency_status()) == {"mosaicking", "previews"}
    assert App.__name__ == "App"


@pytest.mark.gui
def test_gui_constructs_without_permanent_mainloop(tmp_path, monkeypatch):
    from tkinter import TclError
    from tsdp2 import config

    monkeypatch.setattr(config, "_SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(config, "_credential_get", lambda: "")
    monkeypatch.setattr(config, "_credential_set", lambda _token: True)

    from tsdp2.ui.main_window import App

    real_stdout, real_stderr = sys.stdout, sys.stderr
    application = None
    try:
        try:
            application = App(show_onboarding=False)
        except TclError as exc:
            pytest.skip(f"Tk display unavailable: {exc}")
        application.update_idletasks()
        assert application.notebook.index("end") == 5
        assert "Tash Space Data Preprocessor" in application.title()
    finally:
        sys.stdout, sys.stderr = real_stdout, real_stderr
        if application is not None:
            application._on_close()
