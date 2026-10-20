import json
from queue import Queue
from unittest.mock import Mock
import numpy as np
import pytest
from astropy.io import fits
from scipy.ndimage import label
from tsdp2.astronomy.mosaic_quality import inspect_mosaic_holes, automatically_repair_mosaic


def test_exact_holes_include_one_pixel_and_join_strip_boundaries():
    data=np.ones((700,120),dtype='float32')
    data[10,10]=np.nan
    data[230:550,50:70]=np.nan
    data[:,100:105]=np.nan  # exterior across every strip
    data[0:90,:2]=np.nan
    report=inspect_mosaic_holes(data)
    assert report['internal_holes']==2
    assert report['internal_pixels']==6401
    assert report['exterior_pixels']==3500+180
    assert report['invalid_pixels']==10081
    assert sorted(r['pixels'] for r in report['regions'])==[1,6400]


@pytest.mark.parametrize('seed',[1,2,3])
def test_streaming_components_match_full_reference(seed):
    rng=np.random.default_rng(seed)
    bad=rng.random((600,90))<.2
    bad[220:320,30:40]=True
    bad[:,80:85]=True
    data=np.where(bad,np.nan,1)
    ids,n=label(bad)
    edge=set(np.concatenate((ids[0],ids[-1],ids[:,0],ids[:,-1])))
    counts=np.bincount(ids.ravel())
    internal=[i for i in range(1,n+1) if i not in edge]
    report=inspect_mosaic_holes(data,max_regions=10000)
    assert report['internal_holes']==len(internal)
    assert report['internal_pixels']==sum(counts[i] for i in internal)
    assert report['invalid_pixels']==bad.sum()


def inputs(fits_factory):
    rng=np.random.default_rng(64)
    y,x=np.mgrid[:128,:128]
    raw=100+.1*x+.07*y+rng.normal(0,.3,x.shape)
    raw+=40*np.exp(-((x-80)**2+(y-60)**2)/55)
    source=fits_factory('g.fits',data=raw,FILTER='G')
    original=(raw-23-.03*x+.02*y).astype('float32')
    target=fits_factory('mosaic_G.fits',data=original,FILTER='G')
    return source,target,original


def test_repair_uses_original_pixels_local_plane_and_preserves_good_data(fits_factory,tmp_path):
    source,target,original=inputs(fits_factory)
    with fits.open(target,mode='update') as h:
        h[0].data[58:65,78:85]=np.nan
        h[0].data[25,25]=np.nan
        h[0].data[:8,:4]=np.nan  # outside sky must stay uncovered
    before=fits.getdata(target).copy()
    report=automatically_repair_mosaic(target,[str(source)],'G')
    after=fits.getdata(target)
    valid=np.isfinite(before)
    np.testing.assert_array_equal(after[valid],before[valid])
    np.testing.assert_allclose(after[58:65,78:85],original[58:65,78:85],atol=.001)
    assert np.isnan(after[:8,:4]).all()
    assert report['detected_holes']==2 and report['repaired_pixels']==50
    assert report['remaining_pixels']==0
    assert fits.getheader(target)['AUTOCHK']
    assert json.loads((tmp_path/'mosaic_G_quality.json').read_text())['repaired_pixels']==50


def test_weight_mask_prevents_invalid_raw_values_from_filling_hole(fits_factory):
    source,target,original=inputs(fits_factory)
    header=fits.getheader(source)
    weight=np.ones(original.shape,dtype='float32')
    weight[58:65,78:85]=0
    with fits.open(source,mode='append') as h:
        h.append(fits.ImageHDU(weight,name='WHT',header=header))
    with fits.open(target,mode='update') as h:
        h[0].data[60:63,80:83]=np.nan
    report=automatically_repair_mosaic(target,[str(source)],'G')
    assert report['repaired_pixels']==0 and report['remaining_pixels']==9
    assert np.isnan(fits.getdata(target)[60:63,80:83]).all()


def test_dark_finite_pixels_are_not_holes_and_clear_audit_skips_sources(fits_factory,monkeypatch):
    target=fits_factory('g.fits',data=np.zeros((64,64)),FILTER='G')
    from tsdp2.astronomy import seam_repair
    repair=Mock(side_effect=AssertionError('Unnecessary repair'))
    monkeypatch.setattr(seam_repair,'_fast_repair_fits_holes',repair)
    report=automatically_repair_mosaic(target,[],'G')
    assert report['detected_holes']==0
    repair.assert_not_called()
    assert np.all(fits.getdata(target)==0)


