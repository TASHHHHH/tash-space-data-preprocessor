from __future__ import annotations

from tsdp2.ui._common import *

class DownloaderRowsMixin:
    def pick_work_dir(self):
        p = filedialog.askdirectory(title="Select work / unzip directory")
        if p:
            self.work_dir.set(p)

    def pick_aria2(self):
        current = self.aria2_path.get().strip()
        initialdir = str(Path(current).parent) if current else str(Path.home())
        p = filedialog.askopenfilename(
            title="Select aria2 executable",
            initialdir=initialdir if Path(initialdir).exists() else str(Path.home()),
            filetypes=[("aria2 executable", "aria2c.exe"),
                       ("Executable", "*.exe"),
                       ("All files", "*.*")])
        if p:
            self.aria2_path.set(p)
            self._save_download_settings()

    def pick_download_dir(self):
        current = self.download_dir.get().strip()
        p = filedialog.askdirectory(
            title="Select download directory",
            initialdir=current if current and Path(current).exists() else str(Path.home()))
        if p:
            self.download_dir.set(p)
            self._save_download_settings()

    def add_row(self, url="", name=""):
        row = DownloadRow(self.rows_frame, len(self.rows) + 1,
                          self.remove_row)
        row.grid(sticky="ew", pady=4)
        row.url_var.set(url)
        row.name_var.set(name)
        row.bind("<Button-1>", lambda _e, r=row: self.select_row(r))
        for child in row.winfo_children():
            child.bind("<Button-1>", lambda _e, r=row: self.select_row(r))
        self.rows.append(row)
        self.select_row(row)

    def remove_row(self, row):
        if row.worker and row.worker.is_alive():
            if not messagebox.askyesno("Running download",
                                       "This download is active. Remove anyway?"):
                return
            row.worker.stop()
        self.rows.remove(row)
        row.destroy()
        self.selected_row = self.rows[0] if self.rows else None

    def select_row(self, row):
        self.selected_row = row
        self.log_text.delete("1.0", tk.END)
        self.log_text.insert(tk.END, "\n".join(row.log_lines[-200:]))
        self.log_text.see(tk.END)

    def append_dl_log(self, line):
        if self.selected_row:
            self.log_text.insert(tk.END, line + "\n")
            self.log_text.see(tk.END)

    def validate(self):
        aria2 = self.aria2_path.get().strip()
        if not aria2:
            messagebox.showerror("Missing aria2",
                                 "Set the aria2 executable path first.")
            return False
        if not path_exists_or_on_path(aria2):
            messagebox.showerror("aria2 not found",
                                 f"Could not find executable or PATH command:\n{aria2}")
            return False
        dl_dir = self.download_dir.get().strip()
        if not dl_dir:
            messagebox.showerror("Missing directory",
                                 "Choose a download directory.")
            return False
        os.makedirs(dl_dir, exist_ok=True)
        if not [r for r in self.rows if r.url_var.get().strip()]:
            messagebox.showerror("No URLs", "Add at least one URL.")
            return False
        return True

    def export_input_file(self):
        urls = []
        for row in self.rows:
            item = row.get_item()
            if not item.url:
                continue
            urls.append(item.url)
            if item.out_name:
                urls.append(f"  out={item.out_name}")
            urls.append("")
        if not urls:
            messagebox.showerror("No URLs", "Add at least one URL first.")
            return
        path = filedialog.asksaveasfilename(
            title="Save aria2 input file",
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(urls))
        messagebox.showinfo(
            "Saved",
            f"Saved:\n{path}\n\nRun with:\naria2c -i \"{path}\"")

__all__ = [name for name in globals() if not name.startswith('__')]
