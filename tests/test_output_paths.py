from __future__ import annotations

import json
from pathlib import Path

import pytest

from tsdp2.models import RunState
from tsdp2.output_paths import JobManifest, JobPaths, cleanup_job_intermediates, safe_filename


def test_safe_filename_blocks_traversal_and_windows_reserved_names():
    assert safe_filename("../NGC 602: F200W") == "_NGC_602__F200W"
    assert "/" not in safe_filename("a/b")
    assert safe_filename("CON") == "_CON"
    assert "λ" in safe_filename("Orion λ")


def test_job_creation_is_lazy_and_collision_safe(tmp_path):
    first = JobPaths.create(tmp_path, "Orion Mosaic")
    assert list(first.root.iterdir()) == []
    second = JobPaths.create(tmp_path, "Orion Mosaic")
    assert first.root.name == "Orion_Mosaic"
    assert second.root.name == "Orion_Mosaic_02"
    assert first.root != second.root


@pytest.mark.parametrize(
    ("method", "args", "relative_parent"),
    [
        ("final_aligned_fits", ("F200W", "a.fits"), "final/aligned/F200W"),
        ("final_mosaic_fits", ("F444W", "m.fits"), "final/mosaics/F444W"),
        ("final_normalized_fits", ("F770W", "n.fits"), "final/normalized/F770W"),
        ("footprint", ("f.fits",), "diagnostics/footprints"),
        ("coverage_map", ("c.png",), "diagnostics/coverage_maps"),
        ("mask", ("mask.fits",), "diagnostics/masks"),
        ("seam_map", ("seam.fits",), "diagnostics/seam_maps"),
        ("weight_map", ("weight.fits",), "diagnostics/weight_maps"),
        ("rejected_frame", ("bad.txt",), "diagnostics/rejected_frames"),
        ("preview_png", ("view.png",), "previews/png"),
        ("preview_jpeg", ("view.jpeg",), "previews/jpeg"),
        ("intermediate_fits", ("temporary_fits", "tmp.fits"), "intermediate/temporary_fits"),
        ("memmap", ("array.dat",), "intermediate/memmaps"),
        ("recovery_manifest", ("resume.json",), "recovery/manifests"),
        ("checkpoint", ("state.bin",), "recovery/checkpoints"),
        ("processing_log", ("run.log",), "metadata/logs"),
        ("report", ("report.txt",), "metadata/reports"),
        ("file_list", ("inputs.txt",), "metadata/file_lists"),
    ],
)
def test_every_output_category_is_routed(tmp_path, method, args, relative_parent):
    paths = JobPaths.create(tmp_path, "Unicode λ path")
    output = getattr(paths, method)(*args)
    assert output.parent == paths.root / Path(relative_parent)
    assert output.parent.is_dir()


def test_existing_product_is_not_overwritten(tmp_path):
    paths = JobPaths.create(tmp_path, "job")
    first = paths.preview_png("same.png")
    first.write_bytes(b"first")
    second = paths.preview_png("same.png")
    assert second.name == "same_02.png"
    assert first.read_bytes() == b"first"


def test_manifest_paths_are_relative_and_secrets_are_removed(tmp_path):
    paths = JobPaths.create(tmp_path, "job")
    product = paths.final_mosaic_fits("F200W", "mosaic.fits")
    product.write_bytes(b"science")
    manifest = JobManifest(
        paths=paths,
        job_name="job",
        state=RunState.DONE,
        settings={"telegram_token": "secret", "combine": "median"},
    )
    manifest.record_product("final", product)
    manifest.save()
    payload = json.loads(manifest.manifest_path.read_text(encoding="utf-8"))
    assert payload["final_products"] == ["final/mosaics/F200W/mosaic.fits"]
    assert payload["processing_settings"] == {"combine": "median"}
    assert "secret" not in manifest.manifest_path.read_text(encoding="utf-8")
    assert JobManifest.load(paths).products["final"] == payload["final_products"]


def test_cleanup_removes_owned_intermediates_only(tmp_path):
    paths = JobPaths.create(tmp_path, "job")
    final = paths.final_aligned_fits("F200W", "final.fits")
    final.write_bytes(b"keep")
    temporary = paths.memmap("scratch.dat")
    temporary.write_bytes(b"remove")
    checkpoint = paths.checkpoint("resume.bin")
    checkpoint.write_bytes(b"recover")
    removed = cleanup_job_intermediates(paths)
    assert removed == ["intermediate/memmaps"]
    assert final.read_bytes() == b"keep"
    assert checkpoint.read_bytes() == b"recover"
    assert not temporary.exists()
