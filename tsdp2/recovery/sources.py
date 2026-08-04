from __future__ import annotations

from tsdp2.ui._common import *

class RecoverySourcesMixin:
    def add_source_files(self):
        paths = filedialog.askopenfilenames(
            title="Add the original FITS used by the failed mosaic",
            filetypes=[("FITS files", "*.fits *.fit *.fts *.fits.fz *.fit.fz *.fts.fz"),
                       ("All files", "*.*")])
        self._add_sources(paths)

    def add_source_folder(self):
        folder = filedialog.askdirectory(title="Folder containing original FITS")
        if not folder:
            return
        paths = []
        try:
            for entry in os.scandir(folder):
                if entry.is_file() and entry.name.lower().endswith(
                        (".fits", ".fit", ".fts", ".fits.fz", ".fit.fz", ".fts.fz")):
                    paths.append(entry.path)
        except EXPECTED_OPERATION_ERRORS as exc:
            messagebox.showerror("FITS folder error", str(exc))
            return
        self._add_sources(paths)

    def _add_sources(self, paths):
        if not paths:
            return
        expanded, reports, errors = expand_mosaic_input_paths(paths)
        for path in expanded:
            if path not in self.source_paths:
                self.source_paths.append(path)
        groups = group_fits_by_filter(self.source_paths) if self.source_paths else {}
        summary = ", ".join(f"{key}: {len(value)}" for key, value in sorted(groups.items()))
        self.source_label.set(f"Original FITS inputs: {len(self.source_paths)} tile(s) - {summary}")
        if reports:
            self.status_var.set(
                f"Expanded {sum(report['count'] for report in reports)} virtual image tiles; no duplicate FITS were written.")
        if errors:
            messagebox.showwarning("Some FITS could not be inspected", "\n".join(
                f"{path}: {error}" for path, error in errors[:10]))

    def use_mosaic_inputs(self):
        paths = list(getattr(self.mosaic_ref, "fits_paths", []) or []) if self.mosaic_ref else []
        if not paths:
            messagebox.showinfo("No Mosaic inputs", "The Mosaic tab has no loaded FITS inputs.")
            return
        self._add_sources(paths)

    def pick_output(self):
        path = filedialog.askdirectory(title="Safe output folder for recovered FITS")
        if path:
            self.output_dir.set(path)

    def _candidate_from_selection(self):
        paths = self._selected_paths()
        if not paths:
            raise ValueError("Select a combined .f32 candidate or recovery session.")
        path = paths[0]
        if os.path.isdir(path):
            manifest_path = os.path.join(path, _RECOVERY_MANIFEST_NAME)
            preferred = []
            if os.path.isfile(manifest_path):
                try:
                    with open(manifest_path, "r", encoding="utf-8") as fh:
                        manifest = json.load(fh)
                    final_name = manifest.get("final_array")
                    if final_name:
                        preferred.append(os.path.join(path, final_name))
                except EXPECTED_OPERATION_ERRORS:
                    LOGGER.debug("Best-effort operation failed", exc_info=True)
            try:
                preferred.extend(os.path.join(path, name) for name in os.listdir(path)
                                 if re.search(r"_(median|mean|average|feather)_out\.f32$", name, re.I))
            except EXPECTED_OPERATION_ERRORS as exc:
                raise IOError(f"Cannot read recovery session: {exc}") from exc
            path = next((p for p in preferred if os.path.isfile(p)), "")
            if not path:
                raise ValueError("This session has no completed combined float32 mosaic yet.")
        if not path.lower().endswith(".f32"):
            raise ValueError("Select a combined .f32 mosaic, not a mask, manifest, or partial FITS.")
        return path

__all__ = [name for name in globals() if not name.startswith('__')]
