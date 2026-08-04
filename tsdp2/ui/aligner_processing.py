from __future__ import annotations

from tsdp2.ui._common import *

class AlignmentExecution:

    def __init__(self, tab, **context):
        self.tab = tab
        self.__dict__.update(context)

    def progress_cb(self, step, total, msg):
        print(msg, flush=True)
        self.prog_q.put(('PROG', step, total, msg))
        cb = getattr(self.tab, 'pipeline_progress_cb', None)
        if callable(cb):
            try:
                cb(step, total, msg)
            except EXPECTED_OPERATION_ERRORS as exc:
                print(f'[Pipeline dashboard callback failed] {exc}', flush=True)
        elif self.direct_dashboard:
            try:
                pct = int(max(0, min(100, float(step) / max(float(total), 1.0) * 100.0)))
                self.direct_tg._telegram_update_dashboard('align', msg, pct)
            except EXPECTED_OPERATION_ERRORS as exc:
                print(f'[Direct align Telegram dashboard progress failed] {exc}', flush=True)

    def worker(self):
        try:
            print('----- RUN START -----', flush=True)
            print(f'Reference: {self.ref_name}  ({self.ref})', flush=True)
            print(f'Moving filters used: {len(self.items)}', flush=True)
            print(f'Output dir: {self.job_paths.root}', flush=True)
            print(f'Normalize: {self.normalize_enabled} mode={self.normalize_mode}', flush=True)
            print(f'PSF match: {self.psf_match_enabled}', flush=True)
            print(f'Keep full reference canvas: {self.keep_reference_canvas}', flush=True)
            print('', flush=True)
            out_folder, saved, log_path = align_crop_many(reference_path=self.ref, reference_name=self.ref_name, items=self.items, out_base_dir=self.job_paths, normalize_enabled=self.normalize_enabled, normalize_mode=self.normalize_mode, psf_match_enabled=self.psf_match_enabled, keep_reference_canvas=self.keep_reference_canvas, progress_cb=self.progress_cb, cancel_cb=self.tab._is_cancelled, generate_diagnostics=self.generate_diagnostics)
            self.prog_q.put(('DONE', out_folder, saved, log_path))
        except EXPECTED_OPERATION_ERRORS:
            self.prog_q.put(('FAIL', traceback.format_exc()))

    def poll(self):
        elapsed = time.time() - self.run_start
        step_val = int(self.tab.progress['value'])
        max_val = max(int(self.tab.progress['maximum']), 1)
        frac = step_val / max_val
        if step_val > 0 and frac > 0.05:
            eta = elapsed / frac * (1.0 - frac)
            time_sfx = f'  ·  {elapsed:.0f}s  ·  ~{max(0, eta):.0f}s left'
        elif elapsed >= 1.0:
            time_sfx = f'  ·  {elapsed:.0f}s'
        else:
            time_sfx = ''
        self.tab.status.config(text=self.last_msg[0] + time_sfx)
        try:
            while True:
                item = self.prog_q.get_nowait()
                if item[0] == 'PROG':
                    _, step, total, msg = item
                    self.tab.progress['maximum'] = total
                    self.tab.progress['value'] = step
                    self.last_msg[0] = msg
                elif item[0] == 'DONE':
                    _, out_folder, saved, log_path = item
                    self.tab._align_running = False
                    total_t = time.time() - self.run_start
                    self.tab.status.config(text=f'Done  ·  {total_t:.1f}s total')
                    print('\n----- RUN COMPLETE -----\n', flush=True)
                    self.tab.last_run_outputs = [(nm, p) for nm, p, _cov in saved]
                    if self.tab.last_run_outputs:
                        self.tab.blink_btn.config(state='normal')
                    try:
                        self.job_manifest.state = RunState.DONE
                        self.job_manifest.processing_seconds = total_t
                        for _name, product_path, _coverage in saved:
                            self.job_manifest.record_product('final', product_path)
                        self.job_manifest.record_product('metadata', log_path)
                        for diagnostic in self.job_paths.diagnostics.rglob('*'):
                            if diagnostic.is_file():
                                self.job_manifest.record_product('diagnostics', diagnostic)
                        removed = cleanup_job_intermediates(self.job_paths, retain=self.retain_intermediates)
                        if removed:
                            self.job_manifest.products['intermediate'] = [item for item in self.job_manifest.products['intermediate'] if not any((item == prefix or item.startswith(prefix + '/') for prefix in removed))]
                        self.job_manifest.save()
                    except (OSError, ValueError, KeyError):
                        LOGGER.exception('Could not finalize alignment manifest')
                    lines = [f'Saved in:\n{out_folder}\n', f'Log file:\n{log_path}\n']
                    for nm, path, cov in saved:
                        lines.append(f'{nm} -> {os.path.basename(path)} | coverage: {cov:.2f}%')
                    if self.generate_diagnostics:
                        lines.append('')
                        lines.append('Diagnostics saved separately:')
                        lines.append('common_overlap_mask.fits')
                        lines.append('footprint_<name>.fits for each output')
                    done_cb = getattr(self.tab, 'pipeline_done_cb', None)
                    if callable(done_cb):
                        try:
                            done_cb(out_folder, saved, log_path, total_t)
                        except EXPECTED_OPERATION_ERRORS as exc:
                            print(f'[Pipeline done callback failed] {exc}', flush=True)
                    else:
                        direct_tg_done = getattr(self.tab, 'telegram_ref', None)
                        if direct_tg_done is not None and hasattr(direct_tg_done, '_notify'):
                            try:
                                first_path = saved[0][1] if saved else None
                                if hasattr(direct_tg_done, '_telegram_update_dashboard'):
                                    try:
                                        direct_tg_done._last_pipeline_resolution = fits_resolution_text(first_path) if first_path else 'unknown'
                                        direct_tg_done._telegram_update_dashboard('align', f'Alignment complete - {len(saved or [])} file(s)', 100, force=True)
                                    except EXPECTED_OPERATION_ERRORS as dash_exc:
                                        print(f'[Direct align Telegram final dashboard failed] {dash_exc}', flush=True)
                                names = ', '.join((str(nm) for nm, _p, _cov in (saved or [])[:20]))
                                direct_tg_done._notify(f"🔭 <b>TSDP - Alignment complete</b>\n✅ {len(saved or [])} aligned file(s){(': ' + _html_escape(names) if names else '')}\n⏱ {total_t:.0f}s", fits_path=first_path)
                                try:
                                    if hasattr(direct_tg_done, '_send_finish_buttons'):
                                        direct_tg_done._send_finish_buttons()
                                except EXPECTED_OPERATION_ERRORS:
                                    LOGGER.debug("Best-effort operation failed", exc_info=True)
                            except EXPECTED_OPERATION_ERRORS as exc:
                                print(f'[Direct align Telegram complete failed] {exc}', flush=True)
                    self.tab.pipeline_progress_cb = None
                    self.tab.pipeline_done_cb = None
                    messagebox.showinfo('Done', '\n'.join(lines))
                    return
                elif item[0] == 'FAIL':
                    _, tb = item
                    self.tab._align_running = False
                    self.tab.status.config(text='Failed')
                    try:
                        self.job_manifest.cancelled = self.tab._is_cancelled()
                        self.job_manifest.failed = not self.job_manifest.cancelled
                        self.job_manifest.state = RunState.CANCELLED if self.job_manifest.cancelled else RunState.ERROR
                        self.job_manifest.processing_seconds = time.time() - self.run_start
                        self.job_manifest.warnings.append(str(tb).strip().splitlines()[-1][:1000])
                        self.job_manifest.save()
                    except (OSError, ValueError, KeyError):
                        LOGGER.exception('Could not update failed alignment manifest')
                    print('\n----- RUN FAILED -----\n', flush=True)
                    print(tb, flush=True)
                    direct_tg_fail = getattr(self.tab, 'telegram_ref', None)
                    if not callable(getattr(self.tab, 'pipeline_done_cb', None)) and direct_tg_fail is not None and hasattr(direct_tg_fail, '_notify'):
                        try:
                            err_line = str(tb).strip().splitlines()[-1] if str(tb).strip() else 'Unknown error'
                            if hasattr(direct_tg_fail, '_telegram_update_dashboard'):
                                try:
                                    direct_tg_fail._telegram_update_dashboard('error', f'Alignment error: {err_line[:180]}', 0, force=True)
                                except EXPECTED_OPERATION_ERRORS as dash_exc:
                                    print(f'[Direct align Telegram error dashboard failed] {dash_exc}', flush=True)
                            direct_tg_fail._notify(f'❌ <b>TSDP - Alignment error</b>\n<code>{_html_escape(err_line[:800])}</code>')
                        except EXPECTED_OPERATION_ERRORS as exc:
                            print(f'[Direct align Telegram failure notify failed] {exc}', flush=True)
                    self.tab.pipeline_progress_cb = None
                    self.tab.pipeline_done_cb = None
                    messagebox.showerror('Failed', 'Processing failed. See the Live Logs window for details.')
                    return
        except queue.Empty:
            pass
        self.tab.after(100, self.poll)

