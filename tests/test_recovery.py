from __future__ import annotations

import json

from tsdp2.recovery.manifests import _RECOVERY_MANIFEST_NAME, _update_recovery_manifest


def test_recovery_manifest_creation_and_incremental_loading(tmp_path):
    _update_recovery_manifest(
        str(tmp_path),
        state="running",
        filter="F200W",
        input_files=["a.fits", "b.fits"],
    )
    assert (tmp_path / _RECOVERY_MANIFEST_NAME).exists()
    _update_recovery_manifest(str(tmp_path), state="complete", output="mosaic.fits")
    payload = json.loads((tmp_path / _RECOVERY_MANIFEST_NAME).read_text(encoding="utf-8"))
    assert payload["state"] == "complete"
    assert payload["filter"] == "F200W"
    assert payload["output"] == "mosaic.fits"
    assert payload["input_files"] == ["a.fits", "b.fits"]
