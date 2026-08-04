from __future__ import annotations

import json
import tempfile

from tsdp2._deps import *
from tsdp2.config import load_settings, save_settings, settings_file_path
from tsdp2.logging_config import LOGGER
from tsdp2.ui.settings_sections import SettingsSectionsMixin
from tsdp2.ui.settings_state import SettingsStateMixin
from tsdp2.ui.settings_system import SettingsSystemMixin
from tsdp2.utilities.system_profile import SystemProfile


class SettingsTab(
    SettingsSystemMixin,
    SettingsSectionsMixin,
    SettingsStateMixin,
    ttk.Frame,
):
    """Central settings hub for application and workflow defaults."""

    def __init__(self, master, downloader, aligner=None, mosaic=None, recovery=None):
        super().__init__(master)
        self.downloader = downloader
        self.aligner = aligner
        self.mosaic = mosaic
        self.recovery = recovery
        self.startup_tab = tk.StringVar(value="Downloads")
        self._initialize_system_settings()
        self._load_application_settings()
        self._build_ui()

    def apply_startup_preferences(self) -> None:
        """Select the configured startup tab after all notebook tabs exist."""
        labels = ["Downloads", "Mosaic", "Align", "Recovery", "Settings"]
        selected = str(self.startup_tab.get())
        try:
            self.master.select(labels.index(selected))
        except (ValueError, tk.TclError):
            self.master.select(0)

    def _pick_directory(self, variable, title: str) -> None:
        current = str(variable.get()).strip()
        initial = current if current and Path(current).is_dir() else str(Path.home())
        selected = filedialog.askdirectory(title=title, initialdir=initial)
        if selected:
            variable.set(selected)

    def _open_path(self, path) -> None:
        try:
            value = str(path)
            if os.name == "nt":
                os.startfile(value)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", value])
            else:
                subprocess.Popen(["xdg-open", value])
        except EXPECTED_OPERATION_ERRORS as exc:
            messagebox.showerror("Open failed", str(exc))

    def _open_settings_folder(self) -> None:
        self._open_path(settings_file_path().parent)

    def _open_work_folder(self) -> None:
        path = self.downloader.work_dir.get().strip() or str(Path.home() / "JWST_Work")
        os.makedirs(path, exist_ok=True)
        self._open_path(path)

    def _save_now(self) -> None:
        self.downloader._save_tg_settings()
        self.downloader._save_tg_extra_settings()
        self.downloader._save_download_settings()
        self._save_application_settings()
        messagebox.showinfo(
            "Settings saved", f"Saved all defaults to:\n{settings_file_path()}"
        )

    def _reset_processing_fields(self) -> None:
        if not messagebox.askyesno(
            "Reset processing defaults",
            "Restore the recommended Download, Mosaic, Alignment, and Recovery "
            "defaults?\n\nFolder paths and Telegram credentials will not be changed.",
        ):
            return
        self._restore_processing_defaults()
        self._save_application_settings()

    def _reset_telegram_fields(self) -> None:
        if not messagebox.askyesno(
            "Reset Telegram",
            "Clear the Telegram token and Chat ID, and disable Telegram?",
        ):
            return
        downloader = self.downloader
        downloader.tg_enabled.set(False)
        downloader.tg_token.set("")
        downloader.tg_chat_id.set("")
        downloader._save_tg_settings()

    def _export_settings(self) -> None:
        destination = filedialog.asksaveasfilename(
            title="Export TSDP settings",
            defaultextension=".json",
            filetypes=[("JSON settings", "*.json"), ("All files", "*.*")],
            initialfile="tsdp-settings.json",
        )
        if not destination:
            return

        target = Path(destination)
        data = load_settings()
        data.pop("tg_token", None)
        temp_path = None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            descriptor, raw_temp = tempfile.mkstemp(
                prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
            )
            os.close(descriptor)
            temp_path = Path(raw_temp)
            temp_path.write_text(
                json.dumps(data, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.replace(temp_path, target)
        except (OSError, TypeError, ValueError) as exc:
            messagebox.showerror("Export failed", str(exc))
            return
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError as exc:
                    LOGGER.debug("Could not remove temporary export file: %s", exc)
        messagebox.showinfo(
            "Settings exported",
            f"Saved a portable settings file without credentials:\n{target}",
        )

    def _import_settings(self) -> None:
        source = filedialog.askopenfilename(
            title="Import TSDP settings",
            filetypes=[("JSON settings", "*.json"), ("All files", "*.*")],
        )
        if not source:
            return

        try:
            imported = json.loads(Path(source).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            messagebox.showerror("Import failed", str(exc))
            return
        if not isinstance(imported, dict):
            messagebox.showerror(
                "Import failed",
                "The selected file does not contain a TSDP settings object.",
            )
            return

        imported.pop("tg_token", None)
        current = load_settings()
        existing_token = current.get("tg_token")
        current.update(imported)
        if existing_token:
            current["tg_token"] = existing_token
        if not save_settings(current):
            messagebox.showerror(
                "Import failed",
                "TSDP could not safely save the imported settings.",
            )
            return

        self._load_application_settings()
        raw_profile = imported.get("last_system_profile")
        if isinstance(raw_profile, dict):
            try:
                self._set_system_result(SystemProfile.from_dict(raw_profile))
                self.apply_recommendation_button.configure(state="normal")
            except (TypeError, ValueError, KeyError) as exc:
                LOGGER.warning("Imported system profile is invalid: %s", exc)
        messagebox.showinfo(
            "Settings imported",
            "The imported defaults are active. Telegram credentials were not imported.",
        )

    def _app_action(self, name: str) -> None:
        app = self.winfo_toplevel()
        action = getattr(app, name, None)
        if action is None:
            messagebox.showerror("Unavailable", "This action is not available in the current window.")
            return
        action()

    def _open_setup_assistant(self) -> None:
        self._app_action("open_first_run_setup")

    def _open_installation_check(self) -> None:
        self._app_action("open_installation_check")

    def _open_about(self) -> None:
        self._app_action("open_about")

__all__ = [name for name in globals() if not name.startswith("__")]