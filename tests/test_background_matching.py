import numpy as np
import pytest

from tsdp2.astronomy.background_matching import estimate_background_planes, apply_background_plane
from tsdp2.astronomy.mosaic_inputs import _sky_match_tiles
from tsdp2.astronomy.mosaic_reprojection import _estimate_overlap_plane_corrections


def scene():
    y, x = np.mgrid[:180, :260]
    # Extended galaxy and true smooth sky gradient are present in every image.
    sky = 100 + 0.08*x + 0.04*y + 130*np.exp(-((x-95)**2/1600+(y-80)**2/950))
    sky += 5*np.sin(x/23)*np.exp(-(y-80)**2/2000)
    xn, yn = 2*x/259-1, 2*y/179-1
    tiles = []
    for i, (lo, hi) in enumerate(((0, 150), (70, 220), (125, 260))):
        tile = sky + (0, 25, -17)[i] + (0, 7, -4)[i]*xn + (0, -5, 6)[i]*yn
        tile = tile.astype('float32')
        tile[:, :lo] = np.nan
        tile[:, hi:] = np.nan
        tiles.append(tile)
    return sky, tiles


def test_global_overlap_preserves_galaxy_and_true_sky_gradient():
    sky, tiles = scene()
    report = {}
    corrections = estimate_background_planes(tiles, diagnostics=report)
    anchor = next(i for i, c in enumerate(corrections) if not any(c))
    y, x = np.mgrid[:180, :260]
    expected = sky + (0, 25, -17)[anchor] + (0, 7, -4)[anchor]*(2*x/259-1) + (0, -5, 6)[anchor]*(2*y/179-1)
    for tile, correction in zip(tiles, corrections):
        valid = np.isfinite(tile)
        apply_background_plane(tile, correction)
        np.testing.assert_allclose(tile[valid], expected[valid], atol=0.04, rtol=0)
    assert report['overlap_pairs'] == 3
    assert report['median_overlap_after'] < 0.01


def test_cosmic_rays_and_mismatched_stars_do_not_drive_plane():
    _, tiles = scene()
    rng = np.random.default_rng(46)
    for tile in tiles:
        tile += rng.normal(0, 0.25, tile.shape).astype('float32')
        bad = rng.random(tile.shape) < 0.025
        tile[bad] += 10000
    corrections = estimate_background_planes(tiles)
    for tile, correction in zip(tiles, corrections):
        apply_background_plane(tile, correction)
    for i in range(2):
        good = np.isfinite(tiles[i]) & np.isfinite(tiles[i+1])
        assert abs(np.median(tiles[i][good]-tiles[i+1][good])) < 0.12
        assert np.percentile(abs(tiles[i][good]-tiles[i+1][good]), 80) < 0.7


def test_disconnected_pointings_are_not_histogram_equalized():
    a = np.full((100, 120), np.nan, dtype='float32')
    b = a.copy()
    a[:, :50], b[:, 65:] = 100, 600
    out = _sky_match_tiles([a, b])
    np.testing.assert_array_equal(out[0], a)
    np.testing.assert_array_equal(out[1], b)


def test_constant_scene_still_recovers_additive_offset():
    tiles = [np.full((60, 80), 100, dtype='float32'), np.full((60, 80), 130, dtype='float32')]
    result = _sky_match_tiles(tiles)
    np.testing.assert_allclose(result[0], result[1], atol=1e-4)
    assert np.all(tiles[1] == 130)  # default call is non-mutating


def test_narrow_overlap_does_not_extrapolate_noise_into_large_gradients():
    rng = np.random.default_rng(9)
    tiles = [rng.normal(100, 0.2, (180, 260)).astype('float32'),
             rng.normal(125, 0.2, (180, 260)).astype('float32')]
    tiles[0][:, 140:] = np.nan
    tiles[1][:, :136] = np.nan
    result = _sky_match_tiles(tiles)
    assert abs(np.nanmedian(result[0])-np.nanmedian(result[1])) < 1
    assert abs(np.nanmedian(result[1][:, 150:])-100) < 1


