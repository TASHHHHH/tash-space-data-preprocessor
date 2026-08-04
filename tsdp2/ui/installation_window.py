from __future__ import annotations

from collections.abc import Callable
from typing import Mapping

from tsdp2._deps import *
from tsdp2.logging_config import LOGGER
from tsdp2.ui.theme import C_ACCENT, C_BG, C_FG_DIM, C_GOOD, C_WARN
from tsdp2.utilities.installation_check import (
    FAIL,
    PASS,
    WARNING,
    InstallationReport,
    check_installation,
)


class InstallationCheckWindow(tk.Toplevel):
    """Readable, offline installation and folder-permission report."""

    def __init__(
        self,
        master,
        *,
        settings_provider: Callable[[], Mapping[str, object]] | None = None,
        on_open_settings: Callable[[], None] | None = None,
    ):
        super().__init__(master)
        self._settings_provider = settings_provider
        self._on_open_settings = on_open_settings
        self._report: InstallationReport | None = None
        self._queue = queue.Queue()
        self._checking = False

        self.title("Check installation")
        self.geometry("920x650")
        self.minsize(760, 520)
        self.transient(master)
        self.configure(background=C_BG)
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        header = ttk.Frame(self, padding=(22, 18, 22, 10))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(
            header,
            text="Check installation",
            font=("Segoe UI Semibold", 18),
        ).grid(row=0, column=0, sticky="w")
        self._summary = ttk.Label(
            header,
            text="Ready to check the application and selected folders.",
            foreground=C_FG_DIM,
        )
        self._summary.grid(row=1, column=0, sticky="w", pady=(4, 0))

        table_frame = ttk.Frame(self, padding=(22, 4, 22, 10))
        table_frame.grid(row=1, column=0, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        self._table = ttk.Treeview(
            table_frame,
            columns=("status", "check", "detail"),
            show="headings",
            selectmode="browse",
        )
        self._table.heading("status", text="Status")
        self._table.heading("check", text="Check")
        self._table.heading("detail", text="Details")
        self._table.column("status", width=90, minwidth=80, stretch=False)
        self._table.column("check", width=175, minwidth=130, stretch=False)
        self._table.column("detail", width=570, minwidth=280, stretch=True)
        self._table.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(
            table_frame, orient="vertical", command=self._table.yview
        )
        scrollbar.grid(row=0, column=1, sticky="ns")
        self._table.configure(yscrollcommand=scrollbar.set)
        self._table.tag_configure(PASS, foreground=C_GOOD)
        self._table.tag_configure(WARNING, foreground=C_WARN)
        self._table.tag_configure(FAIL, foreground="#b42318")

        footer = ttk.Frame(self, padding=(22, 8, 22, 18))
        footer.grid(row=2, column=0, sticky="ew")
        self._refresh_button = ttk.Button(
            footer,
            text="Run again",
            style="Accent.TButton",
            command=self.refresh,
        )
        self._refresh_button.pack(side="left")
        ttk.Button(
            footer,
            text="Copy report",
            command=self.copy_report,
        ).pack(side="left", padx=7)
        if on_open_settings is not None:
            ttk.Button(
                footer,
                text="Open Settings",
                command=self._open_settings,
            ).pack(side="left", padx=7)
        ttk.Button(footer, text="Close", command=self.destroy).pack(side="right")

        self.after_idle(self.refresh)

    def refresh(self) -> None:
        if self._checking:
            return
        self._checking = True
        self._refresh_button.configure(state="disabled")
        self._summary.configure(
            text="Checking the application and selected folders...",
            foreground=C_FG_DIM,
        )
        settings = (
            dict(self._settings_provider())
            if self._settings_provider is not None
            else None
        )

        def worker() -> None:
            try:
                self._queue.put(("report", check_installation(settings)))
            except EXPECTED_OPERATION_ERRORS as exc:
                LOGGER.exception("Installation check failed")
                self._queue.put(("error", f"{type(exc).__name__}: {exc}"))

        threading.Thread(target=worker, daemon=True).start()
        self.after(100, self._poll)

    def _poll(self) -> None:
        try:
            kind, payload = self._queue.get_nowait()
        except queue.Empty:
            if self._checking and self.winfo_exists():
                self.after(100, self._poll)
            return

        self._checking = False
        self._refresh_button.configure(state="normal")
        if kind == "error":
            self._summary.configure(text=str(payload), foreground="#b42318")
            return
        self._render(payload)

    def _render(self, report: InstallationReport) -> None:
        self._report = report
        for item_id in self._table.get_children():
            self._table.delete(item_id)
        status_labels = {PASS: "Ready", WARNING: "Check", FAIL: "Missing"}
        for result in report.results:
            self._table.insert(
                "",
                "end",
                values=(
                    status_labels.get(result.status, result.status.title()),
                    result.name,
                    result.detail,
                ),
                tags=(result.status,),
            )

        if report.ready:
            summary = "Ready to run"
            if report.warning_count:
                summary += f" - {report.warning_count} optional item(s) need attention"
            color = C_GOOD
        else:
            summary = f"{report.failure_count} required item(s) need attention"
            color = "#b42318"
        self._summary.configure(text=summary, foreground=color)

    def copy_report(self) -> None:
        if self._report is None:
            self._summary.configure(
                text="Run the check before copying a report.",
                foreground=C_FG_DIM,
            )
            return
        self.clipboard_clear()
        self.clipboard_append(self._report.to_text())
        self.update_idletasks()
        self._summary.configure(
            text="Report copied to the clipboard",
            foreground=C_ACCENT,
        )

    def _open_settings(self) -> None:
        callback = self._on_open_settings
        self.destroy()
        if callback is not None:
            callback()


__all__ = ["InstallationCheckWindow"]