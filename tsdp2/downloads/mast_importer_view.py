from __future__ import annotations

from tsdp2.ui._common import *

class MastImporterViewMixin:
    def _initialize_ui(self, master, rows: list, on_add_cb):
        super().__init__(master)
        self.title("Import MAST CSV")
        self.geometry("1220x620")
        self.resizable(True, True)
        self.minsize(900, 420)
        self.grab_set()
        force_light_titlebar(self)

        self._rows = list(rows or [])
        self._on_add = on_add_cb
        self._selected = set()          # stable row keys currently checked
        self._row_by_key = {}

        sources = sorted({self._row_source(r) for r in self._rows if self._row_source(r)})
        self._source_var = tk.StringVar(value="All")
        self._product_var = tk.StringVar(value="any")

        self._build(sources)
        self._populate()
        self._select_visible()

    def _build(self, sources):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        top = ttk.Frame(self, padding=(8, 8, 8, 4))
        top.grid(row=0, column=0, columnspan=2, sticky="ew")

        target = self._row_target(self._rows[0]) if self._rows else "?"
        n = len(self._rows)
        ttk.Label(top, text=f"{n} observation row(s)  ·  {target or 'mixed targets'}",
                  font=("Segoe UI", 10, "bold")).pack(side="left", padx=6)

        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=10, pady=3)

        ttk.Label(top, text="Archive / provenance:").pack(side="left")
        source_cb = ttk.Combobox(top,
                                 values=["All"] + list(sources),
                                 textvariable=self._source_var,
                                 width=22,
                                 state="readonly")
        source_cb.pack(side="left", padx=4)
        self._source_var.trace_add("write", lambda *_: (self._populate(), self._select_visible()))

        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=10, pady=3)

        ttk.Label(top, text="Products:").pack(side="left")
        prod_cb = ttk.Combobox(top,
                               values=list(self.PRODUCT_PRESETS),
                               textvariable=self._product_var,
                               width=18,
                               state="normal")
        prod_cb.pack(side="left", padx=4)
        prod_cb.bind("<KeyRelease>", lambda *_: self._update_count())
        prod_cb.bind("<<ComboboxSelected>>", lambda *_: self._update_count())

        ttk.Label(top, text="examples: any, i2d, cal, drc drz", foreground="gray").pack(side="left", padx=4)

        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=10, pady=3)

        ttk.Button(top, text="☑ All visible", command=self._select_visible).pack(side="left", padx=2)
        ttk.Button(top, text="☐ None", command=self._deselect_all).pack(side="left", padx=2)

        cols = ("collection", "filter", "instrument", "provenance", "target", "exptime", "calib", "obs_id")
        self._tree = ttk.Treeview(self, columns=cols, selectmode="none")

        self._tree.heading("#0", text="", anchor="center")
        self._tree.heading("collection", text="Archive")
        self._tree.heading("filter", text="Filter")
        self._tree.heading("instrument", text="Instrument")
        self._tree.heading("provenance", text="Provenance")
        self._tree.heading("target", text="Target")
        self._tree.heading("exptime", text="Exp (s)", anchor="e")
        self._tree.heading("calib", text="Cal", anchor="center")
        self._tree.heading("obs_id", text="Obs ID / obsid")

        self._tree.column("#0", width=28, minwidth=28, anchor="center")
        self._tree.column("collection", width=80, minwidth=60)
        self._tree.column("filter", width=80, minwidth=60)
        self._tree.column("instrument", width=120, minwidth=80)
        self._tree.column("provenance", width=120, minwidth=80)
        self._tree.column("target", width=170, minwidth=100)
        self._tree.column("exptime", width=72, minwidth=50, anchor="e")
        self._tree.column("calib", width=45, minwidth=36, anchor="center")
        self._tree.column("obs_id", width=350, minwidth=220)

        self._tree.tag_configure("on", foreground="#1565c0")
        self._tree.tag_configure("off", foreground="#888888")
        self._tree.grid(row=1, column=0, sticky="nsew", padx=(10, 0), pady=4)
        self._tree.bind("<Button-1>", self._on_click)

        sb = ttk.Scrollbar(self, orient="vertical", command=self._tree.yview)
        sb.grid(row=1, column=1, sticky="ns", padx=(0, 10), pady=4)
        self._tree.configure(yscrollcommand=sb.set)

        bot = ttk.Frame(self, padding=(8, 4, 8, 8))
        bot.grid(row=2, column=0, columnspan=2, sticky="ew")

        self._count_lbl = ttk.Label(bot, text="0 selected", foreground="#555")
        self._count_lbl.pack(side="left", padx=6)

        ttk.Button(bot, text="Cancel", command=self.destroy).pack(side="right", padx=6)
        self._add_btn = ttk.Button(bot, text="Add to Download Queue  →", command=self._add_to_queue)
        self._add_btn.pack(side="right", padx=6)

        ttk.Label(bot,
                  text="any = all SCIENCE FITS products. Multiple products accepted, for example: drc drz or i2d cal.",
                  foreground="gray", font=("Segoe UI", 8)).pack(side="left", padx=20)

    def _visible_rows(self):
        source = self._source_var.get()
        for idx, r in enumerate(self._rows):
            src = self._row_source(r)
            if source != "All" and src != source:
                continue
            yield idx, r

    def _populate(self):
        self._tree.delete(*self._tree.get_children())
        self._row_by_key.clear()

        for idx, r in self._visible_rows():
            key = self._row_key(r, idx)
            self._row_by_key[key] = r
            on = key in self._selected
            tag = "on" if on else "off"
            mark = "✓" if on else "·"
            collection = self._get(r, "obs_collection", "collection", "mission", default="")
            provenance = self._get(r, "provenance_name", "provenance", default="")
            self._tree.insert("", "end", iid=key, text=mark,
                              values=(collection,
                                      self._row_filter(r),
                                      self._row_instrument(r),
                                      provenance,
                                      self._row_target(r),
                                      self._row_exptime(r),
                                      self._row_calib(r),
                                      self._row_obs_id(r)),
                              tags=(tag,))
        self._update_count()

    def _on_click(self, event):
        iid = self._tree.identify_row(event.y)
        if not iid:
            return
        if iid in self._selected:
            self._selected.discard(iid)
            self._tree.item(iid, text="·", tags=("off",))
        else:
            self._selected.add(iid)
            self._tree.item(iid, text="✓", tags=("on",))
        self._update_count()

    def _select_visible(self):
        for idx, r in self._visible_rows():
            self._selected.add(self._row_key(r, idx))
        self._populate()

    def _deselect_all(self):
        self._selected.clear()
        self._populate()

    def _update_count(self):
        n = len(self._selected)
        products = self._parse_products_text(self._product_var.get())
        prod_label = self._product_label(products)
        self._count_lbl.config(text=f"{n} observation row(s) selected  ·  products: {prod_label}")

__all__ = [name for name in globals() if not name.startswith('__')]
