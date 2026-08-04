from __future__ import annotations

from tsdp2.ui._common import *

class MosaicRecoveryMixin:
    def _remove_file(self, path):
        for lst in self.groups.values():
            if path in lst:
                lst.remove(path)
        if path in self.fits_paths:
            self.fits_paths.remove(path)
        self.groups = {k: v for k, v in self.groups.items() if v}
        self.active_filters &= set(self.groups.keys())
        self._rebuild_tree()

    def clean_existing_background(self):
        """Create a cleaned high-resolution copy without rebuilding the mosaic."""
        if ((self.mosaic_runner and self.mosaic_runner.is_alive()) or
                (self.seam_runner and self.seam_runner.is_alive()) or
                (self.background_runner and self.background_runner.is_alive())):
            messagebox.showwarning(
                "Already working", "Wait for the current Mosaic task to finish.",
                parent=self)
            return
        input_path = filedialog.askopenfilename(
            title="Select an existing high-resolution mosaic FITS",
            filetypes=[("FITS files", "*.fits *.fit *.fts"),
                       ("All files", "*.*")])
        if not input_path:
            return
        from tkinter import simpledialog
        strength = simpledialog.askfloat(
            "Background cleanup strength",
            "Strength: 0.9 is normal. Use 0.5 for a gentler correction.",
            initialvalue=0.9, minvalue=0.0, maxvalue=1.0, parent=self)
        if strength is None:
            return
        base = Path(input_path)
        output_path = filedialog.asksaveasfilename(
            title="Save cleaned high-resolution copy as",
            initialdir=str(base.parent),
            initialfile=base.stem + "_background_clean.fits",
            defaultextension=".fits",
            filetypes=[("FITS files", "*.fits"), ("All files", "*.*")])
        if not output_path:
            return
        if os.path.abspath(input_path) == os.path.abspath(output_path):
            messagebox.showerror(
                "Choose a new file",
                "The cleaner preserves the original. Choose a different output name.",
                parent=self)
            return
        if not messagebox.askyesno(
                "Clean existing high-resolution mosaic",
                f"Input: {os.path.basename(input_path)}\n"
                f"Output: {os.path.basename(output_path)}\n"
                f"Strength: {strength:g}\n\n"
                "This copies the FITS, preserves its WCS, and applies the cosmetic "
                "detector-background correction in memory-safe row blocks. The "
                "original file is not modified.\n\nStart cleanup?", parent=self):
            return

        self.mosaic_start = time.time()
        self._run_kind = "background_clean"
        self._prog["value"] = 0
        self._run_btn.configure(state="disabled")
        self._draft_btn.configure(state="disabled")
        self._repair_btn.configure(state="disabled")
        self._clean_btn.configure(state="disabled")
        self._cancel_btn.configure(state="normal")
        self._stat.configure(text="Starting high-resolution background cleanup...")
        self.background_runner = BackgroundCleanRunner(
            input_path, output_path, strength, self.mosaic_q)
        self.background_runner.start()

    def repair_existing_mosaic(self):
        """Repair seams in a completed mosaic using the loaded original tiles."""
        if not self.groups:
            messagebox.showinfo(
                "Load original tiles first",
                "Add the original FITS tiles to the Mosaic tab first. The repair "
                "uses their overlaps but does not rebuild the full mosaic.",
                parent=self)
            return
        if ((self.mosaic_runner and self.mosaic_runner.is_alive()) or
                (self.seam_runner and self.seam_runner.is_alive()) or
                (self.background_runner and self.background_runner.is_alive())):
            messagebox.showwarning(
                "Already working", "Wait for the current Mosaic task to finish.",
                parent=self)
            return
        mosaic_path = filedialog.askopenfilename(
            title="Select the existing mosaic FITS to repair",
            filetypes=[("FITS files", "*.fits *.fit *.fts"),
                       ("All files", "*.*")])
        if not mosaic_path:
            return

        available = sorted(self.active_filters or self.groups.keys())
        target_code = next(iter(group_fits_by_filter([mosaic_path]).keys()), "UNKNOWN")
        matched = next((code for code in available
                        if code.upper() == target_code.upper()), None)
        if matched:
            chosen = matched
        elif len(available) == 1:
            chosen = available[0]
        else:
            from tkinter import simpledialog
            chosen = simpledialog.askstring(
                "Choose original tile group",
                "Which loaded group created this mosaic?\n\n" +
                "Available: " + ", ".join(available),
                initialvalue=available[0] if available else "", parent=self)
            if not chosen:
                return
            exact = next((code for code in available
                          if code.upper() == chosen.strip().upper()), None)
            if exact is None:
                messagebox.showerror(
                    "Group not found",
                    "Choose one of: " + ", ".join(available), parent=self)
                return
            chosen = exact

        from tkinter import simpledialog
        strength = simpledialog.askfloat(
            "Seam repair strength",
            "Strength: 1.0 is normal. Use 0.5 for a gentler correction.",
            initialvalue=float(self.seam_strength.get() or 1.0),
            minvalue=0.0, maxvalue=2.0, parent=self)
        if strength is None:
            return
        self.seam_strength.set(str(strength))

        base = Path(mosaic_path)
        suggested = base.stem + "_seam_fixed.fits"
        output_path = filedialog.asksaveasfilename(
            title="Save seam-repaired mosaic as",
            initialdir=self.out_dir.get().strip() or str(base.parent),
            initialfile=suggested,
            defaultextension=".fits",
            filetypes=[("FITS files", "*.fits"), ("All files", "*.*")])
        if not output_path:
            return
        if os.path.abspath(output_path) == os.path.abspath(mosaic_path):
            messagebox.showerror(
                "Choose a new file",
                "The repair keeps the original safe. Choose a different output name.",
                parent=self)
            return
        sources = list(self.groups.get(chosen) or [])
        if not sources:
            messagebox.showerror("No source tiles", f"Group {chosen} is empty.", parent=self)
            return
        if not messagebox.askyesno(
                "Repair existing mosaic",
                f"Mosaic: {os.path.basename(mosaic_path)}\n"
                f"Original group: {chosen} ({len(sources)} frame(s))\n"
                f"Strength: {strength:g}\n\n"
                "This builds only a small overlap model, then corrects the "
                "existing FITS without rebuilding its full-resolution detail.\n\n"
                "Start repair?", parent=self):
            return

        try:
            draft_scale = _normalise_mosaic_downscale(
                self.draft_scale.get(), default=0.08)
        except EXPECTED_OPERATION_ERRORS as exc:
            messagebox.showerror("Invalid draft scale", str(exc), parent=self)
            return
        self.mosaic_start = time.time()
        self._run_kind = "seam_repair"
        self._prog["value"] = 0
        self._run_btn.configure(state="disabled")
        self._draft_btn.configure(state="disabled")
        self._repair_btn.configure(state="disabled")
        self._clean_btn.configure(state="disabled")
        self._cancel_btn.configure(state="normal")
        self._stat.configure(text="Starting existing-mosaic seam repair…")
        self.seam_runner = SeamRepairRunner(
            mosaic_path, sources, output_path, chosen, strength,
            draft_scale, self.scratch_dir.get().strip(), self.mosaic_q)
        self.seam_runner.start()

    def _pick_out(self):
        d = filedialog.askdirectory(title="Mosaic output directory")
        if d:
            self.out_dir.set(d)

    def _open_job_folder(self, category: str) -> None:
        paths = getattr(self, "_active_job_paths", None)
        if not isinstance(paths, JobPaths):
            messagebox.showinfo(
                "No job yet", "Start a mosaic job first.", parent=self)
            return
        target = paths.root if category == "root" else getattr(paths, category)
        try:
            open_folder(target)
        except (OSError, FileNotFoundError) as exc:
            messagebox.showinfo("Folder not available", str(exc), parent=self)

    def _pick_scratch(self):
        d = filedialog.askdirectory(
            title="Recovery scratch directory (prefer a reliable internal SSD)")
        if d:
            self.scratch_dir.set(d)

    def _compute_preflight(self, active=None):
        active = active if active is not None else self._active_groups()
        if not active:
            raise ValueError("Select at least one filter group first.")
        return _build_mosaic_preflight(
            active, self.out_dir.get().strip(), self.scratch_dir.get().strip(),
            global_scale=self.downscale_global.get(),
            filter_scales=dict(self.filter_downscales),
            save_coverage_maps=self.save_coverage_maps.get(),
            sky_region=self.sky_region,
            extra_clean_copy=bool(self.highres_background_cleanup.get()))

    def show_preflight(self):
        if not self.groups:
            messagebox.showinfo("No groups", "Load or group FITS files first.", parent=self)
            return
        self._stat.configure(text="Inspecting WCS, real data fill, output size, and disks…")
        self.update_idletasks()
        try:
            report = self._compute_preflight()
        except EXPECTED_OPERATION_ERRORS as exc:
            messagebox.showerror("Preflight failed", str(exc), parent=self)
            self._stat.configure(text="Preflight failed.")
            return
        text_report = _mosaic_preflight_text(report)
        win = tk.Toplevel(self)
        win.title("Mosaic Inspection / Preflight")
        win.geometry("900x680")
        win.transient(self.winfo_toplevel())
        try:
            force_light_titlebar(win)
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
        frame = ttk.Frame(win, padding=10)
        frame.pack(fill="both", expand=True)
        frame.rowconfigure(1, weight=1)
        frame.columnconfigure(0, weight=1)
        style = "Good.TLabel" if report.get("space_ok") else "Bad.TLabel"
        ttk.Label(
            frame,
            text=("Ready: disk-space estimate passed" if report.get("space_ok")
                  else "Not ready: estimated free space is too low"),
            style=style).grid(row=0, column=0, sticky="w", pady=(0, 6))
        viewer = tk.Text(frame, wrap="none", font=("Consolas", 10))
        viewer.grid(row=1, column=0, sticky="nsew")
        sy = ttk.Scrollbar(frame, orient="vertical", command=viewer.yview)
        sx = ttk.Scrollbar(frame, orient="horizontal", command=viewer.xview)
        sy.grid(row=1, column=1, sticky="ns")
        sx.grid(row=2, column=0, sticky="ew")
        viewer.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        viewer.insert("1.0", text_report)
        viewer.configure(state="disabled")
        ttk.Button(frame, text="Close", command=win.destroy).grid(
            row=3, column=0, sticky="e", pady=(8, 0))
        self._stat.configure(
            text=("Preflight passed." if report.get("space_ok")
                  else "Preflight found insufficient disk space."))

__all__ = [name for name in globals() if not name.startswith('__')]
