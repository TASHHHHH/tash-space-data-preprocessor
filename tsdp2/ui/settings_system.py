from __future__ import annotations

from tsdp2._deps import *
from tsdp2.config import load_settings
from tsdp2.logging_config import LOGGER
from tsdp2.ui.theme import C_FG_DIM
from tsdp2.utilities.system_profile import (
    SystemProfile,
    inspect_system,
    recommend_settings,
)


class SettingsSystemMixin:
    """Asynchronous hardware inspection and transparent auto-tuning."""

    def _initialize_system_settings(self) -> None:
        self._system_test_queue = queue.Queue()
        self._system_test_running = False
        self._last_system_profile = None
        self._last_system_recommendation = None
        self.system_test_status = tk.StringVar(value="Not tested yet")
        self.system_test_details = tk.StringVar(
            value="Test CPU, memory, scratch storage, and GPU availability."
        )

        raw = load_settings().get("last_system_profile")
        if isinstance(raw, dict):
            try:
                profile = SystemProfile.from_dict(raw)
                self._set_system_result(profile)
                self.system_test_status.set(
                    f"Last result: {self._last_system_recommendation.tier}"
                )
            except (TypeError, ValueError, KeyError):
                LOGGER.warning("Ignored invalid saved system profile")

    def _build_system_profile(self, parent) -> None:
        frame = ttk.LabelFrame(parent, text="System and Performance", padding=12)
        self._place_settings_section(frame)
        frame.columnconfigure(1, weight=1)

        button_row = ttk.Frame(frame)
        button_row.grid(row=0, column=0, sticky="nw", padx=6, pady=4)
        self.system_test_button = ttk.Button(
            button_row,
            text="Test this system",
            style="Accent.TButton",
            command=self._start_system_test,
        )
        self.system_test_button.pack(side="left", padx=(0, 6))
        self.apply_recommendation_button = ttk.Button(
            button_row,
            text="Apply recommendation",
            command=self._apply_system_recommendation,
            state="normal" if self._last_system_recommendation else "disabled",
        )
        self.apply_recommendation_button.pack(side="left", padx=3)
        ttk.Button(
            button_row,
            text="Copy report",
            command=self._copy_system_report,
        ).pack(side="left", padx=3)

        result = ttk.Frame(frame)
        result.grid(row=0, column=1, sticky="ew", padx=(20, 6), pady=4)
        result.columnconfigure(0, weight=1)
        ttk.Label(
            result,
            textvariable=self.system_test_status,
            font=("Segoe UI Semibold", 10),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            result,
            textvariable=self.system_test_details,
            foreground=C_FG_DIM,
            wraplength=900,
            justify="left",
        ).grid(row=1, column=0, sticky="ew", pady=(3, 0))
        self.system_test_progress = ttk.Progressbar(
            result, mode="indeterminate", length=240
        )
        self.system_test_progress.grid(row=2, column=0, sticky="w", pady=(7, 0))
        self.system_test_progress.grid_remove()

        ttk.Label(
            frame,
            text=(
                "The test writes and removes one 32 MB temporary scratch file. "
                "Recommendations never reduce output safety or disable diagnostics."
            ),
            foreground=C_FG_DIM,
            font=("Segoe UI", 8),
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=6, pady=(7, 0))

    def _start_system_test(self) -> None:
        if self._system_test_running:
            return
        self._system_test_running = True
        self.system_test_button.configure(state="disabled")
        self.apply_recommendation_button.configure(state="disabled")
        self.system_test_status.set("Testing this system...")
        self.system_test_details.set(
            "Reading CPU and memory, then measuring scratch-disk capacity and speed."
        )
        self.system_test_progress.grid()
        self.system_test_progress.start(12)

        target = None
        if self.mosaic is not None:
            target = str(self.mosaic.scratch_dir.get()).strip() or None

        def worker() -> None:
            try:
                profile = inspect_system(target, run_disk_benchmark=True)
                self._system_test_queue.put(("result", profile))
            except EXPECTED_OPERATION_ERRORS as exc:
                self._system_test_queue.put(("error", f"{type(exc).__name__}: {exc}"))

        threading.Thread(target=worker, daemon=True).start()
        self.after(100, self._poll_system_test)

    def _poll_system_test(self) -> None:
        try:
            kind, payload = self._system_test_queue.get_nowait()
        except queue.Empty:
            if self._system_test_running:
                self.after(100, self._poll_system_test)
            return

        self._system_test_running = False
        self.system_test_progress.stop()
        self.system_test_progress.grid_remove()
        self.system_test_button.configure(state="normal")

        if kind == "error":
            self.system_test_status.set("System test could not finish")
            self.system_test_details.set(str(payload))
            return

        self._set_system_result(payload)
        self.apply_recommendation_button.configure(state="normal")
        self._save_application_settings()

    def _set_system_result(self, profile: SystemProfile) -> None:
        recommendation = recommend_settings(profile)
        self._last_system_profile = profile
        self._last_system_recommendation = recommendation
        self.system_test_status.set(f"Recommendation: {recommendation.tier}")
        gpu_text = profile.gpu_name or "Not detected"
        self.system_test_details.set(
            f"{profile.logical_cpus} logical CPU threads | {profile.ram_gb:.1f} GB RAM | "
            f"{profile.disk_free_gb:.0f} GB scratch free | "
            f"{profile.disk_write_mbps:.0f} MB/s write | "
            f"{profile.disk_read_mbps:.0f} MB/s read | GPU: {gpu_text}"
        )

    def _apply_system_recommendation(self) -> None:
        recommendation = self._last_system_recommendation
        if recommendation is None:
            messagebox.showinfo(
                "No recommendation",
                "Run Test this system first.",
            )
            return

        downloader = self.downloader
        downloader.n_workers.set(recommendation.pipeline_workers)
        downloader.concurrent_limit.set(recommendation.concurrent_downloads)
        downloader.connections.set(recommendation.connections)
        downloader.splits.set(recommendation.splits)
        downloader.turbo_fullres.set(recommendation.full_resolution)

        if self.mosaic is not None:
            mosaic = self.mosaic
            mosaic.n_workers.set(recommendation.mosaic_workers)
            mosaic.downscale_global.set(recommendation.mosaic_scale)
            mosaic.draft_scale.set(recommendation.draft_scale)
            mosaic.turbo_fullres.set(recommendation.full_resolution)
            mosaic.retain_intermediates.set(recommendation.retain_intermediates)
            mosaic.resume_cache.set(True)
            mosaic.draft_background_cleanup.set(True)

        self._save_application_settings()
        notes = "\n".join(f"- {note}" for note in recommendation.notes)
        messagebox.showinfo(
            "Recommendation applied",
            (
                f"{recommendation.tier} defaults are now active and saved.\n\n"
                f"Pipeline workers: {recommendation.pipeline_workers}\n"
                f"Mosaic workers: {recommendation.mosaic_workers}\n"
                f"Concurrent downloads: {recommendation.concurrent_downloads}\n"
                f"Output scale: {recommendation.mosaic_scale}\n"
                f"Full resolution: {'On' if recommendation.full_resolution else 'Off'}"
                + (f"\n\n{notes}" if notes else "")
            ),
        )

    def _system_report_text(self) -> str:
        profile = self._last_system_profile
        recommendation = self._last_system_recommendation
        if profile is None or recommendation is None:
            return "TSDP system test has not been run."
        notes = "\n".join(f"- {note}" for note in recommendation.notes)
        return (
            "TSDP system report\n"
            f"CPU: {profile.cpu_name}\n"
            f"Logical CPU threads: {profile.logical_cpus}\n"
            f"RAM: {profile.ram_gb:.1f} GB\n"
            f"GPU: {profile.gpu_name}\n"
            f"Scratch path: {profile.disk_path}\n"
            f"Scratch free: {profile.disk_free_gb:.1f} GB\n"
            f"Disk write: {profile.disk_write_mbps:.1f} MB/s\n"
            f"Disk read: {profile.disk_read_mbps:.1f} MB/s\n"
            f"OS: {profile.operating_system}\n"
            f"Recommendation: {recommendation.tier}\n"
            f"Pipeline workers: {recommendation.pipeline_workers}\n"
            f"Mosaic workers: {recommendation.mosaic_workers}\n"
            f"Concurrent downloads: {recommendation.concurrent_downloads}\n"
            f"Output scale: {recommendation.mosaic_scale}\n"
            f"Full resolution: {recommendation.full_resolution}\n"
            f"{notes}"
        )

    def _copy_system_report(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self._system_report_text())
        self.update_idletasks()


__all__ = [name for name in globals() if not name.startswith("__")]