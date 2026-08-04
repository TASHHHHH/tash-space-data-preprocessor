from __future__ import annotations

from tsdp2._deps import *
from tsdp2.config import load_settings, save_settings
from tsdp2.logging_config import LOGGER
from tsdp2.ui.installation_window import InstallationCheckWindow
from tsdp2.ui.theme import C_ACCENT, C_BG, C_FG_DIM, C_GOOD


class FirstRunWizard(tk.Toplevel):
    """Optional first-run setup that edits the live workflow defaults."""

    PAGE_TITLES = (
        "Welcome",
        "Choose your folders",
        "Downloads and notifications",
        "Check and tune",
    )

    def __init__(self, master, settings_tab, *, on_finish=None):
        super().__init__(master)
        self.settings_tab = settings_tab
        self.downloader = settings_tab.downloader
        self.mosaic = settings_tab.mosaic
        self.aligner = settings_tab.aligner
        self.recovery = settings_tab.recovery
        self._on_finish = on_finish
        self._page_index = 0
        self._pages: list[ttk.Frame] = []

        self.title(f"Set up {APP_TITLE}")
        self.geometry("820x640")
        self.minsize(760, 590)
        self.transient(master)
        self.configure(background=C_BG)
        self.protocol("WM_DELETE_WINDOW", self._skip)

        outer = ttk.Frame(self, padding=24)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)

        ttk.Label(
            outer,
            text=APP_SUBTITLE,
            font=("Segoe UI Semibold", 20),
        ).grid(row=0, column=0, sticky="w")
        self._step_label = ttk.Label(outer, foreground=C_ACCENT)
        self._step_label.grid(row=1, column=0, sticky="w", pady=(4, 16))

        self._page_host = ttk.Frame(outer)
        self._page_host.grid(row=2, column=0, sticky="nsew")
        self._page_host.columnconfigure(0, weight=1)
        self._page_host.rowconfigure(0, weight=1)

        for builder in (
            self._build_welcome_page,
            self._build_folders_page,
            self._build_services_page,
            self._build_check_page,
        ):
            page = ttk.Frame(self._page_host)
            page.grid(row=0, column=0, sticky="nsew")
            builder(page)
            self._pages.append(page)

        footer = ttk.Frame(outer)
        footer.grid(row=3, column=0, sticky="ew", pady=(18, 0))
        ttk.Button(
            footer,
            text="Skip setup",
            command=self._skip,
        ).pack(side="left")
        self._finish_button = ttk.Button(
            footer,
            text="Finish setup",
            style="Accent.TButton",
            command=self._finish,
        )
        self._finish_button.pack(side="right")
        self._next_button = ttk.Button(footer, text="Next", command=self._next)
        self._next_button.pack(side="right", padx=7)
        self._back_button = ttk.Button(footer, text="Back", command=self._back)
        self._back_button.pack(side="right")

        self._show_page(0)
        self.after_idle(self._center_on_master)
        self.grab_set()

    def _build_welcome_page(self, page) -> None:
        ttk.Label(
            page,
            text="Let’s get the workspace ready.",
            font=("Segoe UI Semibold", 16),
        ).pack(anchor="w", pady=(8, 10))
        ttk.Label(
            page,
            text=(
                "This optional setup checks the application, chooses sensible "
                "folders, and can tune performance for this computer. Nothing is "
                "locked: every value remains editable in Settings."
            ),
            wraplength=700,
            justify="left",
        ).pack(anchor="w")
        items = (
            "Choose where downloads, final products, and scratch data are stored",
            "Locate aria2c for managed downloads",
            "Set up Telegram notifications if you use them",
            "Check the installation and calculate a hardware recommendation",
        )
        list_frame = ttk.Frame(page, padding=(8, 22))
        list_frame.pack(fill="x", anchor="w")
        for item in items:
            ttk.Label(
                list_frame,
                text=f"•  {item}",
                wraplength=670,
                justify="left",
            ).pack(anchor="w", pady=5)

    def _folder_row(self, parent, row, label, variable) -> None:
        ttk.Label(parent, text=label).grid(
            row=row, column=0, sticky="e", padx=(0, 8), pady=6
        )
        ttk.Entry(parent, textvariable=variable).grid(
            row=row, column=1, sticky="ew", pady=6
        )
        ttk.Button(
            parent,
            text="Choose...",
            command=lambda: self._choose_folder(variable, label),
        ).grid(row=row, column=2, padx=(8, 0), pady=6)

    def _build_folders_page(self, page) -> None:
        ttk.Label(
            page,
            text="Files stay separate and easy to recover.",
            font=("Segoe UI Semibold", 14),
        ).pack(anchor="w", pady=(4, 12))
        form = ttk.Frame(page)
        form.pack(fill="both", expand=True)
        form.columnconfigure(1, weight=1)
        rows = [
            ("Downloads", self.downloader.download_dir),
            ("Pipeline work", self.downloader.work_dir),
        ]
        if self.mosaic is not None:
            rows.extend(
                [
                    ("Mosaic output", self.mosaic.out_dir),
                    ("Mosaic scratch", self.mosaic.scratch_dir),
                ]
            )
        if self.aligner is not None:
            rows.append(("Alignment output", self.aligner.output_dir))
        if self.recovery is not None:
            rows.append(("Recovery output", self.recovery.output_dir))
        for row, (label, variable) in enumerate(rows):
            self._folder_row(form, row, label, variable)
        ttk.Label(
            form,
            text=(
                "Scratch storage should be on the fastest drive with the most free "
                "space. Final outputs are never removed by intermediate cleanup."
            ),
            foreground=C_FG_DIM,
            wraplength=680,
            justify="left",
        ).grid(
            row=len(rows),
            column=0,
            columnspan=3,
            sticky="w",
            pady=(14, 0),
        )

    def _build_services_page(self, page) -> None:
        page.columnconfigure(1, weight=1)
        ttk.Label(
            page,
            text="Downloads",
            font=("Segoe UI Semibold", 13),
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(4, 10))
        ttk.Label(page, text="aria2c executable").grid(
            row=1, column=0, sticky="e", padx=(0, 8), pady=6
        )
        ttk.Entry(page, textvariable=self.downloader.aria2_path).grid(
            row=1, column=1, sticky="ew", pady=6
        )
        ttk.Button(
            page,
            text="Choose...",
            command=self.downloader.pick_aria2,
        ).grid(row=1, column=2, padx=(8, 0), pady=6)

        ttk.Separator(page).grid(
            row=2, column=0, columnspan=3, sticky="ew", pady=20
        )
        ttk.Label(
            page,
            text="Telegram notifications",
            font=("Segoe UI Semibold", 13),
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(0, 10))
        ttk.Checkbutton(
            page,
            text="Enable Telegram bot",
            variable=self.downloader.tg_enabled,
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=5)
        ttk.Label(page, text="Bot token").grid(
            row=5, column=0, sticky="e", padx=(0, 8), pady=6
        )
        ttk.Entry(
            page,
            textvariable=self.downloader.tg_token,
            show="*",
        ).grid(row=5, column=1, sticky="ew", pady=6)
        ttk.Label(page, text="Chat ID").grid(
            row=6, column=0, sticky="e", padx=(0, 8), pady=6
        )
        ttk.Entry(page, textvariable=self.downloader.tg_chat_id).grid(
            row=6, column=1, sticky="ew", pady=6
        )
        ttk.Label(
            page,
            text=(
                "Telegram is optional. Tokens use Windows credential storage and "
                "are excluded from exported settings and logs."
            ),
            foreground=C_FG_DIM,
            wraplength=680,
            justify="left",
        ).grid(row=7, column=0, columnspan=3, sticky="w", pady=(14, 0))

    def _build_check_page(self, page) -> None:
        ttk.Label(
            page,
            text="Verify the setup, then tune if you want.",
            font=("Segoe UI Semibold", 14),
        ).pack(anchor="w", pady=(4, 10))
        ttk.Label(
            page,
            text=(
                "The installation check is offline. The system test reads CPU, RAM, "
                "GPU availability, free scratch space, and briefly measures the "
                "scratch drive using one temporary 32 MB file."
            ),
            foreground=C_FG_DIM,
            wraplength=700,
            justify="left",
        ).pack(anchor="w")

        actions = ttk.Frame(page, padding=(0, 20))
        actions.pack(fill="x")
        ttk.Button(
            actions,
            text="Check installation",
            style="Accent.TButton",
            command=self._open_installation_check,
        ).pack(side="left")
        self._system_button = ttk.Button(
            actions,
            text="Test this system",
            command=self._start_system_test,
        )
        self._system_button.pack(side="left", padx=8)
        self._apply_button = ttk.Button(
            actions,
            text="Apply recommendation",
            command=self._apply_recommendation,
            state="normal"
            if self.settings_tab._last_system_recommendation
            else "disabled",
        )
        self._apply_button.pack(side="left")

        self._system_status = ttk.Label(
            page,
            textvariable=self.settings_tab.system_test_status,
            font=("Segoe UI Semibold", 11),
        )
        self._system_status.pack(anchor="w", pady=(8, 4))
        ttk.Label(
            page,
            textvariable=self.settings_tab.system_test_details,
            foreground=C_FG_DIM,
            wraplength=700,
            justify="left",
        ).pack(anchor="w")
        ttk.Label(
            page,
            text=(
                "Applying a recommendation changes only editable defaults. "
                "It never removes diagnostics or lowers output safety."
            ),
            foreground=C_GOOD,
            wraplength=700,
            justify="left",
        ).pack(anchor="w", pady=(18, 0))

    def _choose_folder(self, variable, label: str) -> None:
        current = str(variable.get()).strip()
        initial = current if current and Path(current).is_dir() else str(Path.home())
        selected = filedialog.askdirectory(
            title=f"Choose {label.lower()} folder",
            initialdir=initial,
            parent=self,
        )
        if selected:
            variable.set(selected)

    def _show_page(self, index: int) -> None:
        self._page_index = max(0, min(index, len(self._pages) - 1))
        self._pages[self._page_index].tkraise()
        self._step_label.configure(
            text=f"Step {self._page_index + 1} of {len(self._pages)}  •  "
            f"{self.PAGE_TITLES[self._page_index]}"
        )
        self._back_button.configure(
            state="disabled" if self._page_index == 0 else "normal"
        )
        last = self._page_index == len(self._pages) - 1
        self._next_button.configure(state="disabled" if last else "normal")
        self._finish_button.configure(state="normal" if last else "disabled")

    def _next(self) -> None:
        self._show_page(self._page_index + 1)

    def _back(self) -> None:
        self._show_page(self._page_index - 1)

    def _current_settings(self) -> dict[str, object]:
        return self.settings_tab._application_settings_payload()

    def _open_installation_check(self) -> None:
        InstallationCheckWindow(
            self,
            settings_provider=self._current_settings,
            on_open_settings=self._open_settings,
        )

    def _start_system_test(self) -> None:
        self.settings_tab._start_system_test()
        self._system_button.configure(state="disabled")
        self.after(120, self._poll_system_test)

    def _poll_system_test(self) -> None:
        if self.settings_tab._system_test_running:
            self.after(120, self._poll_system_test)
            return
        self._system_button.configure(state="normal")
        state = (
            "normal"
            if self.settings_tab._last_system_recommendation is not None
            else "disabled"
        )
        self._apply_button.configure(state=state)

    def _apply_recommendation(self) -> None:
        self.settings_tab._apply_system_recommendation()

    def _save_live_settings(self) -> bool:
        self.downloader._save_tg_settings()
        self.downloader._save_tg_extra_settings()
        self.downloader._save_download_settings()
        return bool(self.settings_tab._save_application_settings())

    def _record_completion(self, *, skipped: bool) -> bool:
        config = load_settings()
        config.update(
            {
                "first_run_complete": True,
                "first_run_version": str(APP_VERSION).split(maxsplit=1)[0],
                "first_run_skipped": bool(skipped),
            }
        )
        return bool(save_settings(config))

    def _finish(self) -> None:
        try:
            saved = self._save_live_settings() and self._record_completion(skipped=False)
        except EXPECTED_OPERATION_ERRORS as exc:
            LOGGER.exception("First-run setup could not be saved")
            messagebox.showerror("Setup could not be saved", str(exc), parent=self)
            return
        if not saved:
            messagebox.showerror(
                "Setup could not be saved",
                "TSDP could not safely save all settings.",
                parent=self,
            )
            return
        self._close()

    def _skip(self) -> None:
        if not self._record_completion(skipped=True):
            messagebox.showwarning(
                "Setup not recorded",
                "The setup may appear again because its status could not be saved.",
                parent=self,
            )
        self._close()

    def _open_settings(self) -> None:
        try:
            self.master.notebook.select(self.master.settings_tab)
        except (AttributeError, tk.TclError):
            LOGGER.debug("Settings tab could not be selected", exc_info=True)
        self._close()

    def _close(self) -> None:
        try:
            self.grab_release()
        except tk.TclError:
            LOGGER.debug("First-run setup did not own the input grab")
        if self._on_finish is not None:
            self._on_finish()
        self.destroy()

    def _center_on_master(self) -> None:
        self.update_idletasks()
        try:
            x = self.master.winfo_rootx() + (self.master.winfo_width() - self.winfo_width()) // 2
            y = self.master.winfo_rooty() + (self.master.winfo_height() - self.winfo_height()) // 2
            self.geometry(f"+{max(0, x)}+{max(0, y)}")
        except tk.TclError:
            LOGGER.debug("First-run setup could not be centered", exc_info=True)


__all__ = ["FirstRunWizard"]