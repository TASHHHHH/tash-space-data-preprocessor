from __future__ import annotations

import pytest

from tsdp2.downloads.mast import parse_mast_csv


@pytest.mark.gui
def test_csv_importer_lists_every_observation_row(tmp_path):
    """Regression: the dialog crashed on a missing PRODUCT_PRESETS and showed no rows."""
    import tkinter as tk

    csv_path = tmp_path / "mast.csv"
    csv_path.write_text(
        "#@string,string,string,float\n"
        "obsid,obs_id,filters,t_exptime\n"
        "1001,jabc01010,F814W,400\n"
        "1002,jabc02010,F606W,350\n",
        encoding="utf-8",
    )
    rows = parse_mast_csv(str(csv_path))
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk display unavailable: {exc}")
    try:
        from tsdp2.downloads.mast_importer import MASTCsvImporter

        dialog = MASTCsvImporter(root, rows, on_add_cb=lambda urls: None)
        root.update_idletasks()
        assert len(dialog._tree.get_children()) == 2
        assert len(dialog._selected) == 2
    finally:
        root.destroy()
