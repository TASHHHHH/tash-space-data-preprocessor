from __future__ import annotations

from tsdp2.ui._common import *

class RecoveryEventsMixin:
    def cancel(self):
        self.cancel_event.set()
        self.status_var.set("Cancellation requested; finishing the current chunk...")

    def _poll(self):
        try:
            while True:
                message = self.work_q.get_nowait()
                kind = message[0]
                if kind == "scan_done":
                    self._show_scan(message[1])
                elif kind == "progress":
                    self.progress["value"] = message[1]
                    self.status_var.set(message[2])
                elif kind == "recover_done":
                    out_path, shape, scale, method = message[1:]
                    self.progress["value"] = 100
                    self.status_var.set(
                        f"Recovered and verified: {out_path} ({shape[1]} x {shape[0]}, scale {scale:g}, {method})")
                    messagebox.showinfo("Recovery complete", self.status_var.get())
                elif kind == "copy_done":
                    self.progress["value"] = 100
                    self.status_var.set(f"Copied {message[2]} recovery file(s) to {message[1]}")
                elif kind == "hole_repair_done":
                    result = message[1]
                    self.progress["value"] = 100
                    self.status_var.set(
                        f"Fast hole repair complete: {result['repaired']:,} pixels filled in "
                        f"{result['boxes']} region(s); {result['remaining']:,} could not be covered. "
                        f"Saved: {result['path']}")
                    messagebox.showinfo("Fast hole repair complete", self.status_var.get())
                elif kind == "error":
                    self.status_var.set(f"Recovery error: {message[1]}")
                    print(f"[Recovery ERROR]\n{message[2]}", flush=True)
                    messagebox.showerror("Recovery failed", message[1])
        except queue.Empty:
            pass
        self.after(150, self._poll)

__all__ = [name for name in globals() if not name.startswith('__')]
