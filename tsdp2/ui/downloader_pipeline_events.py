from __future__ import annotations

from tsdp2.ui._common import *

class DownloaderPipelineEventsMixin:
    def _handle_pipeline_msg_core(self, msg):
        step = msg.get("step", "")
        text = msg.get("message", "")
        pct  = msg.get("pct")

        elapsed = ""
        if self.pipeline_start:
            elapsed = f"  ·  {time.time() - self.pipeline_start:.0f}s"

        labels = {"space": "Space", "unzip": "Unzip",
                  "filter_select": "Filter selection",
                  "mosaic": "Mosaic", "align": "Align",
                  "done": "Done ✓", "cancelled": "Cancelled",
                  "error": "Error ✗"}
        prefix = labels.get(step, step.title())
        self.pipe_lbl.config(text=f"{prefix}: {text}{elapsed}")
        if pct is not None:
            self.pipe_prog["value"] = pct
        else:
            try:
                pct = int(float(self.pipe_prog["value"]))
            except EXPECTED_OPERATION_ERRORS:
                pct = 0

        print(f"[Pipeline:{step}] {text}", flush=True)
        self._telegram_update_dashboard(step, text, pct)

        if step == "groups_ready":
            groups  = msg.get("groups", {})
            out_dir = msg.get("out_dir", "")
            if groups and self.mosaic_tab_ref is not None:
                self.mosaic_tab_ref.load_from_pipeline(groups, out_dir)
                if self.notebook_ref:
                    self.notebook_ref.select(self.mosaic_tab_ref)   # switch to Mosaic tab
            return

        if step == "done":
            self.pipe_cancel_btn.config(state="disabled")
            mosaic_paths = msg.get("mosaic_paths")
            fits_paths_done = msg.get("fits_paths", [])
            result_kind = msg.get("result_kind", "")

            # Do not mark align jobs as done here. This event only means the
            # downloader/pipeline produced inputs for the Aligner tab. The live
            # dashboard must continue through the actual alignment progress.
            if result_kind == "align_ready":
                filters = ", ".join(sorted(mosaic_paths or {}))
                can_auto_align = bool(mosaic_paths and self.auto_load_aligner.get() and self.aligner_ref is not None)
                if can_auto_align:
                    self.pipe_prog["value"] = 0
                    self._telegram_update_dashboard("align", "Starting alignment", 0, force=True)
                    tg_text = (f"🔭 <b>TSDP — Alignment starting</b>\n"
                               f"Reference mode: {_html_escape(msg.get('reference_mode') or self.reference_mode.get())}\n"
                               f"Input file(s): {len(mosaic_paths or {})}"
                               f"{(': ' + _html_escape(filters)) if filters else ''}")
                else:
                    self.pipe_prog["value"] = 100
                    reason = "Auto-load into Aligner tab is OFF" if not self.auto_load_aligner.get() else "Aligner tab is unavailable"
                    ready_text = f"Ready to align manually ({reason})"
                    self.pipe_lbl.config(text=ready_text)
                    self._telegram_update_dashboard("done", ready_text, 100, force=True)
                    tg_text = (f"⚠️ <b>TSDP — Alignment not started</b>\n"
                               f"{_html_escape(reason)}.\n"
                               f"Input file(s): {len(mosaic_paths or {})}"
                               f"{(': ' + _html_escape(filters)) if filters else ''}")
                first_path = None
            else:
                self.pipe_prog["value"] = 100
                self._telegram_update_dashboard("done", text or "Complete", 100, force=True)
                if mosaic_paths:
                    filters  = ", ".join(sorted(mosaic_paths))
                    tg_text  = (f"🔭 <b>TSDP — Mosaic complete</b>\n"
                                f"✅ {len(mosaic_paths)} mosaic(s): {_html_escape(filters)}\n"
                                f"⏱ {time.time() - (self.pipeline_start or time.time()):.0f}s")
                    first_path = next(iter(mosaic_paths.values()), None)
                    if first_path:
                        self._last_pipeline_resolution = fits_resolution_text(first_path)
                elif fits_paths_done:
                    tg_text  = (f"📦 <b>TSDP — Unzip complete</b>\n"
                                f"{len(fits_paths_done)} FITS files extracted")
                    first_path = None
                else:
                    tg_text   = "✅ <b>TSDP — Pipeline complete</b>"
                    first_path = None
            self._notify(tg_text, fits_path=first_path)

            if (result_kind == "align_ready" and mosaic_paths and self.auto_load_aligner.get()
                    and self.aligner_ref is not None):
                if self.notebook_ref:
                    self.notebook_ref.select(self.aligner_ref)
                ref_mode = msg.get("reference_mode") or self.reference_mode.get() or "Ordinary best"
                job_root = msg.get("job_root", "")
                pipeline_paths = JobPaths(Path(job_root)) if job_root else None
                pipeline_manifest = getattr(
                    self.pipeline_runner, "manifest", None)
                self.after(80, lambda mp=mosaic_paths, rm=ref_mode,
                           jp=pipeline_paths, jm=pipeline_manifest:
                           self.aligner_ref.auto_run_from_pipeline(
                               mp, rm,
                               progress_cb=self._aligner_pipeline_progress,
                               done_cb=self._aligner_pipeline_done,
                               job_paths=jp, job_manifest=jm))
            if result_kind != "align_ready":
                self._trigger_when_done(msg)

        elif step == "cancelled":
            self.pipe_cancel_btn.config(state="disabled")
            self.pipe_prog["value"] = 0
            self._telegram_update_dashboard("cancelled", text or "Cancelled", 0, force=True)

        elif step == "error":
            self.pipe_cancel_btn.config(state="disabled")
            self._notify(f"❌ <b>TSDP — Pipeline error</b>\n{text[:300]}")
            if msg.get("space_error"):
                messagebox.showerror("Disk Space Error", text)
            else:
                messagebox.showerror(
                    "Pipeline Error",
                    f"Step: {step}\n\n{text}\n\n"
                    "See Live Logs window for full traceback.")
            tb = msg.get("tb", "")
            if tb:
                print(f"[Pipeline traceback]\n{tb}", flush=True)

    def _trigger_when_done(self, msg):
        if self.when_done_dialog.get():
            lines = ["Pipeline complete!", ""]
            mp = msg.get("mosaic_paths", {})
            fp = msg.get("fits_paths", [])
            if mp:
                lines.append(f"Mosaic outputs ({len(mp)}):")
                for code, path in sorted(mp.items()):
                    lines.append(f"  {code}: {Path(path).name}")
            elif fp:
                lines.append(f"FITS files found: {len(fp)}")
            messagebox.showinfo("Done", "\n".join(lines))

        if self.when_done_beep.get():
            try:
                if os.name == "nt":
                    import winsound
                    winsound.Beep(880, 250)
                    winsound.Beep(1100, 250)
                else:
                    print("\a", end="", flush=True)
            except EXPECTED_OPERATION_ERRORS:
                LOGGER.debug("Best-effort operation failed", exc_info=True)

        if self.when_done_folder.get():
            d = self.work_dir.get().strip()
            if d and os.path.isdir(d):
                try:
                    if os.name == "nt":
                        os.startfile(d)
                    elif sys.platform == "darwin":
                        subprocess.run(["open", d])
                    else:
                        subprocess.run(["xdg-open", d])
                except EXPECTED_OPERATION_ERRORS:
                    LOGGER.debug("Best-effort operation failed", exc_info=True)

        if self.when_done_shutdown.get():
            self._do_shutdown_countdown()

    def _on_shutdown_check(self):
        if self.when_done_shutdown.get():
            if not messagebox.askyesno(
                    "Enable auto-shutdown?",
                    "The computer will shut down when the pipeline finishes.\n\n"
                    "All unsaved work will be LOST.\n\nAre you sure?",
                    icon="warning"):
                self.when_done_shutdown.set(False)
                return
            if not messagebox.askyesno(
                    "Final confirmation",
                    "LAST CHANCE — shut down the PC after pipeline?",
                    icon="warning"):
                self.when_done_shutdown.set(False)

    def _do_shutdown_countdown(self):
        popup = tk.Toplevel(self)
        popup.title("Shutting down…")
        popup.geometry("380x160")
        popup.grab_set()
        popup.resizable(False, False)
        secs = [45]
        cancelled = [False]
        lbl = ttk.Label(popup, text=f"Shutting down in {secs[0]}s",
                        font=("Segoe UI", 14, "bold"), anchor="center")
        lbl.pack(expand=True, fill="both", padx=20, pady=16)
        ttk.Label(popup, text="All unsaved work will be lost.",
                  foreground="gray").pack()

        def tick():
            if cancelled[0]:
                return
            secs[0] -= 1
            if secs[0] <= 0:
                popup.destroy()
                print("[Shutdown] Executing OS shutdown…", flush=True)
                if os.name == "nt":
                    os.system('shutdown /s /t 5 /c "TSDP pipeline complete"')
                else:
                    os.system("shutdown -h now")
            else:
                lbl.config(text=f"Shutting down in {secs[0]}s")
                popup.after(1000, tick)

        def cancel():
            cancelled[0] = True
            self.when_done_shutdown.set(False)
            popup.destroy()

        ttk.Button(popup, text="Cancel Shutdown", command=cancel).pack(pady=8)
        popup.after(1000, tick)

__all__ = [name for name in globals() if not name.startswith('__')]
