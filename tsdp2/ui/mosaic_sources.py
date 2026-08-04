from __future__ import annotations

from tsdp2.ui._common import *

class MosaicSourcesMixin:
    def load_from_pipeline(self, groups: dict, out_dir: str = ""):
        """
        Called by the pipeline when filter groups are detected.
        Populates the treeview so the user can see (and optionally edit)
        what is about to be mosaicked.
        """
        original_paths = [p for paths in groups.values() for p in paths]
        expanded, reports, errors = expand_mosaic_input_paths(original_paths)
        if reports:
            self.fits_paths = list(dict.fromkeys(expanded))
            self.groups = group_fits_by_filter(
                self.fits_paths, number_unknowns=True)
            for report in reports:
                print(f"[Mosaic Tab] Expanded {os.path.basename(report['source'])} "
                      f"into {report['count']} image HDUs", flush=True)
        else:
            self.fits_paths = original_paths
            self.groups = group_fits_by_filter(
                self.fits_paths, number_unknowns=True)
        for source, error in errors:
            print(f"[Mosaic Tab] Could not inspect {source}: {error}", flush=True)
        self.active_filters = set(self.groups.keys())
        self.filter_downscales = {k: v for k, v in getattr(self, "filter_downscales", {}).items() if k in self.groups}
        if out_dir:
            self.out_dir.set(out_dir)
        self._rebuild_tree()
        # Expand all groups so everything is visible immediately
        for iid in self.tree.get_children():
            self.tree.item(iid, open=True)
        n_files  = len(self.fits_paths)
        n_groups = len(self.groups)
        print(f"[Mosaic Tab] Loaded from pipeline: "
              f"{n_groups} group(s), {n_files} file(s)", flush=True)

    def add_files(self):
        paths = filedialog.askopenfilenames(
            title="Add FITS files",
            filetypes=[("FITS files", "*.fits *.fit *.fts *.fits.fz *.fit.fz *.fts.fz"),
                       ("All files", "*.*")])
        if not paths:
            return
        expanded, reports, errors = expand_mosaic_input_paths(paths)
        added = 0
        for p in expanded:
            if p not in self.fits_paths:
                self.fits_paths.append(p)
                added += 1
        for report in reports:
            print(f"[Mosaic Tab] Expanded {os.path.basename(report['source'])} "
                  f"into {report['count']} WCS image tiles: "
                  f"{', '.join(report['labels'])}", flush=True)
        if errors:
            details = "\n".join(f"{os.path.basename(p)}: {err}" for p, err in errors[:8])
            messagebox.showwarning(
                "Some FITS files could not be inspected",
                "They were added as ordinary files instead.\n\n" + details,
                parent=self)
        if added:
            print(f"[Mosaic Tab] Added {added} mosaic input(s)", flush=True)
            self.regroup()

    def add_folder(self):
        d = filedialog.askdirectory(title="Add FITS folder (scanned recursively)")
        if not d:
            return
        exts = (".fits", ".fit", ".fts", ".fits.fz", ".fit.fz", ".fts.fz")
        discovered = []
        for root, _dirs, files in os.walk(d):
            for fn in sorted(files):
                if fn.lower().endswith(exts):
                    discovered.append(os.path.join(root, fn))
        expanded, reports, errors = expand_mosaic_input_paths(discovered)
        added = 0
        for p in expanded:
            if p not in self.fits_paths:
                self.fits_paths.append(p)
                added += 1
        for report in reports:
            print(f"[Mosaic Tab] Expanded {os.path.basename(report['source'])} "
                  f"into {report['count']} WCS image tiles", flush=True)
        for source, error in errors:
            print(f"[Mosaic Tab] Could not inspect {source}: {error}", flush=True)
        if added:
            print(f"[Mosaic Tab] Added {added} mosaic input(s) from {d}", flush=True)
            self.regroup()
        else:
            messagebox.showinfo("No FITS found", f"No FITS files found in:\n{d}")

    def clear_all(self):
        if self.fits_paths and not messagebox.askyesno(
                "Clear all?", "Remove all files and groups?"):
            return
        self.fits_paths.clear()
        self.groups.clear()
        self.active_filters.clear()
        self.clear_sky_region()
        self._rebuild_tree()

    def regroup(self):
        """Re-read FILTER headers and rebuild groups automatically."""
        if not self.fits_paths:
            self._rebuild_tree()
            return
        self.groups = group_fits_by_filter(
            self.fits_paths, number_unknowns=True)
        self.active_filters = set(self.groups.keys())
        self._rebuild_tree()
        print(f"[Mosaic Tab] Groups: "
              f"{dict((k, len(v)) for k,v in self.groups.items())}", flush=True)

    def _rebuild_tree(self):
        self.tree.delete(*self.tree.get_children())
        for fcode in sorted(self.groups):
            paths = self.groups[fcode]
            n   = len(paths)
            gid = f"grp_{fcode}"
            state = "ON" if fcode in self.active_filters else "OFF"
            try:
                scale_txt = f" · scale {self._scale_for_filter(fcode):g}x"
            except EXPECTED_OPERATION_ERRORS:
                scale_txt = " · scale ?"
            self.tree.insert("", "end", iid=gid,
                             text=f"  {fcode}",
                             values=("", f"{state} · {n} tile{'s' if n != 1 else ''}{scale_txt}"),
                             open=True, tags=("group",))
            for p in paths:
                self.tree.insert(gid, "end",
                                 text=f"    {_mosaic_input_display_name(p)}",
                                 values=(p, ""),
                                 tags=("file",))
        self.tree.tag_configure("group", font=("Segoe UI", 9, "bold"))
        nf = sum(len(v) for v in self.groups.values())
        ng = len(self.groups)
        self._count_lbl.config(
            text=f"{nf} file{'s' if nf != 1 else ''}  |  "
                 f"{ng} group{'s' if ng != 1 else ''}")
        self._update_active_label()

__all__ = [name for name in globals() if not name.startswith('__')]
