from __future__ import annotations

from tsdp2.ui._common import *

class RecoveryScanningMixin:
    def _classify_file(self, path):
        name = os.path.basename(path).lower()
        if name == _RECOVERY_MANIFEST_NAME:
            return "manifest", "Exact geometry/WCS available"
        if re.search(r"_(median|mean|average|feather)_out\.f32$", name):
            return "combined float32", "Best recovery candidate"
        if re.search(r"align_.+_data\.f32$", name):
            return "aligned float32", "Recoverable with the reference FITS"
        if re.search(r"_tile_\d+\.f32$", name):
            return "reprojected tile", "Intermediate tile; keep until recovery succeeds"
        if name.endswith("_fp.f32") or "footprint" in name:
            return "footprint", "Mask/footprint scratch"
        if name.endswith((".fits", ".fit", ".fts", ".fits.fz", ".fit.fz")):
            return "FITS", "Validate before use"
        if name.endswith(self._PARTIAL_SUFFIXES):
            return "partial write/download", "May be resumable or diagnostic"
        if name.endswith(".f32"):
            return "float32 scratch", "Raw geometry required"
        return "other", "Auxiliary temp file"

    def _scan_root(self, root):
        result = []
        base = os.path.basename(os.path.normpath(root)).lower()
        session_dirs = []
        if base.startswith(self._SESSION_PREFIXES):
            session_dirs.append(root)
        else:
            try:
                with os.scandir(root) as entries:
                    for entry in entries:
                        if entry.is_dir(follow_symlinks=False) and entry.name.lower().startswith(self._SESSION_PREFIXES):
                            session_dirs.append(entry.path)
                        elif entry.is_file(follow_symlinks=False) and (
                                entry.name.lower().endswith(self._PARTIAL_SUFFIXES) or
                                entry.name.lower().endswith((".fits", ".fit", ".fts", ".fits.fz", ".f32")) or
                                entry.name.lower() == _RECOVERY_MANIFEST_NAME):
                            stat = entry.stat(follow_symlinks=False)
                            kind, status = self._classify_file(entry.path)
                            result.append({"path": entry.path, "parent": root, "kind": kind,
                                           "status": status, "size": stat.st_size, "session": False})
            except EXPECTED_OPERATION_ERRORS as exc:
                return [{"path": root, "parent": None, "kind": "unreadable folder",
                         "status": str(exc), "size": None, "session": True}]

        for session in session_dirs:
            result.append({"path": session, "parent": None, "kind": "recovery session",
                           "status": "Scan complete", "size": None, "session": True})
            try:
                with os.scandir(session) as entries:
                    for entry in entries:
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        stat = entry.stat(follow_symlinks=False)
                        kind, status = self._classify_file(entry.path)
                        result.append({"path": entry.path, "parent": session, "kind": kind,
                                       "status": status, "size": stat.st_size, "session": False})
            except EXPECTED_OPERATION_ERRORS as exc:
                result[-1]["status"] = f"Drive/read error: {exc}"
        return result

    def refresh(self):
        if self.worker and self.worker.is_alive():
            return
        if not self.roots:
            self.status_var.set("Add a temp folder first.")
            return
        self._set_busy("Scanning temp folders without modifying them...")

        def work():
            found = []
            for root in list(self.roots):
                found.extend(self._scan_root(root))
            self.work_q.put(("scan_done", found))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _show_scan(self, found):
        self.items.clear()
        self.tree.delete(*self.tree.get_children())
        parents = {}
        count_candidates = 0
        for record in found:
            if record["session"]:
                iid = self.tree.insert("", "end", text=os.path.basename(record["path"]) or record["path"],
                    values=(record["kind"], self._size_text(record["size"]), record["status"], record["path"]),
                    open=True)
                parents[record["path"]] = iid
            else:
                parent_iid = parents.get(record.get("parent"), "")
                iid = self.tree.insert(parent_iid, "end", text=os.path.basename(record["path"]),
                    values=(record["kind"], self._size_text(record["size"]), record["status"], record["path"]))
                if record["kind"] == "combined float32":
                    count_candidates += 1
            self.items[iid] = record
        self.progress["value"] = 100
        self.status_var.set(
            f"Found {len(found)} item(s), including {count_candidates} combined mosaic recovery candidate(s).")

__all__ = [name for name in globals() if not name.startswith('__')]