def test_coverage_counts_and_relative_weights_follow_recovered_pixels(fits_factory):
    source,target,original=inputs(fits_factory)
    coverage=np.ones(original.shape,dtype='float32')
    coverage[30:35,30:35]=0
    cov=fits_factory('coverage_G.fits',data=coverage,FILTER='G')
    weight=fits_factory('weight_G.fits',data=coverage,FILTER='G')
    with fits.open(target,mode='update') as h:
        h[0].data[30:35,30:35]=np.nan
    report=automatically_repair_mosaic(target,[str(source)],'G',quality_paths=(cov,weight))
    assert report['repaired_pixels']==25
    assert np.all(fits.getdata(cov)[30:35,30:35]==1)
    assert np.all(fits.getdata(weight)[30:35,30:35]==1)


def test_cancelled_audit_does_not_change_target(fits_factory):
    source,target,_=inputs(fits_factory)
    before=target.read_bytes()
    with pytest.raises(RuntimeError,match='cancelled'):
        automatically_repair_mosaic(target,[str(source)],'G',cancel_cb=lambda:True)
    assert target.read_bytes()==before


def test_insufficient_copy_space_reports_deferred_repair(fits_factory,monkeypatch):
    from types import SimpleNamespace
    import tsdp2.astronomy.mosaic_quality as module
    source,target,_=inputs(fits_factory)
    with fits.open(target,mode='update') as h:
        h[0].data[40:43,40:43]=np.nan
    monkeypatch.setattr(module.shutil,'disk_usage',lambda p:SimpleNamespace(free=1))
    report=automatically_repair_mosaic(target,[str(source)],'G')
    assert report['deferred_disk_holes']==1
    assert report['remaining_pixels']==9


def test_decam_header_saturation_is_masked_but_faint_pixels_are_retained(fits_factory):
    from tsdp2.astronomy.coverage import _load_mosaic_2d_and_header
    raw=np.random.default_rng(55).normal(100,1,(96,96)).astype('float32')
    raw[40,40]=2000
    source=fits_factory('g.fits',data=raw,FILTER='G',INSTRUME='DECam',SATURATE=1000)
    data,_=_load_mosaic_2d_and_header(str(source))
    assert np.isnan(data[40,40])
    assert np.isfinite(data).sum()==raw.size-1


def test_virtual_science_hdu_uses_its_matching_weight_extension(tmp_path,simple_wcs):
    from tsdp2.astronomy.coverage import _load_mosaic_2d_and_header
    data=np.random.default_rng(71).normal(100,1,(64,64)).astype('float32')
    header=simple_wcs.to_header()
    header['EXTVER']=2
    weight=np.ones(data.shape,dtype='float32')
    weight[30:35,30:35]=0
    path=tmp_path/'g.fits'
    fits.HDUList([fits.PrimaryHDU(),fits.ImageHDU(data,name='SCI',header=header),
                  fits.ImageHDU(np.ones(data.shape),name='WHT',header=simple_wcs.to_header()),
                  fits.ImageHDU(weight,name='WHT',header=header)]).writeto(path)
    loaded,_=_load_mosaic_2d_and_header(str(path)+'::HDU=1::SCI 2')
    assert np.isnan(loaded[30:35,30:35]).all()
    assert np.isfinite(loaded[0,0])


@pytest.mark.parametrize('cosmetic',[False,True])
def test_real_draft_runner_audits_and_marks_optional_interpolation(fits_factory,tmp_path,cosmetic):
    from tsdp2.ui.mosaic_runners import MosaicRunner
    raw=np.random.default_rng(19).normal(100,1,(96,96)).astype('float32')
    raw[45:50,45:50]=np.nan
    source=fits_factory('g.fits',data=raw,FILTER='G')
    queue=Queue()
    runner=MosaicRunner({'G':[str(source)]},str(tmp_path/'run'),'feather',queue,
                        draft_mode=True,downscale_global=1,cosmetic_draft_holes=cosmetic)
    runner.run()
    messages=[]
    while not queue.empty():messages.append(queue.get())
    assert messages[-1]['step']=='done',messages[-1]
    result=messages[-1]['out_paths']['G']
    if cosmetic:
        assert np.isfinite(fits.getdata(result)[45:50,45:50]).all()
        assert fits.getheader(result)['COSMPIX']>=25
    else:
        assert np.isnan(fits.getdata(result)[45:50,45:50]).all()
    assert messages[-1]['quality_reports']['G']['remaining_pixels']>=25
    assert fits.getheader(result)['AUTOCHK']
    assert fits.getheader(result)['BGCLMETH']=='global-source-masked-v1'


