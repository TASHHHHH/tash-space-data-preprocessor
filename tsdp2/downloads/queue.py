from __future__ import annotations

from tsdp2._deps import *
from tsdp2.logging_config import LOGGER
from tsdp2.downloads.aria2 import DownloadItem, guess_name_from_url

class DownloadRow(ttk.Frame):
    def __init__(self, master, index: int, remove_callback):
        super().__init__(master)
        self.index = index
        self.remove_callback = remove_callback
        self.url_var = tk.StringVar()
        self.name_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Idle")
        self.meta_var = tk.StringVar(value="")
        self.pct_var = tk.IntVar(value=0)
        self.log_lines = []
        self.worker = None
        self.last_file_path = ""

        self.columnconfigure(1, weight=1)

        ttk.Label(self, text=f"#{index}", width=4).grid(row=0, column=0, padx=6, pady=6, sticky="w")
        ttk.Entry(self, textvariable=self.url_var).grid(row=0, column=1, padx=6, pady=6, sticky="ew")
        ttk.Entry(self, textvariable=self.name_var, width=26).grid(row=0, column=2, padx=6, pady=6, sticky="ew")
        ttk.Button(self, text="Guess Name", command=self.guess_name).grid(row=0, column=3, padx=6, pady=6)
        ttk.Button(self, text="Remove", command=self.remove).grid(row=0, column=4, padx=6, pady=6)

        self.pb = ttk.Progressbar(self, variable=self.pct_var, maximum=100)
        self.pb.grid(row=1, column=1, columnspan=2, padx=6, pady=(0, 6), sticky="ew")
        ttk.Label(self, textvariable=self.status_var, width=18).grid(row=1, column=3, padx=6, pady=(0, 6), sticky="w")
        ttk.Label(self, textvariable=self.meta_var, width=34).grid(row=1, column=4, padx=6, pady=(0, 6), sticky="w")

    def remove(self):
        self.remove_callback(self)

    def guess_name(self):
        guessed = guess_name_from_url(self.url_var.get())
        if guessed:
            self.name_var.set(guessed)

    def get_item(self):
        return DownloadItem(self.url_var.get(), self.name_var.get())

    def append_log(self, line: str):
        self.log_lines.append(line)
        self.log_lines = self.log_lines[-300:]

__all__ = [name for name in globals() if not name.startswith('__')]
