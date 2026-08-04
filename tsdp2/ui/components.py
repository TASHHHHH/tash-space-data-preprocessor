from __future__ import annotations

from tsdp2._deps import *
from tsdp2.logging_config import LOGGER
from tsdp2.astronomy.previews import STRETCH_MODES, THUMB_SIZE, load_thumbnail_cached
from tsdp2.astronomy.reference import get_fits_shape
from tsdp2.ui.theme import C_BG3, C_FG, force_light_titlebar
from tsdp2.utilities.threading import post_to_ui

class TextRedirector:
    def __init__(self, q: "queue.Queue[str]"):
        self.q = q

    def write(self, s: str):
        if s:
            self.q.put(s)

    def flush(self):
        pass


class LogWindow(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title("Live Logs")
        self.geometry("900x520")
        self.resizable(True, True)

        force_light_titlebar(self)
        self._real_stdout = sys.stdout
        self._real_stderr = sys.stderr
        self.q = queue.Queue()

        self.text = tk.Text(self, wrap="word", bg=C_BG3, fg=C_FG,
                             insertbackground=C_FG, relief="flat",
                             font=("Consolas", 9), padx=8, pady=6)
        self.scroll = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=self.scroll.set)

        self.text.pack(side="left", fill="both", expand=True)
        self.scroll.pack(side="right", fill="y")

        self.text.insert("end", "Live log window started.\n\n")
        self.text.see("end")

        sys.stdout = TextRedirector(self.q)
        sys.stderr = TextRedirector(self.q)

        # Closing just hides — keeps capturing, reopen from the header button
        self.protocol("WM_DELETE_WINDOW", self.withdraw)
        self.withdraw()          # start hidden; open on demand

        self.after(50, self._drain)

    def toggle(self):
        if self.state() == "withdrawn":
            self.deiconify()
            self.lift()
        else:
            self.withdraw()

    def _drain(self):
        try:
            while True:
                s = self.q.get_nowait()
                self.text.insert("end", s)
                self.text.see("end")
        except queue.Empty:
            pass
        # Stop draining safely if the window has been destroyed
        try:
            self.after(50, self._drain)
        except tk.TclError:
            pass


