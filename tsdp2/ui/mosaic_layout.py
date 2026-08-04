from __future__ import annotations

from tsdp2.ui._common import *

class MosaicLayoutMixin:
    def _initialize_ui(self, master):
        super().__init__(master)
        self.fits_paths  = []    # flat list of all loaded paths
        self.groups      = {}    # {filter_code: [path, ...]}
        self.active_filters = set()  # filter codes selected for processing
        self.mosaic_q    = queue.Queue()
        self.mosaic_runner = None
        self.seam_runner = None
        self.background_runner = None
        self.mosaic_start  = None

        self.out_dir    = tk.StringVar(value=str(Path.home() / "JWST_Mosaics"))
        self.combine_fn = tk.StringVar(value="feather")
        self.n_workers  = tk.IntVar(value=max(1, (os.cpu_count() or 4) - 1))
        self.downscale_global = tk.StringVar(value="1.0")
        self.draft_scale = tk.StringVar(value="0.08")
        self.draft_background_cleanup = tk.BooleanVar(value=True)
        self.highres_background_cleanup = tk.BooleanVar(value=True)
        self.seam_strength = tk.StringVar(value="1.0")
        self.scratch_dir = tk.StringVar(
            value=str(Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) /
                      "JWST_Suite" / "Mosaic_Scratch"))
        self.filter_downscales = {}  # {filter_code: "0.5"}; blank/missing means global
        self.auto_send  = tk.BooleanVar(value=True)
        self.sky_region = None       # interactive WCS-coverage crop shared by all filters
        self.save_coverage_maps = tk.BooleanVar(value=True)
        self.resume_cache = tk.BooleanVar(value=True)
        self.turbo_fullres = tk.BooleanVar(value=True)
        self.retain_intermediates = tk.BooleanVar(value=False)

        # cross-references set by App
        self.aligner_ref  = None
        self.notebook_ref = None
        self.telegram_ref = None  # DownloaderTab-like object used for direct Mosaic tab Telegram notifications

        self._build()
        self.after(120, self._poll)

    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        # ── Toolbar ──────────────────────────────────────────────────────
        tb = ttk.Frame(self, padding=8)
        tb.grid(row=0, column=0, sticky="ew")
        ttk.Button(tb, text="Add Files…",
                   command=self.add_files).pack(side="left", padx=4)
        ttk.Button(tb, text="Add Folder…",
                   command=self.add_folder).pack(side="left", padx=4)
        ttk.Button(tb, text="Re-group",
                   command=self.regroup).pack(side="left", padx=4)
        ttk.Button(tb, text="Select Filters…",
                   command=self.select_filters).pack(side="left", padx=4)
        ttk.Button(tb, text="WCS Coverage…",
                   command=self.show_wcs_coverage).pack(side="left", padx=4)
        self._repair_btn = ttk.Button(
            tb, text="Repair Existing Mosaic…",
            command=self.repair_existing_mosaic)
        self._repair_btn.pack(side="left", padx=4)
        self._clean_btn = ttk.Button(
            tb, text="Clean Existing High-Res...",
            command=self.clean_existing_background)
        self._clean_btn.pack(side="left", padx=4)
        self._clear_area_btn = ttk.Button(
            tb, text="Clear Area", command=self.clear_sky_region, state="disabled")
        self._clear_area_btn.pack(side="left", padx=4)
        self._area_lbl = ttk.Label(tb, text="Full automatic area", foreground="gray")
        self._area_lbl.pack(side="left", padx=(4, 8))
        self._active_lbl = ttk.Label(tb, text="All filters selected", foreground="gray")
        self._active_lbl.pack(side="left", padx=8)
        ttk.Separator(tb, orient="vertical").pack(
            side="left", padx=10, fill="y", pady=4)
        ttk.Button(tb, text="Clear All",
                   command=self.clear_all).pack(side="left", padx=4)
        self._count_lbl = ttk.Label(tb, text="0 files  |  0 groups",
                                     foreground="gray")
        self._count_lbl.pack(side="left", padx=16)

        # ── Group treeview ────────────────────────────────────────────────
        tf = ttk.LabelFrame(self, text="Filter Groups", padding=6)
        tf.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 4))
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(tf, columns=("path", "info"),
                                  selectmode="browse")
        self.tree.heading("#0",     text="Filter / File")
        self.tree.heading("path",   text="Full path")
        self.tree.heading("info",   text="Info")
        self.tree.column("#0",    width=260, minwidth=160)
        self.tree.column("path",  width=500, minwidth=200)
        self.tree.column("info",  width=120, minwidth=80, anchor="e")
        self.tree.grid(row=0, column=0, sticky="nsew")

        sb_y = ttk.Scrollbar(tf, orient="vertical",   command=self.tree.yview)
        sb_y.grid(row=0, column=1, sticky="ns")
        sb_x = ttk.Scrollbar(tf, orient="horizontal", command=self.tree.xview)
        sb_x.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=sb_y.set, xscrollcommand=sb_x.set)

        ttk.Label(tf,
                  text="Right-click a group to rename / add files.  "
                       "Use WCS Coverage to preview sky footprint before mosaicking.",
                  foreground="gray", font=("Segoe UI", 8)).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(2, 0))

        self._ctx = tk.Menu(self, tearoff=0)
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<Button-2>", self._on_right_click)   # macOS

        # ── Options ──────────────────────────────────────────────────────
        opt = ttk.LabelFrame(self, text="Options", padding=8)
        opt.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 4))
        opt.columnconfigure(1, weight=1)

        ttk.Label(opt, text="Output dir:").grid(
            row=0, column=0, sticky="w", padx=6, pady=3)
        ttk.Entry(opt, textvariable=self.out_dir).grid(
            row=0, column=1, sticky="ew", padx=6, pady=3)
        ttk.Button(opt, text="Browse",
                   command=self._pick_out).grid(
            row=0, column=2, padx=6, pady=3)

        ttk.Label(opt, text="Recovery scratch:").grid(
            row=1, column=0, sticky="w", padx=6, pady=3)
        ttk.Entry(opt, textvariable=self.scratch_dir).grid(
            row=1, column=1, sticky="ew", padx=6, pady=3)
        ttk.Button(opt, text="Browse",
                   command=self._pick_scratch).grid(
            row=1, column=2, padx=6, pady=3)

        orow = ttk.Frame(opt)
        orow.grid(row=2, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Label(orow, text="Combine:").pack(side="left", padx=6)
        ttk.Combobox(orow, values=["feather", "median", "mean"],
                     textvariable=self.combine_fn,
                     width=8, state="readonly").pack(side="left", padx=4)
        ttk.Label(orow, text="Threads:").pack(side="left", padx=(14, 4))
        ttk.Spinbox(orow, from_=1, to=64, textvariable=self.n_workers,
                    width=4).pack(side="left")
        ttk.Label(orow, text="(per filter group)",
                  foreground="gray").pack(side="left", padx=(4, 12))
        ttk.Label(orow, text="Scale:").pack(side="left", padx=(8, 4))
        ttk.Spinbox(orow, from_=0.02, to=1.0, increment=0.05,
                    textvariable=self.downscale_global, width=6).pack(side="left")
        ttk.Label(orow, text="1=full, 0.5=half",
                  foreground="gray").pack(side="left", padx=(4, 8))
        ttk.Button(orow, text="Per-filter scale…",
                   command=self.edit_filter_scales).pack(side="left", padx=(0, 10))
        ttk.Checkbutton(orow,
                        text="Auto-send outputs to Aligner tab when done",
                        variable=self.auto_send).pack(side="left", padx=4)

        xrow = ttk.Frame(opt)
        xrow.grid(row=3, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Checkbutton(
            xrow, text="Save coverage + relative weight FITS maps",
            variable=self.save_coverage_maps).pack(side="left", padx=6)
        ttk.Checkbutton(
            xrow, text="Resume interrupted tile cache",
            variable=self.resume_cache).pack(side="left", padx=(16, 6))
        ttk.Button(xrow, text="Inspect / Preflight…",
                   command=self.show_preflight).pack(side="left", padx=(18, 6))
        ttk.Label(xrow, text="Draft scale:").pack(side="left", padx=(18, 4))
        ttk.Spinbox(xrow, from_=0.02, to=0.25, increment=0.01,
                    textvariable=self.draft_scale, width=6).pack(side="left")
        ttk.Checkbutton(
            xrow, text="Clean blocky draft background",
            variable=self.draft_background_cleanup).pack(side="left", padx=(16, 6))
        ttk.Checkbutton(
            xrow, text="Also write cleaned high-res copy",
            variable=self.highres_background_cleanup).pack(side="left", padx=(16, 6))

        turbo_row = ttk.Frame(opt)
        turbo_row.grid(row=4, column=0, columnspan=3, sticky="w", pady=(3, 0))
        ttk.Checkbutton(
            turbo_row,
            text="Full-resolution processing",
            variable=self.turbo_fullres).pack(side="left", padx=6)
        ttk.Label(
            turbo_row,
            text="Parallelism adapts to current RAM, CPU threads, output size, and tile count; failed tiles retry safely.",
            foreground=C_GOOD).pack(side="left", padx=(6, 0))
        ttk.Checkbutton(
            turbo_row, text="Retain intermediate files after success",
            variable=self.retain_intermediates,
        ).pack(side="left", padx=(18, 6))

        # ── Run bar ──────────────────────────────────────────────────────
        rb = ttk.Frame(self, padding=(10, 4, 10, 10))
        rb.grid(row=3, column=0, sticky="ew")
        rb.columnconfigure(3, weight=1)

        self._run_btn = ttk.Button(rb, text="Run Mosaic",
                                    command=self.run_mosaic,
                                    style="Accent.TButton")
        self._run_btn.grid(row=0, column=0, padx=4)
        self._draft_btn = ttk.Button(
            rb, text="Fast Draft", command=lambda: self.run_mosaic(draft=True))
        self._draft_btn.grid(row=0, column=1, padx=4)
        self._cancel_btn = ttk.Button(rb, text="Cancel",
                                       command=self.cancel_mosaic,
                                       state="disabled")
        self._cancel_btn.grid(row=0, column=2, padx=4)
        self._prog = ttk.Progressbar(rb, orient="horizontal",
                                      mode="determinate", length=360)
        self._prog.grid(row=0, column=3, sticky="ew", padx=8)
        self._stat = ttk.Label(rb, text="Idle", anchor="w")
        self._stat.grid(row=0, column=4, sticky="ew", padx=4)
        folder_buttons = ttk.Frame(rb)
        folder_buttons.grid(row=1, column=0, columnspan=5,
                            sticky="w", pady=(6, 0))
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
            ).pack(side="left", padx=(0, 4))
        rb.columnconfigure(3, weight=1)
        rb.columnconfigure(4, weight=1)

__all__ = [name for name in globals() if not name.startswith('__')]
