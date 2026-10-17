from types import SimpleNamespace
from unittest.mock import Mock
import queue

import numpy as np
import pytest
from astropy.io import fits

from tsdp2.astronomy.mosaicking import mosaic_filter_group
from tsdp2.downloads.mast_importer_products import MastImporterProductsMixin
from tsdp2.ui.coverage_rendering import CoverageRenderingMixin
from tsdp2.ui.mosaic_tab import MosaicTab


def test_mean_mosaic_writes_coverage_maps(tmp_path, fits_factory):
    paths = [str(fits_factory(f'tile_{i}.fits', data=np.full((8, 8), i + 1.0)))
             for i in range(2)]
    output = mosaic_filter_group(paths, str(tmp_path / 'out'), 'F200W',
                                 combine='mean', save_coverage_maps=True)
    assert np.isfinite(fits.getdata(output)).any()
    coverage = fits.getdata(tmp_path / 'out' / 'coverage_F200W.fits')
    assert np.nanmax(coverage) == 2


def test_mast_download_url_extracts_nested_filename():
    url = 'https://mast.stsci.edu/api/v0.1/Download/file?uri=mast%3AJWST%2Fproduct%2Fjw_test_i2d.fits'
    assert MastImporterProductsMixin._filename_from_uri(url) == 'jw_test_i2d.fits'


class CoverageSession(CoverageRenderingMixin):
    def __init__(self, codes):
        self.filter_codes = codes
        self.groups = {code: ['tile.fits'] for code in codes}
        self.state = {'idx': 0}
        self.view_var = Mock()
        self._render = Mock()
        for name in ('prev_btn', 'next_btn', 'all_btn', 'refresh_btn',
                     'holes_chk', 'opacity_chk', 'labels_chk', 'view_combo'):
            setattr(self, name, Mock())


def test_coverage_filter_navigation_and_controls():
    session = CoverageSession(['F200W', 'F444W'])
    session.show_all()
    session._render.assert_called_with(['F200W', 'F444W'], 'All filters')
    session.next_filter()
    session._render.assert_called_with(['F444W'], 'F444W (1 file(s))')
    session.next_filter()
    session._render.assert_called_with(['F200W'], 'F200W (1 file(s))')
    session.view_var.get.return_value = 'F444W'
    session.refresh_current()
    assert session.state['idx'] == 1
    session.view_var.get.return_value = 'F200W'
    session.combo_changed()
    assert session.state['idx'] == 0
    session._set_controls_enabled(True)
    single = CoverageSession(['F200W'])
    single._set_controls_enabled(True)
    single.next_btn.configure.assert_called_with(state='disabled')


def mosaic_tab_stub(tmp_path, path):
    tab = SimpleNamespace(groups={'F200W': [str(path)]}, active_filters={'F200W'},
                          mosaic_runner=None, seam_runner=None, background_runner=None,
                          sky_region=None, filter_downscales={}, mosaic_q=queue.Queue(),
                          _prog={}, fits_paths=[str(path)])
    values = dict(out_dir=str(tmp_path / 'out'), scratch_dir='', combine_fn='median',
                  n_workers=1, downscale_global='1', draft_scale='0.08', seam_strength='1',
                  save_coverage_maps=False, resume_cache=False, turbo_fullres=False,
                  draft_background_cleanup=False, highres_background_cleanup=False,
                  retain_intermediates=False)
    for name, value in values.items():
        setattr(tab, name, Mock(get=Mock(return_value=value)))
    for name in ('_stat', '_run_btn', '_draft_btn', '_repair_btn', '_clean_btn', '_cancel_btn'):
        setattr(tab, name, Mock())
    tab.update_idletasks = Mock()
    tab._active_groups = lambda: tab.groups
    tab._scale_for_filter = Mock()
    tab._compute_preflight = lambda active: {'space_ok': True}
    return tab


@pytest.mark.parametrize("auto", [False, True])
def test_mosaic_button_starts_processing_worker(tmp_path, fits_factory, monkeypatch, auto):
    import tsdp2.ui.mosaic_processing as module
    from tsdp2.ui.mosaic_runners import MosaicRunner
    tab = mosaic_tab_stub(tmp_path, fits_factory('tile.fits', FILTER='F200W'))
    tab.auto_workers = Mock(get=Mock(return_value=auto))
    monkeypatch.setattr(module.os, 'cpu_count', lambda: 24)
    monkeypatch.setattr(module.messagebox, 'askyesno', lambda *a, **k: True)
    monkeypatch.setattr(MosaicRunner, 'start', lambda self: self.run())
    MosaicTab.run_mosaic(tab)
    assert tab.mosaic_runner.n_workers == (22 if auto else 1)
    events = list(tab.mosaic_q.queue)
    assert any(event['step'] == 'done' for event in events), events
    assert list(tab._active_job_paths.final.rglob('*.fits'))


@pytest.mark.parametrize('action,runner_name', [
    ('clean_existing_background', 'BackgroundCleanRunner'),
    ('repair_existing_mosaic', 'SeamRepairRunner'),
])
def test_mosaic_repair_buttons_start_worker(tmp_path, fits_factory, monkeypatch, action, runner_name):
    import tkinter.simpledialog as simpledialog
    import tsdp2.ui.mosaic_recovery as module
    import tsdp2.ui.mosaic_runners as runners
    path = fits_factory('mosaic.fits', FILTER='F200W')
    tab = mosaic_tab_stub(tmp_path, path)
    monkeypatch.setattr(module.filedialog, 'askopenfilename', lambda **k: str(path))
    monkeypatch.setattr(module.filedialog, 'asksaveasfilename', lambda **k: str(tmp_path / 'fixed.fits'))
    monkeypatch.setattr(module.messagebox, 'askyesno', lambda *a, **k: True)
    monkeypatch.setattr(simpledialog, 'askfloat', lambda *a, **k: 0.9)
    started = []
    monkeypatch.setattr(getattr(runners, runner_name), 'start', lambda self: started.append(self))
    getattr(MosaicTab, action)(tab)
    assert len(started) == 1
    assert started[0].status_q is tab.mosaic_q


@pytest.mark.gui
def test_coverage_window_keeps_selected_filters(monkeypatch, tmp_path):
    import tkinter as tk
    from tsdp2.ui.coverage_window import CoverageWindow
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f'Tk display unavailable: {exc}')
    root.withdraw()
    monkeypatch.setattr(CoverageWindow, '_render', Mock())
    try:
        window = CoverageWindow(root, {'F200W': ['tile.fits']}, ['F200W'], str(tmp_path))
        window.show_all()
        window._render.assert_called_with(['F200W'], 'All filters')
        window._set_controls_enabled(True)
        assert str(window.next_btn.cget('state')) == 'disabled'
    finally:
        root.destroy()
