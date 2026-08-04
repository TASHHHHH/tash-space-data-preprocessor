from __future__ import annotations

from tsdp2.ui._common import *

class DownloaderLayoutMixin:
    def _initialize_ui(self, master):
        super().__init__(master)

        # ── pipeline vars ──────────────────────────────────────────────────
        self.pipeline_mode     = tk.StringVar(value="mosaic_align")
        self.work_dir          = tk.StringVar(
            value=str(Path.home() / "JWST_Work"))
        self.combine_fn          = tk.StringVar(value="median")
        self.reference_mode      = tk.StringVar(value="")  # forced blank until user chooses
        self.n_workers           = tk.IntVar(value=max(1, (os.cpu_count() or 4) - 1))
        self.turbo_fullres       = tk.BooleanVar(value=True)
        self.skip_unknown_groups = tk.BooleanVar(value=True)
        self.auto_run_pipeline   = tk.BooleanVar(value=True)
        self.auto_load_aligner = tk.BooleanVar(value=True)
        self.when_done_dialog  = tk.BooleanVar(value=True)
        self.when_done_beep    = tk.BooleanVar(value=False)
        self.when_done_folder  = tk.BooleanVar(value=False)
        self.when_done_shutdown = tk.BooleanVar(value=False)

        self.pipeline_q      = queue.Queue()
        self.pipeline_runner = None
        self.pipeline_start  = None
        self._tg_dashboard_message_id = None
        self._tg_last_dashboard_edit = 0.0
        self._tg_dashboard_lock = threading.Lock()
        self._last_pipeline_resolution = "unknown"
        self._last_pipeline_reference = "Not selected"
        self._last_pipeline_source = ""

        # cross-references set by App after tab creation
        self.aligner_ref  = None
        self.mosaic_tab_ref = None
        self.notebook_ref = None

        # ── Telegram ──────────────────────────────────────────────────────
        _cfg = load_settings()
        self.tg_enabled  = tk.BooleanVar(value=bool(_cfg.get("tg_enabled", False)))
        self.tg_token    = tk.StringVar(value=_cfg.get("tg_token", ""))
        self.tg_chat_id  = tk.StringVar(value=_cfg.get("tg_chat_id", ""))
        self._tg_token_visible = False

        # Extra Telegram/settings-tab options. These are shown in Settings,
        # not in the Downloader tab.
        self.tg_send_fits_setting = tk.BooleanVar(value=bool(_cfg.get("tg_send_fits", False)))
        self.tg_silent_notifications = tk.BooleanVar(value=bool(_cfg.get("tg_silent_notifications", False)))
        self.tg_finish_buttons = tk.BooleanVar(value=bool(_cfg.get("tg_finish_buttons", True)))
        self.tg_auto_blink_gif = tk.BooleanVar(value=bool(_cfg.get("tg_auto_blink_gif", True)))
        self.tg_preview_max = tk.IntVar(value=_safe_int(_cfg.get("tg_preview_max", 30), 30, 1, 100))
        self.tg_document_max_mb = tk.IntVar(value=_safe_int(_cfg.get("tg_document_max_mb", 49), 49, 1, 49))
        self.tg_dashboard_interval = tk.DoubleVar(value=float(_cfg.get("tg_dashboard_interval", 3.0) or 3.0))
        self.tg_batch_dashboard_interval = tk.DoubleVar(value=float(_cfg.get("tg_batch_dashboard_interval", 2.0) or 2.0))
        self.tg_gif_max_frames = tk.IntVar(value=_safe_int(_cfg.get("tg_gif_max_frames", 8), 8, 2, 30))
        self.tg_gif_frame_ms = tk.IntVar(value=_safe_int(_cfg.get("tg_gif_frame_ms", 700), 700, 100, 5000))
        # auto-save whenever any telegram field changes
        for _v in (self.tg_enabled, self.tg_token, self.tg_chat_id):
            _v.trace_add("write", lambda *_: self._save_tg_settings())
        for _v in (self.tg_send_fits_setting, self.tg_silent_notifications,
                   self.tg_finish_buttons, self.tg_auto_blink_gif,
                   self.tg_preview_max, self.tg_document_max_mb,
                   self.tg_dashboard_interval, self.tg_batch_dashboard_interval,
                   self.tg_gif_max_frames, self.tg_gif_frame_ms):
            _v.trace_add("write", lambda *_: self._save_tg_extra_settings())
        self.pipeline_mode.trace_add("write", lambda *_: self._update_reference_mode_state())

        # ── download vars ──────────────────────────────────────────────────
        self.status_queue       = queue.Queue()
        self.rows               = []
        self.next_task_id       = 1
        self.running_workers    = {}
        self.selected_row       = None

        self.aria2_path         = tk.StringVar(
            value=default_aria2_path(_cfg))
        self.download_dir       = tk.StringVar(
            value=default_download_dir(_cfg))
        self.connections        = tk.IntVar(value=16)
        self.splits             = tk.IntVar(value=16)
        self.resume             = tk.BooleanVar(value=True)
        self.disable_allocation = tk.BooleanVar(value=False)
        self.user_agent         = tk.StringVar(value="")
        self.extra_args         = tk.StringVar(value="")
        self.concurrent_limit   = tk.IntVar(value=3)

        # Persist the machine-specific aria2 executable path so users only
        # need to select aria2c.exe once on their own computer.
        for _v in (self.aria2_path, self.download_dir):
            _v.trace_add("write", lambda *_: self._save_download_settings())

        self._build_ui()
        self.add_row()
        self.add_row()
        self.after(100, self.poll_status_queue)
        self.after(150, self.poll_pipeline_queue)

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)   # download queue expands

        # ── Row 0: paths + parallel ────────────────────────────────────────
        top = ttk.Frame(self, padding=10)
        top.grid(row=0, column=0, sticky="ew")
        for i in (1, 4):
            top.columnconfigure(i, weight=1)

        ttk.Label(top, text="aria2 executable").grid(
            row=0, column=0, sticky="w", padx=6, pady=5)
        ttk.Entry(top, textvariable=self.aria2_path).grid(
            row=0, column=1, sticky="ew", padx=6, pady=5)
        ttk.Button(top, text="Browse", command=self.pick_aria2).grid(
            row=0, column=2, padx=6, pady=5)
        ttk.Label(top, text="Download dir").grid(
            row=0, column=3, sticky="w", padx=6, pady=5)
        ttk.Entry(top, textvariable=self.download_dir).grid(
            row=0, column=4, sticky="ew", padx=6, pady=5)
        ttk.Button(top, text="Browse", command=self.pick_download_dir).grid(
            row=0, column=5, padx=6, pady=5)
        ttk.Label(top, text="Parallel").grid(
            row=0, column=6, sticky="w", padx=6, pady=5)
        ttk.Spinbox(top, from_=1, to=50,
                    textvariable=self.concurrent_limit, width=5).grid(
            row=0, column=7, sticky="w", padx=6, pady=5)

        # ── Row 1: download options ────────────────────────────────────────
        opts = ttk.LabelFrame(self, text="Download Options", padding=8)
        opts.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 4))
        for i in range(10):
            opts.columnconfigure(i, weight=1)

        ttk.Label(opts, text="Connections/server").grid(
            row=0, column=0, sticky="w", padx=6, pady=3)
        ttk.Spinbox(opts, from_=1, to=16, textvariable=self.connections,
                    width=6).grid(row=0, column=1, sticky="w", padx=6, pady=3)
        ttk.Label(opts, text="Split count").grid(
            row=0, column=2, sticky="w", padx=6, pady=3)
        ttk.Spinbox(opts, from_=1, to=32, textvariable=self.splits,
                    width=6).grid(row=0, column=3, sticky="w", padx=6, pady=3)
        ttk.Checkbutton(opts, text="Resume (-c)",
                        variable=self.resume).grid(
            row=0, column=4, sticky="w", padx=6, pady=3)
        ttk.Checkbutton(opts, text="No file allocation",
                        variable=self.disable_allocation).grid(
            row=0, column=5, sticky="w", padx=6, pady=3)
        ttk.Label(opts, text="User-Agent").grid(
            row=1, column=0, sticky="w", padx=6, pady=3)
        ttk.Entry(opts, textvariable=self.user_agent).grid(
            row=1, column=1, columnspan=3, sticky="ew", padx=6, pady=3)
        ttk.Label(opts, text="Extra args").grid(
            row=1, column=4, sticky="w", padx=6, pady=3)
        ttk.Entry(opts, textvariable=self.extra_args).grid(
            row=1, column=5, columnspan=5, sticky="ew", padx=6, pady=3)

        # ── Row 2: download queue ──────────────────────────────────────────
        center = ttk.LabelFrame(self, text="Download Queue", padding=8)
        center.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 4))
        center.rowconfigure(0, weight=1)
        center.columnconfigure(0, weight=1)

        self.dl_canvas = tk.Canvas(center, highlightthickness=0)
        self.dl_canvas.grid(row=0, column=0, sticky="nsew")
        yscroll = ttk.Scrollbar(center, orient="vertical",
                                command=self.dl_canvas.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        self.dl_canvas.configure(yscrollcommand=yscroll.set)

        self.rows_frame = ttk.Frame(self.dl_canvas)
        self.rows_frame.columnconfigure(0, weight=1)
        self.window_id = self.dl_canvas.create_window(
            (0, 0), window=self.rows_frame, anchor="nw")
        self.rows_frame.bind(
            "<Configure>",
            lambda e: self.dl_canvas.configure(
                scrollregion=self.dl_canvas.bbox("all")))
        self.dl_canvas.bind(
            "<Configure>",
            lambda e: self.dl_canvas.itemconfig(
                self.window_id, width=e.width))

        # ── Row 3: pipeline panel ──────────────────────────────────────────
        pipe = ttk.LabelFrame(self, text="Post-Download Pipeline", padding=8)
        pipe.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 4))
        pipe.columnconfigure(1, weight=1)

        # Left: mode radios
        mode_f = ttk.Frame(pipe)
        mode_f.grid(row=0, column=0, sticky="nw", padx=(0, 16))
        for i, (val, lbl) in enumerate([
            ("download",    "Download  →  Unzip only"),
            ("align",       "Download  →  Unzip  →  WCS Align"),
            ("mosaic",      "Download  →  Unzip  →  Mosaic only"),
            ("mosaic_align", "Download  →  Unzip  →  Mosaic  →  WCS Align"),
        ]):
            ttk.Radiobutton(mode_f, text=lbl,
                            variable=self.pipeline_mode, value=val).grid(
                row=i, column=0, sticky="w", pady=1)

        # Right: work dir + options
        opt2 = ttk.Frame(pipe)
        opt2.grid(row=0, column=1, sticky="nsew")
        opt2.columnconfigure(1, weight=1)

        ttk.Label(opt2, text="Work / unzip dir:").grid(
            row=0, column=0, sticky="w", padx=6, pady=3)
        ttk.Entry(opt2, textvariable=self.work_dir).grid(
            row=0, column=1, sticky="ew", padx=6, pady=3)
        ttk.Button(opt2, text="Browse",
                   command=self.pick_work_dir).grid(
            row=0, column=2, padx=6, pady=3)

        row1 = ttk.Frame(opt2)
        row1.grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Label(row1, text="Mosaic combine:").pack(side="left", padx=6)
        ttk.Combobox(row1, values=["feather", "median", "mean"],
                     textvariable=self.combine_fn,
                     width=8, state="readonly").pack(side="left", padx=4)
        ttk.Label(row1, text="Threads:").pack(side="left", padx=(14, 4))
        ttk.Spinbox(row1, from_=1, to=64, textvariable=self.n_workers,
                    width=4).pack(side="left", padx=(0, 4))
        ttk.Label(row1, text="(tile reprojections per filter)",
                  foreground="gray").pack(side="left", padx=(0, 8))
        ttk.Checkbutton(row1, text="Auto-run after download",
                        variable=self.auto_run_pipeline).pack(
            side="left", padx=14)
        ttk.Checkbutton(row1, text="Auto-load into Aligner tab",
                        variable=self.auto_load_aligner).pack(
            side="left", padx=4)

        row1b = ttk.Frame(opt2)
        row1b.grid(row=2, column=0, columnspan=3, sticky="w", pady=(2, 0))
        ttk.Checkbutton(
            row1b, text="Full-resolution mosaic (automatic; NVMe recommended)",
            variable=self.turbo_fullres).pack(side="left", padx=6)
        ttk.Checkbutton(row1b, text="Skip UNKNOWN filter group",
                        variable=self.skip_unknown_groups).pack(side="left", padx=(16, 6))
        ttk.Label(row1b,
                  text="(weight maps, context images, catalogs with no filter ID)",
                  foreground="gray").pack(side="left", padx=4)

        refrow = ttk.Frame(opt2)
        refrow.grid(row=3, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Label(refrow, text="Reference frame:").pack(side="left", padx=6)
        self.reference_mode_combo = ttk.Combobox(
            refrow,
            textvariable=self.reference_mode,
            values=["", "Highest resolution", "Ordinary best"],
            width=22,
            state="readonly")
        self.reference_mode_combo.pack(side="left", padx=4)
        ttk.Label(refrow,
                  text="required for WCS Align",
                  foreground="gray").pack(side="left", padx=(4, 0))

        row2 = ttk.Frame(opt2)
        row2.grid(row=4, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Label(row2, text="When done:").pack(side="left", padx=6)
        ttk.Checkbutton(row2, text="Summary",
                        variable=self.when_done_dialog).pack(
            side="left", padx=4)
        ttk.Checkbutton(row2, text="Beep",
                        variable=self.when_done_beep).pack(
            side="left", padx=4)
        ttk.Checkbutton(row2, text="Open folder",
                        variable=self.when_done_folder).pack(
            side="left", padx=4)
        ttk.Checkbutton(row2, text="Shutdown PC",
                        variable=self.when_done_shutdown,
                        command=self._on_shutdown_check).pack(
            side="left", padx=4)

        # ── Row 4: pipeline status ────────────────────────────────────────
        ps = ttk.LabelFrame(self, text="Pipeline Status", padding=6)
        ps.grid(row=4, column=0, sticky="ew", padx=10, pady=(0, 4))
        ps.columnconfigure(0, weight=1)

        self.pipe_prog = ttk.Progressbar(
            ps, orient="horizontal", mode="determinate", length=400)
        self.pipe_prog.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        self.pipe_lbl = ttk.Label(ps, text="Idle", anchor="w")
        self.pipe_lbl.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ps.columnconfigure(1, weight=1)

        pbtn = ttk.Frame(ps)
        pbtn.grid(row=0, column=2)
        ttk.Button(pbtn, text="Run Pipeline…",
                   command=self.run_pipeline_manual,
                   style="Accent.TButton").pack(
            side="left", padx=4)
        self.pipe_cancel_btn = ttk.Button(
            pbtn, text="Cancel",
            command=self.cancel_pipeline, state="disabled")
        self.pipe_cancel_btn.pack(side="left", padx=4)

        # ── Row 5: controls + download log ───────────────────────────────
        bot = ttk.Frame(self, padding=(10, 0, 10, 8))
        bot.grid(row=5, column=0, sticky="nsew")
        bot.columnconfigure(1, weight=1)

        ctrl = ttk.LabelFrame(bot, text="Controls", padding=6)
        ctrl.grid(row=0, column=0, sticky="ns", padx=(0, 6))
        ttk.Button(ctrl, text="Add URL",
                   command=self.add_row).grid(
            row=0, column=0, padx=4, pady=4, sticky="ew")
        ttk.Button(ctrl, text="Import MAST CSV…",
                   command=self._import_mast_csv).grid(
            row=0, column=1, padx=4, pady=4, sticky="ew")
        ttk.Button(ctrl, text="Start All",
                   command=self.start_all, style="Accent.TButton").grid(
            row=0, column=2, padx=4, pady=4, sticky="ew")
        ttk.Button(ctrl, text="Stop All",
                   command=self.stop_all).grid(
            row=0, column=3, padx=4, pady=4, sticky="ew")
        ttk.Button(ctrl, text="Export .txt",
                   command=self.export_input_file).grid(
            row=0, column=4, padx=4, pady=4, sticky="ew")

        lf = ttk.LabelFrame(bot, text="Download Logs", padding=6)
        lf.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        lf.rowconfigure(0, weight=1)
        lf.columnconfigure(0, weight=1)
        self.log_text = tk.Text(lf, height=7, wrap="word")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        lsc = ttk.Scrollbar(lf, orient="vertical",
                            command=self.log_text.yview)
        lsc.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=lsc.set)

        # Telegram account/settings UI was moved to the Settings tab.
        # Keep the Downloader focused on downloads and the post-download pipeline.

        self._update_reference_mode_state()

__all__ = [name for name in globals() if not name.startswith('__')]
