from __future__ import annotations

from tsdp2.ui._common import *

class MosaicEventsMixin:
    def _poll(self):
        try:
            while True:
                msg = self.mosaic_q.get_nowait()
                self._handle(msg)
        except queue.Empty:
            pass
        self.after(120, self._poll)

    def _handle(self, msg):
        step = msg.get("step", "")
        text = msg.get("message", "")
        pct  = msg.get("pct")
        elapsed = (f"  ·  {time.time() - self.mosaic_start:.0f}s"
                   if self.mosaic_start else "")
        if pct is not None:
            self._prog["value"] = pct
        self._stat.config(text=f"{text}{elapsed}")
        print(f"[Mosaic:{step}] {text}", flush=True)

        if step == "clean_progress":
            return
        if step == "clean_done":
            self._prog["value"] = 100
            self._run_btn.configure(state="normal")
            self._draft_btn.configure(state="normal")
            self._repair_btn.configure(state="normal")
            self._clean_btn.configure(state="normal")
            self._cancel_btn.configure(state="disabled")
            result = msg.get("result") or {}
            path = result.get("output_path", "")
            messagebox.showinfo(
                "High-resolution cleanup complete",
                "Saved the cleaned WCS-preserving FITS copy:\n\n" + str(path),
                parent=self)
            return
        if step in ("clean_error", "clean_cancelled"):
            self._run_btn.configure(state="normal")
            self._draft_btn.configure(state="normal")
            self._repair_btn.configure(state="normal")
            self._clean_btn.configure(state="normal")
            self._cancel_btn.configure(state="disabled")
            self._prog["value"] = 0
            if step == "clean_error":
                messagebox.showerror("Background cleanup failed", text, parent=self)
                if msg.get("tb"):
                    print(f"[Background cleanup traceback]\n{msg['tb']}", flush=True)
            return

        if step == "repair_progress":
            return
        if step == "repair_done":
            self._prog["value"] = 100
            self._run_btn.configure(state="normal")
            self._draft_btn.configure(state="normal")
            self._repair_btn.configure(state="normal")
            self._clean_btn.configure(state="normal")
            self._cancel_btn.configure(state="disabled")
            result = msg.get("result") or {}
            path = result.get("output_path", "")
            messagebox.showinfo(
                "Seam repair complete",
                "Saved the repaired full-resolution FITS:\n\n" + str(path),
                parent=self)
            return
        if step in ("repair_error", "repair_cancelled"):
            self._run_btn.configure(state="normal")
            self._draft_btn.configure(state="normal")
            self._repair_btn.configure(state="normal")
            self._clean_btn.configure(state="normal")
            self._cancel_btn.configure(state="disabled")
            self._prog["value"] = 0
            if step == "repair_error":
                messagebox.showerror("Seam repair failed", text, parent=self)
                if msg.get("tb"):
                    print(f"[Seam repair traceback]\n{msg['tb']}", flush=True)
            return

        tg_direct = getattr(self, "telegram_ref", None)
        if tg_direct is not None and hasattr(tg_direct, "_telegram_update_dashboard"):
            try:
                dash_pct = 100 if step == "done" else (pct if pct is not None else 0)
                tg_direct._telegram_update_dashboard(
                    "mosaic" if step != "error" else "error",
                    text or step or "Working", dash_pct, force=(step in ("done", "error", "cancelled")))
            except EXPECTED_OPERATION_ERRORS as exc:
                print(f"[Direct mosaic Telegram dashboard update failed] {exc}", flush=True)

        if step == "done":
            self._prog["value"] = 100
            self._run_btn.config(state="normal")
            self._draft_btn.config(state="normal")
            self._repair_btn.config(state="normal")
            self._clean_btn.config(state="normal")
            self._cancel_btn.config(state="disabled")
            out_paths = msg.get("out_paths", {})
            lines = [f"Mosaic complete — {len(out_paths)} output(s):\n"]
            for code, path in sorted(out_paths.items()):
                lines.append(f"  {code}: {Path(path).name}")
            done_title = "Fast draft complete" if getattr(self, "_run_kind", "final") == "draft" else "Done"
            messagebox.showinfo(done_title, "\n".join(lines))
            tg_direct = getattr(self, "telegram_ref", None)
            if tg_direct is not None and hasattr(tg_direct, "_notify"):
                try:
                    filters = ", ".join(sorted(out_paths.keys()))
                    first_path = next(iter(out_paths.values()), None) if out_paths else None
                    dur = time.time() - (self.mosaic_start or time.time())
                    if hasattr(tg_direct, "_telegram_update_dashboard"):
                        try:
                            tg_direct._last_pipeline_resolution = (
                                fits_resolution_text(first_path) if first_path else "unknown")
                            tg_direct._telegram_update_dashboard(
                                "mosaic", f"Mosaic complete - {len(out_paths)} output(s)",
                                100, force=True)
                        except EXPECTED_OPERATION_ERRORS as dash_exc:
                            print(f"[Direct mosaic Telegram final dashboard failed] {dash_exc}", flush=True)
                    tg_direct._notify(
                        f"🧩 <b>TSDP - Mosaic complete</b>\n"
                        f"✅ {len(out_paths)} mosaic(s): {_html_escape(filters)}\n"
                        f"⏱ {dur:.0f}s",
                        fits_path=first_path)
                    try:
                        if hasattr(tg_direct, "_send_finish_buttons"):
                            tg_direct._send_finish_buttons()
                    except EXPECTED_OPERATION_ERRORS:
                        LOGGER.debug("Best-effort operation failed", exc_info=True)
                except EXPECTED_OPERATION_ERRORS as exc:
                    print(f"[Direct mosaic Telegram complete failed] {exc}", flush=True)
            if (out_paths and self.auto_send.get()
                    and self.aligner_ref is not None):
                if self.notebook_ref:
                    self.notebook_ref.select(self.aligner_ref)
                self.after(80, lambda mp=out_paths:
                           self.aligner_ref.auto_run_from_pipeline(mp))

        elif step in ("error", "cancelled"):
            self._run_btn.config(state="normal")
            self._draft_btn.config(state="normal")
            self._repair_btn.config(state="normal")
            self._clean_btn.config(state="normal")
            self._cancel_btn.config(state="disabled")
            self._prog["value"] = 0
            if step == "error":
                tg_direct = getattr(self, "telegram_ref", None)
                if tg_direct is not None and hasattr(tg_direct, "_notify"):
                    try:
                        tg_direct._notify(
                            f"❌ <b>TSDP - Mosaic error</b>\n"
                            f"<code>{_html_escape(str(text)[:800])}</code>")
                    except EXPECTED_OPERATION_ERRORS as exc:
                        print(f"[Direct mosaic Telegram error notify failed] {exc}", flush=True)
                messagebox.showerror("Mosaic error", text)
                tb = msg.get("tb", "")
                if tb:
                    print(f"[Mosaic traceback]\n{tb}", flush=True)

__all__ = [name for name in globals() if not name.startswith('__')]
