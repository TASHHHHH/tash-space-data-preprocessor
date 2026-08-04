from __future__ import annotations

from tsdp2.ui._common import *

class AlignerPreviewMixin:
    def toggle_previews(self):
        self.preview_visible = not self.preview_visible
        if self.preview_visible:
            self.preview_btn.config(text="Hide Previews  ▼")
            self.ref_pf.grid()
            for r in self.rows:
                r.show_preview()
            self.update_all_previews()
        else:
            self.preview_btn.config(text="Show Previews  ▶")
            self.ref_pf.grid_remove()
            for r in self.rows:
                r.hide_preview()

    def update_all_previews(self):
        if not self.preview_visible:
            return
        mode = self.stretch_mode.get()
        self._update_ref_preview(mode)
        for r in self.rows:
            r.update_preview(mode)

    def _update_ref_preview(self, stretch_mode: str):
        if not HAVE_PIL:
            self.ref_img_lbl.config(image="", text="Install\nPillow")
            return
        p = self.reference_path.get().strip()
        if not p or not os.path.exists(p):
            self.ref_img_lbl.config(image="", text="No file")
            self.ref_photo = None
            return
        self.ref_img_lbl.config(image="", text="Loading…")
        self.ref_photo = None
        path_snap, mode_snap = p, stretch_mode

        def _worker():
            try:
                img, shp = load_thumbnail_cached(path_snap, mode_snap)
                post_to_ui(self._apply_ref, img, shp, mode_snap)
            except EXPECTED_OPERATION_ERRORS as exc:
                err = str(exc)[:55]
                post_to_ui(self._apply_ref_err, err)

        threading.Thread(target=_worker, daemon=True).start()

    def _apply_ref(self, img, shape, mode):
        if img is None:
            self.ref_img_lbl.config(image="", text="PIL missing")
            return
        photo = ImageTk.PhotoImage(img)
        self.ref_img_lbl.config(image=photo, text="")
        self.ref_photo = photo
        h, w = shape
        self.ref_info_lbl.config(text=f"REF  {w}×{h}  [{mode}]")

    def _apply_ref_err(self, msg):
        self.ref_img_lbl.config(image="", text=f"Err:\n{msg}")
        self.ref_photo = None

    def _update_ref_size(self, path: str):
        if not path or not os.path.exists(path):
            self.ref_size_lbl.config(text="")
            return
        snap = path
        def _worker():
            h, w = get_fits_shape(snap)
            post_to_ui(lambda: self.ref_size_lbl.config(
                text=f"{w} × {h} px" if (h and w) else ""))
        threading.Thread(target=_worker, daemon=True).start()

    def open_blink_window(self):
        if not self.last_run_outputs:
            messagebox.showinfo("No outputs", "Run an alignment first.")
            return
        if not HAVE_PIL:
            messagebox.showerror(
                "Missing Pillow",
                "Install Pillow to use the Blink Comparator:\n  pip install Pillow")
            return
        BlinkWindow(self, self.last_run_outputs,
                    stretch_mode=self.stretch_mode.get())

    def auto_pick_reference(self):
        paths = []
        ref_path = self.reference_path.get().strip()
        if ref_path and os.path.exists(ref_path):
            paths.append(ref_path)

        for r in self.rows:
            p = r.path.get().strip()
            if p and os.path.exists(p):
                paths.append(p)

        if not paths:
            messagebox.showerror("No files", "Select some FITS files first.")
            return

        best, _table = choose_best_reference(paths)

        if ref_path and os.path.exists(ref_path) and os.path.abspath(best) == os.path.abspath(ref_path):
            messagebox.showinfo("Auto-pick reference", "Current reference already looks best.")
            return

        if not ref_path or not os.path.exists(ref_path):
            self.reference_path.set(best)
            self.reference_name.set(suggest_name_from_fits(best))
            for r in self.rows:
                rp = r.path.get().strip()
                if rp and os.path.abspath(rp) == os.path.abspath(best):
                    r.path.set("")
                    r.name.set(f"filter_{r.idx}")
                    break
            messagebox.showinfo("Auto-pick reference", "Reference set. The promoted row is now blank (ignored).")
            return

        old_ref = ref_path
        self.reference_path.set(best)
        self.reference_name.set(suggest_name_from_fits(best))

        swapped = False
        for r in self.rows:
            rp = r.path.get().strip()
            if rp and os.path.abspath(rp) == os.path.abspath(best):
                r.path.set(old_ref)
                r.name.set(suggest_name_from_fits(old_ref))
                swapped = True
                break

        if swapped:
            messagebox.showinfo("Auto-pick reference", "Reference swapped with one of your selected filters.")
        else:
            messagebox.showinfo("Auto-pick reference", "Reference changed, but could not find a matching row to swap.")

    def _collect_alignment_items_for_check(self):
        ref = self.reference_path.get().strip()
        if not ref or not os.path.exists(ref):
            messagebox.showerror("Missing reference", "Pick a valid reference FITS file first.")
            return None, None
        items = []
        names_seen = set()
        for r in self.rows:
            p = r.path.get().strip()
            nm = r.name.get().strip() or f"filter_{r.idx}"
            if not p:
                continue
            if not os.path.exists(p):
                messagebox.showerror("Invalid file", f"Filter {r.idx} has an invalid FITS file:\n{p}")
                return None, None
            safe_nm = safe_filename(nm).lower()
            if safe_nm in names_seen:
                messagebox.showerror("Duplicate name", f"Name '{nm}' is duplicated. Names must be unique.")
                return None, None
            names_seen.add(safe_nm)
            items.append({"path": p, "name": nm})
        if not items:
            messagebox.showerror("No moving filters", "Pick at least one moving FITS file first.")
            return None, None
        return ref, items

    def check_wcs_skip_status(self):
        """User-facing preflight: tells which moving files already match reference WCS."""
        ref, items = self._collect_alignment_items_for_check()
        if not ref:
            return
        self.status.config(text="Checking WCS grids...")
        self.update_idletasks()
        try:
            rows = wcs_alignment_check_paths(ref, items, tol_pix=0.05)
        except EXPECTED_OPERATION_ERRORS as exc:
            self.status.config(text="WCS check failed")
            messagebox.showerror("WCS check failed", str(exc))
            return

        skip_rows = [r for r in rows if r.get("same")]
        align_rows = [r for r in rows if not r.get("same")]
        lines = []
        lines.append(f"Reference: {os.path.basename(ref)}")
        lines.append("")
        lines.append(f"Can skip alignment: {len(skip_rows)}/{len(rows)}")
        for r in skip_rows[:30]:
            err = r.get("max_err")
            err_txt = f"{err:.4f} px" if isinstance(err, (float, int)) else "n/a"
            lines.append(f"  SKIP  {r['name']}  ({err_txt})")
        if len(skip_rows) > 30:
            lines.append(f"  ... {len(skip_rows) - 30} more")
        lines.append("")
        lines.append(f"Need reprojection: {len(align_rows)}/{len(rows)}")
        for r in align_rows[:30]:
            lines.append(f"  ALIGN {r['name']} - {r.get('reason', '')}")
        if len(align_rows) > 30:
            lines.append(f"  ... {len(align_rows) - 30} more")

        self.status.config(text=f"WCS check: {len(skip_rows)} skip, {len(align_rows)} align")
        messagebox.showinfo("WCS alignment pre-check", "\n".join(lines))

__all__ = [name for name in globals() if not name.startswith('__')]