@pytest.mark.parametrize('draft',[True,False])
def test_runner_audits_automatically_before_clean_copy_and_done(draft,fits_factory,tmp_path,monkeypatch):
    from tsdp2.ui import mosaic_runners as module
    source,target,_=inputs(fits_factory)
    queue=Queue()
    events=[]
    monkeypatch.setattr(module,'fast_draft_filter_group',lambda *a,**k:str(target))
    monkeypatch.setattr(module,'mosaic_filter_group',lambda *a,**k:str(target))
    def audit(*a,**k):
        events.append('audit')
        return dict(remaining_pixels=0)
    def clean(*a,**k):
        events.append('clean')
        return dict(output_path=str(target))
    monkeypatch.setattr(module,'automatically_repair_mosaic',audit)
    monkeypatch.setattr(module,'write_background_clean_fits_copy',clean)
    runner=module.MosaicRunner({'G':[str(source)]},str(tmp_path),'feather',queue,draft_mode=draft,
                               cosmetic_draft_holes=False)
    runner._run()
    assert events==['audit','clean']
    messages=[]
    while not queue.empty():messages.append(queue.get())
    assert messages[-1]['step']=='done'


def test_cosmetic_draft_fill_marks_pixels_and_preserves_valid_and_exterior(fits_factory,tmp_path):
    from tsdp2.astronomy.mosaic_quality import cosmetically_fill_draft
    data=np.random.default_rng(17).normal(100,1,(120,120)).astype('float32')
    data[40:50,40:50]=np.nan
    data[:10,:5]=np.nan
    target=fits_factory('draft_G.fits',data=data,FILTER='G',FASTDRFT=True)
    before=data.copy()
    result=cosmetically_fill_draft(target)
    after,hdr=fits.getdata(target,header=True)
    assert result['cosmetic_pixels']==100 and result['visible_gap_pixels']==0
    np.testing.assert_array_equal(after[np.isfinite(before)],before[np.isfinite(before)])
    assert np.isfinite(after[40:50,40:50]).all()
    assert np.isnan(after[:10,:5]).all()
    mask=fits.getdata(result['cosmetic_mask'])
    assert mask.sum()==100 and np.all(mask[40:50,40:50]==1)
    assert hdr['COSMFILL'] and hdr['COSMPIX']==100
    assert fits.getheader(result['cosmetic_mask'])['MASKTYPE']=='DRAFT_INTERPOLATED'


def test_cosmetic_fill_rejects_science_and_cancel_preserves_input(fits_factory):
    from tsdp2.astronomy.mosaic_quality import cosmetically_fill_draft
    source,target,original=inputs(fits_factory)
    before=target.read_bytes()
    with pytest.raises(ValueError,match='restricted'):
        cosmetically_fill_draft(target)
    assert target.read_bytes()==before

    with fits.open(target,mode='update') as h:
        h[0].header['FASTDRFT']=True
        h[0].data[40:45,40:45]=np.nan
    before=target.read_bytes()
    calls=[0]
    def cancel():
        calls[0]+=1
        return calls[0]>=2
    with pytest.raises(RuntimeError,match='cancelled'):
        cosmetically_fill_draft(target,cancel_cb=cancel)
    assert target.read_bytes()==before



def test_cosmetic_gap_does_not_clone_neighboring_bright_star(fits_factory):
    from tsdp2.astronomy.mosaic_quality import cosmetically_fill_draft
    y,x=np.mgrid[:120,:120]
    data=np.random.default_rng(17).normal(100,1,x.shape).astype('float32')
    data+=200*np.exp(-((x-39)**2+(y-55)**2)/3)
    data[48:64,42:54]=np.nan
    target=fits_factory('draft_G.fits',data=data,FILTER='G',FASTDRFT=True)
    cosmetically_fill_draft(target)
    after=fits.getdata(target)
    assert np.max(after[48:64,42:54])<108
    assert after[55,39]==data[55,39]