class AlignerProcessingMixin:
    def cancel(self):
        self.cancel_requested = True
        self.status.config(text="Cancel requested...")

    def _is_cancelled(self):
        return bool(self.cancel_requested)

    def run(self):
        self.cancel_requested = False
        self.status.config(text='Starting...')
        self.progress['value'] = 0
        ref = self.reference_path.get().strip()
        ref_name = self.reference_name.get().strip()
        out_dir = self.output_dir.get().strip()
        if not ref or not os.path.exists(ref):
            messagebox.showerror('Missing reference', 'Pick a valid reference FITS file.')
            return
        if not ref_name:
            messagebox.showerror('Missing name', 'Give the reference an output name.')
            return
        if not out_dir or not os.path.isdir(out_dir):
            messagebox.showerror('Missing output directory', 'Pick a valid output directory.')
            return
        items = []
        names_seen = set([safe_filename(ref_name).lower()])
        for r in self.rows:
            p = r.path.get().strip()
            nm = r.name.get().strip()
            if not p:
                continue
            if not os.path.exists(p):
                messagebox.showerror('Invalid file', f'Filter {r.idx} has an invalid FITS file:\n{p}')
                return
            if not nm:
                messagebox.showerror('Missing name', f'Filter {r.idx} needs an output name.')
                return
            nm_safe = safe_filename(nm).lower()
            if nm_safe in names_seen:
                messagebox.showerror('Duplicate name', f"Name '{nm}' is duplicated. Names must be unique.")
                return
            names_seen.add(nm_safe)
            items.append({'path': p, 'name': nm})
        if len(items) == 0:
            messagebox.showerror('No moving filters', 'All rows are blank. Pick at least one moving filter.')
            return
        job_paths = getattr(self, '_pending_job_paths', None)
        self._pending_job_paths = None
        if not isinstance(job_paths, JobPaths):
            try:
                job_paths = JobPaths.create(out_dir, f'{safe_filename(ref_name)}_Alignment')
            except (OSError, ValueError) as exc:
                messagebox.showerror('Output folder error', str(exc))
                return
        self._active_job_paths = job_paths
        generate_diagnostics = bool(self.generate_diagnostics.get())
        retain_intermediates = bool(self.retain_intermediates.get())
        job_manifest = getattr(self, '_pending_job_manifest', None)
        self._pending_job_manifest = None
        if not isinstance(job_manifest, JobManifest):
            job_manifest = JobManifest(paths=job_paths, job_name=job_paths.root.name)
        job_manifest.paths = job_paths
        job_manifest.state = RunState.RUNNING
        job_manifest.cancelled = False
        job_manifest.failed = False
        for source_file in [ref] + [item['path'] for item in items]:
            if source_file not in job_manifest.source_files:
                job_manifest.source_files.append(source_file)
        job_manifest.settings.update({'normalize_enabled': bool(self.normalize_enabled.get()), 'normalize_mode': str(self.normalize_mode.get()), 'psf_match_enabled': bool(self.psf_match_enabled.get()), 'keep_reference_canvas': bool(self.keep_reference_canvas.get()), 'generate_diagnostics': generate_diagnostics, 'retain_intermediates': retain_intermediates})
        try:
            job_manifest.save()
        except OSError as exc:
            messagebox.showerror('Manifest error', str(exc))
            return
        self._active_job_manifest = job_manifest
        normalize_enabled = bool(self.normalize_enabled.get())
        normalize_mode = str(self.normalize_mode.get())
        psf_match_enabled = bool(self.psf_match_enabled.get())
        keep_reference_canvas = bool(self.keep_reference_canvas.get())
        if keep_reference_canvas and psf_match_enabled:
            psf_match_enabled = False
            self.psf_match_enabled.set(False)
            job_manifest.settings['psf_match_enabled'] = False
            try:
                job_manifest.save()
            except OSError:
                LOGGER.exception('Could not update alignment manifest settings')
            messagebox.showinfo('Full reference canvas', 'PSF matching has been turned off for this run. Blurring the full Euclid reference would soften the background canvas and damage the edges of partial Hubble overlays.', parent=self)
        direct_tg = None
        try:
            if not callable(getattr(self, 'pipeline_done_cb', None)):
                direct_tg = getattr(self, 'telegram_ref', None)
        except EXPECTED_OPERATION_ERRORS:
            direct_tg = None
        direct_dashboard = direct_tg is not None and hasattr(direct_tg, '_telegram_update_dashboard')
        if direct_dashboard:
            try:
                direct_tg.pipeline_start = time.time()
                direct_tg._tg_dashboard_message_id = None
                direct_tg._last_pipeline_reference = os.path.basename(ref)
                direct_tg._last_pipeline_resolution = fits_resolution_text(ref)
                direct_tg._telegram_update_dashboard('align', 'Starting direct alignment', 0, force=True)
            except EXPECTED_OPERATION_ERRORS as exc:
                print(f'[Direct align Telegram dashboard start failed] {exc}', flush=True)
        elif direct_tg is not None and hasattr(direct_tg, '_notify'):
            try:
                direct_tg._notify(f'🔭 <b>TSDP - Alignment started</b>\nReference: <code>{_html_escape(os.path.basename(ref))}</code>\nMoving file(s): {len(items)}\nOutput: <code>{_html_escape(job_paths.root)}</code>')
            except EXPECTED_OPERATION_ERRORS as exc:
                print(f'[Direct align Telegram start failed] {exc}', flush=True)
        prog_q = queue.Queue()
        self._align_running = True
        execution = AlignmentExecution(self, direct_dashboard=direct_dashboard, direct_tg=direct_tg, generate_diagnostics=generate_diagnostics, items=items, job_manifest=job_manifest, job_paths=job_paths, keep_reference_canvas=keep_reference_canvas, normalize_enabled=normalize_enabled, normalize_mode=normalize_mode, prog_q=prog_q, psf_match_enabled=psf_match_enabled, ref=ref, ref_name=ref_name, retain_intermediates=retain_intermediates)
        threading.Thread(target=execution.worker, daemon=True).start()
        execution.run_start = time.time()
        execution.last_msg = ['Starting…']
        execution.poll()

    def auto_run_from_pipeline(self, mosaic_paths, reference_mode="Ordinary best",
                               progress_cb=None, done_cb=None,
                               job_paths: Optional[JobPaths] = None,
                               job_manifest: Optional[JobManifest] = None):
        """
        Called by DownloaderTab after mosaic/align handoff.
        Auto-populates rows and runs alignment with pipeline defaults.
        """
        self.pipeline_progress_cb = progress_cb
        self.pipeline_done_cb = done_cb

        if not mosaic_paths:
            self.pipeline_progress_cb = None
            self.pipeline_done_cb = None
            messagebox.showwarning("No outputs",
                                   "Pipeline produced no files to align.")
            return
        try:
            mode = (reference_mode or "Ordinary best").strip().lower()
            if mode.startswith("highest"):
                paths = list(mosaic_paths.values())
                ref_path = choose_highest_resolution_reference(paths)
                ref_code = next((k for k, v in mosaic_paths.items() if v == ref_path), "reference")
                items = []
                for code, path in sorted(mosaic_paths.items()):
                    if path != ref_path:
                        items.append({"name": safe_filename(code), "path": path})
            else:
                ref_code, ref_path, items = pick_reference_from_mosaic_paths(
                    mosaic_paths)
        except EXPECTED_OPERATION_ERRORS as e:
            self.pipeline_progress_cb = None
            self.pipeline_done_cb = None
            messagebox.showerror("Aligner setup failed", str(e))
            return

        # Reference
        self.reference_path.set(ref_path)
        self.reference_name.set(safe_filename(ref_code))

        # Reuse the pipeline's job so mosaic and aligned finals remain isolated
        # but share one portable manifest tree.
        if job_paths is None:
            ref_parent = Path(ref_path).resolve()
            try:
                if (ref_parent.parent.name == "mosaics"
                        and ref_parent.parent.parent.name == "final"):
                    job_paths = JobPaths(ref_parent.parents[2])
            except (IndexError, OSError):
                job_paths = None
        self._pending_job_paths = job_paths
        self._pending_job_manifest = job_manifest
        out_dir = str(job_paths.root if job_paths else Path(ref_path).parent.parent)
        self.output_dir.set(out_dir)

        # Guard: need at least 1 moving filter. For pipeline/Telegram auto-runs,
        # finish gracefully instead of leaving the dashboard stuck at "Starting alignment".
        if not items:
            msg = (f"Only one filter/file ({ref_code}) was produced. "
                   "No WCS alignment is needed.")
            if isinstance(job_paths, JobPaths):
                try:
                    ref_candidate = Path(ref_path).resolve()
                    if ref_candidate.is_relative_to(
                            job_paths.intermediate.resolve()):
                        final_copy = job_paths.final_aligned_fits(
                            str(ref_code), ref_candidate.name)
                        shutil.copy2(ref_candidate, final_copy)
                        ref_path = os.fspath(final_copy)
                        self.reference_path.set(ref_path)
                except OSError:
                    LOGGER.exception(
                        "Could not promote the single alignment input to final")
            if isinstance(job_manifest, JobManifest):
                try:
                    job_manifest.state = RunState.DONE
                    job_manifest.record_product("final", ref_path)
                    cleanup_job_intermediates(job_paths)
                    job_manifest.save()
                except (OSError, ValueError, KeyError):
                    LOGGER.exception("Could not finalize single-filter manifest")
            done_cb = getattr(self, "pipeline_done_cb", None)
            if callable(done_cb):
                try:
                    done_cb(str(Path(ref_path).parent), [(ref_code, ref_path, 100.0)], "", 0.0)
                except EXPECTED_OPERATION_ERRORS as exc:
                    print(f"[Pipeline single-file done callback failed] {exc}", flush=True)
            else:
                messagebox.showwarning(
                    "Single filter",
                    msg + "\n\n" + f"Output: {ref_path}")
            self.pipeline_progress_cb = None
            self.pipeline_done_cb = None
            return

        # Rebuild filter rows
        n = len(items)
        self.num_filters.set(n)
        self.combo.set(str(n))
        self._rebuild_filter_rows(n)
        for r, item in zip(self.rows, items):
            r.path.set(item["path"])
            r.name.set(item["name"])

        # Pipeline defaults
        self.normalize_enabled.set(False)
        self.psf_match_enabled.set(False)

        # Update size labels + previews
        self._update_ref_size(ref_path)
        for r in self.rows:
            if r.path.get():
                r.update_size(r.path.get())
        if self.preview_visible:
            self.update_all_previews()

        # Create the output directory so the aligner's os.path.isdir check passes
        os.makedirs(out_dir, exist_ok=True)

        print("\n[Aligner] auto-run from pipeline", flush=True)
        print(f"  Reference : {ref_code}  →  {ref_path}", flush=True)
        for item in items:
            print(f"  Filter    : {item['name']}  →  {item['path']}",
                  flush=True)
        print(f"  Output    : {out_dir}\n", flush=True)

        self.run()

__all__ = [name for name in globals() if not name.startswith('__')]
