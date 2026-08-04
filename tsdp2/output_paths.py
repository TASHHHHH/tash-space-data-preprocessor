from __future__ import annotations

from tsdp2._deps import *
from tsdp2.logging_config import LOGGER
from tsdp2.models import RunState

def safe_filename(name: str) -> str:
    """Return a Windows-safe filename component while preserving Unicode."""
    bad = '<>:"/\\|?*'
    out = "".join(
        "_" if c in bad or ord(c) < 32 else c
        for c in str(name or "").strip()
    )
    out = out.replace(" ", "_")
    out = out.strip(". ")
    if out.upper() in {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        out = f"_{out}"
    return out[:180] if out else "unnamed"


def _unique_path(path: Path) -> Path:
    """Return *path* or a numbered sibling without overwriting a file."""
    if not path.exists():
        return path
    suffixes = "".join(path.suffixes)
    stem = path.name[:-len(suffixes)] if suffixes else path.name
    for number in range(2, 10_000):
        candidate = path.with_name(f"{stem}_{number:02d}{suffixes}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Could not allocate a unique path beside {path}")


@dataclass(frozen=True)
class JobPaths:
    """Central, lazy path manager for one isolated processing job.

    Only the job root is created by :meth:`create`. Category directories are
    created when a caller asks for a concrete output path, keeping unused
    folders out of completed jobs.
    """

    root: Path

    @classmethod
    def create(cls, output_root: os.PathLike | str, job_name: str,
               collision: str = "number") -> "JobPaths":
        base = Path(output_root).expanduser()
        base.mkdir(parents=True, exist_ok=True)
        name = safe_filename(job_name)
        candidate = base / name
        policy = str(collision or "number").lower()
        if candidate.exists():
            if policy == "resume":
                return cls(candidate.resolve())
            if policy == "error":
                raise FileExistsError(f"Job folder already exists: {candidate}")
            if policy != "number":
                raise ValueError("collision must be 'number', 'resume', or 'error'")
            for number in range(2, 10_000):
                numbered = base / f"{name}_{number:02d}"
                if not numbered.exists():
                    candidate = numbered
                    break
            else:
                raise FileExistsError(f"No free numbered job folder for {name}")
        candidate.mkdir(parents=False, exist_ok=False)
        return cls(candidate.resolve())

    @property
    def final(self) -> Path:
        return self.root / "final"

    @property
    def aligned(self) -> Path:
        return self.final / "aligned"

    @property
    def mosaics(self) -> Path:
        return self.final / "mosaics"

    @property
    def normalized(self) -> Path:
        return self.final / "normalized"

    @property
    def previews(self) -> Path:
        return self.root / "previews"

    @property
    def preview_png_dir(self) -> Path:
        return self.previews / "png"

    @property
    def preview_jpeg_dir(self) -> Path:
        return self.previews / "jpeg"

    @property
    def diagnostics(self) -> Path:
        return self.root / "diagnostics"

    @property
    def intermediate(self) -> Path:
        return self.root / "intermediate"

    @property
    def recovery(self) -> Path:
        return self.root / "recovery"

    @property
    def metadata(self) -> Path:
        return self.root / "metadata"

    def _output(self, directory: Path, filename: str,
                suffix: Optional[str] = None) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        leaf = safe_filename(Path(str(filename or "unnamed")).name)
        path = directory / leaf
        if suffix and path.suffix.lower() != suffix.lower():
            path = path.with_suffix(suffix)
        return _unique_path(path)

    def _filtered_output(self, directory: Path, filter_name: str,
                         filename: str, suffix: str = ".fits") -> Path:
        group = directory / safe_filename(filter_name or "UNKNOWN")
        return self._output(group, filename, suffix)

    def final_aligned_fits(self, filter_name: str, filename: str) -> Path:
        return self._filtered_output(self.aligned, filter_name, filename)

    def final_mosaic_fits(self, filter_name: str, filename: str) -> Path:
        return self._filtered_output(self.mosaics, filter_name, filename)

    def final_normalized_fits(self, filter_name: str, filename: str) -> Path:
        return self._filtered_output(self.normalized, filter_name, filename)

    def preview_png(self, filename: str) -> Path:
        return self._output(self.preview_png_dir, filename, ".png")

    def preview_jpeg(self, filename: str) -> Path:
        return self._output(self.preview_jpeg_dir, filename, ".jpg")

    def footprint(self, filename: str) -> Path:
        return self._output(self.diagnostics / "footprints", filename, ".fits")

    def coverage_map(self, filename: str) -> Path:
        return self._output(self.diagnostics / "coverage_maps", filename)

    def mask(self, filename: str) -> Path:
        return self._output(self.diagnostics / "masks", filename, ".fits")

    def seam_map(self, filename: str) -> Path:
        return self._output(self.diagnostics / "seam_maps", filename, ".fits")

    def weight_map(self, filename: str) -> Path:
        return self._output(self.diagnostics / "weight_maps", filename, ".fits")

    def rejected_frame(self, filename: str) -> Path:
        return self._output(self.diagnostics / "rejected_frames", filename)

    def intermediate_fits(self, stage: str, filename: str) -> Path:
        stage_name = safe_filename(stage or "temporary_fits")
        return self._output(self.intermediate / stage_name, filename, ".fits")

    def memmap(self, filename: str) -> Path:
        return self._output(self.intermediate / "memmaps", filename)

    def recovery_manifest(self, filename: str) -> Path:
        return self._output(self.recovery / "manifests", filename, ".json")

    def checkpoint(self, filename: str) -> Path:
        return self._output(self.recovery / "checkpoints", filename)

    def processing_log(self, filename: str = "processing.log") -> Path:
        return self._output(self.metadata / "logs", filename, ".log")

    def report(self, filename: str) -> Path:
        return self._output(self.metadata / "reports", filename)

    def file_list(self, filename: str) -> Path:
        return self._output(self.metadata / "file_lists", filename)


def open_folder(path: os.PathLike | str) -> None:
    """Open an existing folder with the platform file manager."""
    folder = Path(path)
    if not folder.is_dir():
        raise FileNotFoundError(f"Folder has not been created yet: {folder}")
    if os.name == "nt":
        os.startfile(os.fspath(folder))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", os.fspath(folder)])
    else:
        subprocess.Popen(["xdg-open", os.fspath(folder)])


def cleanup_job_intermediates(paths: JobPaths, retain: bool = False) -> list[str]:
    """Remove owned temporary stages after success, never final products."""
    if retain:
        return []
    removed = []
    root = paths.root.resolve()
    for name in (
        "memmaps", "temporary_fits", "reprojected", "cropped",
        "normalized", "extracted",
    ):
        target = (paths.intermediate / name).resolve()
        if not target.is_relative_to(root) or not target.exists():
            continue
        try:
            shutil.rmtree(target)
            removed.append(target.relative_to(root).as_posix())
        except OSError:
            LOGGER.debug("Could not remove successful-job temporary folder %s",
                         target, exc_info=True)
    return removed


def atomic_json_write(path: os.PathLike | str,
                      payload: Mapping[str, Any]) -> None:
    """Atomically replace a JSON file without exposing a partial document."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(dict(payload), handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            LOGGER.debug("Could not clean temporary JSON %s", tmp,
                         exc_info=True)


@dataclass
class JobManifest:
    """Portable record of one processing run and its generated products."""

    paths: JobPaths
    job_name: str
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = ""
    state: RunState = RunState.PENDING
    cancelled: bool = False
    failed: bool = False
    source_files: list[str] = field(default_factory=list)
    observation_ids: list[str] = field(default_factory=list)
    instruments: list[str] = field(default_factory=list)
    filters: list[str] = field(default_factory=list)
    settings: dict[str, Any] = field(default_factory=dict)
    products: dict[str, list[str]] = field(default_factory=lambda: {
        "final": [], "previews": [], "diagnostics": [],
        "intermediate": [], "recovery": [], "metadata": [], "failed": [],
        "rejected": [],
    })
    warnings: list[str] = field(default_factory=list)
    processing_seconds: float = 0.0

    @property
    def manifest_path(self) -> Path:
        return self.paths.root / "job_manifest.json"

    def _relative(self, path: os.PathLike | str) -> str:
        candidate = Path(path).resolve()
        try:
            return candidate.relative_to(self.paths.root.resolve()).as_posix()
        except ValueError as exc:
            raise ValueError("Manifest products must stay inside the job folder") from exc

    def record_product(self, category: str, path: os.PathLike | str) -> None:
        if category not in self.products:
            raise KeyError(f"Unknown manifest product category: {category}")
        relative = self._relative(path)
        if relative not in self.products[category]:
            self.products[category].append(relative)

    def to_dict(self) -> dict[str, Any]:
        self.updated_at = datetime.now(timezone.utc).isoformat()
        clean_settings = {
            key: value for key, value in self.settings.items()
            if "token" not in key.lower() and "secret" not in key.lower()
        }
        return {
            "job_name": self.job_name,
            "job_id": self.job_id,
            "creation_date": self.created_at,
            "last_update_date": self.updated_at,
            "application_version": APP_VERSION,
            "completion_state": self.state.value,
            "cancellation_state": self.cancelled,
            "failure_state": self.failed,
            "source_files": list(self.source_files),
            "source_observation_ids": list(self.observation_ids),
            "instruments": list(self.instruments),
            "filters": list(self.filters),
            "processing_settings": clean_settings,
            "output_root": ".",
            "products": self.products,
            "final_products": list(self.products["final"]),
            "preview_products": list(self.products["previews"]),
            "diagnostic_products": list(self.products["diagnostics"]),
            "intermediate_products": list(self.products["intermediate"]),
            "recovery_files": list(self.products["recovery"]),
            "failed_products": list(self.products["failed"]),
            "rejected_products": list(self.products["rejected"]),
            "warnings": list(self.warnings),
            "processing_duration_seconds": float(self.processing_seconds),
            "library_versions": {
                "python": sys.version.split()[0],
                "numpy": getattr(np, "__version__", "unknown"),
            },
        }

    def save(self) -> Path:
        atomic_json_write(self.manifest_path, self.to_dict())
        return self.manifest_path

    @classmethod
    def load(cls, paths: JobPaths) -> "JobManifest":
        """Load a previously saved job manifest from *paths*."""
        with (paths.root / "job_manifest.json").open(
                encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise TypeError("job manifest root must be a JSON object")
        try:
            state = RunState(data.get("completion_state", RunState.PENDING.value))
        except ValueError:
            state = RunState.PENDING
        manifest = cls(
            paths=paths,
            job_name=str(data.get("job_name") or paths.root.name),
            job_id=str(data.get("job_id") or uuid.uuid4().hex),
            created_at=str(data.get("creation_date") or
                           datetime.now(timezone.utc).isoformat()),
            updated_at=str(data.get("last_update_date") or ""),
            state=state,
            cancelled=bool(data.get("cancellation_state", False)),
            failed=bool(data.get("failure_state", False)),
            source_files=list(data.get("source_files") or []),
            observation_ids=list(data.get("source_observation_ids") or []),
            instruments=list(data.get("instruments") or []),
            filters=list(data.get("filters") or []),
            settings=dict(data.get("processing_settings") or {}),
            warnings=list(data.get("warnings") or []),
            processing_seconds=float(
                data.get("processing_duration_seconds") or 0.0),
        )
        products = data.get("products")
        if isinstance(products, dict):
            for category in manifest.products:
                manifest.products[category] = list(products.get(category) or [])
        return manifest

__all__ = [name for name in globals() if not name.startswith('__')]
