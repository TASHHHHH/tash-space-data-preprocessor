from __future__ import annotations

from tsdp2._deps import *
from tsdp2.logging_config import LOGGER
from tsdp2.config import _safe_int, resolve_executable_path

class DownloadItem:
    def __init__(self, url: str = "", out_name: str = ""):
        self.url = url.strip()
        self.out_name = out_name.strip()


class Aria2DownloadWorker(threading.Thread):
    def __init__(self, task_id: int, aria2_path: str, download_dir: str, item: DownloadItem,
                 connections: int, splits: int, resume: bool, disable_allocation: bool,
                 user_agent: str, extra_args: str, status_queue: queue.Queue):
        super().__init__(daemon=True)
        self.task_id = task_id
        self.aria2_path = aria2_path
        self.download_dir = download_dir
        self.item = item
        self.connections = _safe_int(connections, default=16, lo=1, hi=16)
        self.splits = _safe_int(splits, default=16, lo=1, hi=32)
        self.resume = bool(resume)
        self.disable_allocation = bool(disable_allocation)
        self.user_agent = user_agent.strip()
        self.extra_args = extra_args.strip()
        self.status_queue = status_queue
        self.proc = None
        self.stop_requested = False

    def stop(self):
        self.stop_requested = True
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except EXPECTED_OPERATION_ERRORS:
                LOGGER.debug("Best-effort operation failed", exc_info=True)

    def build_command(self):
        cmd = [resolve_executable_path(self.aria2_path)]
        cmd += ["-x", str(self.connections)]
        cmd += ["-s", str(self.splits)]
        cmd += ["-d", self.download_dir]
        if self.resume:
            cmd += ["-c"]
        if self.disable_allocation:
            cmd += ["--file-allocation=none"]
        if self.user_agent:
            cmd += ["--user-agent", self.user_agent]
        if self.item.out_name:
            cmd += ["-o", self.item.out_name]
        if self.extra_args:
            cmd += shlex.split(self.extra_args)
        cmd += [self.item.url]
        return cmd

    def expected_output_path(self) -> str:
        """Best-effort output file path for aria2.

        aria2 reports success with return code 0, but older code only trusted a
        parsed FILE line. Normal aria2 output says "Download complete: ...",
        so Telegram jobs were counted as failed even when the FITS existed.
        """
        name = (self.item.out_name or "").strip() or guess_name_from_url(self.item.url)
        if not name:
            return ""
        return os.path.abspath(os.path.join(self.download_dir, name))

    def send(self, kind: str, **payload):
        payload["task_id"] = self.task_id
        payload["kind"] = kind
        self.status_queue.put(payload)

    def run(self):
        if not self.item.url:
            self.send("error", message="Empty URL")
            return

        cmd = self.build_command()
        self.send("started", command=subprocess.list2cmdline(cmd))

        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NO_WINDOW

        try:
            self.proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True,
                creationflags=creationflags,
            )
        except FileNotFoundError:
            self.send("error", message=f"aria2 executable not found: {self.aria2_path}")
            return
        except EXPECTED_OPERATION_ERRORS as e:
            self.send("error", message=str(e))
            return

        completed_path = ""
        for raw_line in self.proc.stdout:
            if self.stop_requested:
                break
            line = raw_line.strip()
            if not line:
                continue
            self.send("log", line=line)
            parsed = parse_aria2_progress(line)
            if parsed:
                if parsed.get("file_path"):
                    completed_path = parsed.get("file_path", "")
                self.send("progress", **parsed)

        rc = self.proc.wait()
        if self.stop_requested:
            self.send("stopped", message="Stopped by user")
        elif rc == 0:
            file_path = completed_path or self.expected_output_path()
            if file_path:
                self.send("progress", file_path=file_path)
            self.send("done", message="Download completed", file_path=file_path)
        else:
            self.send("error", message=f"aria2 exited with code {rc}")


PROGRESS_RE = re.compile(
    r"\[(?P<gid>#[^\s]+)\s+"
    r"(?P<done>[0-9.]+(?:KiB|MiB|GiB|B))/"
    r"(?P<total>[0-9.]+(?:KiB|MiB|GiB|B)|0B)"
    r"\((?P<pct>\d+)%\)\s+CN:(?P<cn>\d+)\s+DL:(?P<speed>[0-9.]+(?:KiB|MiB|GiB|B))"
    r"(?:\s+ETA:(?P<eta>[^\]]+))?\]"
)


FILE_RE = re.compile(r"(?:FILE:\s+|Download complete:\s+)(?P<path>.+)$", re.IGNORECASE)


def parse_aria2_progress(line: str):
    m = PROGRESS_RE.search(line)
    if m:
        return {
            "gid": m.group("gid"),
            "done": m.group("done"),
            "total": m.group("total"),
            "pct": int(m.group("pct")),
            "connections": int(m.group("cn")),
            "speed": m.group("speed"),
            "eta": (m.group("eta") or "").strip(),
        }

    m2 = FILE_RE.search(line)
    if m2:
        return {"file_path": m2.group("path").strip()}

    return None


def guess_name_from_url(url: str) -> str:
    """Guess output filename from normal URLs and MAST download URLs.

    Uses URL/URI semantics, not pathlib, because Windows treats ``mast:`` like
    a drive prefix and can guess the wrong name.
    """
    import posixpath as _posixpath

    def _clean_candidate(value: str) -> str:
        value = unquote(str(value or "")).strip().strip('"')
        if not value:
            return ""
        value = value.replace("\\", "/")
        name = _posixpath.basename(value.split("?", 1)[0].split("#", 1)[0])
        name = name.strip()
        if name and name.lower() not in ("file", "download"):
            return name
        return ""

    try:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query or "")
        for key in ("uri", "filename", "file", "path"):
            vals = qs.get(key) or []
            if vals:
                candidate = _clean_candidate(vals[0])
                if candidate:
                    return candidate
        candidate = _clean_candidate(parsed.path)
        if candidate:
            return candidate
    except EXPECTED_OPERATION_ERRORS:
        LOGGER.debug("Best-effort operation failed", exc_info=True)
    return ""

__all__ = [name for name in globals() if not name.startswith('__')]
