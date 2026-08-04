from __future__ import annotations

from tsdp2._deps import *
from tsdp2.logging_config import LOGGER
from tsdp2.astronomy.mosaic_inputs import _prefer_drc_over_drz, group_fits_by_filter
from tsdp2.astronomy.mosaicking import mosaic_filter_group
from tsdp2.astronomy.reference import choose_best_reference
from tsdp2.astronomy.regions import _mosaic_filter_scale
from tsdp2.config import _safe_int
from tsdp2.models import RunState
from tsdp2.output_paths import JobManifest, JobPaths, cleanup_job_intermediates

class PipelineRunner(threading.Thread):
    """
    Background thread: disk-check → unzip → [mosaic] → [align handoff].

    Status dicts sent to status_q:
        {"step": "space"|"unzip"|"mosaic"|"align"|"done"|"cancelled"|"error",
         "message": str, "pct": int, ...extra keys per step}
    """

    MODES = {
        "download":    "Download  →  Unzip only",
        "align":       "Download  →  Unzip  →  WCS Align",
        "mosaic":      "Download  →  Unzip  →  Mosaic only",
        "mosaic_align":"Download  →  Unzip  →  Mosaic  →  WCS Align",
    }

    def __init__(self, source_path, work_dir, mode, combine, status_q,
                 n_workers=1, skip_unknown=True, reference_mode="",
                 filter_selector_cb=None, mosaic_downscale_global=1.0,
                 mosaic_filter_downscales=None, mosaic_turbo_fullres=True,
                 mosaic_scratch_dir=None,
                 job_paths: Optional[JobPaths] = None):
        """
        source_path  : zip file path (or FITS folder)
        work_dir     : extraction / mosaic target
        mode         : "download" | "align" | "mosaic" | "mosaic_align"
        combine      : "median" | "mean"
        status_q     : queue.Queue
        n_workers    : parallel tile reprojections per filter group
        skip_unknown : discard the UNKNOWN filter group (weight maps, catalogs…)
        """
        super().__init__(daemon=True)
        self.source_path  = source_path
        self.work_dir     = work_dir
        self.mode         = mode
        self.combine      = combine
        self.status_q     = status_q
        self.n_workers    = _safe_int(n_workers, default=1, lo=1)
        self.skip_unknown = bool(skip_unknown)
        self.reference_mode = (reference_mode or "").strip()
        self.filter_selector_cb = filter_selector_cb
        self.mosaic_downscale_global = mosaic_downscale_global
        self.mosaic_filter_downscales = dict(mosaic_filter_downscales or {})
        self.mosaic_turbo_fullres = bool(mosaic_turbo_fullres)
        self.mosaic_scratch_dir = (
            os.fspath(mosaic_scratch_dir).strip() if mosaic_scratch_dir else "")
        self._stop_event  = threading.Event()
        self.job_paths = job_paths
        self.manifest = None
        self._started_at = time.time()
        if self.job_paths is not None:
            self.manifest = JobManifest(
                paths=self.job_paths,
                job_name=self.job_paths.root.name,
                state=RunState.RUNNING,
                source_files=[os.fspath(source_path)],
                settings={
                    "mode": mode,
                    "combine": combine,
                    "workers": self.n_workers,
                    "skip_unknown": self.skip_unknown,
                    "reference_mode": self.reference_mode,
                    "mosaic_downscale_global": mosaic_downscale_global,
                    "mosaic_filter_downscales": self.mosaic_filter_downscales,
                    "mosaic_turbo_fullres": self.mosaic_turbo_fullres,
                },
            )
            self.manifest.save()

    def stop(self):   self._stop_event.set()
    def stopped(self): return self._stop_event.is_set()

    def send(self, step, **kw):
        if self.job_paths is not None:
            kw.setdefault("job_root", os.fspath(self.job_paths.root))
        if self.manifest is not None:
            try:
                groups = kw.get("groups") or {}
                if groups:
                    self.manifest.filters = [
                        str(code) for code in sorted(groups)]
                mosaic_paths = kw.get("mosaic_paths") or {}
                for product in mosaic_paths.values():
                    try:
                        self.manifest.record_product("final", product)
                    except ValueError:
                        LOGGER.warning("Pipeline product is outside its job: %s",
                                       product)
                if step == "cancelled":
                    self.manifest.state = RunState.CANCELLED
                    self.manifest.cancelled = True
                elif step == "error":
                    self.manifest.state = RunState.ERROR
                    self.manifest.failed = True
                    if kw.get("message"):
                        self.manifest.warnings.append(
                            str(kw["message"])[:1000])
                elif step == "done" and kw.get("result_kind") != "align_ready":
                    self.manifest.state = RunState.DONE
                    if self.mode != "download":
                        removed = cleanup_job_intermediates(self.job_paths)
                        if removed:
                            self.manifest.products["intermediate"] = [
                                item for item in self.manifest.products["intermediate"]
                                if not any(item == prefix or item.startswith(prefix + "/")
                                           for prefix in removed)
                            ]
                if step in {"groups_ready", "done", "cancelled", "error"}:
                    self.manifest.processing_seconds = (
                        time.time() - self._started_at)
                    self.manifest.save()
            except (OSError, TypeError, ValueError, KeyError):
                LOGGER.exception("Pipeline manifest update failed")
        kw["step"] = step
        self.status_q.put(kw)

    def run(self):
        try:
            self._run()
        except EXPECTED_OPERATION_ERRORS as exc:
            self.send("error", message=str(exc), tb=traceback.format_exc())
            LOGGER.exception("Pipeline worker failed")

    # ─────────────────────────────────────────────────────────────────────────

    def _run(self):
        src    = self.source_path
        is_zip = os.path.isfile(src) and zipfile.is_zipfile(src)
        extraction_dir = (
            os.fspath(self.job_paths.intermediate / "extracted")
            if self.job_paths is not None else self.work_dir)

        # ── 1. Disk space check ───────────────────────────────────────────
        if is_zip:
            self.send("space", message="Checking disk space…", pct=0)
            os.makedirs(extraction_dir, exist_ok=True)
            with zipfile.ZipFile(src) as zf:
                total_unc = sum(i.file_size for i in zf.infolist())
            needed = int(total_unc * 1.1)
            free   = shutil.disk_usage(extraction_dir).free
            if free < needed:
                self.send("error", space_error=True, needed=needed, free=free,
                          message=(f"Not enough disk space.\n"
                                   f"Need : {needed / 1e9:.2f} GB\n"
                                   f"Free : {free  / 1e9:.2f} GB"))
                return
            self.send("space",
                      message=f"Space OK — need {needed/1e9:.2f} GB, "
                              f"{free/1e9:.2f} GB free", pct=100)

        # ── 2. Unzip / locate FITS inputs ─────────────────────────────────
        exts = (".fits", ".fit", ".fts")
        scan_roots = []
        fits_paths = []

        if is_zip:
            self.send("unzip", message="Unzipping…", pct=0)
            os.makedirs(extraction_dir, exist_ok=True)
            extracted_fits = []
            with zipfile.ZipFile(src) as zf:
                members = zf.infolist()
                n = max(len(members), 1)
                for i, m in enumerate(members):
                    if self.stopped():
                        self.send("cancelled", message="Cancelled during unzip.")
                        return
                    # Avoid zip-slip paths writing outside the work directory.
                    target = os.path.abspath(os.path.join(extraction_dir, m.filename))
                    work_abs = os.path.abspath(extraction_dir)
                    if not (target == work_abs or target.startswith(work_abs + os.sep)):
                        self.send("error", message=f"Unsafe ZIP member skipped: {m.filename}")
                        return
                    zf.extract(m, extraction_dir)
                    if target.lower().endswith(exts):
                        extracted_fits.append(target)
                    self.send("unzip",
                              message=f"Extracting {i + 1}/{n}: {m.filename}",
                              pct=int((i + 1) / n * 100))
            # Only use FITS from this ZIP, never stale files already in work_dir.
            fits_paths = [p for p in extracted_fits if os.path.isfile(p)]

        elif os.path.isdir(src):
            self.work_dir = src
            scan_roots = [src]

        elif os.path.isfile(src) and src.lower().endswith(exts):
            # Important: GUI auto-run and manual Run Pipeline can pass a single
            # downloaded FITS file. Older code rejected this even though the file
            # dialog allowed FITS input.
            self.work_dir = os.path.dirname(os.path.abspath(src)) or self.work_dir
            fits_paths = [os.path.abspath(src)]

        else:
            self.send("error", message=f"Source not found or unreadable:\n{src}")
            return

        if scan_roots:
            for scan_root in scan_roots:
                for root, _dirs, files in os.walk(scan_root):
                    for fn in sorted(files):
                        if fn.lower().endswith(exts):
                            fits_paths.append(os.path.join(root, fn))

        if not fits_paths:
            self.send("error", message="No FITS files found after extraction/input scan.")
            return

        fits_paths = _prefer_drc_over_drz(fits_paths)
        if self.manifest is not None:
            for fits_path in fits_paths:
                if fits_path not in self.manifest.source_files:
                    self.manifest.source_files.append(fits_path)
                try:
                    if Path(fits_path).resolve().is_relative_to(
                            self.job_paths.intermediate.resolve()):
                        self.manifest.record_product("intermediate", fits_path)
                except (OSError, ValueError):
                    pass
            try:
                self.manifest.save()
            except OSError:
                LOGGER.exception("Could not record extracted pipeline inputs")
        self.send("unzip",
                  message=f"Unzip done — {len(fits_paths)} FITS files "
                          f"(after DRZ/DRC dedup)",
                  pct=100)
        print(f"[Pipeline] {len(fits_paths)} FITS files in scan roots",
              flush=True)

        if self.mode == "download":
            self.send("done", message="Unzip complete.", fits_paths=fits_paths)
            return
        if self.stopped():
            return

        # ── 3. Mosaic ────────────────────────────────────────────────────
        if self.mode in ("mosaic", "mosaic_align"):
            if not HAVE_MOSAIC:
                self.send("error",
                          message="reproject.mosaicking not available.\n"
                                  "Run: pip install --upgrade reproject\n\n"
                                  + MOSAIC_IMPORT_ERR)
                return

            groups = group_fits_by_filter(fits_paths)
            if self.skip_unknown and "UNKNOWN" in groups:
                n_unk = len(groups.pop("UNKNOWN"))
                print(f"[Mosaic] Skipped UNKNOWN group "
                      f"({n_unk} file(s) — weight maps / catalogs?)",
                      flush=True)
            if not groups:
                self.send("error",
                          message="No recognisable filter groups remain after "
                                  "skipping UNKNOWN. Check your files or "
                                  "uncheck 'Skip UNKNOWN group'.")
                return
            if self.job_paths is not None:
                mosaic_dir = os.fspath(self.job_paths.mosaics)
            else:
                mosaic_dir = os.path.join(self.work_dir, "mosaics")
            # Notify UI so the Mosaic tab can be populated before we start
            self.send("groups_ready",
                      groups={k: list(v) for k, v in groups.items()},
                      out_dir=mosaic_dir)

            # Give the user a chance to choose which detected filters continue.
            # This happens after sorting/grouping, before any expensive mosaic work.
            if self.filter_selector_cb is not None:
                self.send("filter_select",
                          message="Waiting for filter selection…", pct=0,
                          groups={k: list(v) for k, v in groups.items()})
                selected_info = self.filter_selector_cb({k: list(v) for k, v in groups.items()}, mosaic_dir)
                if isinstance(selected_info, dict):
                    selected = selected_info.get("selected")
                    if "global_scale" in selected_info:
                        self.mosaic_downscale_global = selected_info.get("global_scale")
                    self.mosaic_filter_downscales.update(selected_info.get("scales") or {})
                else:
                    selected = selected_info
                if self.stopped():
                    self.send("cancelled", message="Cancelled before mosaic.")
                    return
                if selected is None:
                    self.send("cancelled", message="Mosaic cancelled at filter selection.")
                    return
                selected_set = {str(x) for x in selected}
                groups = {k: v for k, v in groups.items() if str(k) in selected_set}
                if not groups:
                    self.send("error", message="No filter groups selected for mosaic.")
                    return

            self.send("mosaic",
                      message=f"{len(groups)} selected filter group(s): "
                              f"{', '.join(sorted(groups))}",
                      pct=0)
            print(f"[Mosaic] groups: "
                  f"{dict((k, len(v)) for k, v in groups.items())}", flush=True)

            mosaic_paths = {}
            n_groups     = max(len(groups), 1)

            for gi, (fcode, gpaths) in enumerate(sorted(groups.items())):
                if self.stopped():
                    self.send("cancelled", message="Cancelled during mosaic.")
                    return

                def _cb(step, total, msg, _gi=gi):
                    p = int((_gi + step / max(total, 1)) / n_groups * 100)
                    self.send("mosaic", message=msg, pct=p)

                self.send("mosaic",
                          message=f"Mosaicking {fcode}: "
                                  f"{len(gpaths)} tile(s), {self.combine}…",
                          pct=int(gi / n_groups * 100))

                scale = _mosaic_filter_scale(fcode, self.mosaic_downscale_global,
                                             self.mosaic_filter_downscales)
                out = mosaic_filter_group(
                    gpaths, mosaic_dir, fcode,
                    combine=self.combine, progress_cb=_cb,
                    n_workers=self.n_workers, downscale=scale,
                    scratch_dir=(self.mosaic_scratch_dir or
                                 (os.fspath(self.job_paths.intermediate / "memmaps")
                                  if self.job_paths is not None else None)),
                    turbo_fullres=self.mosaic_turbo_fullres,
                    job_paths=self.job_paths)
                mosaic_paths[fcode] = out

            self.send("mosaic",
                      message=f"Mosaic complete — {len(mosaic_paths)} filter(s)",
                      pct=100, mosaic_paths=mosaic_paths)

            if self.mode == "mosaic":
                self.send("done", message="Mosaic complete.",
                          mosaic_paths=mosaic_paths)
                return
            if self.stopped():
                return
            self._handoff_align(mosaic_paths)
            return

        # ── 4. Align only (no mosaic) ────────────────────────────────────
        if self.mode == "align":
            groups = group_fits_by_filter(fits_paths)
            if self.skip_unknown and "UNKNOWN" in groups:
                groups.pop("UNKNOWN", None)
            if not groups:
                self.send("error", message="No recognisable FITS filter groups found for alignment.")
                return
            best = {}
            for fcode, gpaths in groups.items():
                if len(gpaths) == 1:
                    best[fcode] = gpaths[0]
                else:
                    try:
                        b, _ = choose_best_reference(gpaths)
                        best[fcode] = b
                    except EXPECTED_OPERATION_ERRORS:
                        best[fcode] = gpaths[0]
            self._handoff_align(best)

    def _handoff_align(self, mosaic_paths):
        self.send("align", message="Handing off to aligner…",
                  pct=0, mosaic_paths=mosaic_paths,
                  reference_mode=self.reference_mode)
        self.send("done", message="Ready to align.",
                  mosaic_paths=mosaic_paths,
                  reference_mode=self.reference_mode,
                  result_kind="align_ready",
                  source_mode=self.mode)

__all__ = [name for name in globals() if not name.startswith('__')]
