from __future__ import annotations

from tsdp2._deps import *
from tsdp2.config import load_settings, save_settings
from tsdp2.logging_config import LOGGER


def _bounded_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _bounded_float(value, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _as_bool(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


class SettingsStateMixin:
    """Load and save shared application defaults through Tk variables."""

    def _load_application_settings(self) -> None:
        config = load_settings()
        downloader = self.downloader
        mosaic = self.mosaic
        aligner = self.aligner
        recovery = self.recovery

        def assign(key, variable, convert=lambda value: value):
            if key not in config or variable is None:
                return
            try:
                variable.set(convert(config[key]))
            except (tk.TclError, TypeError, ValueError):
                LOGGER.warning("Ignored invalid setting %s=%r", key, config[key])

        assign("startup_tab", self.startup_tab, str)

        for key, variable in (
            ("download_dir", downloader.download_dir),
            ("aria2_path", downloader.aria2_path),
            ("work_dir", downloader.work_dir),
            ("user_agent", downloader.user_agent),
            ("extra_args", downloader.extra_args),
            ("pipeline_mode", downloader.pipeline_mode),
            ("reference_mode", downloader.reference_mode),
        ):
            assign(key, variable, str)

        for key, variable, default, minimum, maximum in (
            ("connections", downloader.connections, 16, 1, 64),
            ("splits", downloader.splits, 16, 1, 64),
            ("concurrent_limit", downloader.concurrent_limit, 3, 1, 50),
            ("pipeline_workers", downloader.n_workers, 4, 1, 64),
        ):
            assign(
                key,
                variable,
                lambda value, d=default, lo=minimum, hi=maximum: _bounded_int(
                    value, d, lo, hi
                ),
            )

        assign("pipeline_combine", downloader.combine_fn, str)
        for key, variable in (
            ("resume_downloads", downloader.resume),
            ("disable_allocation", downloader.disable_allocation),
            ("auto_run_pipeline", downloader.auto_run_pipeline),
            ("auto_load_aligner", downloader.auto_load_aligner),
            ("pipeline_full_resolution", downloader.turbo_fullres),
            ("skip_unknown_groups", downloader.skip_unknown_groups),
            ("when_done_summary", downloader.when_done_dialog),
            ("when_done_beep", downloader.when_done_beep),
            ("when_done_open_folder", downloader.when_done_folder),
            ("when_done_shutdown", downloader.when_done_shutdown),
        ):
            assign(key, variable, _as_bool)

        if mosaic is not None:
            for key, variable in (
                ("mosaic_output_dir", mosaic.out_dir),
                ("mosaic_scratch_dir", mosaic.scratch_dir),
                ("mosaic_combine", mosaic.combine_fn),
                ("mosaic_scale", mosaic.downscale_global),
                ("mosaic_draft_scale", mosaic.draft_scale),
                ("mosaic_seam_strength", mosaic.seam_strength),
            ):
                assign(key, variable, str)
            assign(
                "mosaic_workers",
                mosaic.n_workers,
                lambda value: _bounded_int(value, 4, 1, 64),
            )
            for key, variable in (
                ("mosaic_auto_send", mosaic.auto_send),
                ("mosaic_auto_workers", mosaic.auto_workers),
                ("mosaic_save_coverage_maps", mosaic.save_coverage_maps),
                ("mosaic_resume_cache", mosaic.resume_cache),
                ("mosaic_full_resolution", mosaic.turbo_fullres),
                ("mosaic_retain_intermediates", mosaic.retain_intermediates),
                ("mosaic_draft_cleanup", mosaic.draft_background_cleanup),
                ("mosaic_highres_cleanup", mosaic.highres_background_cleanup),
            ):
                assign(key, variable, _as_bool)

        if aligner is not None:
            for key, variable in (
                ("align_output_dir", aligner.output_dir),
                ("align_stretch_mode", aligner.stretch_mode),
                ("align_normalize_mode", aligner.normalize_mode),
            ):
                assign(key, variable, str)
            for key, variable in (
                ("align_normalize", aligner.normalize_enabled),
                ("align_psf_match", aligner.psf_match_enabled),
                ("align_keep_reference_canvas", aligner.keep_reference_canvas),
                ("align_generate_diagnostics", aligner.generate_diagnostics),
                ("align_retain_intermediates", aligner.retain_intermediates),
            ):
                assign(key, variable, _as_bool)

        if recovery is not None:
            assign("recovery_output_dir", recovery.output_dir, str)
            assign("recovery_scale", recovery.scale_var, str)

    def _application_settings_payload(self) -> dict[str, Any]:
        downloader = self.downloader
        mosaic = self.mosaic
        aligner = self.aligner
        recovery = self.recovery

        payload = {
            "startup_tab": str(self.startup_tab.get()),
            "download_dir": str(downloader.download_dir.get()).strip(),
            "aria2_path": str(downloader.aria2_path.get()).strip(),
            "work_dir": str(downloader.work_dir.get()).strip(),
            "user_agent": str(downloader.user_agent.get()).strip(),
            "extra_args": str(downloader.extra_args.get()).strip(),
            "pipeline_mode": str(downloader.pipeline_mode.get()),
            "reference_mode": str(downloader.reference_mode.get()),
            "connections": _bounded_int(downloader.connections.get(), 16, 1, 64),
            "splits": _bounded_int(downloader.splits.get(), 16, 1, 64),
            "concurrent_limit": _bounded_int(
                downloader.concurrent_limit.get(), 3, 1, 50
            ),
            "pipeline_workers": _bounded_int(downloader.n_workers.get(), 4, 1, 64),
            "pipeline_combine": str(downloader.combine_fn.get()),
            "resume_downloads": bool(downloader.resume.get()),
            "disable_allocation": bool(downloader.disable_allocation.get()),
            "auto_run_pipeline": bool(downloader.auto_run_pipeline.get()),
            "auto_load_aligner": bool(downloader.auto_load_aligner.get()),
            "pipeline_full_resolution": bool(downloader.turbo_fullres.get()),
            "skip_unknown_groups": bool(downloader.skip_unknown_groups.get()),
            "when_done_summary": bool(downloader.when_done_dialog.get()),
            "when_done_beep": bool(downloader.when_done_beep.get()),
            "when_done_open_folder": bool(downloader.when_done_folder.get()),
            "when_done_shutdown": bool(downloader.when_done_shutdown.get()),
        }

        if mosaic is not None:
            payload.update(
                {
                    "mosaic_output_dir": str(mosaic.out_dir.get()).strip(),
                    "mosaic_scratch_dir": str(mosaic.scratch_dir.get()).strip(),
                    "mosaic_combine": str(mosaic.combine_fn.get()),
                    "mosaic_workers": _bounded_int(mosaic.n_workers.get(), 4, 1, 64),
                    "mosaic_auto_workers": bool(mosaic.auto_workers.get()),
                    "mosaic_scale": str(mosaic.downscale_global.get()),
                    "mosaic_draft_scale": str(mosaic.draft_scale.get()),
                    "mosaic_seam_strength": str(mosaic.seam_strength.get()),
                    "mosaic_auto_send": bool(mosaic.auto_send.get()),
                    "mosaic_save_coverage_maps": bool(mosaic.save_coverage_maps.get()),
                    "mosaic_resume_cache": bool(mosaic.resume_cache.get()),
                    "mosaic_full_resolution": bool(mosaic.turbo_fullres.get()),
                    "mosaic_retain_intermediates": bool(
                        mosaic.retain_intermediates.get()
                    ),
                    "mosaic_draft_cleanup": bool(
                        mosaic.draft_background_cleanup.get()
                    ),
                    "mosaic_highres_cleanup": bool(
                        mosaic.highres_background_cleanup.get()
                    ),
                }
            )

        if aligner is not None:
            payload.update(
                {
                    "align_output_dir": str(aligner.output_dir.get()).strip(),
                    "align_stretch_mode": str(aligner.stretch_mode.get()),
                    "align_normalize": bool(aligner.normalize_enabled.get()),
                    "align_normalize_mode": str(aligner.normalize_mode.get()),
                    "align_psf_match": bool(aligner.psf_match_enabled.get()),
                    "align_keep_reference_canvas": bool(
                        aligner.keep_reference_canvas.get()
                    ),
                    "align_generate_diagnostics": bool(
                        aligner.generate_diagnostics.get()
                    ),
                    "align_retain_intermediates": bool(
                        aligner.retain_intermediates.get()
                    ),
                }
            )

        if recovery is not None:
            payload.update(
                {
                    "recovery_output_dir": str(recovery.output_dir.get()).strip(),
                    "recovery_scale": str(recovery.scale_var.get()),
                }
            )

        profile = getattr(self, "_last_system_profile", None)
        recommendation = getattr(self, "_system_recommendation", None)
        if profile is not None:
            payload["last_system_profile"] = profile.as_dict()
        if recommendation is not None:
            payload["last_system_recommendation"] = recommendation.as_dict()
        return payload

    def _save_application_settings(self) -> bool:
        config = load_settings()
        config.update(self._application_settings_payload())
        return save_settings(config)

    def _restore_processing_defaults(self) -> None:
        downloader = self.downloader
        downloader.connections.set(16)
        downloader.splits.set(16)
        downloader.concurrent_limit.set(3)
        downloader.n_workers.set(max(1, (os.cpu_count() or 4) - 1))
        downloader.combine_fn.set("median")
        downloader.resume.set(True)
        downloader.disable_allocation.set(False)
        downloader.auto_run_pipeline.set(True)
        downloader.auto_load_aligner.set(True)
        downloader.turbo_fullres.set(True)
        downloader.skip_unknown_groups.set(True)
        downloader.when_done_dialog.set(True)
        downloader.when_done_beep.set(False)
        downloader.when_done_folder.set(False)
        downloader.when_done_shutdown.set(False)
        downloader.pipeline_mode.set("mosaic_align")
        downloader.reference_mode.set("")
        downloader.user_agent.set("")
        downloader.extra_args.set("")

        if self.mosaic is not None:
            mosaic = self.mosaic
            mosaic.combine_fn.set("feather")
            mosaic.n_workers.set(max(1, (os.cpu_count() or 4) - 1))
            mosaic.auto_workers.set(True)
            mosaic.downscale_global.set("1.0")
            mosaic.draft_scale.set("0.08")
            mosaic.seam_strength.set("1.0")
            mosaic.auto_send.set(True)
            mosaic.save_coverage_maps.set(True)
            mosaic.resume_cache.set(True)
            mosaic.turbo_fullres.set(True)
            mosaic.retain_intermediates.set(False)
            mosaic.draft_background_cleanup.set(True)
            mosaic.highres_background_cleanup.set(True)

        if self.aligner is not None:
            aligner = self.aligner
            aligner.stretch_mode.set("asinh")
            aligner.normalize_enabled.set(False)
            aligner.normalize_mode.set("median")
            aligner.psf_match_enabled.set(False)
            aligner.keep_reference_canvas.set(False)
            aligner.generate_diagnostics.set(True)
            aligner.retain_intermediates.set(False)

        if self.recovery is not None:
            self.recovery.scale_var.set("0.5")


__all__ = [name for name in globals() if not name.startswith("__")]
