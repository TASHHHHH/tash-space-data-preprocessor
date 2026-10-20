from __future__ import annotations

import json

from tsdp2.release_smoke import run_release_smoke


def test_release_smoke_exercises_fits_and_reproject(tmp_path):
    report = tmp_path / "release-smoke.json"

    assert run_release_smoke(report) == 0
    payload = json.loads(report.read_text(encoding="utf-8"))

    assert payload["ok"] is True
    assert payload["checks"] == {"fits": True, "reproject": True, "normalization": True}
    assert payload["product"] == "Tash Space Data Preprocessor"
