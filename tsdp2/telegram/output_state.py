from __future__ import annotations

from tsdp2._deps import *
from tsdp2.logging_config import LOGGER

_TG_OUTPUT_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}


_TG_OUTPUT_FITS_EXTS = {".fits", ".fit", ".fts"}


_TG_OUTPUT_LOG_EXTS = {".log", ".txt"}


def _tg_output_remember_path(store, key, path):
    try:
        if not path:
            return
        p = os.path.abspath(str(path))
        if os.path.exists(p) and p not in store.setdefault(key, []):
            store[key].append(p)
    except EXPECTED_OPERATION_ERRORS:
        LOGGER.debug("Best-effort operation failed", exc_info=True)


def _tg_output_remember_dir(store, path):
    try:
        if not path:
            return
        p = os.path.abspath(str(path))
        if os.path.isfile(p):
            p = os.path.dirname(p)
        if os.path.isdir(p) and p not in store.setdefault("dirs", []):
            store["dirs"].append(p)
    except EXPECTED_OPERATION_ERRORS:
        LOGGER.debug("Best-effort operation failed", exc_info=True)


def _tg_record_outputs(self, *, fits=None, images=None, logs=None, csvs=None, dirs=None, source="", label="pipeline"):
    store = getattr(self, "_tg_recent_outputs", None)
    if not isinstance(store, dict):
        store = {"fits": [], "images": [], "logs": [], "dirs": [], "source": "", "updated": 0.0, "label": "none"}
        self._tg_recent_outputs = store
    for p in fits or []:
        _tg_output_remember_path(store, "fits", p)
        _tg_output_remember_dir(store, p)
    for p in images or []:
        _tg_output_remember_path(store, "images", p)
        _tg_output_remember_dir(store, p)
    for p in logs or []:
        _tg_output_remember_path(store, "logs", p)
        _tg_output_remember_dir(store, p)
    for p in csvs or []:
        _tg_output_remember_path(store, "csvs", p)
        _tg_output_remember_dir(store, p)
    for d in dirs or []:
        _tg_output_remember_dir(store, d)
    if source:
        store["source"] = str(source)
        _tg_output_remember_dir(store, source)
    store["label"] = label or store.get("label") or "pipeline"
    store["updated"] = time.time()


def _tg_output_existing(paths):
    out = []
    seen = set()
    for p in paths or []:
        try:
            ap = os.path.abspath(str(p))
            if ap not in seen and os.path.isfile(ap):
                out.append(ap)
                seen.add(ap)
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
    return out


def _tg_output_recent_files_in_dir(root, max_files=300, newest_within_days=21):
    files = []
    try:
        root = os.path.abspath(str(root))
        if not os.path.isdir(root):
            return []
        cutoff = time.time() - newest_within_days * 86400
        for dirpath, dirnames, filenames in os.walk(root):
            # Avoid walking dependency/cache trash and huge raw download junk when possible.
            base = os.path.basename(dirpath).lower()
            if base in {"__pycache__", ".git", ".venv", "venv", "env", "cache"}:
                dirnames[:] = []
                continue
            depth = len(os.path.relpath(dirpath, root).split(os.sep)) if os.path.relpath(dirpath, root) != "." else 0
            if depth > 4:
                dirnames[:] = []
                continue
            for name in filenames:
                ext = os.path.splitext(name)[1].lower()
                if ext not in (_TG_OUTPUT_IMAGE_EXTS | _TG_OUTPUT_FITS_EXTS | _TG_OUTPUT_LOG_EXTS | {".csv"}):
                    continue
                p = os.path.join(dirpath, name)
                try:
                    mt = os.path.getmtime(p)
                except EXPECTED_OPERATION_ERRORS:
                    mt = 0
                if mt and mt < cutoff:
                    continue
                files.append((mt, os.path.abspath(p)))
                if len(files) >= max_files:
                    break
            if len(files) >= max_files:
                break
    except EXPECTED_OPERATION_ERRORS:
        return []
    files.sort(reverse=True)
    return [p for _mt, p in files]


def _tg_output_candidate_dirs(self):
    dirs = []
    store = getattr(self, "_tg_recent_outputs", {}) or {}
    dirs.extend(store.get("dirs", []) or [])
    src = store.get("source") or getattr(self, "_last_pipeline_source", "")
    if src:
        dirs.append(src)
        if os.path.isfile(str(src)):
            dirs.append(os.path.dirname(str(src)))
    try:
        if self.aligner_ref is not None:
            dirs.append(self.aligner_ref.output_dir.get().strip())
    except EXPECTED_OPERATION_ERRORS:
        LOGGER.debug("Best-effort operation failed", exc_info=True)
    try:
        if self.mosaic_tab_ref is not None:
            dirs.append(self.mosaic_tab_ref.out_dir.get().strip())
    except EXPECTED_OPERATION_ERRORS:
        LOGGER.debug("Best-effort operation failed", exc_info=True)
    try:
        dirs.append(self.work_dir.get().strip())
    except EXPECTED_OPERATION_ERRORS:
        LOGGER.debug("Best-effort operation failed", exc_info=True)
    # Deduplicate valid dirs only.
    out = []
    seen = set()
    for d in dirs:
        try:
            if not d:
                continue
            d = os.path.abspath(str(d))
            if os.path.isfile(d):
                d = os.path.dirname(d)
            if os.path.isdir(d) and d not in seen:
                out.append(d)
                seen.add(d)
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
    return out


def _tg_collect_outputs(self):
    store = getattr(self, "_tg_recent_outputs", {}) or {}
    fits = _tg_output_existing(store.get("fits", []))
    images = _tg_output_existing(store.get("images", []))
    logs = _tg_output_existing(store.get("logs", []))
    csvs = _tg_output_existing(store.get("csvs", []))

    # Fallback scan only if tracked outputs are missing. This is intentionally
    # conservative so /fits does not try to send thousands of raw downloads.
    if not (fits or images or logs or csvs):
        for d in _tg_output_candidate_dirs(self)[:5]:
            for p in _tg_output_recent_files_in_dir(d):
                ext = os.path.splitext(p)[1].lower()
                name = os.path.basename(p).lower()
                if ext in _TG_OUTPUT_FITS_EXTS:
                    # Prefer files that look like products of the suite, but do
                    # not fully exclude direct download-only outputs.
                    fits.append(p)
                elif ext in _TG_OUTPUT_IMAGE_EXTS:
                    images.append(p)
                elif ext == ".csv":
                    csvs.append(p)
                elif ext in _TG_OUTPUT_LOG_EXTS and ("log" in name or "report" in name or "run" in name):
                    logs.append(p)
            if fits or images or logs or csvs:
                break

    def uniq(seq):
        out, seen = [], set()
        for p in seq:
            ap = os.path.abspath(str(p))
            if ap not in seen and os.path.isfile(ap):
                out.append(ap)
                seen.add(ap)
        return sorted(out, key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0, reverse=True)

    return {"fits": uniq(fits), "images": uniq(images), "logs": uniq(logs), "csv": uniq(csvs)}


def empty_output_state() -> dict[str, object]:
    """Create isolated output-delivery state for one application instance."""
    return {
        "fits": [],
        "images": [],
        "logs": [],
        "csvs": [],
        "dirs": [],
        "source": "",
        "updated": 0.0,
        "label": "none",
    }


__all__ = [name for name in globals() if not name.startswith('__')]
