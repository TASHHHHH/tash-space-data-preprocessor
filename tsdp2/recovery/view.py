from __future__ import annotations

from tsdp2.ui._common import *

class RecoveryViewMixin:
    def _initialize_ui(self, master):
        super().__init__(master)
        self.mosaic_ref = None
        self.roots = []
        self.source_paths = []
        self.items = {}
        self.work_q = queue.Queue()
        self.worker = None
        self.cancel_event = threading.Event()
        self.output_dir = tk.StringVar(value=str(Path.home() / "JWST_Recovered"))
        self.scale_var = tk.StringVar(value="0.5")
        self.filter_var = tk.StringVar(value="")
        self.source_label = tk.StringVar(value="No original FITS inputs loaded")
        self.status_var = tk.StringVar(value="Add the failed run's temp folder to begin.")
        self._build()
        self.after(150, self._poll)

    @staticmethod
    def _size_text(value):
        try:
            value = int(value)
        except EXPECTED_OPERATION_ERRORS:
            return ""
        units = ("B", "KB", "MB", "GB", "TB")
        size = float(value)
        for unit in units:
            if size < 1024 or unit == units[-1]:
                return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
            size /= 1024.0
        return f"{value:,} B"

    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        top = ttk.Frame(self, padding=8)
        top.grid(row=0, column=0, sticky="ew")
        ttk.Button(top, text="Add temp folder...", command=self.add_temp_folder).pack(side="left", padx=3)
        ttk.Button(top, text="Refresh", command=self.refresh).pack(side="left", padx=3)
        ttk.Button(top, text="Add original FITS...", command=self.add_source_files).pack(side="left", padx=(14, 3))
        ttk.Button(top, text="Add FITS folder...", command=self.add_source_folder).pack(side="left", padx=3)
        ttk.Button(top, text="Use Mosaic-tab inputs", command=self.use_mosaic_inputs).pack(side="left", padx=3)
        ttk.Button(top, text="Clear", command=self.clear).pack(side="right", padx=3)

        box = ttk.LabelFrame(self, text="Recovery candidates", padding=6)
        box.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 5))
        box.rowconfigure(0, weight=1)
        box.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            box, columns=("kind", "size", "status", "path"), selectmode="extended")
        self.tree.heading("#0", text="Session / File")
        self.tree.heading("kind", text="Kind")
        self.tree.heading("size", text="Size")
        self.tree.heading("status", text="Recovery status")
        self.tree.heading("path", text="Full path")
        self.tree.column("#0", width=260, minwidth=150)
        self.tree.column("kind", width=145, minwidth=90)
        self.tree.column("size", width=95, minwidth=70, anchor="e")
        self.tree.column("status", width=270, minwidth=150)
        self.tree.column("path", width=540, minwidth=220)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sy = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
        sx = ttk.Scrollbar(box, orient="horizontal", command=self.tree.xview)
        sy.grid(row=0, column=1, sticky="ns")
        sx.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        self.tree.bind("<Double-1>", lambda _event: self.validate_selected())

        setup = ttk.LabelFrame(self, text="Rebuild WCS and export", padding=8)
        setup.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 5))
        setup.columnconfigure(1, weight=1)
        ttk.Label(setup, textvariable=self.source_label, foreground="gray").grid(
            row=0, column=0, columnspan=5, sticky="w", padx=5, pady=(0, 5))
        ttk.Label(setup, text="Output folder:").grid(row=1, column=0, sticky="e", padx=5, pady=3)
        ttk.Entry(setup, textvariable=self.output_dir).grid(row=1, column=1, sticky="ew", padx=5, pady=3)
        ttk.Button(setup, text="Browse", command=self.pick_output).grid(row=1, column=2, padx=5, pady=3)
        ttk.Label(setup, text="Failed-run scale:").grid(row=1, column=3, sticky="e", padx=(16, 4), pady=3)
        ttk.Entry(setup, textvariable=self.scale_var, width=8).grid(row=1, column=4, sticky="w", pady=3)
        ttk.Label(setup, text="Filter override:").grid(row=2, column=3, sticky="e", padx=(16, 4), pady=3)
        ttk.Entry(setup, textvariable=self.filter_var, width=12).grid(row=2, column=4, sticky="w", pady=3)
        ttk.Label(
            setup,
            text="Legacy temp folders need the same original FITS tiles and scale. New runs store these automatically in recovery_manifest.json.",
            foreground="gray", font=("Segoe UI", 8)).grid(
                row=2, column=0, columnspan=3, sticky="w", padx=5, pady=3)

        actions = ttk.Frame(self, padding=(10, 4, 10, 4))
        actions.grid(row=3, column=0, sticky="ew")
        ttk.Button(actions, text="Inspect / validate", command=self.validate_selected).pack(side="left", padx=3)
        ttk.Button(actions, text="Recover .f32 to WCS FITS", command=self.recover_selected).pack(side="left", padx=3)
        ttk.Button(actions, text="Fast repair black holes", command=self.repair_holes_selected).pack(side="left", padx=3)
        ttk.Button(actions, text="Copy selected to safe folder", command=self.copy_selected).pack(side="left", padx=3)
        ttk.Button(actions, text="Open containing folder", command=self.open_selected_folder).pack(side="left", padx=3)
        ttk.Button(actions, text="Delete selected temp...", command=self.delete_selected).pack(side="right", padx=3)

        status = ttk.Frame(self, padding=(10, 0, 10, 10))
        status.grid(row=4, column=0, sticky="ew")
        status.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(status, mode="determinate", maximum=100)
        self.progress.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ttk.Button(status, text="Cancel", command=self.cancel).grid(row=0, column=1, padx=3)
        ttk.Label(status, textvariable=self.status_var, anchor="w").grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))

    def _selected_paths(self):
        result = []
        for iid in self.tree.selection():
            item = self.items.get(iid)
            if item and item.get("path"):
                result.append(item["path"])
        return result

    def _set_busy(self, message):
        self.status_var.set(message)
        self.progress["value"] = 0

    def add_temp_folder(self):
        path = filedialog.askdirectory(title="Select failed run temp folder or its parent")
        if not path:
            return
        path = os.path.abspath(path)
        if path not in self.roots:
            self.roots.append(path)
        self.refresh()

    def clear(self):
        if self.worker and self.worker.is_alive():
            messagebox.showwarning("Recovery busy", "Cancel or wait for the current recovery action.")
            return
        self.roots.clear()
        self.source_paths.clear()
        self.items.clear()
        self.tree.delete(*self.tree.get_children())
        self.source_label.set("No original FITS inputs loaded")
        self.status_var.set("Cleared.")

__all__ = [name for name in globals() if not name.startswith('__')]
