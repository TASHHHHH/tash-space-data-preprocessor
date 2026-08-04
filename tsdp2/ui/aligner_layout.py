from __future__ import annotations

from tsdp2.ui._common import *

class AlignerLayoutMixin:
    def _initialize_ui(self, master):
        super().__init__(master)

        self.reference_path = tk.StringVar()
        self.reference_name = tk.StringVar(value="reference")
        self.output_dir = tk.StringVar()

        self.num_filters = tk.IntVar(value=3)
        self.rows = []

        self.normalize_enabled = tk.BooleanVar(value=False)
        self.normalize_mode = tk.StringVar(value="median")
        self.psf_match_enabled = tk.BooleanVar(value=False)
        self.keep_reference_canvas = tk.BooleanVar(value=False)
        self.generate_diagnostics = tk.BooleanVar(value=True)
        self.retain_intermediates = tk.BooleanVar(value=False)

        self.preview_visible = False
        self.stretch_mode = tk.StringVar(value="asinh")
        self.stretch_mode.trace_add("write", lambda *_: self.update_all_previews())
        self.ref_photo = None      # GC anchor for reference thumbnail
        self.last_run_outputs = []  # populated after each successful run
        self._align_running = False
        self.pipeline_progress_cb = None  # optional callback used by Downloader Telegram dashboard
        self.pipeline_done_cb = None      # optional callback used by Downloader Telegram dashboard
        self.telegram_ref = None          # DownloaderTab-like object used for direct Aligner tab Telegram notifications

        self.cancel_requested = False
        self.preset = tk.StringVar(value="Custom")

        self._build()

    def _build(self):
        root = ttk.Frame(self)
        root.pack(fill="both", expand=True, padx=12, pady=12)

        ttk.Label(root, text="Reference", font=("Segoe UI", 12, "bold")).grid(row=0, column=0, sticky="w", padx=6, pady=6)

        ref_frame = ttk.Frame(root)
        ref_frame.grid(row=1, column=0, columnspan=4, sticky="w")

        ttk.Label(ref_frame, text="Reference FITS:").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(ref_frame, textvariable=self.reference_path, width=70).grid(row=0, column=1, sticky="w", padx=6, pady=4)
        ttk.Button(ref_frame, text="Browse", command=self.pick_reference).grid(row=0, column=2, sticky="w", padx=6, pady=4)

        ttk.Label(ref_frame, text="Output name:").grid(row=0, column=3, sticky="w", padx=6, pady=4)
        ttk.Entry(ref_frame, textvariable=self.reference_name, width=22).grid(row=0, column=4, sticky="w", padx=6, pady=4)
        ttk.Button(ref_frame, text="Auto-name", command=self.auto_name_reference).grid(row=0, column=5, sticky="w", padx=6, pady=4)

        ttk.Button(ref_frame, text="Auto-pick reference", command=self.auto_pick_reference).grid(row=0, column=6, sticky="w", padx=6, pady=4)
        ttk.Button(ref_frame, text="Check WCS / skip", command=self.check_wcs_skip_status).grid(row=0, column=7, sticky="w", padx=6, pady=4)

        self.ref_size_lbl = ttk.Label(ref_frame, text="",
                                       foreground="#888", font=("Segoe UI", 8))
        self.ref_size_lbl.grid(row=0, column=8, sticky="w", padx=10, pady=4)

        # ── reference preview panel (toggled globally) ─────────────────────
        self.ref_pf = ttk.Frame(ref_frame, width=THUMB_SIZE + 6, height=THUMB_SIZE + 22)
        self.ref_pf.grid_propagate(False)
        self.ref_pf.grid(row=1, column=0, columnspan=7, sticky="w", padx=6, pady=4)
        self.ref_pf.grid_remove()

        self.ref_img_lbl = tk.Label(
            self.ref_pf, text="—",
            bg="#101118", fg="#666",
            font=("Segoe UI", 8), anchor="center",
        )
        self.ref_img_lbl.place(relx=0, rely=0, relwidth=1, relheight=0.86)

        self.ref_info_lbl = tk.Label(
            self.ref_pf, text="",
            bg="#101118", fg="#999",
            font=("Segoe UI", 7), anchor="sw",
        )
        self.ref_info_lbl.place(relx=0.02, rely=0.87, relwidth=0.98, relheight=0.13)

        ttk.Separator(root).grid(row=2, column=0, columnspan=4, sticky="ew", pady=10)

        ttk.Label(root, text="Moving filters (blank rows are ignored)", font=("Segoe UI", 12, "bold")).grid(
            row=3, column=0, sticky="w", padx=6, pady=6
        )

        control = ttk.Frame(root)
        control.grid(row=4, column=0, columnspan=4, sticky="w")

        ttk.Label(control, text="Preset:").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        ttk.Combobox(
            control,
            values=["Custom", "NIRCam 3-filter RGB", "NIRCam 8-filter luminance workflow", "MIRI 5-filter set"],
            textvariable=self.preset,
            width=34,
            state="readonly",
        ).grid(row=0, column=1, sticky="w", padx=6, pady=4)
        ttk.Button(control, text="Apply preset", command=self.apply_preset).grid(row=0, column=2, sticky="w", padx=6, pady=4)

        ttk.Label(control, text="How many rows?").grid(row=1, column=0, sticky="w", padx=6, pady=4)

        options = list(range(1, 61))
        self.combo = ttk.Combobox(control, values=options, width=6, state="readonly")
        self.combo.set(str(self.num_filters.get()))
        self.combo.grid(row=1, column=1, sticky="w", padx=6, pady=4)
        ttk.Button(control, text="Apply", command=self.apply_num_filters).grid(row=1, column=2, sticky="w", padx=6, pady=4)

        ttk.Button(control, text="Batch Add Folders...", command=self.batch_add_folders).grid(row=1, column=3, sticky="w", padx=16, pady=4)
        ttk.Label(control, text="(pick a main folder; each subfolder = one filter)").grid(row=1, column=4, sticky="w", padx=6, pady=4)

        ttk.Button(control, text="Add FITS Folder...", command=self.add_fits_folder_flat).grid(row=1, column=5, sticky="w", padx=16, pady=4)
        ttk.Label(control, text="(pick a folder of loose FITS files; no subfolders needed)").grid(row=1, column=6, sticky="w", padx=6, pady=4)

        # ── preview toggle + stretch controls ──────────────────────────────
        prev_bar = ttk.Frame(control)
        prev_bar.grid(row=2, column=0, columnspan=5, sticky="w", pady=(4, 6))

        self.preview_btn = ttk.Button(prev_bar, text="Show Previews  ▶",
                                       command=self.toggle_previews)
        self.preview_btn.pack(side="left", padx=(6, 14))

        ttk.Label(prev_bar, text="Stretch:").pack(side="left", padx=(0, 4))
        ttk.Combobox(
            prev_bar,
            values=STRETCH_MODES,
            textvariable=self.stretch_mode,
            width=9,
            state="readonly",
        ).pack(side="left")

        ttk.Button(prev_bar, text="Refresh All",
                   command=self.update_all_previews).pack(side="left", padx=8)

        if not HAVE_PIL:
            ttk.Label(prev_bar,
                      text="  ⚠  install Pillow to enable previews",
                      foreground="#c04000").pack(side="left")

        ttk.Separator(root).grid(row=5, column=0, columnspan=4, sticky="ew", pady=10)

        self.canvas = tk.Canvas(root, height=420)
        self.scroll = ttk.Scrollbar(root, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scroll.set)

        self.scroll.grid(row=6, column=3, sticky="ns")
        self.canvas.grid(row=6, column=0, columnspan=3, sticky="nsew")

        root.grid_rowconfigure(6, weight=1)
        root.grid_columnconfigure(2, weight=1)

        self.filters_container = ttk.Frame(self.canvas)
        self.canvas_window = self.canvas.create_window((0, 0), window=self.filters_container, anchor="nw")

        self.filters_container.bind("<Configure>", self._on_frame_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        self._rebuild_filter_rows(self.num_filters.get())

        ttk.Separator(root).grid(row=7, column=0, columnspan=4, sticky="ew", pady=10)

        opt = ttk.Frame(root)
        opt.grid(row=8, column=0, columnspan=4, sticky="w")

        ttk.Checkbutton(opt, text="Normalize to reference", variable=self.normalize_enabled).grid(row=0, column=0, sticky="w", padx=6, pady=4)
        ttk.Label(opt, text="Mode:").grid(row=0, column=1, sticky="w", padx=6, pady=4)
        ttk.Combobox(opt, values=["median", "p95", "background"], textvariable=self.normalize_mode, width=12, state="readonly").grid(row=0, column=2, sticky="w", padx=6, pady=4)

        ttk.Checkbutton(opt, text="PSF match to worst (approx)", variable=self.psf_match_enabled).grid(row=0, column=3, sticky="w", padx=16, pady=4)

        ttk.Checkbutton(
            opt,
            text="Keep full reference canvas (allow partial overlays)",
            variable=self.keep_reference_canvas,
        ).grid(row=1, column=0, columnspan=3, sticky="w", padx=6, pady=4)
        ttk.Label(
            opt,
            text="Euclid stays full-size; Hubble is WCS-aligned with NaN outside its footprint.",
            foreground="#666",
        ).grid(row=1, column=3, sticky="w", padx=16, pady=4)

        out_frame = ttk.Frame(root)
        out_frame.grid(row=9, column=0, columnspan=4, sticky="w")

        ttk.Label(out_frame, text="Output directory:").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(out_frame, textvariable=self.output_dir, width=70).grid(row=0, column=1, sticky="w", padx=6, pady=4)
        ttk.Button(out_frame, text="Browse", command=self.pick_output_dir).grid(row=0, column=2, sticky="w", padx=6, pady=4)

        folder_buttons = ttk.Frame(out_frame)
        folder_buttons.grid(row=1, column=0, columnspan=3, sticky="w", padx=6)
        for label, category in (
            ("Open Job Folder", "root"),
            ("Open Final Results", "final"),
            ("Open Diagnostics", "diagnostics"),
            ("Open Intermediate Files", "intermediate"),
            ("Open Recovery", "recovery"),
        ):
            ttk.Button(
                folder_buttons, text=label,
                command=lambda key=category: self._open_job_folder(key),
            ).pack(side="left", padx=(0, 4), pady=(0, 4))
        ttk.Checkbutton(
            out_frame, text="Generate diagnostic masks and footprints",
            variable=self.generate_diagnostics,
        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=6)
        ttk.Checkbutton(
            out_frame, text="Retain intermediate files after success",
            variable=self.retain_intermediates,
        ).grid(row=2, column=2, sticky="w", padx=6)

        run_frame = ttk.Frame(root)
        run_frame.grid(row=10, column=0, columnspan=4, sticky="ew", padx=6, pady=10)
        run_frame.grid_columnconfigure(1, weight=1)

        self.progress = ttk.Progressbar(run_frame, orient="horizontal", length=420, mode="determinate")
        self.progress.grid(row=0, column=0, sticky="w", padx=6, pady=4)

        self.status = ttk.Label(run_frame, text="Idle")
        self.status.grid(row=0, column=1, sticky="w", padx=10, pady=4)

        self.blink_btn = ttk.Button(run_frame, text="Blink Outputs  ▶",
                                      command=self.open_blink_window,
                                      state="disabled")
        self.blink_btn.grid(row=0, column=2, sticky="e", padx=6, pady=4)
        ttk.Button(run_frame, text="Run (Align + Common Crop)",
                   command=self.run, style="Accent.TButton").grid(
                       row=0, column=3, sticky="e", padx=6, pady=4)
        ttk.Button(run_frame, text="Cancel",
                   command=self.cancel).grid(row=0, column=4, sticky="e", padx=6, pady=4)

    def _on_frame_configure(self, event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfig(self.canvas_window, width=event.width)

    def apply_preset(self):
        p = self.preset.get()
        if p == "NIRCam 3-filter RGB":
            self.num_filters.set(3)
            self.combo.set("3")
            self._rebuild_filter_rows(3)
            self.normalize_enabled.set(True)
            self.normalize_mode.set("median")
            self.psf_match_enabled.set(False)
            defaults = ["nircam_F115W", "nircam_F210M", "nircam_F356W"]
            for i, r in enumerate(self.rows):
                r.name.set(defaults[i])

        elif p == "NIRCam 8-filter luminance workflow":
            self.num_filters.set(8)
            self.combo.set("8")
            self._rebuild_filter_rows(8)
            self.normalize_enabled.set(True)
            self.normalize_mode.set("median")
            self.psf_match_enabled.set(True)
            defaults = ["nircam_F115W", "nircam_F140M", "nircam_F150W", "nircam_F210M",
                        "nircam_F277W", "nircam_F335M", "nircam_F356W", "nircam_F480M"]
            for i, r in enumerate(self.rows):
                r.name.set(defaults[i])

        elif p == "MIRI 5-filter set":
            self.num_filters.set(5)
            self.combo.set("5")
            self._rebuild_filter_rows(5)
            self.normalize_enabled.set(True)
            self.normalize_mode.set("median")
            self.psf_match_enabled.set(False)
            defaults = ["miri_F770W", "miri_F1000W", "miri_F1130W", "miri_F1500W", "miri_F2550W"]
            for i, r in enumerate(self.rows):
                r.name.set(defaults[i])

    def pick_reference(self):
        p = filedialog.askopenfilename(
            title="Select reference FITS",
            filetypes=[("FITS files", "*.fits *.fit *.fts"), ("All files", "*.*")]
        )
        if p:
            self.reference_path.set(p)
            if self.reference_name.get().strip().lower() in ("reference", "ref", ""):
                self.reference_name.set(suggest_name_from_fits(p))
            self._update_ref_size(p)
            if self.preview_visible:
                self._update_ref_preview(self.stretch_mode.get())

    def auto_name_reference(self):
        p = self.reference_path.get().strip()
        if not p or not os.path.exists(p):
            messagebox.showerror("No reference", "Pick a reference FITS first.")
            return
        self.reference_name.set(suggest_name_from_fits(p))

    def pick_output_dir(self):
        p = filedialog.askdirectory(title="Select output directory")
        if p:
            self.output_dir.set(p)

    def _open_job_folder(self, category: str) -> None:
        paths = getattr(self, "_active_job_paths", None)
        if not isinstance(paths, JobPaths):
            messagebox.showinfo(
                "No job yet", "Start an alignment job first.", parent=self)
            return
        target = paths.root if category == "root" else getattr(paths, category)
        try:
            open_folder(target)
        except (OSError, FileNotFoundError) as exc:
            messagebox.showinfo("Folder not available", str(exc), parent=self)

    def browse_row(self, row: FilterRow):
        p = filedialog.askopenfilename(
            title=f"Select FITS for Filter {row.idx}",
            filetypes=[("FITS files", "*.fits *.fit *.fts"), ("All files", "*.*")]
        )
        if p:
            row.path.set(p)
            cur = row.name.get().strip().lower()
            if cur in ("", f"filter_{row.idx}", f"filter{row.idx}"):
                row.name.set(suggest_name_from_fits(p))
            row.update_size(p)
            if self.preview_visible:
                row.update_preview(self.stretch_mode.get())

    def auto_name_row(self, row: FilterRow):
        p = row.path.get().strip()
        if not p or not os.path.exists(p):
            messagebox.showerror("No file", f"Pick a FITS file for Filter {row.idx} first.")
            return
        row.name.set(suggest_name_from_fits(p))

    def apply_num_filters(self):
        try:
            n = int(self.combo.get().strip())
        except EXPECTED_OPERATION_ERRORS:
            messagebox.showerror("Invalid number", "Pick a valid number of rows.")
            return
        if n < 1:
            messagebox.showerror("Invalid number", "Need at least 1 row.")
            return
        self.num_filters.set(n)
        self._rebuild_filter_rows(n)

    def _rebuild_filter_rows(self, n: int):
        old = [(r.path.get(), r.name.get()) for r in self.rows]

        for r in self.rows:
            r.destroy()
        self.rows = []

        for i in range(1, n + 1):
            r = FilterRow(self.filters_container, i,
                          self.browse_row, self.auto_name_row, app_ref=self)
            r.grid(row=i - 1)

            if i - 1 < len(old):
                p, nm = old[i - 1]
                r.path.set(p)
                r.name.set(nm)
                if p and os.path.exists(p):
                    r.update_size(p)
            else:
                r.path.set("")
                r.name.set(f"filter_{i}")

            if self.preview_visible:
                r.show_preview()

            self.rows.append(r)

        if self.preview_visible:
            self.update_all_previews()

__all__ = [name for name in globals() if not name.startswith('__')]