def test_disk_and_ram_models_agree_and_leave_missing_pixels(tmp_path):
    _, tiles = scene()
    paths = []
    for i, tile in enumerate(tiles):
        path = tmp_path / f'{i}.f32'
        tile.tofile(path)
        paths.append(path)
    ram = estimate_background_planes(tiles)
    disk = _estimate_overlap_plane_corrections(paths, tiles[0].shape, dense=True, nearby_edges=True)
    np.testing.assert_allclose(ram, disk, rtol=0, atol=1e-10)
    out = _sky_match_tiles(tiles)
    for before, after in zip(tiles, out):
        np.testing.assert_array_equal(np.isfinite(before), np.isfinite(after))


@pytest.mark.parametrize('tiles', [[], [np.full((32, 32), np.nan, dtype='float32')],
                                  [np.full((32, 32), np.nan, dtype='float32') for _ in range(2)]])
def test_empty_or_invalid_inputs_are_noops(tiles):
    assert estimate_background_planes(tiles) == [(0, 0, 0)]*len(tiles)


@pytest.mark.parametrize('combine', ['mean', 'median', 'feather'])
@pytest.mark.parametrize('disk', [False, True])
def test_written_mosaic_preserves_signal_and_matches_ram_disk(combine, disk, fits_factory, tmp_path, monkeypatch):
    from astropy.io import fits
    from astropy.wcs import WCS
    from reproject import reproject_interp
    import tsdp2.astronomy.mosaicking as mosaicking
    y, x = np.mgrid[:96, :128]
    sky = 100 + 0.1*x + 40*np.exp(-((x-70)**2+(y-40)**2)/180)
    sky += np.random.default_rng(115).normal(0, 0.2, sky.shape)
    paths = []
    for i, offset in enumerate((0, 25, -13)):
        tile = sky + offset + i*(3*(2*x/127-1)-2*(2*y/95-1))
        paths.append(str(fits_factory(f'g_{i}.fits', data=tile, FILTER='G')))
    monkeypatch.setattr(mosaicking, '_mosaic_disk_mode_needed', lambda *a: (disk, 'test'))
    out = mosaicking.mosaic_filter_group(paths, str(tmp_path/str(disk)), 'G', combine=combine, resume_cache=False)
    data, hdr = fits.getdata(out, header=True)
    original, original_header = fits.getdata(paths[0], header=True)
    expected, footprint = reproject_interp((original, original_header), WCS(hdr), shape_out=data.shape)
    good = np.isfinite(data) & np.isfinite(expected) & (footprint>0.5)
    np.testing.assert_allclose(data[good], expected[good], atol=0.03, rtol=0)
    assert hdr['BGNORM'] == 'robust-overlap-v1'


def test_optional_sky_fit_rejects_galaxy_cells_and_preserves_contrast():
    from tsdp2.astronomy.background_matching import estimate_source_masked_sky_planes
    y, x = np.mgrid[:160, :160]
    source = 70*np.exp(-((x-80)**2+(y-70)**2)/80)
    source += 140*np.exp(-((x-40)**2+(y-40)**2)/2)
    tiles = [(50+20*i+0.12*x-0.08*y+source).astype('float32') for i in range(3)]
    corrections = estimate_source_masked_sky_planes(tiles)
    for tile, correction in zip(tiles,corrections):
        apply_background_plane(tile,correction)
        residual=tile-source
        assert np.std(residual) < 0.15
        assert abs((tile[70,80]-np.median(residual))-source[70,80]) < 0.2


def test_fast_draft_uses_source_masked_normalization_only_when_enabled(tmp_path, fits_factory):
    from astropy.io import fits
    from tsdp2.astronomy.seam_repair import fast_draft_filter_group
    rng=np.random.default_rng(74)
    y,x=np.mgrid[:96,:128]
    data=100+0.1*x+0.05*y+rng.normal(0,0.15,x.shape)
    data+=50*np.exp(-((x-70)**2+(y-50)**2)/50)
    paths=[str(fits_factory('g.fits',data=data,FILTER='G'))]
    out=fast_draft_filter_group(paths,str(tmp_path/'normalized'),'G',downscale=1,cosmetic_background=True)
    actual,hdr=fits.getdata(out,header=True)
    assert hdr['SKYNORM'] and hdr['BGNORM']=='robust-overlap-v1'
    assert np.nanmedian(actual[:20,-20:])-np.nanmedian(actual[:20,:20]) < 1
    plain=fast_draft_filter_group(paths,str(tmp_path/'plain'),'G',downscale=1,cosmetic_background=False)
    assert not fits.getheader(plain)['SKYNORM']
