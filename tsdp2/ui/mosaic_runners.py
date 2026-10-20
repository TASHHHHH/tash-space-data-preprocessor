from __future__ import annotations

from tsdp2._deps import *
from tsdp2.logging_config import LOGGER
from tsdp2.astronomy.mosaicking import mosaic_filter_group
from tsdp2.astronomy.mosaic_quality import automatically_repair_mosaic, cosmetically_fill_draft
from tsdp2.astronomy.regions import _mosaic_filter_scale
from tsdp2.astronomy.seam_repair import fast_draft_filter_group, repair_existing_mosaic_seams, write_background_clean_fits_copy
from tsdp2.config import _safe_int
from tsdp2.models import RunState
from tsdp2.output_paths import JobManifest, JobPaths, cleanup_job_intermediates, safe_filename

class MosaicRunner(threading.Thread):
    """Runs mosaic for explicitly grouped FITS files from MosaicTab."""

    def __init__(self, groups, out_dir, combine, status_q, n_workers=1,
                 downscale_global=1.0, filter_downscales=None, scratch_dir=None,
                 sky_region=None, save_coverage_maps=True, resume_cache=True,
                 draft_mode=False, draft_background_cleanup=True,
                 highres_background_cleanup=True, turbo_fullres=True,
                 retain_intermediates=False, cosmetic_draft_holes=True):
        super().__init__(daemon=True)
        self.groups    = groups    # {filter_code: [path, ...]}
        self.job_paths = out_dir if isinstance(out_dir, JobPaths) else None
        if self.job_paths is not None:
            category = (self.job_paths.intermediate / "draft_preview"
                        if draft_mode else self.job_paths.mosaics)
            self.out_dir = os.fspath(category)
        else:
            self.out_dir = os.fspath(out_dir)
        self.combine   = combine
        self.status_q  = status_q
        self.n_workers = _safe_int(n_workers, default=1, lo=1)
        self.downscale_global = downscale_global
        self.filter_downscales = dict(filter_downscales or {})
        if scratch_dir:
            self.scratch_dir = os.fspath(scratch_dir).strip()
        elif self.job_paths is not None:
            self.scratch_dir = os.fspath(
                self.job_paths.intermediate / "memmaps")
        else:
            self.scratch_dir = ""
        self.sky_region = dict(sky_region) if sky_region else None
        self.save_coverage_maps = bool(save_coverage_maps)
        self.resume_cache = bool(resume_cache)
        self.draft_mode = bool(draft_mode)
        self.draft_background_cleanup = bool(draft_background_cleanup)
        self.highres_background_cleanup = bool(highres_background_cleanup)
        self.turbo_fullres = bool(turbo_fullres)
        self.retain_intermediates = bool(retain_intermediates)
        self.cosmetic_draft_holes = bool(cosmetic_draft_holes)
        self._stop_event = threading.Event()
        self._started_at = time.time()
        self.manifest = None
        if self.job_paths is not None:
            source_files = [
                os.fspath(path)
                for paths in groups.values() for path in paths
            ]
            self.manifest = JobManifest(
                paths=self.job_paths,
                job_name=self.job_paths.root.name,
                state=RunState.RUNNING,
                source_files=source_files,
                filters=[str(code) for code in sorted(groups)],
                settings={
                    "mode": "draft_mosaic" if draft_mode else "mosaic",
                    "combine": combine,
                    "workers": self.n_workers,
                    "downscale_global": downscale_global,
                    "filter_downscales": self.filter_downscales,
                    "save_coverage_maps": self.save_coverage_maps,
                    "resume_cache": self.resume_cache,
                    "turbo_fullres": self.turbo_fullres,
                    "retain_intermediates": self.retain_intermediates,
                    "automatic_source_repair": True,
                    "cosmetic_draft_holes": self.cosmetic_draft_holes,
                },
            )
            self.manifest.save()

    def stop(self):     self._stop_event.set()
    def stopped(self):  return self._stop_event.is_set()

    def send(self, step, **kw):
        if self.job_paths is not None:
            kw.setdefault("job_root", os.fspath(self.job_paths.root))
        if self.manifest is not None and step in {"done", "cancelled", "error"}:
            try:
                self.manifest.processing_seconds = time.time() - self._started_at
                if step == "done":
                    self.manifest.state = RunState.DONE
                    category = "intermediate" if self.draft_mode else "final"
                    for product in (kw.get("out_paths") or {}).values():
                        self.manifest.record_product(category, product)
                    if not self.draft_mode:
                        for product in self.job_paths.final.rglob("*"):
                            if product.is_file():
                                self.manifest.record_product("final", product)
                    for diagnostic in self.job_paths.diagnostics.rglob("*"):
                        if diagnostic.is_file():
                            self.manifest.record_product("diagnostics", diagnostic)
                    removed = cleanup_job_intermediates(
                        self.job_paths, retain=self.retain_intermediates)
                    if removed:
                        self.manifest.products["intermediate"] = [
                            item for item in self.manifest.products["intermediate"]
                            if not any(item == prefix or item.startswith(prefix + "/")
                                       for prefix in removed)
                        ]
                elif step == "cancelled":
                    self.manifest.state = RunState.CANCELLED
                    self.manifest.cancelled = True
                else:
                    self.manifest.state = RunState.ERROR
                    self.manifest.failed = True
                    self.manifest.warnings.append(
                        str(kw.get("message", "Mosaic failed"))[:1000])
                self.manifest.save()
            except (OSError, ValueError, KeyError, TypeError):
                LOGGER.exception("Mosaic manifest update failed")
        kw["step"] = step
        self.status_q.put(kw)

    def run(self):
        try:
            self._run()
        except EXPECTED_OPERATION_ERRORS as exc:
            if self.stopped():
                self.send('cancelled',message='Cancelled; existing output preserved.')
            else:
                self.send("error", message=str(exc), tb=traceback.format_exc())
                LOGGER.exception("Mosaic worker failed")

    def _run(self):
        n = max(len(self.groups), 1)
        out_paths = {}
        quality_reports = {}
        for gi, (fcode, paths) in enumerate(sorted(self.groups.items())):
            if self.stopped():
                self.send("cancelled", message="Cancelled.")
                return

            def cb(step, total, msg, _gi=gi):
                p = int((_gi + step / max(total, 1)) / n * 100)
                self.send("progress", message=msg, pct=p)

            self.send("progress",
                      message=f"Mosaicking {fcode}  ({len(paths)} tile(s))…",
                      pct=int(gi / n * 100))
            scale = _mosaic_filter_scale(fcode, self.downscale_global, self.filter_downscales)
            if self.draft_mode:
                out = fast_draft_filter_group(
                    paths, self.out_dir, fcode,
                    combine=self.combine, progress_cb=cb,
                    downscale=scale, scratch_dir=self.scratch_dir or None,
                    sky_region=self.sky_region, cancel_cb=self.stopped,
                    cosmetic_background=self.draft_background_cleanup)
            else:
                out = mosaic_filter_group(
                    paths, self.out_dir, fcode,
                    combine=self.combine, progress_cb=cb,
                    n_workers=self.n_workers, downscale=scale,
                    scratch_dir=self.scratch_dir or None,
                    sky_region=self.sky_region,
                    save_coverage_maps=self.save_coverage_maps,
                    resume_cache=self.resume_cache,
                    turbo_fullres=self.turbo_fullres,
                    job_paths=self.job_paths)
            quality = automatically_repair_mosaic(
                out, paths, fcode, progress_cb=cb, cancel_cb=self.stopped,
                report_dir=(self.job_paths.diagnostics / 'quality'
                            if self.job_paths is not None else None),
                quality_paths=((self.job_paths.coverage_map(f'coverage_{safe_filename(fcode)}.fits'),
                                self.job_paths.weight_map(f'weight_{safe_filename(fcode)}.fits'))
                               if self.job_paths is not None else
                               (Path(self.out_dir)/f'coverage_{safe_filename(fcode)}.fits',
                                Path(self.out_dir)/f'weight_{safe_filename(fcode)}.fits')))
            if self.manifest is not None and quality['remaining_pixels']:
                self.manifest.warnings.append(
                    f"{fcode}: {quality['remaining_pixels']} internal no-data pixels "
                    "could not be recovered from valid originals.")
            quality_reports[fcode] = quality
            if self.draft_mode and self.cosmetic_draft_holes:
                cosmetic=cosmetically_fill_draft(
                    out,cancel_cb=self.stopped,
                    mask_path=(self.job_paths.mask(f'draft_interpolation_{safe_filename(fcode)}.fits')
                               if self.job_paths is not None else None))
                quality.update(cosmetic)
                Path(quality['report_path']).write_text(
                    json.dumps({k:v for k,v in quality.items() if k!='report_path'},indent=2)+'\n',
                    encoding='utf-8')
            if not self.draft_mode:
                if self.highres_background_cleanup:
                    base = Path(out)
                    clean_out = str(base.with_name(base.stem + "_background_clean.fits"))
                    result = write_background_clean_fits_copy(
                        out, clean_out, strength=0.90, progress_cb=cb,
                        cancel_cb=self.stopped)
                    out = result["output_path"]
            out_paths[fcode] = out

        self.send("done",
                  message=f"Complete — {len(out_paths)} filter(s); "
                          f"{sum(r.get('repaired_pixels',0) for r in quality_reports.values())} hole pixels recovered, "
                          f"{sum(r.get('cosmetic_pixels',0) for r in quality_reports.values())} draft pixels interpolated; "
                          f"{sum(r['remaining_pixels'] for r in quality_reports.values())} lack measured data.",
                  out_paths=out_paths,quality_reports=quality_reports)


