import numpy as np
import pytest
from astropy.io import fits
from tsdp2.astronomy.global_background import estimate_global_background,global_correction_block


def synthetic():
    y,x=np.mgrid[:320,:480]
    background=100+.04*x+.03*y+18*(y>=105)-12*(y>=215)+9*(x>=260)
    galaxy=80*np.exp(-((x-235)**2/450+(y-160)**2/800))
    stars=np.zeros(x.shape)
    for sx,sy in ((60,45),(150,260),(410,70),(380,260)):
        stars+=120*np.exp(-((x-sx)**2+(y-sy)**2)/3)
    data=background+galaxy+stars+np.random.default_rng(12).normal(0,.6,x.shape)
    return data.astype('float32'),background,galaxy,stars


def corrected(data,source_mask=None,max_samples=500000):
    field,mask,report=estimate_global_background(data,source_mask=source_mask,max_samples=max_samples)
    output=data-global_correction_block(field,data.shape,report['sample_step'],0,len(data))
    return output,mask,report


def test_global_model_removes_gradient_and_detector_steps_preserving_sources():
    data,bg,galaxy,stars=synthetic()
    output,mask,report=corrected(data)
    sky=(galaxy<.1)&(stars<.1)
    assert np.std(output[sky])<np.std(data[sky])*.25
    # Contrast relative to the known sky under the source must survive.
    known=data-bg
    actual=output-report['baseline']
    core=galaxy>15
    assert abs(actual[core].sum()/known[core].sum()-1)<.12
    for x,y in ((60,45),(150,260),(410,70),(380,260)):
        assert abs(actual[y,x]/known[y,x]-1)<.05
    assert mask[160,235]


@pytest.mark.parametrize('factor',[1e-6,1e6])
def test_estimation_is_independent_of_data_units(factor):
    data,*_=synthetic()
    output,_,_=corrected(data)
    scaled,_,_=corrected(data*factor)
    np.testing.assert_allclose(scaled/factor,output,atol=.02,rtol=1e-4)


def test_nan_coverage_and_masked_extended_source_are_preserved():
    data,bg,galaxy,stars=synthetic()
    data[:15,:30]=np.nan
    data[40:50,80:90]=np.nan
    source=galaxy>1
    result,mask,report=corrected(data,source)
    assert np.array_equal(np.isnan(data),np.isnan(result))
    assert np.all(mask[source])
    assert report['method']=='global-source-masked-v1'


def test_model_sampling_is_bounded():
    data,*_=synthetic()
    _,_,report=corrected(data,max_samples=20000)
    assert np.prod(report['analysis_shape'])<=21000
    assert report['sample_step']>1


def test_constant_sky_goes_to_zero_and_invalid_data_is_rejected():
    output,_,_=corrected(np.ones((64,80),dtype='float32')*123)
    np.testing.assert_array_equal(output,np.zeros((64,80),dtype='float32'))
    with pytest.raises(ValueError,match='valid pixels'):
        corrected(np.full((64,80),np.nan,dtype='float32'))
    with pytest.raises(ValueError,match='unmasked sky'):
        corrected(np.ones((64,80),dtype='float32'),np.ones((64,80),dtype=bool))


def test_streamed_fits_copy_matches_model_and_preserves_source(tmp_path,fits_factory):
    from tsdp2.astronomy.seam_repair import write_background_clean_fits_copy
    data,*_=synthetic()
    data[:10,:10]=np.nan
    source=fits_factory('g.fits',data=data,FILTER='G')
    original=source.read_bytes()
    target=tmp_path/'g_global.fits'
    report=write_background_clean_fits_copy(source,target)
    output,hdr=fits.getdata(target,header=True)
    expected,_,_=corrected(data)
    np.testing.assert_allclose(output,expected,atol=2e-5,rtol=0,equal_nan=True)
    assert source.read_bytes()==original
    assert hdr['BGCLMETH']=='global-source-masked-v1'
    assert hdr['CRVAL1']==fits.getheader(source)['CRVAL1']
    assert report['model']['sample_step']==1


def test_cancelled_copy_leaves_source_and_existing_destination(fits_factory,tmp_path):
    from tsdp2.astronomy.seam_repair import write_background_clean_fits_copy
    data,*_=synthetic()
    source=fits_factory('g.fits',data=data,FILTER='G')
    target=tmp_path/'keep.fits'
    target.write_bytes(b'keep destination')
    with pytest.raises(RuntimeError,match='cancelled'):
        write_background_clean_fits_copy(source,target,cancel_cb=lambda:True)
    assert target.read_bytes()==b'keep destination'
