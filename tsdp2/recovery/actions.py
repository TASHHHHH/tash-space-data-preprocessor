from __future__ import annotations

from tsdp2.ui._common import *

class RecoveryActionsMixin:
    def _geometry_for_candidate(self, raw_path):
        folder = os.path.dirname(raw_path)
        manifest_path = os.path.join(folder, _RECOVERY_MANIFEST_NAME)
        if os.path.isfile(manifest_path):
            with open(manifest_path, "r", encoding="utf-8") as fh:
                manifest = json.load(fh)
            shape = tuple(int(x) for x in manifest["shape_out"])
            header = fits.Header.fromstring(manifest["wcs_header"], sep="\n")
            expected = int(shape[0]) * int(shape[1]) * 4
            if os.path.getsize(raw_path) != expected:
                raise ValueError(
                    f"Manifest expects {expected:,} raw bytes but the candidate has "
                    f"{os.path.getsize(raw_path):,}; the scratch file is incomplete or mismatched.")
            code = str(manifest.get("filter_code") or self.filter_var.get().strip() or "RECOVERED")
            return header, shape, float(manifest.get("downscale", 1.0)), code, "manifest"

        if not self.source_paths:
            raise ValueError(
                "This is a legacy temp folder with no recovery manifest. Add the original FITS tiles "
                "or use the Mosaic-tab inputs first.")
        if re.search(r"align_.+_data\.f32$", os.path.basename(raw_path), re.I):
            raw_bytes = os.path.getsize(raw_path)
            matches = []
            for source in self.source_paths:
                try:
                    light, _names = _recovery_header_inputs([source])
                    shape = tuple(int(x) for x in light[0][0].shape)
                    if int(shape[0]) * int(shape[1]) * 4 == raw_bytes:
                        matches.append((light[0][1], shape, source))
                except EXPECTED_OPERATION_ERRORS:
                    continue
            if not matches:
                raise ValueError(
                    "No loaded FITS has the same pixel grid as this alignment scratch. "
                    "Add the alignment reference FITS, then retry.")
            header, shape, source = matches[0]
            code = self.filter_var.get().strip()
            if not code:
                code = parse_filter_code(str(header.get("FILTER", ""))) or "ALIGNED"
            return header, shape, 1.0, code, f"reference grid from {os.path.basename(_parse_mosaic_hdu_ref(source)[0])}"
        match = re.match(r"(.+?)_(?:median|mean|average|feather)_out\.f32$",
                         os.path.basename(raw_path), re.I)
        code = self.filter_var.get().strip() or (match.group(1) if match else "")
        groups = group_fits_by_filter(self.source_paths)
        group_paths = groups.get(code)
        if group_paths is None:
            group_paths = next((value for key, value in groups.items()
                                if key.upper() == code.upper()), None)
        if group_paths is None and len(groups) == 1:
            code, group_paths = next(iter(groups.items()))
        if not group_paths:
            raise ValueError(
                f"No original FITS group matches filter '{code}'. Available groups: " +
                ", ".join(sorted(groups)))
        inputs, names = _recovery_header_inputs(group_paths)
        inputs, names, _dropped = _cluster_tiles_by_sky(inputs, names)
        wcs_native, shape_native = find_optimal_celestial_wcs(inputs)
        preferred_scale = _normalise_mosaic_downscale(self.scale_var.get(), default=1.0)
        wcs_out, shape_out, used_scale = _infer_recovery_geometry(
            raw_path, wcs_native, shape_native, preferred_scale)
        header = wcs_out.to_header()
        header["FILTER"] = code or "RECOVERED"
        return header, shape_out, used_scale, code or "RECOVERED", "reconstructed"

    def recover_selected(self):
        if self.worker and self.worker.is_alive():
            messagebox.showwarning("Recovery busy", "Wait for or cancel the current action.")
            return
        try:
            raw_path = self._candidate_from_selection()
            output_dir = os.path.abspath(self.output_dir.get().strip())
            if not output_dir:
                raise ValueError("Choose a safe output folder.")
        except EXPECTED_OPERATION_ERRORS as exc:
            messagebox.showerror("Cannot start recovery", str(exc))
            return
        self.cancel_event.clear()
        self._set_busy("Reconstructing WCS/shape from manifest or original FITS headers...")

        def work():
            try:
                header, shape, scale, code, method = self._geometry_for_candidate(raw_path)
                safe_code = safe_filename(code or "RECOVERED")
                out_path = os.path.join(output_dir, f"recovered_mosaic_{safe_code}.fits")
                def progress(done, total, message):
                    self.work_q.put(("progress", int(done / max(total, 1) * 100), message))
                _stream_float32_to_fits(
                    raw_path, shape, header, out_path, progress_cb=progress,
                    cancel_cb=self.cancel_event.is_set)
                self.work_q.put(("recover_done", out_path, shape, scale, method))
            except EXPECTED_OPERATION_ERRORS as exc:
                self.work_q.put(("error", str(exc), traceback.format_exc()))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def validate_selected(self):
        paths = self._selected_paths()
        if not paths:
            messagebox.showinfo("Nothing selected", "Select a recovery item first.")
            return
        lines = []
        for path in paths[:20]:
            try:
                if os.path.isdir(path):
                    lines.append(f"{os.path.basename(path)}: readable recovery session")
                elif path.lower().endswith(".f32"):
                    size = os.path.getsize(path)
                    state = "whole float32 values" if size % 4 == 0 else "TRUNCATED/not float32"
                    lines.append(f"{os.path.basename(path)}: {self._size_text(size)}, {size // 4:,} pixels, {state}")
                elif path.lower().endswith((".fits", ".fit", ".fts", ".fits.fz")):
                    with fits.open(path, memmap=True, lazy_load_hdus=True) as hdul:
                        image = next((hdu for hdu in hdul if getattr(hdu, "shape", None)), None)
                        if image is None:
                            raise ValueError("no image HDU")
                        has_wcs = WCS(image.header, naxis=2).has_celestial
                        lines.append(f"{os.path.basename(path)}: shape={image.shape}, WCS={'yes' if has_wcs else 'no'}")
                elif os.path.basename(path).lower() == _RECOVERY_MANIFEST_NAME:
                    with open(path, "r", encoding="utf-8") as fh:
                        manifest = json.load(fh)
                    lines.append(
                        f"manifest: stage={manifest.get('stage')}, filter={manifest.get('filter_code')}, "
                        f"shape={manifest.get('shape_out')}, scale={manifest.get('downscale')}")
                else:
                    lines.append(f"{os.path.basename(path)}: readable, {self._size_text(os.path.getsize(path))}")
            except EXPECTED_OPERATION_ERRORS as exc:
                lines.append(f"{os.path.basename(path)}: ERROR - {exc}")
        messagebox.showinfo("Recovery inspection", "\n".join(lines))

    def repair_holes_selected(self):
        if self.worker and self.worker.is_alive():
            messagebox.showwarning("Recovery busy", "Wait for or cancel the current action.")
            return
        paths = [path for path in self._selected_paths()
                 if os.path.isfile(path) and path.lower().endswith((".fits", ".fit", ".fts"))]
        if not paths:
            initial = next((root for root in self.roots if os.path.isdir(root)),
                           self.output_dir.get().strip() or str(Path.home()))
            picked = filedialog.askopenfilename(
                title="Select the existing mosaic FITS with black holes",
                initialdir=initial,
                filetypes=[("Uncompressed FITS", "*.fits *.fit *.fts"),
                           ("All files", "*.*")])
            if not picked:
                return
            paths = [picked]
        if not self.source_paths:
            sources = filedialog.askopenfilenames(
                title="Select the original FITS/MEF tiles used for this mosaic",
                initialdir=os.path.dirname(paths[0]),
                filetypes=[("FITS files", "*.fits *.fit *.fts *.fits.fz *.fit.fz *.fts.fz"),
                           ("All files", "*.*")])
            if not sources:
                return
            self._add_sources(sources)
            if not self.source_paths:
                return
        output_dir = os.path.abspath(self.output_dir.get().strip())
        if not output_dir:
            messagebox.showerror("No output folder", "Choose a safe output folder first.")
            return
        target = paths[0]
        output_path = os.path.join(output_dir, "hole_repaired_" + os.path.basename(target))
        self.cancel_event.clear()
        self._set_busy("Finding enclosed black/NaN regions at low resolution...")

        def work():
            try:
                def progress(done, total, message):
                    self.work_q.put(("progress", int(done / max(total, 1) * 100), message))
                result = _fast_repair_fits_holes(
                    target, list(self.source_paths), output_path,
                    filter_override=self.filter_var.get().strip(),
                    progress_cb=progress, cancel_cb=self.cancel_event.is_set)
                self.work_q.put(("hole_repair_done", result))
            except EXPECTED_OPERATION_ERRORS as exc:
                self.work_q.put(("error", str(exc), traceback.format_exc()))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def copy_selected(self):
        if self.worker and self.worker.is_alive():
            return
        paths = [path for path in self._selected_paths() if os.path.isfile(path)]
        if not paths:
            messagebox.showinfo("Nothing to copy", "Select one or more files, not a session folder.")
            return
        destination = filedialog.askdirectory(title="Copy recovery files to a reliable drive")
        if not destination:
            return
        self.cancel_event.clear()
        self._set_busy("Copying selected recovery files...")

        def work():
            try:
                total = sum(os.path.getsize(path) for path in paths)
                done = 0
                for source in paths:
                    final = os.path.join(destination, os.path.basename(source))
                    temp = final + ".copying"
                    with open(source, "rb") as src, open(temp, "wb") as dst:
                        while True:
                            if self.cancel_event.is_set():
                                raise RuntimeError("Copy cancelled by user.")
                            chunk = src.read(16 * 1024 * 1024)
                            if not chunk:
                                break
                            dst.write(chunk)
                            done += len(chunk)
                            self.work_q.put(("progress", int(done / max(total, 1) * 100),
                                             f"Copying {os.path.basename(source)}"))
                        dst.flush()
                        try:
                            os.fsync(dst.fileno())
                        except OSError:
                            pass
                    os.replace(temp, final)
                self.work_q.put(("copy_done", destination, len(paths)))
            except EXPECTED_OPERATION_ERRORS as exc:
                self.work_q.put(("error", str(exc), traceback.format_exc()))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def open_selected_folder(self):
        paths = self._selected_paths()
        if not paths:
            return
        path = paths[0] if os.path.isdir(paths[0]) else os.path.dirname(paths[0])
        try:
            if os.name == "nt":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except EXPECTED_OPERATION_ERRORS as exc:
            messagebox.showerror("Open folder failed", str(exc))

    def _inside_registered_root(self, path):
        path = os.path.abspath(path)
        for root in self.roots:
            try:
                if os.path.commonpath([path, os.path.abspath(root)]) == os.path.abspath(root):
                    return True
            except ValueError:
                continue
        return False

    def delete_selected(self):
        paths = self._selected_paths()
        if not paths:
            return
        unsafe = [path for path in paths if not self._inside_registered_root(path)]
        if unsafe:
            messagebox.showerror("Cleanup blocked", "Only items beneath an added recovery root can be deleted.")
            return
        preview = "\n".join(paths[:8]) + ("\n..." if len(paths) > 8 else "")
        if not messagebox.askyesno(
                "Permanently delete temp data?",
                f"Delete these {len(paths)} selected recovery item(s)?\n\n{preview}\n\n"
                "Only do this after a recovered FITS has been validated."):
            return
        errors = []
        for path in paths:
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
            except EXPECTED_OPERATION_ERRORS as exc:
                errors.append(f"{path}: {exc}")
        self.refresh()
        if errors:
            messagebox.showwarning("Some items were not deleted", "\n".join(errors[:10]))

__all__ = [name for name in globals() if not name.startswith('__')]
