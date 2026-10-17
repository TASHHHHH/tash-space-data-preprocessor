"""Application startup and packaged-runtime checks."""

from __future__ import annotations

import os


def dependency_status() -> dict[str, bool]:
    from tsdp2._deps import HAVE_MOSAIC, HAVE_PIL

    return {"mosaicking": HAVE_MOSAIC, "previews": HAVE_PIL}


def main() -> int:
    smoke_output = os.environ.get("TSDP_RELEASE_SMOKE_OUTPUT", "").strip()
    if smoke_output:
        from tsdp2.release_smoke import run_release_smoke

        return run_release_smoke(
            smoke_output, check_skyview=os.environ.get("TSDP_RELEASE_SKY_SMOKE") == "1")

    from tsdp2.logging_config import configure_logging

    configure_logging()
    from tsdp2.ui.main_window import App

    application = App()
    application.mainloop()
    return 0


__all__ = ["dependency_status", "main"]

