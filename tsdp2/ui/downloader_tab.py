from __future__ import annotations

from tsdp2.ui._common import *
from tsdp2.ui.downloader_layout import DownloaderLayoutMixin
from tsdp2.ui.downloader_rows import DownloaderRowsMixin
from tsdp2.ui.downloader_telegram import DownloaderTelegramMixin
from tsdp2.ui.downloader_pipeline import DownloaderPipelineMixin
from tsdp2.ui.downloader_pipeline_events import DownloaderPipelineEventsMixin
from tsdp2.ui.downloader_downloads import DownloaderDownloadsMixin
from tsdp2.telegram.client import TelegramNotifier
from tsdp2.telegram.commands import (
    _global_batch_counts,
    _global_common_source_for_batch,
    _global_row_has_url,
    _global_unique_rows,
    _tg_batch_counts,
    _tg_check_active_batch,
    _tg_update_active_batch_dashboard,
    _tg_update_batch_dashboard,
)
from tsdp2.telegram.listener import ensure_listener, poll_command_queue
from tsdp2.telegram.output_delivery import _tg_send_blink_gif_worker, _tg_send_finish_buttons
from tsdp2.telegram.output_state import _tg_record_outputs, empty_output_state
from tsdp2.recovery.cleanup import flush_application_state


class DownloaderTab(
    DownloaderTelegramMixin,
    DownloaderPipelineMixin,
    DownloaderPipelineEventsMixin,
    DownloaderDownloadsMixin,
    DownloaderRowsMixin,
    DownloaderLayoutMixin,
    ttk.Frame,
):
    """Downloader controller with one explicit orchestration path per event."""

    def __init__(self, *args, **kwargs):
        self._initialize_ui(*args, **kwargs)
        self._tg_listener_thread = None
        self._tg_listener_stop = False
        self._tg_newjob = None
        self._tg_active_batch = None
        self._tg_command_queue = queue.Queue()
        self._tg_download_dashboard_lock = threading.Lock()
        self._tg_dashboard_handoff_message_id = None
        self._tg_suppress_row_download_notify = False
        self._tg_runtime_enabled = bool(self.tg_enabled.get())
        self._tg_runtime_token = self.tg_token.get()
        self._tg_runtime_chat_id = self.tg_chat_id.get()
        self._post_download_batch = None
        self._post_download_batch_seq = 0
        settings = load_settings()
        self._tg_send_fits_enabled = bool(settings.get("tg_send_fits", False))
        self._tg_recent_outputs = empty_output_state()
        try:
            self.after(300, self._tg_poll_command_queue)
            self.after(1500, self._tg_ensure_listener)
        except tk.TclError:
            LOGGER.debug("Downloader closed before Telegram polling was scheduled", exc_info=True)

    def _tg_poll_command_queue(self):
        poll_command_queue(self)

    def _tg_ensure_listener(self):
        ensure_listener(self)

    def _notify(self, text: str, fits_path: str | None = None):
        if self._tg_suppress_row_download_notify and "Download complete" in str(text):
            return None
        return self._notify_core(text, fits_path=fits_path)

    def _start_pipeline_for(self, source_path):
        batch = self._tg_active_batch
        if batch and not batch.get("pipeline_started", False):
            counts = _tg_batch_counts(self, batch)
            note = (
                "Blocked premature pipeline start. Waiting for all files "
                f"({counts['terminal']}/{counts['expected']} finished)"
            )
            _tg_update_batch_dashboard(self, batch, force=True, note=note)
            LOGGER.info("Telegram batch barrier blocked premature pipeline start for %s", source_path)
            return None
        return self._start_pipeline_for_core(source_path)

    def maybe_start_queued(self):
        batch = self._tg_active_batch
        if not batch or batch.get("pipeline_started"):
            return self._maybe_start_queued_core()
        limit = _safe_int(self.concurrent_limit.get(), default=1, lo=1, hi=50)
        active = sum(1 for row in self.rows if row.worker and row.worker.is_alive())
        for row in self.rows:
            if active >= limit:
                break
            try:
                belongs = getattr(row, "_tg_job_id", None) == batch.get("job_id")
                queued = row.status_var.get() == "Queued"
                available = not (row.worker and row.worker.is_alive())
                if belongs and queued and row.url_var.get().strip() and available:
                    self.start_row(row)
                    active += 1
            except (AttributeError, RuntimeError, tk.TclError):
                LOGGER.debug("Skipped an invalid Telegram queue row", exc_info=True)

    def start_all(self):
        if not self.validate():
            return
        pending = [row for row in self.rows if _global_row_has_url(row) and not (row.worker and row.worker.is_alive())]
        active = [row for row in self.rows if _global_row_has_url(row) and row.worker and row.worker.is_alive()]
        if not pending and not active:
            messagebox.showinfo("Nothing to start", "No pending downloads.")
            return
        if bool(self.auto_run_pipeline.get()):
            self._post_download_batch_seq += 1
            batch_rows = _global_unique_rows(active + pending)
            self._post_download_batch = {
                "id": self._post_download_batch_seq,
                "rows": batch_rows,
                "expected": len(batch_rows),
                "download_dir": self.download_dir.get().strip(),
                "pipeline_started": False,
                "cancelled": False,
                "created": time.time(),
            }
            for row in batch_rows:
                row._post_download_batch_id = self._post_download_batch_seq
                row._no_individual_pipeline = True
            self.pipe_lbl.config(text=f"Waiting for downloads — 0/{len(batch_rows)} finished")
            self.pipe_prog["value"] = 0
        limit = _safe_int(self.concurrent_limit.get(), default=1, lo=1, hi=50)
        active_count = sum(1 for row in self.rows if row.worker and row.worker.is_alive())
        for row in pending:
            if active_count >= limit:
                row.status_var.set("Queued")
            else:
                self.start_row(row)
                active_count += 1

    def stop_all(self):
        batch = self._post_download_batch
        if batch and not batch.get("pipeline_started"):
            batch["cancelled"] = True
        return self._stop_all_core()

    def _handle_dl_status(self, payload):
        kind = payload.get("kind")
        row = self.running_workers.get(payload.get("task_id"))
        is_tg_row = bool(row is not None and getattr(row, "_tg_job_id", None))
        old_auto = None
        if kind == "done":
            old_auto = bool(self.auto_run_pipeline.get())
            if old_auto:
                self.auto_run_pipeline.set(False)
        self._tg_suppress_row_download_notify = bool(is_tg_row and kind == "done")
        try:
            self._handle_dl_status_core(payload)
        finally:
            self._tg_suppress_row_download_notify = False
            if old_auto is not None:
                self.auto_run_pipeline.set(old_auto)
        if is_tg_row:
            _tg_update_active_batch_dashboard(self, force=kind in {"started", "done", "stopped", "error"})
        _tg_check_active_batch(self)
        if kind in {"done", "error", "stopped"}:
            self._maybe_start_global_post_download_pipeline()

    def _maybe_start_global_post_download_pipeline(self):
        batch = self._post_download_batch
        if not batch or batch.get("pipeline_started"):
            return
        counts = _global_batch_counts(self, batch)
        rows, expected = counts["rows"], counts["expected"]
        if expected <= 0:
            self._post_download_batch = None
            return
        if len(rows) < expected:
            self.pipe_lbl.config(text=f"Preparing download batch — {len(rows)}/{expected} rows")
            return
        if counts["terminal"] < expected or counts["running"] > 0:
            self.pipe_prog["value"] = int(counts["terminal"] / max(expected, 1) * 100)
            self.pipe_lbl.config(text=(f"Waiting for all downloads — {counts['terminal']}/{expected} finished "
                                       f"({counts['done']} done, {counts['failed']} failed, {counts['stopped']} stopped)"))
            return
        if batch.get("cancelled"):
            self.pipe_lbl.config(text="Download batch cancelled — pipeline not started")
            self._post_download_batch = None
            return
        pipeline_source, completed = _global_common_source_for_batch(self, batch, rows)
        if not completed:
            self.pipe_lbl.config(text="All downloads finished, but no successful FITS/ZIP file was found. Pipeline not started")
            self._post_download_batch = None
            return
        batch["pipeline_started"] = True
        self._post_download_batch = None
        self.pipe_lbl.config(text=f"All downloads complete — starting pipeline on {len(completed)} file(s)")
        self.pipe_prog["value"] = 100
        self._start_pipeline_for(pipeline_source)

    def _handle_pipeline_msg(self, message):
        if isinstance(message, dict) and message.get("step") == "done":
            mosaic_paths = message.get("mosaic_paths") or {}
            fits_paths = message.get("fits_paths") or []
            all_paths = list(mosaic_paths.values()) + list(fits_paths)
            label = "align inputs" if message.get("result_kind") == "align_ready" else "pipeline"
            _tg_record_outputs(self, fits=all_paths, dirs=[os.path.dirname(path) for path in all_paths if path],
                               source=getattr(self, "_last_pipeline_source", ""), label=label)
        result = self._handle_pipeline_msg_core(message)
        if isinstance(message, dict) and message.get("step") == "done" and message.get("result_kind") != "align_ready":
            self.after(250, lambda: _tg_send_finish_buttons(self, "Pipeline complete"))
        return result

    def _aligner_pipeline_done(self, out_folder, saved, log_path, total_t):
        paths = [str(item[1]) for item in (saved or []) if len(item) > 1]
        _tg_record_outputs(self, fits=paths, logs=[log_path] if log_path else [], dirs=[out_folder], label="alignment")
        result = self._aligner_pipeline_done_core(out_folder, saved, log_path, total_t)
        self.after(250, lambda: _tg_send_finish_buttons(self, "Alignment complete"))
        available = [path for path in paths if os.path.isfile(path)]
        if len(available) >= 2 and self._tg_runtime_enabled and self._tg_runtime_token and self._tg_runtime_chat_id:
            notifier = TelegramNotifier(self._tg_runtime_token, self._tg_runtime_chat_id)
            threading.Thread(target=_tg_send_blink_gif_worker,
                             args=(self, notifier, {"fits": available, "images": [], "logs": [], "csv": []}),
                             daemon=True).start()
        return result

    def flush_all(self):
        return flush_application_state(self)

__all__ = [name for name in globals() if not name.startswith('__')]
