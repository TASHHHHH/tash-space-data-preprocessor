from __future__ import annotations

from tsdp2.ui._common import *
from tsdp2.ui.mosaic_runners import MosaicRunner

class MosaicProcessingMixin:
    def run_mosaic(self, draft=False):
        if not self.groups:
            messagebox.showerror("No files", "Add FITS files first.")
            return
        if not HAVE_MOSAIC:
            messagebox.showerror(
                "Missing dependency",
                "reproject.mosaicking not available.\n"
                "Run: pip install --upgrade reproject")
            return
        if ((self.mosaic_runner and self.mosaic_runner.is_alive()) or
                (self.seam_runner and self.seam_runner.is_alive()) or
                (self.background_runner and self.background_runner.is_alive())):
            messagebox.showwarning("Already running",
                                   "Wait for the current mosaic to finish.")
            return
        base_out = self.out_dir.get().strip()
        if not base_out:
            messagebox.showerror("No output dir",
                                 "Set an output directory first.")
            return
        out = os.path.join(base_out, "draft_preview") if draft else base_out
        active = self._active_groups()
        try:
            if draft:
                effective_global_scale = _normalise_mosaic_downscale(
                    self.draft_scale.get(), default=0.08)
                effective_filter_scales = {}
                effective_save_maps = False
                effective_resume_cache = False
            else:
                effective_global_scale = _normalise_mosaic_downscale(
                    self.downscale_global.get(), default=1.0)
                effective_filter_scales = dict(self.filter_downscales)
                effective_save_maps = bool(self.save_coverage_maps.get())
                effective_resume_cache = bool(self.resume_cache.get())
                for _fc in active:
                    self._scale_for_filter(_fc)
        except EXPECTED_OPERATION_ERRORS as exc:
            messagebox.showerror("Invalid mosaic scale", str(exc), parent=self)
            return
        if not active:
            messagebox.showerror("No filters selected",
                                 "Select at least one filter group before running mosaic.")
            return

        self._stat.configure(
            text=("Checking fast draft…" if draft
                  else "Running automatic inspection / preflight…"))
        self.update_idletasks()
        try:
            if draft:
                preflight = _build_mosaic_preflight(
                    active, out, self.scratch_dir.get().strip(),
                    global_scale=effective_global_scale,
                    filter_scales=effective_filter_scales,
                    save_coverage_maps=False, sky_region=self.sky_region,
                    combine=self.combine_fn.get())
            else:
                preflight = self._compute_preflight(active)
        except EXPECTED_OPERATION_ERRORS as exc:
            messagebox.showerror("Preflight failed", str(exc), parent=self)
            self._stat.configure(text="Preflight failed.")
            return
        if not preflight.get("space_ok"):
            messagebox.showerror(
                "Not enough estimated free space",
                _mosaic_preflight_text(preflight), parent=self)
            self._stat.configure(text="Mosaic stopped by disk-space preflight.")
            return
        filter_lines = "\n".join(
            f"{item['filter']}: {item['width']:,} × {item['height']:,} px, "
            f"{item['tiles']} tile(s)"
            for item in preflight.get("filters") or [])
        partial_count = sum(
            1 for item in preflight.get("filters") or []
            for _path, fraction in item.get("tile_stats") or [] if fraction < 95.0)
        area_description = "full footprint"
        if self.sky_region:
            area_description = (
                "selected sky rectangle, rotation "
                f"{float(self.sky_region.get('rotation_deg', 0.0) or 0.0):+.1f}°")
        confirm_text = (
            f"{filter_lines}\n\n"
            f"Estimated peak working memory: {_format_gib(preflight.get('peak_memory', 0))}\n"
            f"Estimated final products: {_format_gib(preflight.get('total_output', 0))}\n"
            f"Peak resumable scratch: {_format_gib(preflight.get('total_scratch', 0))}\n"
            f"Partial/irregular data rectangles detected: {partial_count}\n"
            f"Area: {area_description}\n\n"
            "Start the mosaic?")
        if not draft:
            if not messagebox.askyesno("Preflight passed", confirm_text, parent=self):
                self._stat.configure(text="Mosaic cancelled after preflight.")
                return

        job_label = "_".join(safe_filename(code) for code in sorted(active)[:3])
        job_label = f"{job_label or 'TSDP'}_{'Draft' if draft else 'Mosaic'}"
        try:
            job_paths = JobPaths.create(base_out, job_label)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Job folder error", str(exc), parent=self)
            return
        self._active_job_paths = job_paths
        out = os.fspath(job_paths.root)

        self.mosaic_start = time.time()
        self._prog["value"] = 0
        self._prog["maximum"] = 100
        self._stat.config(text="Starting fast draft…" if draft else "Starting…")
        self._run_btn.config(state="disabled")
        self._draft_btn.config(state="disabled")
        self._repair_btn.config(state="disabled")
        self._clean_btn.config(state="disabled")
        self._cancel_btn.config(state="normal")
        self._run_kind = "draft" if draft else "final"

        print(f"\n[Mosaic Tab] Starting  combine={self.combine_fn.get()}",
              flush=True)
        print(f"[Mosaic Tab] Output: {out}", flush=True)
        print(f"[Mosaic Tab] Mode: {self._run_kind}", flush=True)
        print(f"[Mosaic Tab] Global output scale: {effective_global_scale:g}", flush=True)
        print(f"[Mosaic Tab] Full-resolution processing: "
              f"{bool(self.turbo_fullres.get()) and not draft}", flush=True)
        for code, paths in sorted(active.items()):
            used_scale = _mosaic_filter_scale(
                code, effective_global_scale, effective_filter_scales)
            print(f"  {code}: {len(paths)} tile(s), scale={used_scale:g}x", flush=True)

        tg_direct = getattr(self, "telegram_ref", None)
        if tg_direct is not None:
            try:
                n_files = sum(len(v) for v in active.values())
                filters = ", ".join(sorted(active.keys()))
                if hasattr(tg_direct, "_telegram_update_dashboard"):
                    tg_direct.pipeline_start = self.mosaic_start
                    tg_direct._tg_dashboard_message_id = None
                    tg_direct._last_pipeline_reference = "direct mosaic"
                    tg_direct._last_pipeline_resolution = "unknown"
                    tg_direct._telegram_update_dashboard(
                        "mosaic",
                        f"Starting direct mosaic - {len(active)} group(s), {n_files} file(s)",
                        0, force=True)
                elif hasattr(tg_direct, "_notify"):
                    tg_direct._notify(
                        f"🧩 <b>TSDP - Mosaic started</b>\n"
                        f"Groups: {len(active)} ({_html_escape(filters)})\n"
                        f"Input file(s): {n_files}\n"
                        f"Combine: {_html_escape(self.combine_fn.get())}\n"
                        f"Output: <code>{_html_escape(out)}</code>")
            except EXPECTED_OPERATION_ERRORS as exc:
                print(f"[Direct mosaic Telegram start failed] {exc}", flush=True)

        requested_workers = self.n_workers.get()
        auto_workers = getattr(self, "auto_workers", None)
        if auto_workers is not None and auto_workers.get():
            requested_workers = max(1, (os.cpu_count() or 2) - 2)
        print(f"[Mosaic Tab] Worker limit: {requested_workers} (automatically bounded by free RAM)", flush=True)
        self.mosaic_runner = MosaicRunner(
            groups=active, out_dir=job_paths,
            combine=self.combine_fn.get(), status_q=self.mosaic_q,
            n_workers=requested_workers,
            downscale_global=effective_global_scale,
            filter_downscales=effective_filter_scales,
            scratch_dir=self.scratch_dir.get().strip(),
            sky_region=self.sky_region,
            save_coverage_maps=effective_save_maps,
            resume_cache=effective_resume_cache,
            draft_mode=bool(draft),
            draft_background_cleanup=bool(self.draft_background_cleanup.get()),
            highres_background_cleanup=(
                bool(self.highres_background_cleanup.get()) and not draft),
            turbo_fullres=(bool(self.turbo_fullres.get()) and not draft),
            retain_intermediates=bool(self.retain_intermediates.get()),
            cosmetic_draft_holes=(bool(self.cosmetic_draft_holes.get())
                                  if hasattr(self,'cosmetic_draft_holes') else True))
        self.mosaic_runner.start()

    def cancel_mosaic(self):
        if self.mosaic_runner and self.mosaic_runner.is_alive():
            self.mosaic_runner.stop()
            self._stat.config(text="Cancelling mosaic...")
        if self.seam_runner and self.seam_runner.is_alive():
            self.seam_runner.stop()
            self._stat.config(text="Cancelling seam repair...")
        if self.background_runner and self.background_runner.is_alive():
            self.background_runner.stop()
            self._stat.config(text="Cancelling background cleanup...")
        self._cancel_btn.config(state="disabled")

__all__ = [name for name in globals() if not name.startswith('__')]
