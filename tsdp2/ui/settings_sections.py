from __future__ import annotations

from tsdp2._deps import *
from tsdp2.astronomy.previews import STRETCH_MODES
from tsdp2.config import settings_file_path, telegram_secret_storage_warning
from tsdp2.ui.theme import C_BG, C_FG_DIM, C_WARN


class SettingsSectionsMixin:
    """Build the grouped controls shown by :class:`SettingsTab`."""

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        canvas = tk.Canvas(self, highlightthickness=0, background=C_BG)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=scrollbar.set)

        body = ttk.Frame(canvas, padding=14)
        body.columnconfigure(0, weight=1)
        window = canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind(
            "<Configure>",
            lambda _event: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.bind(
            "<Configure>",
            lambda event: canvas.itemconfig(window, width=event.width),
        )

        self._next_settings_row = 0
        for builder in self._settings_section_builders():
            builder(body)

    def _settings_section_builders(self):
        """Return ordered section builders; extensions can append new sections."""
        return (
            self._build_application_behavior,
            self._build_system_profile,
            self._build_help_and_setup,
            self._build_default_folders,
            self._build_download_defaults,
            self._build_mosaic_defaults,
            self._build_alignment_defaults,
            self._build_recovery_defaults,
            self._build_advanced_defaults,
            self._build_telegram_account,
            self._build_telegram_behavior,
            self._build_program_tools,
        )

    def _place_settings_section(self, frame) -> None:
        frame.grid(
            row=self._next_settings_row,
            column=0,
            sticky="ew",
            padx=4,
            pady=(0, 10),
        )
        self._next_settings_row += 1

    def _build_application_behavior(self, parent) -> None:
        downloader = self.downloader
        frame = ttk.LabelFrame(parent, text="Application Behavior", padding=12)
        self._place_settings_section(frame)

        ttk.Label(frame, text="Open on startup:").grid(
            row=0, column=0, sticky="w", padx=6, pady=4
        )
        ttk.Combobox(
            frame,
            textvariable=self.startup_tab,
            values=["Downloads", "Mosaic", "Align", "Recovery", "Settings"],
            state="readonly",
            width=16,
        ).grid(row=0, column=1, sticky="w", padx=6, pady=4)

        ttk.Label(frame, text="When processing finishes:").grid(
            row=0, column=2, sticky="e", padx=(30, 6), pady=4
        )
        ttk.Checkbutton(
            frame, text="Show summary", variable=downloader.when_done_dialog
        ).grid(row=0, column=3, sticky="w", padx=6, pady=4)
        ttk.Checkbutton(
            frame, text="Play sound", variable=downloader.when_done_beep
        ).grid(row=0, column=4, sticky="w", padx=6, pady=4)
        ttk.Checkbutton(
            frame, text="Open output folder", variable=downloader.when_done_folder
        ).grid(row=0, column=5, sticky="w", padx=6, pady=4)

        ttk.Checkbutton(
            frame,
            text="Run the processing pipeline automatically after downloads",
            variable=downloader.auto_run_pipeline,
        ).grid(row=1, column=0, columnspan=3, sticky="w", padx=6, pady=4)
        ttk.Checkbutton(
            frame,
            text="Send completed mosaics to Align automatically",
            variable=downloader.auto_load_aligner,
        ).grid(row=1, column=3, columnspan=3, sticky="w", padx=6, pady=4)

    def _folder_row(self, parent, row, label, variable, title) -> None:
        ttk.Label(parent, text=label).grid(
            row=row, column=0, sticky="w", padx=6, pady=4
        )
        ttk.Entry(parent, textvariable=variable).grid(
            row=row, column=1, sticky="ew", padx=6, pady=4
        )
        ttk.Button(
            parent,
            text="Choose…",
            command=lambda: self._pick_directory(variable, title),
        ).grid(row=row, column=2, padx=6, pady=4)

    def _build_help_and_setup(self, parent) -> None:
        frame = ttk.LabelFrame(parent, text="Help and Setup", padding=12)
        self._place_settings_section(frame)

        ttk.Button(
            frame,
            text="Run setup assistant",
            style="Accent.TButton",
            command=self._open_setup_assistant,
        ).pack(side="left", padx=6, pady=4)
        ttk.Button(
            frame,
            text="Check installation",
            command=self._open_installation_check,
        ).pack(side="left", padx=6, pady=4)
        ttk.Button(
            frame,
            text="About TSDP",
            command=self._open_about,
        ).pack(side="left", padx=6, pady=4)
        ttk.Label(
            frame,
            text=(
                "You can rerun setup at any time. Checks are offline and never "
                "change processing settings automatically."
            ),
            foreground=C_FG_DIM,
        ).pack(side="left", padx=(18, 6), pady=4)
    def _build_default_folders(self, parent) -> None:
        frame = ttk.LabelFrame(parent, text="Default Folders", padding=12)
        self._place_settings_section(frame)
        frame.columnconfigure(1, weight=1)

        rows = [
            ("Downloads", self.downloader.download_dir, "Choose download folder"),
            ("Work and unzip", self.downloader.work_dir, "Choose work folder"),
        ]
        if self.mosaic is not None:
            rows.extend(
                [
                    ("Mosaic output", self.mosaic.out_dir, "Choose mosaic output folder"),
                    (
                        "Mosaic recovery scratch",
                        self.mosaic.scratch_dir,
                        "Choose recovery scratch folder",
                    ),
                ]
            )
        if self.aligner is not None:
            rows.append(
                ("Alignment output", self.aligner.output_dir, "Choose alignment output folder")
            )
        if self.recovery is not None:
            rows.append(
                ("Recovery output", self.recovery.output_dir, "Choose recovery output folder")
            )

        for row, values in enumerate(rows):
            self._folder_row(frame, row, *values)

        ttk.Label(
            frame,
            text="These values populate the matching workflow tabs and become the defaults for future sessions.",
            foreground=C_FG_DIM,
            font=("Segoe UI", 8),
        ).grid(
            row=len(rows), column=0, columnspan=3, sticky="w", padx=6, pady=(6, 0)
        )

    def _build_download_defaults(self, parent) -> None:
        downloader = self.downloader
        frame = ttk.LabelFrame(parent, text="Download and Pipeline Defaults", padding=12)
        self._place_settings_section(frame)

        fields = (
            ("Connections per server", downloader.connections, 1, 64),
            ("Split count", downloader.splits, 1, 64),
            ("Simultaneous downloads", downloader.concurrent_limit, 1, 50),
            ("Processing threads", downloader.n_workers, 1, 64),
        )
        for index, (label, variable, minimum, maximum) in enumerate(fields):
            column = index * 2
            ttk.Label(frame, text=f"{label}:").grid(
                row=0, column=column, sticky="e", padx=(6, 4), pady=4
            )
            ttk.Spinbox(
                frame,
                from_=minimum,
                to=maximum,
                textvariable=variable,
                width=6,
            ).grid(row=0, column=column + 1, sticky="w", padx=(0, 18), pady=4)

        ttk.Label(frame, text="Pipeline combine:").grid(
            row=1, column=0, sticky="e", padx=(6, 4), pady=4
        )
        ttk.Combobox(
            frame,
            values=["feather", "median", "mean"],
            textvariable=downloader.combine_fn,
            state="readonly",
            width=10,
        ).grid(row=1, column=1, sticky="w", padx=(0, 18), pady=4)

        checks = (
            ("Resume partial downloads", downloader.resume),
            ("Disable file pre-allocation", downloader.disable_allocation),
            ("Full-resolution processing", downloader.turbo_fullres),
            ("Skip groups without a filter ID", downloader.skip_unknown_groups),
        )
        for index, (label, variable) in enumerate(checks):
            ttk.Checkbutton(frame, text=label, variable=variable).grid(
                row=1,
                column=index + 2,
                columnspan=1,
                sticky="w",
                padx=8,
                pady=4,
            )

    def _build_mosaic_defaults(self, parent) -> None:
        if self.mosaic is None:
            return
        mosaic = self.mosaic
        frame = ttk.LabelFrame(parent, text="Mosaic Defaults", padding=12)
        self._place_settings_section(frame)

        ttk.Label(frame, text="Combine:").grid(row=0, column=0, sticky="e", padx=6, pady=4)
        ttk.Combobox(
            frame,
            values=["feather", "median", "mean"],
            textvariable=mosaic.combine_fn,
            state="readonly",
            width=10,
        ).grid(row=0, column=1, sticky="w", padx=6, pady=4)
        ttk.Label(frame, text="Workers:").grid(row=0, column=2, sticky="e", padx=6, pady=4)
        ttk.Spinbox(frame, from_=1, to=64, textvariable=mosaic.n_workers, width=6).grid(
            row=0, column=3, sticky="w", padx=6, pady=4
        )
        ttk.Label(frame, text="Output scale:").grid(row=0, column=4, sticky="e", padx=6, pady=4)
        ttk.Spinbox(
            frame,
            from_=0.02,
            to=1.0,
            increment=0.05,
            textvariable=mosaic.downscale_global,
            width=7,
        ).grid(row=0, column=5, sticky="w", padx=6, pady=4)
        ttk.Label(frame, text="Draft scale:").grid(row=0, column=6, sticky="e", padx=6, pady=4)
        ttk.Spinbox(
            frame,
            from_=0.02,
            to=0.25,
            increment=0.01,
            textvariable=mosaic.draft_scale,
            width=7,
        ).grid(row=0, column=7, sticky="w", padx=6, pady=4)

        checks = (
            ("Send output to Align", mosaic.auto_send),
            ("Save coverage maps", mosaic.save_coverage_maps),
            ("Resume tile cache", mosaic.resume_cache),
            ("Clean draft background", mosaic.draft_background_cleanup),
            ("Write cleaned high-resolution copy", mosaic.highres_background_cleanup),
            ("Full-resolution processing", mosaic.turbo_fullres),
            ("Retain intermediates", mosaic.retain_intermediates),
        )
        for index, (label, variable) in enumerate(checks):
            row = 1 + index // 4
            column = (index % 4) * 2
            ttk.Checkbutton(frame, text=label, variable=variable).grid(
                row=row,
                column=column,
                columnspan=2,
                sticky="w",
                padx=6,
                pady=4,
            )

    def _build_alignment_defaults(self, parent) -> None:
        if self.aligner is None:
            return
        aligner = self.aligner
        frame = ttk.LabelFrame(parent, text="Alignment Defaults", padding=12)
        self._place_settings_section(frame)

        ttk.Label(frame, text="Preview stretch:").grid(
            row=0, column=0, sticky="e", padx=6, pady=4
        )
        ttk.Combobox(
            frame,
            values=STRETCH_MODES,
            textvariable=aligner.stretch_mode,
            state="readonly",
            width=12,
        ).grid(row=0, column=1, sticky="w", padx=6, pady=4)
        ttk.Label(frame, text="Normalization mode:").grid(
            row=0, column=2, sticky="e", padx=(20, 6), pady=4
        )
        ttk.Combobox(
            frame,
            values=["median", "p95", "background"],
            textvariable=aligner.normalize_mode,
            state="readonly",
            width=14,
        ).grid(row=0, column=3, sticky="w", padx=6, pady=4)

        checks = (
            ("Normalize to reference", aligner.normalize_enabled),
            ("PSF-match to the broadest filter", aligner.psf_match_enabled),
            ("Keep the full reference canvas", aligner.keep_reference_canvas),
            ("Generate diagnostics", aligner.generate_diagnostics),
            ("Retain intermediates", aligner.retain_intermediates),
        )
        for index, (label, variable) in enumerate(checks):
            ttk.Checkbutton(frame, text=label, variable=variable).grid(
                row=1 + index // 3,
                column=(index % 3) * 2,
                columnspan=2,
                sticky="w",
                padx=6,
                pady=4,
            )

    def _build_recovery_defaults(self, parent) -> None:
        if self.recovery is None:
            return
        recovery = self.recovery
        frame = ttk.LabelFrame(parent, text="Recovery Defaults", padding=12)
        self._place_settings_section(frame)

        ttk.Label(frame, text="Failed-run scale:").grid(
            row=0, column=0, sticky="e", padx=6, pady=4
        )
        ttk.Spinbox(
            frame,
            from_=0.02,
            to=1.0,
            increment=0.05,
            textvariable=recovery.scale_var,
            width=8,
        ).grid(row=0, column=1, sticky="w", padx=6, pady=4)
        ttk.Label(
            frame,
            text=(
                "Used when rebuilding legacy sessions that do not include "
                "their own recovery manifest."
            ),
            foreground=C_FG_DIM,
        ).grid(row=0, column=2, sticky="w", padx=(12, 6), pady=4)

    def _build_advanced_defaults(self, parent) -> None:
        downloader = self.downloader
        frame = ttk.LabelFrame(parent, text="Advanced Overrides", padding=12)
        self._place_settings_section(frame)
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(3, weight=1)

        ttk.Label(frame, text="aria2 executable:").grid(
            row=0, column=0, sticky="e", padx=6, pady=4
        )
        ttk.Entry(frame, textvariable=downloader.aria2_path).grid(
            row=0, column=1, sticky="ew", padx=6, pady=4
        )
        ttk.Button(frame, text="Choose…", command=downloader.pick_aria2).grid(
            row=0, column=2, padx=6, pady=4
        )

        ttk.Label(frame, text="HTTP User-Agent:").grid(
            row=1, column=0, sticky="e", padx=6, pady=4
        )
        ttk.Entry(frame, textvariable=downloader.user_agent).grid(
            row=1, column=1, sticky="ew", padx=6, pady=4
        )
        ttk.Label(frame, text="Additional aria2 arguments:").grid(
            row=1, column=2, sticky="e", padx=6, pady=4
        )
        ttk.Entry(frame, textvariable=downloader.extra_args).grid(
            row=1, column=3, sticky="ew", padx=6, pady=4
        )

        ttk.Label(frame, text="Pipeline after download:").grid(
            row=2, column=0, sticky="e", padx=6, pady=4
        )
        modes = ttk.Frame(frame)
        modes.grid(row=2, column=1, columnspan=3, sticky="w", padx=6, pady=4)
        for value, label in (
            ("download", "Unzip only"),
            ("align", "Align"),
            ("mosaic", "Mosaic"),
            ("mosaic_align", "Mosaic and Align"),
        ):
            ttk.Radiobutton(
                modes,
                text=label,
                variable=downloader.pipeline_mode,
                value=value,
            ).pack(side="left", padx=(0, 14))

        ttk.Label(frame, text="Preferred reference:").grid(
            row=3, column=0, sticky="e", padx=6, pady=4
        )
        ttk.Combobox(
            frame,
            textvariable=downloader.reference_mode,
            values=["", "Highest resolution", "Ordinary best"],
            state="readonly",
            width=22,
        ).grid(row=3, column=1, sticky="w", padx=6, pady=4)

        if self.mosaic is not None:
            ttk.Label(frame, text="Seam correction strength:").grid(
                row=3, column=2, sticky="e", padx=6, pady=4
            )
            ttk.Spinbox(
                frame,
                from_=0.0,
                to=3.0,
                increment=0.1,
                textvariable=self.mosaic.seam_strength,
                width=8,
            ).grid(row=3, column=3, sticky="w", padx=6, pady=4)

        ttk.Checkbutton(
            frame,
            text="Allow shutdown-PC action after a completed pipeline",
            variable=downloader.when_done_shutdown,
            command=downloader._on_shutdown_check,
        ).grid(row=4, column=0, columnspan=4, sticky="w", padx=6, pady=4)
        ttk.Label(
            frame,
            text=(
                "Advanced overrides are passed through to the existing workflow "
                "engines. Invalid command-line arguments can make downloads fail."
            ),
            foreground=C_FG_DIM,
            font=("Segoe UI", 8),
        ).grid(row=5, column=0, columnspan=4, sticky="w", padx=6, pady=(5, 0))
    def _build_telegram_account(self, parent) -> None:
        downloader = self.downloader
        frame = ttk.LabelFrame(parent, text="Telegram Notifications", padding=12)
        self._place_settings_section(frame)
        frame.columnconfigure(2, weight=1)

        ttk.Checkbutton(
            frame, text="Enable Telegram bot", variable=downloader.tg_enabled
        ).grid(row=0, column=0, sticky="w", padx=6, pady=4)
        ttk.Label(frame, text="Bot token:").grid(
            row=0, column=1, sticky="e", padx=(12, 4), pady=4
        )
        downloader._tg_token_entry = ttk.Entry(
            frame, textvariable=downloader.tg_token, show="•", width=60
        )
        downloader._tg_token_entry.grid(
            row=0, column=2, sticky="ew", padx=(0, 4), pady=4
        )
        ttk.Button(
            frame, text="Show", width=6, command=downloader._tg_toggle_token
        ).grid(row=0, column=3, padx=2, pady=4)

        ttk.Label(frame, text="Chat ID:").grid(
            row=1, column=1, sticky="e", padx=(12, 4), pady=4
        )
        ttk.Entry(frame, textvariable=downloader.tg_chat_id, width=24).grid(
            row=1, column=2, sticky="w", padx=(0, 4), pady=4
        )
        button_row = ttk.Frame(frame)
        button_row.grid(row=1, column=3, padx=2, pady=4, sticky="e")
        ttk.Button(button_row, text="Test", command=downloader._tg_test).pack(
            side="left", padx=2
        )
        ttk.Button(
            button_row,
            text="Get my Chat ID",
            command=downloader._tg_get_chat_id,
        ).pack(side="left", padx=2)

        ttk.Label(
            frame,
            text="Create a bot with @BotFather, message it once, then use Get my Chat ID.",
            foreground=C_FG_DIM,
            font=("Segoe UI", 8),
        ).grid(row=2, column=0, columnspan=4, sticky="w", padx=6, pady=(4, 2))

        warning = telegram_secret_storage_warning()
        if warning:
            ttk.Label(
                frame,
                text=warning,
                foreground=C_WARN,
                wraplength=900,
            ).grid(row=3, column=0, columnspan=4, sticky="w", padx=6, pady=(2, 4))

    def _build_telegram_behavior(self, parent) -> None:
        downloader = self.downloader
        frame = ttk.LabelFrame(parent, text="Telegram Delivery", padding=12)
        self._place_settings_section(frame)
        for index in range(6):
            frame.columnconfigure(index, weight=1)

        ttk.Checkbutton(
            frame,
            text="Allow FITS sending (/sendfits on)",
            variable=downloader.tg_send_fits_setting,
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=6, pady=4)
        ttk.Checkbutton(
            frame,
            text="Silent notifications",
            variable=downloader.tg_silent_notifications,
        ).grid(row=0, column=2, columnspan=2, sticky="w", padx=6, pady=4)
        ttk.Checkbutton(
            frame,
            text="Send finish buttons",
            variable=downloader.tg_finish_buttons,
        ).grid(row=0, column=4, columnspan=2, sticky="w", padx=6, pady=4)
        ttk.Checkbutton(
            frame,
            text="Auto-send blink GIF after alignment",
            variable=downloader.tg_auto_blink_gif,
        ).grid(row=1, column=0, columnspan=3, sticky="w", padx=6, pady=4)

        fields = (
            ("Max previews", downloader.tg_preview_max, 1, 100, 1),
            ("Max document MB", downloader.tg_document_max_mb, 1, 49, 1),
            ("Pipeline update interval (s)", downloader.tg_dashboard_interval, 0.5, 30.0, 0.5),
            ("Download update interval (s)", downloader.tg_batch_dashboard_interval, 0.5, 30.0, 0.5),
            ("GIF frames", downloader.tg_gif_max_frames, 2, 30, 1),
            ("GIF frame ms", downloader.tg_gif_frame_ms, 100, 5000, 50),
        )
        for index, (label, variable, minimum, maximum, increment) in enumerate(fields):
            row = 2 + index // 2
            column = (index % 2) * 3
            ttk.Label(frame, text=f"{label}:").grid(
                row=row, column=column, sticky="e", padx=6, pady=4
            )
            ttk.Spinbox(
                frame,
                from_=minimum,
                to=maximum,
                increment=increment,
                textvariable=variable,
                width=7,
            ).grid(row=row, column=column + 1, sticky="w", padx=6, pady=4)

    def _build_program_tools(self, parent) -> None:
        frame = ttk.LabelFrame(parent, text="Configuration and Maintenance", padding=12)
        self._place_settings_section(frame)
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="Settings file:").grid(
            row=0, column=0, sticky="e", padx=6, pady=4
        )
        entry = ttk.Entry(frame, state="normal", width=80)
        entry.grid(row=0, column=1, sticky="ew", padx=6, pady=4)
        entry.insert(0, str(settings_file_path()))
        entry.configure(state="readonly")

        buttons = ttk.Frame(frame)
        buttons.grid(row=1, column=0, columnspan=2, sticky="w", padx=6, pady=(6, 2))
        ttk.Button(
            buttons,
            text="Save all defaults",
            style="Accent.TButton",
            command=self._save_now,
        ).pack(side="left", padx=3)
        ttk.Button(
            buttons,
            text="Reset processing defaults",
            command=self._reset_processing_fields,
        ).pack(side="left", padx=3)
        ttk.Button(
            buttons,
            text="Reset Telegram",
            command=self._reset_telegram_fields,
        ).pack(side="left", padx=3)
        ttk.Button(
            buttons,
            text="Open settings folder",
            command=self._open_settings_folder,
        ).pack(side="left", padx=3)
        ttk.Button(
            buttons, text="Open work folder", command=self._open_work_folder
        ).pack(side="left", padx=3)
        ttk.Button(
            buttons, text="Export settings…", command=self._export_settings
        ).pack(side="left", padx=3)
        ttk.Button(
            buttons, text="Import settings…", command=self._import_settings
        ).pack(side="left", padx=3)

        ttk.Label(
            frame,
            text="Changes affect the current tabs immediately. Save all defaults to reuse them after restarting TSDP.",
            foreground=C_FG_DIM,
            font=("Segoe UI", 8),
        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=6, pady=(6, 0))


__all__ = [name for name in globals() if not name.startswith("__")]