class SeamRepairRunner(threading.Thread):
    """Background worker for repairing one already-built mosaic FITS."""

    def __init__(self, mosaic_path, source_paths, output_path, filter_code,
                 strength, preview_scale, scratch_dir, status_q):
        super().__init__(daemon=True)
        self.mosaic_path = mosaic_path
        self.source_paths = list(source_paths)
        self.output_path = output_path
        self.filter_code = filter_code
        self.strength = strength
        self.preview_scale = preview_scale
        self.scratch_dir = scratch_dir
        self.status_q = status_q
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def stopped(self):
        return self._stop_event.is_set()

    def send(self, step, **payload):
        payload["step"] = step
        self.status_q.put(payload)

    def run(self):
        try:
            result = repair_existing_mosaic_seams(
                self.mosaic_path, self.source_paths, self.output_path,
                filter_code=self.filter_code, strength=self.strength,
                preview_scale=self.preview_scale,
                scratch_dir=self.scratch_dir,
                progress_cb=lambda done, total, msg: self.send(
                    "repair_progress", message=msg,
                    pct=int(done / max(total, 1) * 100)),
                cancel_cb=self.stopped)
            if self.stopped():
                self.send("repair_cancelled", message="Seam repair cancelled.")
            else:
                self.send("repair_done", message="Existing mosaic seam repair complete.",
                          result=result)
        except EXPECTED_OPERATION_ERRORS as exc:
            if self.stopped() or "cancel" in str(exc).lower():
                self.send("repair_cancelled", message=str(exc))
            else:
                self.send("repair_error", message=str(exc),
                          tb=traceback.format_exc())


