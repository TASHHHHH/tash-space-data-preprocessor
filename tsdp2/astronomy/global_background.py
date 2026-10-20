"""Source-masked, full-field background equalization for visual products."""
from __future__ import annotations

import numpy as np
from scipy.ndimage import (distance_transform_edt, gaussian_filter, median_filter,
                           label, find_objects, binary_dilation, map_coordinates)
from scipy.interpolate import griddata


def _mesh_background(image, excluded, box):
    h,w=image.shape
    gh,gw=int(np.ceil(h/box)),int(np.ceil(w/box))
    values=np.full((gh,gw),np.nan,dtype=np.float32)
    for iy in range(gh):
        for ix in range(gw):
            patch=image[iy*box:min(h,(iy+1)*box),ix*box:min(w,(ix+1)*box)]
            mask=excluded[iy*box:min(h,(iy+1)*box),ix*box:min(w,(ix+1)*box)]
            sky=patch[~mask & np.isfinite(patch)].astype(np.float64)
            if sky.size<max(8,int(patch.size*.12)):
                continue
            for _ in range(5):
                centre=np.median(sky)
                noise=max(np.finfo(np.float32).tiny,np.finfo(np.float32).eps*abs(centre),
                          1.4826*np.median(np.abs(sky-centre)))
                keep=(sky>centre-3*noise)&(sky<centre+2.5*noise)
                if keep.all() or keep.sum()<8:
                    break
                sky=sky[keep]
            values[iy,ix]=np.median(sky)
    good=np.isfinite(values)
    if good.sum()<4:
        raise ValueError('Insufficient exposed sky to estimate a global background.')
    yy,xx=np.mgrid[:gh,:gw]
    points=np.column_stack((yy[good],xx[good]))
    if good.sum()>=4 and np.linalg.matrix_rank(points-points.mean(axis=0))==2:
        linear=griddata(points,values[good],(yy,xx),method='linear')
    else:
        linear=np.full(values.shape,np.nan)
    nearest=griddata(points,values[good],(yy,xx),method='nearest')
    values=np.where(np.isfinite(linear),linear,nearest).astype(np.float32)
    # A mesh is defined at its cell centres.
    y=(np.arange(h,dtype=np.float32)+.5)/box-.5
    x=(np.arange(w,dtype=np.float32)+.5)/box-.5
    return map_coordinates(values,np.broadcast_arrays(y[:,None],x[None,:]),order=1,mode='nearest')


def estimate_global_background(data, max_samples=500_000, source_mask=None):
    """Model broad illumination AND detector steps, without scaling flux.

    Multiscale structure detects resolved sources and stars across the image.
    Clipped sky cells interpolate underneath those sources. A
    source-free local median surface then follows detector discontinuities
    instead of forcing every background into one smooth polynomial plane.
    """
    if data.ndim!=2 or min(data.shape)<32:
        raise ValueError('Global equalization needs a 2-D image at least 32 pixels across.')
    step=max(1,int(np.ceil(np.sqrt(data.size/max_samples))))
    sample=np.array(data[::step,::step],dtype=np.float32,copy=True)
    valid=np.isfinite(sample)
    if valid.sum()<1024:
        raise ValueError('Too few valid pixels for global background equalization.')
    if not valid.all():
        indices=distance_transform_edt(~valid,return_distances=False,return_indices=True)
        sample[~valid]=sample[tuple(indices[:,~valid])]
    dx=np.diff(sample,axis=1)
    pair=valid[:,1:]&valid[:,:-1]
    differences=dx[pair]
    if not differences.size:
        dy=np.diff(sample,axis=0)
        differences=dy[valid[1:]&valid[:-1]]
    floor=max(np.finfo(np.float32).tiny,np.finfo(np.float32).eps*float(np.median(np.abs(sample[valid]))))
    noise=max(floor,float(np.median(np.abs(differences-np.median(differences))))/.9539) if differences.size else floor
    protected=np.zeros(sample.shape,dtype=bool)
    # Compact multiscale curvature separates galaxies/stars from broad flat
    # detector pedestals. A flat bright detector must not become a source mask.
    for inner,outer in ((1.5,8.0),(4.0,20.0)):
        structure=gaussian_filter(sample,inner)-gaussian_filter(sample,outer)
        candidates=valid & (structure>noise*.9)
        components,_=label(candidates)
        for code,slices in enumerate(find_objects(components),1):
            if slices is None:
                continue
            sy,sx=slices
            local=components[sy,sx]==code
            size=int(local.sum())
            if size<3:
                continue
            height,width=sy.stop-sy.start,sx.stop-sx.start
            aspect=max(height,width)/max(1,min(height,width))
            if size>100 and aspect>3:
                continue
            if width>sample.shape[1]*.8 or height>sample.shape[0]*.8:
                continue
            py,px=np.nonzero(local)
            weights=np.maximum(structure[sy,sx][local],0).astype(np.float64)
            total=weights.sum()
            cx,cy=np.sum(px*weights)/total,np.sum(py*weights)/total
            vx,vy=np.sum((px-cx)**2*weights)/total,np.sum((py-cy)**2*weights)/total
            covariance=np.sum((px-cx)*(py-cy)*weights)/total
            widths=np.sqrt(np.maximum(0,np.linalg.eigvalsh([[vx,covariance],[covariance,vy]])))
            extended=widths[0]>5 and widths[1]>7
            # The broad kernel detects stellar wings too. Keep it only for
            # spatially resolved emission; compact stars use the small mask.
            if inner>2 and not extended:
                continue
            grow=min(28,max(6,int(max(height,width)*.5))) if extended else 1
            y0,y1=max(0,sy.start-grow),min(sample.shape[0],sy.stop+grow)
            x0,x1=max(0,sx.start-grow),min(sample.shape[1],sx.stop+grow)
            patch=components[y0:y1,x0:x1]==code
            protected[y0:y1,x0:x1] |= binary_dilation(patch,iterations=grow)
    if source_mask is not None:
        if source_mask.shape!=data.shape:
            raise ValueError('Source mask shape does not match the image.')
        protected |= np.asarray(source_mask[::step,::step],dtype=bool)
    if np.count_nonzero(valid&~protected)<max(128,int(valid.sum()*.05)):
        raise ValueError('Too little unmasked sky for reliable global equalization.')
    # Refit sky only, with no galaxy/star pixels entering the cell statistics.
    sky=_mesh_background(sample,protected|~valid,max(12,min(24,min(sample.shape)//12)))
    cleaned=sample.copy()
    cleaned[protected|~valid]=sky[protected|~valid]
    window=max(9,min(25,2*(min(sample.shape)//32)+1))
    background=gaussian_filter(median_filter(cleaned,size=window,mode='nearest'),.7)
    original_sky=float(np.median(background[valid&~protected]))
    baseline=0.0
    correction=background.astype(np.float32)
    report=dict(method='global-source-masked-v1',sample_step=step,
                analysis_shape=list(sample.shape),baseline=baseline,noise=noise,
                original_sky=original_sky,target_sky=baseline,
                protected_fraction=float(protected[valid].mean()),
                correction_rms=float(np.sqrt(np.mean(correction[valid].astype(np.float64)**2))),
                correction_peak=float(np.max(np.abs(correction[valid]))))
    return correction,protected,report


def global_correction_block(correction, source_shape, step, y0, y1):
    y=np.arange(y0,y1,dtype=np.float32)/step
    x=np.arange(source_shape[1],dtype=np.float32)/step
    return map_coordinates(correction,np.broadcast_arrays(y[:,None],x[None,:]),
                           order=1,mode='nearest')
