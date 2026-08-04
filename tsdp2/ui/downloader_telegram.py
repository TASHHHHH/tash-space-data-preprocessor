from __future__ import annotations

from tsdp2.ui._common import *
from tsdp2.downloads.mast_importer import MASTCsvImporter

class DownloaderTelegramMixin:
    def _update_reference_mode_state(self):
        """Enable reference-mode picking only when pipeline includes WCS alignment."""
        try:
            needs_ref = self.pipeline_mode.get() in ("align", "mosaic_align")
            state = "readonly" if needs_ref else "disabled"
            if hasattr(self, "reference_mode_combo"):
                self.reference_mode_combo.configure(state=state)
            if not needs_ref:
                self.reference_mode.set("")
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)

    def _resource_text(self):
        """Small resource monitor. Uses psutil if available, otherwise stdlib fallback."""
        base = self.work_dir.get().strip() or self.download_dir.get().strip() or str(Path.home())
        try:
            p = Path(base)
            while not p.exists() and p.parent != p:
                p = p.parent
            base = str(p if p.exists() else Path.home())
        except EXPECTED_OPERATION_ERRORS:
            base = str(Path.home())
        try:
            import psutil
            cpu = psutil.cpu_percent(interval=None)
            vm = psutil.virtual_memory()
            du = shutil.disk_usage(base)
            return (f"CPU: {cpu:.0f}%\n"
                    f"RAM: {vm.used/1e9:.1f} / {vm.total/1e9:.1f} GB\n"
                    f"Disk free: {du.free/1e9:.1f} GB")
        except EXPECTED_OPERATION_ERRORS:
            try:
                du = shutil.disk_usage(base)
                return f"Disk free: {du.free/1e9:.1f} GB"
            except EXPECTED_OPERATION_ERRORS:
                return "Resources unavailable"

    def _pipeline_dashboard_text(self, step, text, pct):
        elapsed_s = int(time.time() - self.pipeline_start) if self.pipeline_start else 0
        eta = "unknown"
        if pct and pct > 0:
            eta_s = int(max(0, elapsed_s * (100 - pct) / max(pct, 1)))
            eta = f"{eta_s//60}m {eta_s%60:02d}s"
        elapsed = f"{elapsed_s//60}m {elapsed_s%60:02d}s"
        title = "🔭 TSDP"
        return (f"{title}\n\n"
                f"{text_progress_bar(pct or 0)} {int(pct or 0)}%\n\n"
                f"Current Stage\n{_html_escape(step.title() or 'Starting')}\n\n"
                f"Progress\n{_html_escape(text or 'Working')}\n\n"
                f"Reference\n{_html_escape(self._last_pipeline_reference)}\n\n"
                f"Resolution\n{_html_escape(self._last_pipeline_resolution)}\n\n"
                f"Elapsed\n{elapsed}\n\n"
                f"ETA\n{eta}\n\n"
                f"{_html_escape(self._resource_text())}")

    def _telegram_update_dashboard(self, step, text, pct, force=False):
        tg = self._get_tg()
        if not tg:
            return
        now = time.time()
        try:
            min_interval = max(0.5, float(self.tg_dashboard_interval.get()))
        except EXPECTED_OPERATION_ERRORS:
            min_interval = 3.0
        if not force and now - self._tg_last_dashboard_edit < min_interval:
            return
        self._tg_last_dashboard_edit = now
        dashboard = self._pipeline_dashboard_text(step, text, pct)
        def _worker():
            lock = getattr(self, "_tg_dashboard_lock", None)
            if lock is None:
                lock = threading.Lock()
                self._tg_dashboard_lock = lock
            with lock:
                if not self._tg_dashboard_message_id:
                    mid = tg.send_dashboard(dashboard)
                    if mid:
                        self._tg_dashboard_message_id = mid
                else:
                    ok = tg.edit_dashboard(self._tg_dashboard_message_id, dashboard)
                    if not ok:
                        # Message may have been deleted or become uneditable.
                        mid = tg.send_dashboard(dashboard)
                        if mid:
                            self._tg_dashboard_message_id = mid
        threading.Thread(target=_worker, daemon=True).start()

    def _save_tg_settings(self):
        enabled = bool(self.tg_enabled.get())
        token = self.tg_token.get()
        chat_id = self.tg_chat_id.get()
        # Plain Python snapshots for the background listener. Tk variables are
        # not safe to read from non-Tk threads.
        self._tg_runtime_enabled = enabled
        self._tg_runtime_token = token
        self._tg_runtime_chat_id = chat_id
        cfg = load_settings()
        cfg["tg_enabled"] = enabled
        cfg["tg_token"]   = token
        cfg["tg_chat_id"] = chat_id
        stored_securely = save_settings(cfg)
        if token and not stored_securely and not getattr(
                self, "_secret_storage_warning_shown", False):
            self._secret_storage_warning_shown = True
            messagebox.showwarning(
                "Telegram token storage",
                telegram_secret_storage_warning()
                or "The Telegram token could not be stored securely. Use "
                   "JWST_SUITE_TELEGRAM_TOKEN or install/configure keyring.")

    def _save_tg_extra_settings(self):
        cfg = load_settings()
        try:
            cfg["tg_send_fits"] = bool(self.tg_send_fits_setting.get())
            cfg["tg_silent_notifications"] = bool(self.tg_silent_notifications.get())
            cfg["tg_finish_buttons"] = bool(self.tg_finish_buttons.get())
            cfg["tg_auto_blink_gif"] = bool(self.tg_auto_blink_gif.get())
            cfg["tg_preview_max"] = _safe_int(self.tg_preview_max.get(), 30, 1, 100)
            cfg["tg_document_max_mb"] = _safe_int(self.tg_document_max_mb.get(), 49, 1, 49)
            cfg["tg_dashboard_interval"] = max(0.5, float(self.tg_dashboard_interval.get() or 3.0))
            cfg["tg_batch_dashboard_interval"] = max(0.5, float(self.tg_batch_dashboard_interval.get() or 2.0))
            cfg["tg_gif_max_frames"] = _safe_int(self.tg_gif_max_frames.get(), 8, 2, 30)
            cfg["tg_gif_frame_ms"] = _safe_int(self.tg_gif_frame_ms.get(), 700, 100, 5000)
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
        save_settings(cfg)

    def _save_download_settings(self):
        cfg = load_settings()
        cfg["aria2_path"] = self.aria2_path.get().strip()
        cfg["download_dir"] = self.download_dir.get().strip()
        save_settings(cfg)

    def _get_tg(self):
        """Return a ready TelegramNotifier or None."""
        if not self.tg_enabled.get():
            return None
        tok = self.tg_token.get().strip()
        cid = self.tg_chat_id.get().strip()
        return TelegramNotifier(tok, cid) if (tok and cid) else None

    def _notify_core(self, text: str, fits_path: str = None):
        """Fire-and-forget Telegram notification (background thread)."""
        tg = self._get_tg()
        if not tg:
            return
        fp = fits_path

        def _worker():
            tg.send(text)
            if fp:
                png = TelegramNotifier.fits_to_png(fp)
                if png:
                    tg.send_photo(png, caption=text)

        threading.Thread(target=_worker, daemon=True).start()

    def _tg_toggle_token(self):
        self._tg_token_visible = not self._tg_token_visible
        self._tg_token_entry.config(
            show="" if self._tg_token_visible else "•")

    def _tg_test(self):
        tg = self._get_tg()
        if not tg:
            messagebox.showwarning(
                "Not configured",
                "Enable Telegram and enter both Bot Token and Chat ID first.")
            return

        def _worker():
            ok = tg.send(
                "🔭 <b>TSDP</b> — test message\n"
                "Connection works! You'll receive pipeline notifications here.")
            post_to_ui(lambda: messagebox.showinfo(
                "Telegram test",
                "✅ Message sent successfully!" if ok
                else "❌ Failed — check your token and chat ID."))

        threading.Thread(target=_worker, daemon=True).start()

    def _tg_get_chat_id(self):
        tok = self.tg_token.get().strip()
        if not tok:
            messagebox.showwarning("No token",
                                   "Enter your bot token first.")
            return
        messagebox.showinfo(
            "Get Chat ID — step 1 of 2",
            "Open Telegram and send any message to your bot.\n\n"
            "Then click OK and we will fetch your Chat ID automatically.")

        def _worker():
            tg      = TelegramNotifier(tok, "")
            updates = tg.get_updates()
            if not updates:
                post_to_ui(lambda: messagebox.showerror(
                    "No messages found",
                    "Your bot received no messages.\n"
                    "Make sure you sent something to it, then try again."))
                return
            latest = updates[-1]
            chat   = (latest.get("message") or
                      latest.get("channel_post") or {}).get("chat", {})
            cid    = str(chat.get("id", ""))
            name   = (chat.get("first_name") or
                      chat.get("username") or
                      chat.get("title") or "—")
            if cid:
                post_to_ui(lambda: (
                    self.tg_chat_id.set(cid),
                    messagebox.showinfo(
                        "Chat ID found ✅",
                        f"Chat ID : {cid}\nName    : {name}\n\n"
                        "Saved automatically. Click Test to verify.")))
            else:
                post_to_ui(lambda: messagebox.showerror(
                    "No chat ID",
                    "Could not extract a chat ID.\n"
                    "Try sending another message and retrying."))

        threading.Thread(target=_worker, daemon=True).start()

    def _import_mast_csv(self):
        """Open a MAST search-results CSV and let the user select what to download."""
        path = filedialog.askopenfilename(
            title="Open MAST search results CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if not path:
            return
        try:
            rows = parse_mast_csv(path)
        except EXPECTED_OPERATION_ERRORS as exc:
            messagebox.showerror("CSV parse error", str(exc))
            return
        if not rows:
            messagebox.showwarning("Empty file",
                                   "No observations found in this CSV.")
            return

        def _on_add(urls):
            for url, name in urls:
                self.add_row(url=url, name=name)
            messagebox.showinfo(
                "Added to queue",
                f"{len(urls)} URL(s) added.\n\n"
                "Click «Start All» to begin downloading.")

        MASTCsvImporter(self, rows, on_add_cb=_on_add)

__all__ = [name for name in globals() if not name.startswith('__')]
