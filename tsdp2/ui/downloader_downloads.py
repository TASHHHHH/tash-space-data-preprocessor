from __future__ import annotations

from tsdp2.ui._common import *

class DownloaderDownloadsMixin:
    def _start_all_core(self):
        if not self.validate():
            return
        pending = [r for r in self.rows
                   if r.url_var.get().strip()
                   and not (r.worker and r.worker.is_alive())]
        if not pending:
            messagebox.showinfo("Nothing to start", "No pending downloads.")
            return
        limit  = _safe_int(self.concurrent_limit.get(), default=1, lo=1, hi=50)
        active = sum(1 for r in self.rows
                     if r.worker and r.worker.is_alive())
        for row in pending:
            if active >= limit:
                row.status_var.set("Queued")
                continue
            self.start_row(row)
            active += 1

    def start_row(self, row):
        item    = row.get_item()
        task_id = self.next_task_id
        self.next_task_id += 1
        row_download_dir = (getattr(row, "_download_dir_override", None)
                            or self.download_dir.get().strip())
        try:
            os.makedirs(row_download_dir, exist_ok=True)
        except EXPECTED_OPERATION_ERRORS as exc:
            row.status_var.set("Error")
            row.meta_var.set(f"Cannot create download dir: {exc}")
            return
        worker = Aria2DownloadWorker(
            task_id=task_id,
            aria2_path=self.aria2_path.get().strip(),
            download_dir=row_download_dir,
            item=item,
            connections=self.connections.get(),
            splits=self.splits.get(),
            resume=self.resume.get(),
            disable_allocation=self.disable_allocation.get(),
            user_agent=self.user_agent.get(),
            extra_args=self.extra_args.get(),
            status_queue=self.status_queue,
        )
        row.worker = worker
        row.status_var.set("Starting")
        row.meta_var.set("")
        row.pct_var.set(0)
        self.running_workers[task_id] = row
        worker.start()

    def _stop_all_core(self):
        for row in self.rows:
            if row.worker and row.worker.is_alive():
                row.worker.stop()
                row.status_var.set("Stopping")

    def _maybe_start_queued_core(self):
        limit  = _safe_int(self.concurrent_limit.get(), default=1, lo=1, hi=50)
        active = sum(1 for r in self.rows
                     if r.worker and r.worker.is_alive())
        if active >= limit:
            return
        for row in self.rows:
            if (row.status_var.get() == "Queued"
                    and row.url_var.get().strip()
                    and not (row.worker and row.worker.is_alive())):
                self.start_row(row)
                active += 1
                if active >= limit:
                    break

    def poll_status_queue(self):
        try:
            while True:
                payload = self.status_queue.get_nowait()
                self._handle_dl_status(payload)
        except queue.Empty:
            pass
        self.after(100, self.poll_status_queue)

    def _handle_dl_status_core(self, payload):
        task_id = payload.get("task_id")
        row     = self.running_workers.get(task_id)
        if not row:
            return
        kind = payload["kind"]

        if kind == "started":
            row.status_var.set("Running")
            line = f"\n=== START #{task_id} ===\n{payload['command']}\n"
            row.append_log(line)
            if row is self.selected_row:
                self.append_dl_log(line)

        elif kind == "log":
            line = payload["line"]
            row.append_log(line)
            if row is self.selected_row:
                self.append_dl_log(line)

        elif kind == "progress":
            if "pct" in payload:
                row.pct_var.set(payload.get("pct", 0))
                row.status_var.set("Running")
                meta = (f"DL {payload.get('speed','')} | "
                        f"CN {payload.get('connections','')}")
                if payload.get("eta"):
                    meta += f" | ETA {payload['eta']}"
                row.meta_var.set(meta)
            elif "file_path" in payload:
                row.last_file_path = payload["file_path"]
                row.meta_var.set(Path(row.last_file_path).name)

        elif kind == "done":
            if not row.last_file_path and payload.get("file_path"):
                row.last_file_path = payload.get("file_path", "")
            # aria2 may rename files due Content-Disposition or existing .1 files.
            # If the expected path does not exist, scan the actual row download
            # folder for the newest FITS-like file so Telegram batches do not
            # miscount successful downloads as failed.
            try:
                if row.last_file_path and not os.path.exists(row.last_file_path):
                    row.last_file_path = ""
                if not row.last_file_path:
                    dl_dir = getattr(row, "_download_dir_override", None) or self.download_dir.get().strip()
                    exts = (".fits", ".fit", ".fts", ".zip")
                    candidates = []
                    if dl_dir and os.path.isdir(dl_dir):
                        for fn in os.listdir(dl_dir):
                            p = os.path.join(dl_dir, fn)
                            if os.path.isfile(p) and fn.lower().endswith(exts):
                                candidates.append(p)
                    if candidates:
                        row.last_file_path = max(candidates, key=lambda p: os.path.getmtime(p))
            except EXPECTED_OPERATION_ERRORS:
                LOGGER.debug("Best-effort operation failed", exc_info=True)
            row.status_var.set("Done")
            row.pct_var.set(100)
            if row.last_file_path:
                row.meta_var.set(Path(row.last_file_path).name)
                fname = Path(row.last_file_path).name
                try:
                    size_txt = f"  ({os.path.getsize(row.last_file_path) / 1e6:.0f} MB)"
                except OSError:
                    size_txt = ""
                self._notify(f"✅ <b>TSDP — Download complete</b>\n"
                             f"📥 {fname}{size_txt}")
            self.running_workers.pop(task_id, None)
            self.maybe_start_queued()
            # ── trigger pipeline ──────────────────────────────────────────
            # Telegram /newjob queues many files as separate aria2 rows, but the
            # pipeline must run ONCE after every selected row has finished.
            # Never auto-run per individual Telegram batch row, even if the GUI
            # Auto-run checkbox is enabled or gets restored by another path.
            is_tg_batch_row = bool(getattr(row, "_tg_job_id", None))
            if self.auto_run_pipeline.get() and row.last_file_path and not is_tg_batch_row:
                self._start_pipeline_for(row.last_file_path)

        elif kind == "stopped":
            row.status_var.set("Stopped")
            self.running_workers.pop(task_id, None)
            self.maybe_start_queued()

        elif kind == "error":
            row.status_var.set("Error")
            row.meta_var.set(payload.get("message", ""))
            row.append_log("ERROR: " + payload.get("message", ""))
            if row is self.selected_row:
                self.append_dl_log("ERROR: " + payload.get("message", ""))
            self.running_workers.pop(task_id, None)
            self.maybe_start_queued()

__all__ = [name for name in globals() if not name.startswith('__')]
