from __future__ import annotations

from tsdp2.ui._common import *

class DownloaderPipelineMixin:
    def run_pipeline_manual(self):
        p = filedialog.askopenfilename(
            title="Select ZIP or FITS file for pipeline",
            filetypes=[("ZIP files", "*.zip"),
                       ("FITS files", "*.fits *.fit *.fts"),
                       ("All files", "*.*")])
        if p:
            self._start_pipeline_for(p)

    def cancel_pipeline(self):
        stopped_any = False
        if self.pipeline_runner and self.pipeline_runner.is_alive():
            self.pipeline_runner.stop()
            stopped_any = True
        # After the downloader hands off to the Aligner tab, the PipelineRunner
        # thread is already finished. The Downloader Cancel button must still
        # cancel the real alignment worker, otherwise Telegram /stop looks like
        # it worked but alignment keeps running.
        try:
            if self.aligner_ref is not None and getattr(self.aligner_ref, "_align_running", False):
                self.aligner_ref.cancel()
                stopped_any = True
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
        if stopped_any:
            self.pipe_lbl.config(text="Cancelling…")
            self._telegram_update_dashboard("cancelled", "Cancel requested", 0, force=True)
        self.pipe_cancel_btn.config(state="disabled")

    def _select_pipeline_mosaic_filters(self, groups: dict, out_dir: str = ""):
        """Ask the user which detected filter groups should continue.

        PipelineRunner runs in a worker thread, so the actual Tk dialog is
        scheduled onto the main thread and this method waits for the result.
        """
        result = {"selected": None}
        done = threading.Event()

        def _ui():
            try:
                if groups and self.mosaic_tab_ref is not None:
                    self.mosaic_tab_ref.load_from_pipeline(groups, out_dir)
                    if self.notebook_ref:
                        self.notebook_ref.select(self.mosaic_tab_ref)
                initial_scales = {}
                global_scale = "1.0"
                try:
                    if self.mosaic_tab_ref is not None:
                        initial_scales = dict(getattr(self.mosaic_tab_ref, "filter_downscales", {}) or {})
                        global_scale = getattr(self.mosaic_tab_ref, "downscale_global", tk.StringVar(value="1.0")).get()
                except EXPECTED_OPERATION_ERRORS:
                    LOGGER.debug("Best-effort operation failed", exc_info=True)
                selected = ask_filter_selection(
                    self,
                    groups,
                    title="Select mosaic filters",
                    initial_selected=sorted(groups.keys()),
                    message="Mosaic groups are ready. Select filters and optional output scale before continuing.",
                    out_dir=out_dir,
                    initial_scales=initial_scales,
                    global_scale=global_scale,
                    return_scales=True
                )
                if isinstance(selected, dict) and self.mosaic_tab_ref is not None:
                    try:
                        self.mosaic_tab_ref.downscale_global.set(str(selected.get("global_scale", global_scale)))
                        self.mosaic_tab_ref.filter_downscales = dict(selected.get("scales") or {})
                        self.mosaic_tab_ref._rebuild_tree()
                    except EXPECTED_OPERATION_ERRORS:
                        LOGGER.debug("Best-effort operation failed", exc_info=True)
                result["selected"] = selected
            except EXPECTED_OPERATION_ERRORS as exc:
                print(f"[Pipeline filter selection failed] {exc}", flush=True)
                result["selected"] = None
            finally:
                done.set()

        post_to_ui(_ui)
        while not done.wait(0.1):
            try:
                if self.pipeline_runner and self.pipeline_runner.stopped():
                    return None
            except EXPECTED_OPERATION_ERRORS:
                LOGGER.debug("Best-effort operation failed", exc_info=True)
        return result.get("selected")

    def _start_pipeline_for_core(self, source_path):
        if self.pipeline_runner and self.pipeline_runner.is_alive():
            messagebox.showwarning(
                "Pipeline running",
                "A pipeline is already running. Wait for it to finish.")
            return
        try:
            if self.aligner_ref is not None and getattr(self.aligner_ref, "_align_running", False):
                messagebox.showwarning(
                    "Alignment running",
                    "An alignment job is already running. Wait for it to finish or cancel it first.")
                return
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
        work = self.work_dir.get().strip()
        if not work:
            messagebox.showerror("No work dir",
                                 "Set a work / unzip directory first.")
            return
        mode    = self.pipeline_mode.get()
        combine = self.combine_fn.get()
        ref_mode = self.reference_mode.get().strip()
        if mode in ("align", "mosaic_align") and not ref_mode:
            messagebox.showerror(
                "Reference frame required",
                "Choose a Reference frame mode before starting a WCS Align pipeline.\n\n"
                "Use 'Highest resolution' or 'Ordinary best'.")
            return

        source_leaf = Path(os.fspath(source_path)).name
        source_stem = Path(source_leaf).stem or "TSDP"
        try:
            job_paths = JobPaths.create(
                work, f"{safe_filename(source_stem)}_{safe_filename(mode)}")
        except (OSError, ValueError) as exc:
            messagebox.showerror("Job folder error", str(exc))
            return
        self._current_job_paths = job_paths

        self.pipeline_start = time.time()
        self._last_pipeline_source = source_path
        # If a Telegram batch download just finished, keep editing that same
        # message for the pipeline dashboard instead of sending a new message.
        handoff_mid = getattr(self, "_tg_dashboard_handoff_message_id", None)
        self._tg_dashboard_message_id = handoff_mid or None
        self._tg_dashboard_handoff_message_id = None
        self._tg_last_dashboard_edit = 0.0
        self._last_pipeline_reference = ref_mode or "Not selected"
        self._last_pipeline_resolution = "unknown"
        self.pipe_prog["value"]   = 0
        self.pipe_prog["maximum"] = 100
        self.pipe_lbl.config(
            text=f"Starting [{PipelineRunner.MODES.get(mode, mode)}] — "
                 f"{job_paths.root}")
        self.pipe_cancel_btn.config(state="normal")

        print(f"\n[Pipeline] mode={mode}  combine={combine}", flush=True)
        print(f"[Pipeline] source : {source_path}", flush=True)
        print(f"[Pipeline] workdir: {work}\n", flush=True)
        print(f"[Pipeline] job dir: {job_paths.root}\n", flush=True)

        mosaic_downscale_global = "1.0"
        mosaic_filter_downscales = {}
        mosaic_scratch_dir = ""
        try:
            if self.mosaic_tab_ref is not None:
                mosaic_downscale_global = self.mosaic_tab_ref.downscale_global.get()
                mosaic_filter_downscales = dict(getattr(self.mosaic_tab_ref, "filter_downscales", {}) or {})
                mosaic_scratch_dir = self.mosaic_tab_ref.scratch_dir.get().strip()
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)

        self.pipeline_runner = PipelineRunner(
            source_path=source_path,
            work_dir=work,
            mode=mode,
            combine=combine,
            status_q=self.pipeline_q,
            n_workers=self.n_workers.get(),
            skip_unknown=self.skip_unknown_groups.get(),
            reference_mode=ref_mode,
            filter_selector_cb=self._select_pipeline_mosaic_filters
                if mode in ("mosaic", "mosaic_align") else None,
            mosaic_downscale_global=mosaic_downscale_global,
            mosaic_filter_downscales=mosaic_filter_downscales,
            mosaic_turbo_fullres=self.turbo_fullres.get(),
            mosaic_scratch_dir=mosaic_scratch_dir,
            job_paths=job_paths,
        )
        self.pipeline_runner.start()

    def poll_pipeline_queue(self):
        try:
            while True:
                msg = self.pipeline_q.get_nowait()
                self._handle_pipeline_msg(msg)
        except queue.Empty:
            pass
        self.after(120, self.poll_pipeline_queue)

    def _aligner_pipeline_progress(self, step, total, text):
        """Forward AlignerTab progress into the same Telegram dashboard."""
        try:
            pct = int(max(0, min(100, (float(step) / max(float(total), 1.0)) * 100)))
        except EXPECTED_OPERATION_ERRORS:
            pct = 0
        post_to_ui(lambda t=text, p=pct: self._telegram_update_dashboard(
            "align", t or "Aligning", p, force=(p >= 100)))

    def _aligner_pipeline_done_core(self, out_folder, saved, log_path, total_t):
        """Send a truthful final Telegram message when alignment actually finishes."""
        self.pipe_cancel_btn.config(state="disabled")
        self.pipe_prog["value"] = 100
        self.pipe_lbl.config(text=f"Done ✓: Alignment complete  ·  {float(total_t):.0f}s")
        self._telegram_update_dashboard("done", "Alignment complete", 100, force=True)
        try:
            # AlignerTab returns tuples shaped (name, path, coverage), while the
            # old notifier tried to unpack them as (name, path). That made the
            # final notification/when-done actions fail after a successful align.
            normalized = []
            for item in (saved or []):
                try:
                    name = item[0]
                    path = item[1]
                    cov = item[2] if len(item) > 2 else None
                except EXPECTED_OPERATION_ERRORS:
                    continue
                normalized.append((str(name), str(path), cov))

            n = len(normalized)
            names = ", ".join(name for name, _path, _cov in normalized[:20])
            extra = f": {_html_escape(names)}" if names else ""
            text = (f"🔭 <b>TSDP — Alignment complete</b>\n"
                    f"✅ {n} aligned file(s){extra}\n"
                    f"⏱ {float(total_t):.0f}s")
            first_path = normalized[0][1] if normalized else None
            self._notify(text, fits_path=first_path)
            self._trigger_when_done({"fits_paths": [path for _name, path, _cov in normalized]})
        except EXPECTED_OPERATION_ERRORS as exc:
            print(f"[Telegram alignment complete notify failed] {exc}", flush=True)

__all__ = [name for name in globals() if not name.startswith('__')]
