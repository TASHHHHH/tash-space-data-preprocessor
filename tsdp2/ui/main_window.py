from __future__ import annotations

from tsdp2._deps import *
from tsdp2.config import load_settings
from tsdp2.logging_config import LOGGER
from tsdp2.recovery.ui import RecoveryTab
from tsdp2.resources import asset_path
from tsdp2.ui.about_window import AboutWindow
from tsdp2.ui.aligner_tab import AlignerTab
from tsdp2.ui.components import LogWindow
from tsdp2.ui.downloader_tab import DownloaderTab
from tsdp2.ui.installation_window import InstallationCheckWindow
from tsdp2.ui.mosaic_tab import MosaicTab
from tsdp2.ui.onboarding import FirstRunWizard
from tsdp2.ui.settings_tab import SettingsTab
from tsdp2.ui.theme import apply_theme, force_light_titlebar
from tsdp2.ui.window_chrome import WindowChrome
from tsdp2.utilities.threading import (
    TkUiDispatcher,
    current_ui_dispatcher,
    install_ui_dispatcher,
)


class App(tk.Tk):
    """Desktop shell that composes the independent workflow tabs."""

    def __init__(self, *, show_onboarding: bool = True):
        super().__init__()
        install_ui_dispatcher(TkUiDispatcher(self))
        self.display_version = str(APP_VERSION).split(maxsplit=1)[0]
        self.title(f"{APP_SUBTITLE} {self.display_version}")
        self.geometry("1440x920")
        self.minsize(1100, 720)
        self.resizable(True, True)
        try:
            self.iconbitmap(default=os.fspath(asset_path("app_icon.ico")))
        except tk.TclError:
            LOGGER.debug("Could not load the application icon", exc_info=True)
        self.overrideredirect(True)

        self._setup_window = None
        self._installation_window = None
        self._about_window = None

        apply_theme(self)
        force_light_titlebar(self)

        self.log_window = LogWindow(self)
        self.bind_all("<Control-l>", lambda _event: self.log_window.toggle())
        print(f"{APP_SUBTITLE} {APP_VERSION} ready.\n", flush=True)
        if not HAVE_MOSAIC:
            print(
                "[Warning] reproject.mosaicking is unavailable - "
                "the mosaic pipeline is disabled.\n"
                "  Fix: pip install --upgrade reproject\n",
                flush=True,
            )
        if not HAVE_PIL:
            print(
                "[Warning] Pillow is not installed - FITS previews are disabled.\n"
                "  Fix: pip install Pillow\n",
                flush=True,
            )

        self.window_chrome = WindowChrome(
            self,
            f"{APP_SUBTITLE}  {self.display_version}",
            self._on_close,
        )
        self.window_chrome.pack(fill="x")
        self._build_toolbar()
        self._build_statusbar()

        notebook = ttk.Notebook(self)
        self.notebook = notebook
        notebook.pack(fill="both", expand=True, padx=18, pady=(12, 10))

        self.downloader_tab = DownloaderTab(notebook)
        self.aligner_tab = AlignerTab(notebook)
        self.mosaic_tab = MosaicTab(notebook)
        self.recovery_tab = RecoveryTab(notebook)
        self.settings_tab = SettingsTab(
            notebook,
            self.downloader_tab,
            self.aligner_tab,
            self.mosaic_tab,
            self.recovery_tab,
        )

        self.downloader_tab.aligner_ref = self.aligner_tab
        self.downloader_tab.mosaic_tab_ref = self.mosaic_tab
        self.downloader_tab.notebook_ref = notebook
        self.mosaic_tab.aligner_ref = self.aligner_tab
        self.mosaic_tab.notebook_ref = notebook
        self.recovery_tab.mosaic_ref = self.mosaic_tab
        self.aligner_tab.telegram_ref = self.downloader_tab
        self.mosaic_tab.telegram_ref = self.downloader_tab

        notebook.add(self.downloader_tab, text="  Downloads  ")
        notebook.add(self.mosaic_tab, text="  Mosaic  ")
        notebook.add(self.aligner_tab, text="  Align  ")
        notebook.add(self.recovery_tab, text="  Recovery  ")
        notebook.add(self.settings_tab, text="  Settings  ")
        self.after_idle(self.settings_tab.apply_startup_preferences)

        if show_onboarding and not bool(load_settings().get("first_run_complete")):
            self.after(650, self.open_first_run_setup)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_toolbar(self) -> None:
        toolbar = ttk.Frame(self, style="AppToolbar.TFrame", padding=(22, 14))
        toolbar.pack(fill="x")

        ttk.Label(
            toolbar,
            text=APP_SUBTITLE,
            style="AppTitle.TLabel",
        ).pack(side="left", anchor="w", pady=4)

        actions = ttk.Frame(toolbar, style="AppToolbar.TFrame")
        actions.pack(side="right", fill="y")
        ttk.Button(actions, text="View Logs", command=self.log_window.toggle).pack(
            side="right", padx=(8, 0)
        )
        ttk.Button(actions, text="About", command=self.open_about).pack(
            side="right", padx=(8, 0)
        )
        ttk.Button(
            actions,
            text="Check Setup",
            style="Accent.TButton",
            command=self.open_installation_check,
        ).pack(side="right", padx=(12, 0))
        ttk.Label(
            actions,
            text=f"Version {self.display_version}",
            style="AppToolbar.TLabel",
            foreground="#68716b",
        ).pack(side="right", pady=7)

    def _build_statusbar(self) -> None:
        statusbar = ttk.Frame(self, style="Statusbar.TFrame", padding=(18, 5))
        statusbar.pack(side="bottom", fill="x")

        missing = []
        if not HAVE_MOSAIC:
            missing.append("mosaic tools unavailable")
        if not HAVE_PIL:
            missing.append("previews unavailable")
        status_text = "; ".join(missing) if missing else "Ready"

        ttk.Label(
            statusbar,
            text=status_text,
            style="ReadyStatus.TLabel" if not missing else "Statusbar.TLabel",
        ).pack(side="left")
        ttk.Label(
            statusbar,
            text="Ctrl+L opens logs",
            style="Statusbar.TLabel",
        ).pack(side="right")

    def _settings_snapshot(self) -> dict[str, object]:
        return self.settings_tab._application_settings_payload()

    def open_settings(self) -> None:
        self.notebook.select(self.settings_tab)

    @staticmethod
    def _focus_window(window) -> bool:
        try:
            if window is not None and window.winfo_exists():
                window.deiconify()
                window.lift()
                window.focus_force()
                return True
        except tk.TclError:
            return False
        return False

    def open_installation_check(self) -> None:
        if self._focus_window(self._installation_window):
            return
        self._installation_window = InstallationCheckWindow(
            self,
            settings_provider=self._settings_snapshot,
            on_open_settings=self.open_settings,
        )

    def open_about(self) -> None:
        if self._focus_window(self._about_window):
            return
        self._about_window = AboutWindow(
            self,
            on_check_setup=self.open_installation_check,
        )

    def open_first_run_setup(self) -> None:
        if self._focus_window(self._setup_window):
            return
        self._setup_window = FirstRunWizard(
            self,
            self.settings_tab,
            on_finish=self._clear_setup_window,
        )

    def _clear_setup_window(self) -> None:
        self._setup_window = None

    def _on_close(self):
        """Request bounded worker shutdown and destroy the application."""
        try:
            downloader = getattr(self, "downloader_tab", None)
            if downloader is not None:
                downloader._tg_listener_stop = True
                if downloader.pipeline_runner:
                    downloader.pipeline_runner.stop()
                for row in list(getattr(downloader, "rows", [])):
                    if row.worker and row.worker.is_alive():
                        row.worker.stop()
        except (AttributeError, RuntimeError):
            LOGGER.debug("Downloader shutdown was incomplete", exc_info=True)

        for tab_name, method_name in (
            ("aligner_tab", "cancel"),
            ("mosaic_tab", "cancel_mosaic"),
        ):
            try:
                tab = getattr(self, tab_name, None)
                if tab is not None:
                    getattr(tab, method_name)()
            except (AttributeError, RuntimeError, tk.TclError):
                LOGGER.debug(
                    "%s shutdown was incomplete", tab_name, exc_info=True
                )

        dispatcher = current_ui_dispatcher()
        if dispatcher is not None:
            dispatcher.close()
            install_ui_dispatcher(None)

        try:
            sys.stdout = self.log_window._real_stdout
            sys.stderr = self.log_window._real_stderr
        except (AttributeError, RuntimeError):
            LOGGER.debug("Log streams were already detached", exc_info=True)
        self.destroy()


__all__ = [name for name in globals() if not name.startswith("__")]
