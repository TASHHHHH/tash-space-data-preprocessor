from __future__ import annotations

from tsdp2.ui._common import *

class AlignerInputMixin:
    @staticmethod
    def _list_fits_in_folder(folder: str):
        exts = (".fits", ".fit", ".fts")
        out = []
        try:
            for entry in sorted(os.listdir(folder)):
                full = os.path.join(folder, entry)
                if os.path.isfile(full) and entry.lower().endswith(exts):
                    out.append(full)
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
        return out

    @staticmethod
    def _list_fits_recursive(folder: str):
        """Walk a folder tree and return every FITS file found, regardless of layout."""
        exts = (".fits", ".fit", ".fts")
        out = []
        try:
            for root, _dirs, files in os.walk(folder):
                for entry in sorted(files):
                    if entry.lower().endswith(exts):
                        out.append(os.path.join(root, entry))
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
        return sorted(out)

    def batch_add_folders(self):
        main = filedialog.askdirectory(title="Select MAIN folder (contains one subfolder per filter)")
        if not main:
            return
        if not os.path.isdir(main):
            messagebox.showerror("Invalid folder", "That is not a valid folder.")
            return

        # Find subfolders that contain at least one FITS file.
        subfolders = []
        try:
            for entry in sorted(os.listdir(main)):
                sub = os.path.join(main, entry)
                if os.path.isdir(sub) and self._list_fits_in_folder(sub):
                    subfolders.append(sub)
        except EXPECTED_OPERATION_ERRORS as e:
            messagebox.showerror("Scan failed", f"Could not read the main folder:\n{e}")
            return

        if not subfolders:
            messagebox.showerror(
                "No filter folders found",
                "No subfolders containing FITS files were found in:\n"
                f"{main}\n\n"
                "Expected layout:\n"
                "  main/\n"
                "    F115W/  -> *.fits\n"
                "    F200W/  -> *.fits\n"
                "    ...",
            )
            return

        # Build (path, name) for each subfolder.
        # If a subfolder has multiple FITS, pick the best one as reference candidate.
        picked = []  # list of (fits_path, suggested_name, folder_name, n_files)
        for sub in subfolders:
            files = self._list_fits_in_folder(sub)
            if len(files) == 1:
                chosen = files[0]
            else:
                try:
                    chosen, _table = choose_best_reference(files)
                except EXPECTED_OPERATION_ERRORS:
                    chosen = files[0]
            folder_name = os.path.basename(sub.rstrip("/\\"))
            nm = suggest_name_from_fits(chosen)
            if not nm:
                nm = safe_filename(folder_name)
            picked.append((chosen, nm, folder_name, len(files)))

        # Ensure unique output names (folders can collide on auto-name).
        seen = {}
        for i, (p, nm, folder_name, n) in enumerate(picked):
            base = nm
            key = base.lower()
            if key in seen:
                seen[key] += 1
                nm = f"{base}_{seen[key]}"
            else:
                seen[key] = 1
            picked[i] = (p, nm, folder_name, n)

        # Auto-pick the overall reference from all chosen files, then make the rest moving filters.
        all_paths = [p for (p, _nm, _fn, _n) in picked]
        try:
            best_ref, _t = choose_best_reference(all_paths)
        except EXPECTED_OPERATION_ERRORS:
            best_ref = all_paths[0]

        ref_entry = None
        moving = []
        for entry in picked:
            if entry[0] == best_ref and ref_entry is None:
                ref_entry = entry
            else:
                moving.append(entry)

        # Set reference fields.
        if ref_entry is not None:
            self.reference_path.set(ref_entry[0])
            self.reference_name.set(ref_entry[1])

        # Rebuild moving-filter rows to match the number of moving folders (min 1 row).
        n_rows = max(1, len(moving))
        self.num_filters.set(n_rows)
        self.combo.set(str(n_rows))
        self._rebuild_filter_rows(n_rows)

        for r, (p, nm, _fn, _n) in zip(self.rows, moving):
            r.path.set(p)
            r.name.set(nm)

        self.preset.set("Custom")

        # Summary.
        lines = [
            f"Main folder:\n{main}\n",
            f"Filter folders found: {len(subfolders)}",
            "",
            f"Reference: {ref_entry[1]}  (from folder '{ref_entry[2]}')" if ref_entry else "Reference: (none)",
            "",
            "Moving filters:",
        ]
        for (p, nm, fn, n) in moving:
            extra = f"  [{n} FITS, best picked]" if n > 1 else ""
            lines.append(f"  {nm}  (folder '{fn}'){extra}")
        lines.append("")
        lines.append("Review the rows, then set an output directory and Run.")
        messagebox.showinfo("Batch add complete", "\n".join(lines))

    def add_fits_folder_flat(self):
        """Import every FITS file sitting in one folder, no per-filter subfolder
        structure required (unlike Batch Add Folders). Each FITS file becomes its
        own row; the sharpest/best file is auto-picked as the reference.

        If the folder has no FITS files directly inside it, subfolders are
        scanned too, so a messy mix of loose files and nested folders both work.
        """
        folder = filedialog.askdirectory(
            title="Select folder containing FITS files (loose files, any layout)"
        )
        if not folder:
            return
        if not os.path.isdir(folder):
            messagebox.showerror("Invalid folder", "That is not a valid folder.")
            return

        files = self._list_fits_in_folder(folder)
        scanned_recursive = False
        if not files:
            files = self._list_fits_recursive(folder)
            scanned_recursive = True

        if not files:
            messagebox.showerror(
                "No FITS files found",
                f"No .fits/.fit/.fts files were found in:\n{folder}\n\n"
                "(subfolders were searched too)",
            )
            return

        # Build (path, suggested name) for each FITS file found.
        picked = []
        for f in files:
            nm = suggest_name_from_fits(f)
            if not nm:
                nm = safe_filename(os.path.splitext(os.path.basename(f))[0])
            picked.append((f, nm))

        # Ensure unique output names (files can collide on auto-name).
        seen = {}
        for i, (p, nm) in enumerate(picked):
            base = nm
            key = base.lower()
            if key in seen:
                seen[key] += 1
                nm = f"{base}_{seen[key]}"
            else:
                seen[key] = 1
            picked[i] = (p, nm)

        # Auto-pick the overall reference from all files, rest become moving filters.
        all_paths = [p for (p, _nm) in picked]
        try:
            best_ref, _t = choose_best_reference(all_paths)
        except EXPECTED_OPERATION_ERRORS:
            best_ref = all_paths[0]

        ref_entry = None
        moving = []
        for entry in picked:
            if entry[0] == best_ref and ref_entry is None:
                ref_entry = entry
            else:
                moving.append(entry)

        if ref_entry is not None:
            self.reference_path.set(ref_entry[0])
            self.reference_name.set(ref_entry[1])

        # Rebuild moving-filter rows to match the number of files found (min 1 row).
        n_rows = max(1, len(moving))
        self.num_filters.set(n_rows)
        self.combo.set(str(n_rows))
        self._rebuild_filter_rows(n_rows)

        for r, (p, nm) in zip(self.rows, moving):
            r.path.set(p)
            r.name.set(nm)

        self.preset.set("Custom")

        # Summary.
        lines = [
            f"Folder:\n{folder}\n",
            f"FITS files found: {len(files)}"
            + ("  (searched subfolders)" if scanned_recursive else ""),
            "",
            f"Reference: {ref_entry[1]}" if ref_entry else "Reference: (none)",
            "",
            "Moving filters:",
        ]
        for (p, nm) in moving:
            lines.append(f"  {nm}   <-   {os.path.basename(p)}")
        lines.append("")
        lines.append("Review the rows, then set an output directory and Run.")
        messagebox.showinfo("Folder import complete", "\n".join(lines))

__all__ = [name for name in globals() if not name.startswith('__')]
