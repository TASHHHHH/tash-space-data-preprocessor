from __future__ import annotations

from tsdp2.ui._common import *

class MosaicGroupsMixin:
    def _update_active_label(self):
        total = len(self.groups)
        active = len([c for c in self.groups if c in self.active_filters])
        try:
            if total == 0:
                self._active_lbl.config(text="No filters")
            elif active == total:
                self._active_lbl.config(text="All filters selected")
            else:
                self._active_lbl.config(text=f"{active}/{total} filters selected")
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)

    def _set_group_active(self, fcode, active=True):
        if active:
            self.active_filters.add(fcode)
        else:
            self.active_filters.discard(fcode)
        self._rebuild_tree()

    def _scale_for_filter(self, fcode):
        return _mosaic_filter_scale(fcode, self.downscale_global.get(), self.filter_downscales)

    def edit_filter_scales(self):
        if not self.groups:
            messagebox.showinfo("No groups", "Load or group FITS files first.", parent=self)
            return
        chosen = ask_filter_selection(
            self, self.groups,
            title="Mosaic output scale per filter",
            initial_selected=sorted(self.active_filters or self.groups.keys()),
            message="Set global/per-filter output scale. Blank per-filter values use global.",
            out_dir=self.out_dir.get().strip(),
            initial_scales=self.filter_downscales,
            global_scale=self.downscale_global.get(),
            return_scales=True
        )
        if chosen is None:
            return
        if isinstance(chosen, dict):
            self.active_filters = set(chosen.get("selected") or [])
            self.downscale_global.set(str(chosen.get("global_scale", self.downscale_global.get())))
            self.filter_downscales = dict(chosen.get("scales") or {})
            self._rebuild_tree()

    def _set_group_scale(self, fcode):
        from tkinter import simpledialog
        current = str(self.filter_downscales.get(fcode, "") or "")
        global_txt = str(self.downscale_global.get() or "1.0")
        val = simpledialog.askstring(
            "Filter output scale",
            f'Scale for {fcode} (blank = global {global_txt}).\n'
            f'Use 1 for full resolution, 0.5 for half width/height, 0.25 for quarter.',
            initialvalue=current, parent=self)
        if val is None:
            return
        val = val.strip()
        if val:
            try:
                self.filter_downscales[fcode] = str(_normalise_mosaic_downscale(val, default=global_txt))
            except EXPECTED_OPERATION_ERRORS as exc:
                messagebox.showerror("Invalid scale", str(exc), parent=self)
                return
        else:
            self.filter_downscales.pop(fcode, None)
        self._rebuild_tree()

    def select_filters(self):
        if not self.groups:
            messagebox.showinfo("No groups", "Load or group FITS files first.", parent=self)
            return
        chosen = ask_filter_selection(
            self,
            self.groups,
            title="Select mosaic filters",
            initial_selected=sorted(self.active_filters or self.groups.keys()),
            message="Choose filters and optional output scale overrides for the next mosaic run.",
            out_dir=self.out_dir.get().strip(),
            initial_scales=self.filter_downscales,
            global_scale=self.downscale_global.get(),
            return_scales=True
        )
        if chosen is None:
            return
        if isinstance(chosen, dict):
            self.active_filters = set(chosen.get("selected") or [])
            self.downscale_global.set(str(chosen.get("global_scale", self.downscale_global.get())))
            self.filter_downscales = dict(chosen.get("scales") or {})
        else:
            self.active_filters = set(chosen)
        self._rebuild_tree()

    def show_wcs_coverage(self):
        if not self.groups:
            messagebox.showinfo("No groups", "Load or group FITS files first.", parent=self)
            return
        selected = sorted(self.active_filters or self.groups.keys())
        show_wcs_coverage_window(
            self, self.groups, selected_filters=selected,
            out_dir=self.out_dir.get().strip(),
            on_report=self._on_coverage_report,
            on_region=self._on_coverage_region,
            global_scale=self.downscale_global.get(),
            filter_scales=dict(self.filter_downscales),
            on_scale=self._on_coverage_scale)

    def _on_coverage_scale(self, scale):
        self.downscale_global.set(str(_normalise_mosaic_downscale(scale)))
        self._rebuild_tree()

    def _on_coverage_region(self, region):
        if not isinstance(region, dict) or len(region.get("world_corners") or []) != 4:
            raise ValueError("The selected sky rectangle is invalid.")
        self.sky_region = dict(region)
        if 'output_scale' in region:
            self._on_coverage_scale(region['output_scale'])
        self._clear_area_btn.configure(state="normal")
        rotation = float(region.get("rotation_deg", 0.0) or 0.0)
        self._area_lbl.configure(
            text=(f"Selected RA {region.get('center_ra', 0):.4f}, "
                  f"Dec {region.get('center_dec', 0):.4f}, "
                  f"rot {rotation:+.1f}°"),
            foreground=C_ACCENT)
        self._stat.configure(text="Interactive sky area selected for the next mosaic.")

    def clear_sky_region(self):
        self.sky_region = None
        self._clear_area_btn.configure(state="disabled")
        self._area_lbl.configure(text="Full automatic area", foreground="gray")
        self._stat.configure(
            text="Interactive sky crop cleared; full footprint will be mosaicked.")

    def _on_coverage_report(self, report):
        """Runs on the Tk thread after every successful coverage render.

        The old code used the dialog's return value, which was always None
        (the first render happens via after()), and called tg_direct._tg()
        which does not exist — so the status update and Telegram send never
        fired. This callback path actually works; the Telegram send is
        throttled so paging through filters does not spam the chat.
        """
        if not isinstance(report, dict):
            return
        try:
            self._stat.config(
                text=f"WCS coverage: {report.get('valid', 0)} valid footprint(s), "
                     f"{report.get('invalid', 0)} invalid")
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)

        tg_direct = getattr(self, "telegram_ref", None)
        png_path = report.get("png_path", "")
        if tg_direct is None or not png_path or not os.path.isfile(png_path):
            return
        now = time.time()
        if now - getattr(self, "_cov_tg_last_send", 0.0) < 30.0:
            return
        try:
            tg = tg_direct._get_tg() if hasattr(tg_direct, "_get_tg") else None
        except EXPECTED_OPERATION_ERRORS:
            tg = None
        if not tg or not getattr(tg, "ready", False):
            return
        self._cov_tg_last_send = now
        caption = ("🗺 <b>WCS coverage preview</b>\n"
                   f"Valid footprints: {report.get('valid', 0)}\n"
                   f"Invalid/no WCS: {report.get('invalid', 0)}")

        def _send():
            try:
                with open(png_path, "rb") as f:
                    data = f.read()
                tg.send_photo(data, caption=caption)
            except EXPECTED_OPERATION_ERRORS as exc:
                print(f"[WCS coverage Telegram send failed] {exc}", flush=True)

        threading.Thread(target=_send, daemon=True).start()

    def _active_groups(self):
        if not self.groups:
            return {}
        if not self.active_filters:
            return {}
        return {k: list(v) for k, v in self.groups.items()
                if k in self.active_filters and v}

    def _on_right_click(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        self.tree.selection_set(iid)
        tags = self.tree.item(iid, "tags")
        self._ctx.delete(0, "end")

        if "group" in tags:
            fcode = self.tree.item(iid, "text").strip()
            self._ctx.add_command(
                label=f'Rename "{fcode}"…',
                command=lambda c=fcode: self._rename_group(c))
            self._ctx.add_command(
                label=f'Add files to "{fcode}"…',
                command=lambda c=fcode: self._add_to_group(c))
            if fcode in self.active_filters:
                self._ctx.add_command(
                    label=f'Disable "{fcode}" for next run',
                    command=lambda c=fcode: self._set_group_active(c, False))
            else:
                self._ctx.add_command(
                    label=f'Enable "{fcode}" for next run',
                    command=lambda c=fcode: self._set_group_active(c, True))
            self._ctx.add_command(
                label=f'Set output scale for "{fcode}"…',
                command=lambda c=fcode: self._set_group_scale(c))
            self._ctx.add_separator()
            self._ctx.add_command(
                label=f'Remove "{fcode}" and its files',
                command=lambda c=fcode: self._remove_group(c))

        elif "file" in tags:
            path = self.tree.item(iid, "values")[0]
            bn   = os.path.basename(path)
            cur  = self.tree.item(self.tree.parent(iid), "text").strip()
            sub  = tk.Menu(self._ctx, tearoff=0)
            for fcode in sorted(self.groups):
                if fcode != cur:
                    sub.add_command(
                        label=fcode,
                        command=lambda p=path, fc=fcode: self._move(p, fc))
            sub.add_separator()
            sub.add_command(
                label="New group…",
                command=lambda p=path: self._move_new(p))
            self._ctx.add_cascade(label=f'Move "{bn}" to →', menu=sub)
            self._ctx.add_separator()
            self._ctx.add_command(
                label=f'Remove "{bn}"',
                command=lambda p=path: self._remove_file(p))

        try:
            self._ctx.tk_popup(event.x_root, event.y_root)
        finally:
            self._ctx.grab_release()

    def _rename_group(self, old_code):
        from tkinter import simpledialog
        new = simpledialog.askstring(
            "Rename group", f'New name for group "{old_code}":',
            parent=self)
        if not new:
            return
        new = new.strip().upper()
        if not new or new == old_code:
            return
        paths = self.groups.pop(old_code, [])
        self.groups.setdefault(new, []).extend(paths)
        if old_code in self.filter_downscales:
            self.filter_downscales[new] = self.filter_downscales.pop(old_code)
        self.active_filters.add(new)
        self.active_filters.discard(old_code)
        self._rebuild_tree()

    def _add_to_group(self, fcode):
        paths = filedialog.askopenfilenames(
            title=f"Add files to group {fcode}",
            filetypes=[("FITS files", "*.fits *.fit *.fts"),
                       ("All files", "*.*")])
        if not paths:
            return
        for p in paths:
            if p not in self.fits_paths:
                self.fits_paths.append(p)
            for lst in self.groups.values():
                if p in lst:
                    lst.remove(p)
            self.groups.setdefault(fcode, []).append(p)
        self.active_filters.add(fcode)
        self.groups = {k: v for k, v in self.groups.items() if v}
        self.active_filters &= set(self.groups.keys())
        self._rebuild_tree()

    def _remove_group(self, fcode):
        if not messagebox.askyesno(
                "Remove group",
                f'Remove group "{fcode}" and all its files?'):
            return
        for p in self.groups.pop(fcode, []):
            if p in self.fits_paths:
                self.fits_paths.remove(p)
        self.active_filters.discard(fcode)
        self.filter_downscales.pop(fcode, None)
        self._rebuild_tree()

    def _move(self, path, new_code):
        for lst in self.groups.values():
            if path in lst:
                lst.remove(path)
        self.groups.setdefault(new_code, []).append(path)
        self.active_filters.add(new_code)
        self.groups = {k: v for k, v in self.groups.items() if v}
        self.active_filters &= set(self.groups.keys())
        self._rebuild_tree()

    def _move_new(self, path):
        from tkinter import simpledialog
        new = simpledialog.askstring(
            "New group", "Name for the new group:", parent=self)
        if not new:
            return
        new = new.strip().upper()
        if new:
            self._move(path, new)

__all__ = [name for name in globals() if not name.startswith('__')]
