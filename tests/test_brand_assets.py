from __future__ import annotations

import hashlib

from PIL import Image

from tsdp2.resources import asset_path


def test_earth_icon_assets_are_present_and_multisize():
    for name in (
        "logo_source.png",
        "app_icon.png",
        "app_icon_24.png",
        "app_icon.ico",
        "NOTICE.txt",
    ):
        assert asset_path(name).is_file()

    with Image.open(asset_path("logo_source.png")) as source:
        assert source.size == (2500, 2500)
    with Image.open(asset_path("app_icon.ico")) as icon:
        sizes = icon.ico.sizes()
    assert {(16, 16), (32, 32), (48, 48), (256, 256)} <= sizes


def test_earth_icon_notice_records_owner_source():
    digest = hashlib.sha256(asset_path("logo_source.png").read_bytes()).hexdigest()
    notice = asset_path("NOTICE.txt").read_text(encoding="utf-8")
    assert digest == "c78803d853d07db33d1e3aff9063e97c83927ae4107d665a94695acebf885619"
    assert "supplied by the TSDP project owner" in notice
    assert digest.upper() in notice
