from __future__ import annotations

from tsdp2._deps import *
from tsdp2.logging_config import LOGGER
from tsdp2.astronomy.previews import _THUMB_CACHE
from tsdp2.utilities.threading import post_to_ui

def flush_application_state(self):
    """Reset Downloader, Aligner, and Mosaic tabs to a fresh state.

    Must run on the Tk main thread. Stops running workers/pipelines first,
    then clears rows, queues, Telegram job/batch state, dashboards, and the
    thumbnail cache. Keeps user settings (paths, tokens, spinboxes) intact.
    Returns a summary dict.
    """
    summary = {"downloads": 0, "stopped": 0, "aligner_rows": 0,
               "mosaic_files": 0, "thumb_cache": 0}

    # ── Downloader: stop everything running ────────────────────────────────
    try:
        for row in list(self.rows):
            try:
                if row.worker and row.worker.is_alive():
                    row.worker.stop()
                    summary["stopped"] += 1
            except EXPECTED_OPERATION_ERRORS:
                LOGGER.debug("Best-effort operation failed", exc_info=True)
        try:
            self.cancel_pipeline()
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
        summary["downloads"] = len(self.rows)
        for row in list(self.rows):
            try:
                row.destroy()
            except EXPECTED_OPERATION_ERRORS:
                LOGGER.debug("Best-effort operation failed", exc_info=True)
        self.rows = []
        self.selected_row = None
        try:
            self.running_workers.clear()
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
        try:
            self.log_text.delete("1.0", tk.END)
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
        try:
            self.pipe_prog["value"] = 0
            self.pipe_lbl.config(text="Idle")
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
        self.pipeline_start = None
    except EXPECTED_OPERATION_ERRORS as exc:
        print(f"[Flush] downloader: {exc}", flush=True)

    # ── Telegram job / batch / dashboard state ──────────────────────────────
    for attr, val in (
        ("_tg_newjob", None),
        ("_tg_active_batch", None),
        ("_tg_dashboard_message_id", None),
        ("_tg_dashboard_handoff_message_id", None),
        ("_tg_last_dashboard_edit", 0.0),
        ("_tg_last_finish_buttons_key", None),
        ("_tg_last_finish_buttons_time", 0.0),
        ("_tg_suppress_row_download_notify", False),
    ):
        try:
            setattr(self, attr, val)
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
    try:
        self._tg_recent_outputs = {
            "fits": [], "images": [], "logs": [], "csvs": [], "dirs": [],
            "source": "", "updated": 0.0, "label": "none",
        }
    except EXPECTED_OPERATION_ERRORS:
        LOGGER.debug("Best-effort operation failed", exc_info=True)

    # ── Aligner tab ─────────────────────────────────────────────────────────
    try:
        a = self.aligner_ref
        if a is not None:
            try:
                a.cancel()
            except EXPECTED_OPERATION_ERRORS:
                LOGGER.debug("Best-effort operation failed", exc_info=True)
            a._align_running = False
            summary["aligner_rows"] = len(getattr(a, "rows", []) or [])
            a.reference_path.set("")
            a.reference_name.set("reference")
            a.output_dir.set("")
            try:
                a.keep_reference_canvas.set(False)
            except EXPECTED_OPERATION_ERRORS:
                LOGGER.debug("Best-effort operation failed", exc_info=True)
            try:
                n = int(a.num_filters.get() or 3)
            except EXPECTED_OPERATION_ERRORS:
                n = 3
            a._rebuild_filter_rows(n)
            for i, r in enumerate(a.rows, start=1):
                r.path.set("")
                r.name.set(f"filter_{i}")
            a.last_run_outputs = []
            try:
                a.blink_btn.config(state="disabled")
            except EXPECTED_OPERATION_ERRORS:
                LOGGER.debug("Best-effort operation failed", exc_info=True)
            try:
                a.progress["value"] = 0
                a.status.config(text="Idle")
            except EXPECTED_OPERATION_ERRORS:
                LOGGER.debug("Best-effort operation failed", exc_info=True)
            try:
                if a.preview_visible:
                    a.update_all_previews()
            except EXPECTED_OPERATION_ERRORS:
                LOGGER.debug("Best-effort operation failed", exc_info=True)
    except EXPECTED_OPERATION_ERRORS as exc:
        print(f"[Flush] aligner: {exc}", flush=True)

    # ── Mosaic tab ──────────────────────────────────────────────────────────
    try:
        m = self.mosaic_tab_ref
        if m is not None:
            try:
                m.cancel_mosaic()
            except EXPECTED_OPERATION_ERRORS:
                LOGGER.debug("Best-effort operation failed", exc_info=True)
            summary["mosaic_files"] = len(getattr(m, "fits_paths", []) or [])
            m.fits_paths.clear()
            m.groups.clear()
            m._rebuild_tree()
            try:
                m._prog["value"] = 0
                m._stat.config(text="Idle")
                m._run_btn.config(state="normal")
                m._cancel_btn.config(state="disabled")
            except EXPECTED_OPERATION_ERRORS:
                LOGGER.debug("Best-effort operation failed", exc_info=True)
    except EXPECTED_OPERATION_ERRORS as exc:
        print(f"[Flush] mosaic: {exc}", flush=True)

    # ── Thumbnail cache ─────────────────────────────────────────────────────
    try:
        summary["thumb_cache"] = len(_THUMB_CACHE)
        _THUMB_CACHE.clear()
    except EXPECTED_OPERATION_ERRORS:
        LOGGER.debug("Best-effort operation failed", exc_info=True)

    print(f"[Flush] complete: {summary}", flush=True)
    return summary


def _tg_do_flush(self, tg):
    """Run the flush on the Tk thread, then report over Telegram."""
    def _on_main():
        summary = flush_application_state(self)
        def _report():
            tg.send(
                "🔄 <b>Flushed — fresh state, no restart</b>\n"
                f"Download rows cleared: {summary['downloads']} "
                f"(stopped {summary['stopped']} running)\n"
                f"Aligner rows reset: {summary['aligner_rows']}\n"
                f"Mosaic files cleared: {summary['mosaic_files']}\n"
                f"Thumbnail cache: {summary['thumb_cache']} entries\n\n"
                "Settings, paths, and Telegram config were kept."
            )
        threading.Thread(target=_report, daemon=True).start()
    post_to_ui(_on_main)


def active_flush_jobs(controller) -> list[str]:
    """Return human-readable names of currently active jobs."""
    running = []
    try:
        if any(row.worker and row.worker.is_alive() for row in controller.rows):
            running.append("downloads")
        if controller.pipeline_runner and controller.pipeline_runner.is_alive():
            running.append("pipeline")
        aligner = controller.aligner_ref
        if aligner is not None and getattr(aligner, "_align_running", False):
            running.append("alignment")
    except (AttributeError, RuntimeError, tk.TclError):
        LOGGER.debug("Could not inspect every running job before flush", exc_info=True)
    return running

__all__ = [name for name in globals() if not name.startswith('__')]