class BackgroundCleanRunner(threading.Thread):
    """Background worker for cleaning one existing high-resolution mosaic."""

    def __init__(self, input_path, output_path, strength, status_q):
        super().__init__(daemon=True)
        self.input_path = input_path
        self.output_path = output_path
        self.strength = strength
        self.status_q = status_q
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def stopped(self):
        return self._stop_event.is_set()

    def send(self, step, **payload):
        payload["step"] = step
        self.status_q.put(payload)

    def run(self):
        try:
            result = write_background_clean_fits_copy(
                self.input_path, self.output_path, strength=self.strength,
                progress_cb=lambda done, total, msg: self.send(
                    "clean_progress", message=msg,
                    pct=int(done / max(total, 1) * 100)),
                cancel_cb=self.stopped)
            if self.stopped():
                self.send("clean_cancelled", message="Background cleanup cancelled.")
            else:
                self.send("clean_done", message="High-resolution background cleanup complete.",
                          result=result)
        except EXPECTED_OPERATION_ERRORS as exc:
            if self.stopped() or "cancel" in str(exc).lower():
                self.send("clean_cancelled", message=str(exc))
            else:
                self.send("clean_error", message=str(exc), tb=traceback.format_exc())

__all__ = [name for name in globals() if not name.startswith('__')]