class BlinkWindow(tk.Toplevel):
    """Cycles through aligned FITS outputs for quick blink comparison."""

    def __init__(self, master, outputs: list, stretch_mode: str = "asinh"):
        super().__init__(master)
        self.title("Blink Comparator")
        self.resizable(True, True)
        self.minsize(420, 440)
        force_light_titlebar(self)

        self._outputs   = outputs          # [(name, path), ...]
        self._idx       = 0
        self._playing   = False
        self._after_id  = None
        self._cache     = {}               # (path, mode) -> (PhotoImage, (H,W))
        self._cur_photo = None             # GC anchor for displayed image

        self._stretch  = tk.StringVar(value=stretch_mode)
        self._interval = tk.DoubleVar(value=0.8)

        if not outputs:
            ttk.Label(self, text="No outputs to display.", padding=20).pack()
            return
        if not HAVE_PIL:
            ttk.Label(self,
                      text="Install Pillow to use the Blink Comparator:\n"
                           "  pip install Pillow",
                      padding=20).pack()
            return

        self._build()
        self._load_all()
        self._show(0)

    def _build(self):
        # ── image area ──────────────────────────────────────────────────────
        disp = tk.Frame(self, bg="#0c0d15")
        disp.pack(fill="both", expand=True)

        self._canvas = tk.Canvas(disp, bg="#0c0d15", highlightthickness=0)
        self._canvas.pack(fill="both", expand=True)
        self._canvas_img_id = None

        self._name_lbl = tk.Label(
            disp, text="",
            font=("Segoe UI", 11, "bold"),
            bg="#0c0d15", fg="#dde4ff", anchor="w")
        self._name_lbl.pack(fill="x", padx=8)

        self._info_lbl = tk.Label(
            disp, text="",
            font=("Segoe UI", 8),
            bg="#0c0d15", fg="#666", anchor="w")
        self._info_lbl.pack(fill="x", padx=8, pady=(0, 4))

        # ── controls ────────────────────────────────────────────────────────
        ctrl = ttk.Frame(self, padding=(8, 6, 8, 10))
        ctrl.pack(fill="x")

        nav = ttk.Frame(ctrl)
        nav.pack(side="left")
        ttk.Button(nav, text="◀", width=3,
                   command=lambda: self._step(-1)).pack(side="left", padx=2)
        self._play_btn = ttk.Button(nav, text="▶  Play", width=9,
                                     command=self.toggle_play)
        self._play_btn.pack(side="left", padx=2)
        ttk.Button(nav, text="▶", width=3,
                   command=lambda: self._step(1)).pack(side="left", padx=2)

        self._prog_lbl = ttk.Label(ctrl, text=f"1 / {len(self._outputs)}")
        self._prog_lbl.pack(side="left", padx=12)

        spd = ttk.Frame(ctrl)
        spd.pack(side="left", padx=8)
        ttk.Label(spd, text="Speed:").pack(side="left")
        ttk.Scale(spd, from_=0.12, to=3.0, variable=self._interval,
                  orient="horizontal", length=90).pack(side="left", padx=4)
        self._spd_lbl = ttk.Label(spd, text="0.80s", width=6)
        self._spd_lbl.pack(side="left")
        self._interval.trace_add("write",
            lambda *_: self._spd_lbl.config(text=f"{self._interval.get():.2f}s"))

        sth = ttk.Frame(ctrl)
        sth.pack(side="left", padx=8)
        ttk.Label(sth, text="Stretch:").pack(side="left")
        ttk.Combobox(sth, values=STRETCH_MODES, textvariable=self._stretch,
                     width=9, state="readonly").pack(side="left", padx=4)
        self._stretch.trace_add("write", lambda *_: self._reload_cache())

        self.geometry("560x620")

    # ── image loading ──────────────────────────────────────────────────────────

    def _load_all(self):
        for _name, path in self._outputs:
            self._enqueue(path)

    def _enqueue(self, path: str):
        mode = self._stretch.get()
        key  = (path, mode)
        if key in self._cache:
            return

        def _worker():
            try:
                img, shp = load_thumbnail_cached(path, mode, max_px=500)
                if img is not None:
                    photo = ImageTk.PhotoImage(img)
                    self._cache[key] = (photo, shp)
                    _name, cur_path = self._outputs[self._idx]
                    if cur_path == path:
                        post_to_ui(lambda: self._show(self._idx))
            except EXPECTED_OPERATION_ERRORS:
                LOGGER.debug("Best-effort operation failed", exc_info=True)

        threading.Thread(target=_worker, daemon=True).start()

    def _reload_cache(self):
        self._cache.clear()
        self._cur_photo = None
        self._load_all()
        self._show(self._idx)

    # ── display ───────────────────────────────────────────────────────────────

    def _show(self, idx):
        if not self._outputs:
            return
        self._idx = idx % len(self._outputs)
        name, path = self._outputs[self._idx]
        mode = self._stretch.get()
        key  = (path, mode)

        self._name_lbl.config(text=name)
        self._prog_lbl.config(text=f"{self._idx + 1} / {len(self._outputs)}")

        if key in self._cache:
            photo, shape = self._cache[key]
            self._cur_photo = photo
            cw = self._canvas.winfo_width()  or 520
            ch = self._canvas.winfo_height() or 480
            if self._canvas_img_id:
                self._canvas.delete(self._canvas_img_id)
            self._canvas_img_id = self._canvas.create_image(
                cw // 2, ch // 2, anchor="center", image=photo)
            h, w = shape
            self._info_lbl.config(
                text=f"{w} × {h} px  [{mode}]  —  {os.path.basename(path)}")
        else:
            if self._canvas_img_id:
                self._canvas.delete(self._canvas_img_id)
                self._canvas_img_id = None
            self._cur_photo = None
            self._info_lbl.config(text="Loading…")
            self._enqueue(path)

    def _step(self, delta: int):
        if self._playing:
            self.toggle_play()
        self._show(self._idx + delta)

    def toggle_play(self):
        self._playing = not self._playing
        if self._playing:
            self._play_btn.config(text="⏸  Pause")
            self._tick()
        else:
            self._play_btn.config(text="▶  Play")
            if self._after_id:
                self.after_cancel(self._after_id)
                self._after_id = None

    def _tick(self):
        if not self._playing:
            return
        try:
            self._show(self._idx + 1)
            ms = max(80, int(self._interval.get() * 1000))
            self._after_id = self.after(ms, self._tick)
        except tk.TclError:
            # Window was destroyed while playing — stop gracefully
            self._playing = False


class FilterRow:
    """One moving-filter input row with an optional FITS preview panel."""

    THUMB = THUMB_SIZE

    def __init__(self, parent, idx: int, on_browse, on_name_hint, app_ref=None):
        self.idx = idx
        self.path = tk.StringVar()
        self.name = tk.StringVar()
        self._photo = None      # keep PhotoImage alive (prevent GC)
        self._app = app_ref

        self.frame = ttk.Frame(parent)

        # ── controls ──────────────────────────────────────────────────────────
        ttk.Label(self.frame, text=f"Filter {idx}:").grid(
            row=0, column=0, sticky="w", padx=6, pady=3)
        ttk.Entry(self.frame, textvariable=self.path, width=52).grid(
            row=0, column=1, sticky="w", padx=6, pady=3)
        ttk.Button(self.frame, text="Browse",
                   command=lambda: on_browse(self)).grid(
            row=0, column=2, sticky="w", padx=6, pady=3)
        ttk.Label(self.frame, text="Name:").grid(
            row=0, column=3, sticky="w", padx=6, pady=3)
        ttk.Entry(self.frame, textvariable=self.name, width=20).grid(
            row=0, column=4, sticky="w", padx=6, pady=3)
        ttk.Button(self.frame, text="Auto-name",
                   command=lambda: on_name_hint(self)).grid(
            row=0, column=5, sticky="w", padx=6, pady=3)

        # ── preview panel (hidden by default; revealed by global toggle) ──────
        self._pf = ttk.Frame(self.frame, width=self.THUMB + 6, height=self.THUMB + 22)
        self._pf.grid_propagate(False)

        self._img_lbl = tk.Label(
            self._pf, text="—",
            bg="#101118", fg="#666",
            font=("Segoe UI", 8), anchor="center",
        )
        self._img_lbl.place(relx=0, rely=0, relwidth=1, relheight=0.86)

        self._info_lbl = tk.Label(
            self._pf, text="",
            bg="#101118", fg="#999",
            font=("Segoe UI", 7), anchor="sw",
        )
        self._info_lbl.place(relx=0.02, rely=0.87, relwidth=0.98, relheight=0.13)

        # ── pixel dimensions (shown below path once a file is loaded) ──────
        self._size_lbl = ttk.Label(self.frame, text="",
                                    foreground="#888", font=("Segoe UI", 8))
        self._size_lbl.grid(row=1, column=1, columnspan=4, sticky="w", padx=6, pady=0)

    # ── layout helpers ─────────────────────────────────────────────────────────

    def grid(self, row: int):
        self.frame.grid(row=row, column=0, columnspan=3, sticky="w")

    def destroy(self):
        self.frame.destroy()

    # ── preview show / hide / render ───────────────────────────────────────────

    def show_preview(self):
        self._pf.grid(in_=self.frame, row=0, column=6, rowspan=2, padx=10, pady=3, sticky="nsw")

    def hide_preview(self):
        self._pf.grid_remove()

    def update_preview(self, stretch_mode: str):
        """Load and render a stretched thumbnail (runs in background thread)."""
        if not HAVE_PIL:
            self._img_lbl.config(image="", text="Install\nPillow")
            return
        p = self.path.get().strip()
        if not p or not os.path.exists(p):
            self._img_lbl.config(image="", text="No file")
            self._photo = None
            return

        self._img_lbl.config(image="", text="Loading…")
        self._photo = None
        path_snap, mode_snap = p, stretch_mode

        def _worker():
            try:
                img, shp = load_thumbnail_cached(path_snap, mode_snap)
                self.frame.after(0, lambda: self._apply(img, shp, mode_snap))
            except EXPECTED_OPERATION_ERRORS as exc:
                err = str(exc)[:55]
                self.frame.after(0, lambda: self._apply_err(err))

        threading.Thread(target=_worker, daemon=True).start()

    def _apply(self, img, shape, mode):
        if img is None:
            self._img_lbl.config(image="", text="PIL missing")
            return
        photo = ImageTk.PhotoImage(img)
        self._img_lbl.config(image=photo, text="")
        self._photo = photo
        h, w = shape
        self._info_lbl.config(text=f"{w}×{h}  [{mode}]")

    def _apply_err(self, msg):
        self._img_lbl.config(image="", text=f"Err:\n{msg}")
        self._photo = None

    # ── size label update ──────────────────────────────────────────────────────

    def update_size(self, path: str):
        """Read pixel dims from FITS header (threaded) and update the size label."""
        if not path or not os.path.exists(path):
            self._size_lbl.config(text="")
            return
        snap = path
        def _worker():
            h, w = get_fits_shape(snap)
            self.frame.after(0, lambda: self._size_lbl.config(
                text=f"{w} × {h} px" if (h and w) else ""))
        threading.Thread(target=_worker, daemon=True).start()

__all__ = [name for name in globals() if not name.startswith('__')]
