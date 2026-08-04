from __future__ import annotations

import platform
import webbrowser

from tsdp2._deps import *
from tsdp2.ui.theme import C_ACCENT, C_BG, C_FG_DIM


def application_folder() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _open_path(path: Path) -> None:
    if os.name == "nt":
        os.startfile(str(path))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


class AboutWindow(tk.Toplevel):
    """Product identity, build information, and release access."""

    def __init__(self, master, *, on_check_setup=None):
        super().__init__(master)
        self._on_check_setup = on_check_setup
        self.title(f"About {APP_TITLE}")
        self.geometry("650x470")
        self.resizable(False, False)
        self.transient(master)
        self.configure(background=C_BG)

        card = ttk.Frame(self, padding=28)
        card.pack(fill="both", expand=True)
        ttk.Label(
            card,
            text=APP_SUBTITLE,
            font=("Segoe UI Semibold", 22),
        ).pack(anchor="w")
        ttk.Label(
            card,
            text=f"{APP_TITLE}  |  Version {str(APP_VERSION).split(maxsplit=1)[0]}",
            foreground=C_ACCENT,
            font=("Segoe UI Semibold", 10),
        ).pack(anchor="w", pady=(5, 18))

        ttk.Separator(card).pack(fill="x", pady=(0, 18))
        ttk.Label(
            card,
            text=(
                "A desktop workspace for downloading, inspecting, mosaicking, "
                "aligning, and recovering astronomical FITS data."
            ),
            wraplength=570,
            justify="left",
        ).pack(anchor="w")

        mode = "Packaged Windows application" if getattr(sys, "frozen", False) else "Source checkout"
        details = (
            f"Build mode: {mode}\n"
            f"Python runtime: {platform.python_version()} ({platform.architecture()[0]})\n"
            f"Operating system: {platform.platform()}\n"
            f"Application folder: {application_folder()}"
        )
        ttk.Label(
            card,
            text=details,
            foreground=C_FG_DIM,
            justify="left",
            wraplength=570,
        ).pack(anchor="w", pady=(20, 16))

        ttk.Label(
            card,
            text=(
                "Updates can be installed over the current version. "
                "Your settings, logs, and processing jobs are stored outside "
                "the application folder and remain in place."
            ),
            foreground=C_FG_DIM,
            wraplength=570,
            justify="left",
        ).pack(anchor="w")

        actions = ttk.Frame(card)
        actions.pack(side="bottom", fill="x", pady=(22, 0))
        if APP_UPDATE_URL:
            ttk.Button(
                actions,
                text="Open update page",
                style="Accent.TButton",
                command=lambda: webbrowser.open(APP_UPDATE_URL),
            ).pack(side="left")
        else:
            ttk.Button(
                actions,
                text="Open application folder",
                style="Accent.TButton",
                command=lambda: _open_path(application_folder()),
            ).pack(side="left")
        if on_check_setup is not None:
            ttk.Button(
                actions,
                text="Check installation",
                command=self._check_setup,
            ).pack(side="left", padx=7)
        ttk.Button(actions, text="Close", command=self.destroy).pack(side="right")

    def _check_setup(self) -> None:
        if self._on_check_setup is not None:
            self._on_check_setup()
        self.destroy()


__all__ = ["AboutWindow", "application_folder"]