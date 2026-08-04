from __future__ import annotations

import sys

import pytest


@pytest.mark.gui
def test_custom_chrome_maximize_and_restore():
    from tkinter import TclError

    from tsdp2.ui.main_window import App

    real_stdout, real_stderr = sys.stdout, sys.stderr
    application = None
    try:
        try:
            application = App()
        except TclError as exc:
            pytest.skip(f"Tk display unavailable: {exc}")
        application.update_idletasks()
        application.update()
        original_geometry = application.geometry()

        application.window_chrome.toggle_maximize()
        application.update_idletasks()
        assert application.window_chrome._maximized is True
        assert application.window_chrome._restore_geometry == original_geometry

        application.window_chrome.toggle_maximize()
        application.update_idletasks()
        assert application.window_chrome._maximized is False
        assert application.geometry() == original_geometry
    finally:
        sys.stdout, sys.stderr = real_stdout, real_stderr
        if application is not None:
            application._on_close()