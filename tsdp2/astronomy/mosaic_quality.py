"""Exact, strip-wise no-data inspection and automatic source-backed repair."""
from __future__ import annotations

import json
import os
import tempfile
import shutil
from pathlib import Path
import numpy as np
from scipy.ndimage import label, find_objects, distance_transform_edt, gaussian_filter
from astropy.io import fits
from astropy.wcs import WCS


def inspect_mosaic_holes(data, cancel_cb=None, max_regions=2000):
    """Scan every pixel with 4-connected labels and bounded strip arrays.

    Components are joined across strip boundaries. A component touching any
    outer image edge is uncovered sky, not an enclosed hole. Valid dark/zero
    pixels are never treated as holes. Only active frontier records are kept.
    """
    h, w = data.shape
    rows = max(1, min(256, (16*1024**2)//max(w*8,1)))
    active, previous = {}, np.zeros(w,dtype=np.int64)
    next_id = 1
    regions = []
    stats = dict(internal_holes=0, internal_pixels=0, exterior_pixels=0,
                 invalid_pixels=0, repair_regions_truncated=False)

    def finish(record):
        size,y0,y1,x0,x1,edge,seed = record
        if edge:
            stats['exterior_pixels'] += int(size)
        else:
            stats['internal_holes'] += 1
            stats['internal_pixels'] += int(size)
            if len(regions)<max_regions:
                regions.append(dict(box=[y0,y1,x0,x1],seed=list(seed),pixels=int(size)))
            else:
                stats['repair_regions_truncated'] = True

    for start in range(0,h,rows):
        if cancel_cb and cancel_cb():
            raise RuntimeError('Mosaic quality check cancelled by user.')
        stop=min(h,start+rows)
        bad=~np.isfinite(data[start:stop])
        labels,count=label(bad)
        stats['invalid_pixels'] += int(np.count_nonzero(bad))
        if count>100000:
            raise RuntimeError('Too many isolated invalid pixels in one strip for automatic inspection.')
        counts=np.bincount(labels.ravel(),minlength=count+1)
        records=dict(active)
        parents={i:i for i in records}
        ids=np.zeros(count+1,dtype=np.int64)
        for k,slices in enumerate(find_objects(labels),1):
            if slices is None:
                continue
            sy,sx=slices
            # Representative pixel of this exact component, not its rectangle.
            px=int(np.flatnonzero(labels[sy.start,sx] == k)[0])+sx.start
            uid=next_id
            next_id+=1
            ids[k]=uid
            parents[uid]=uid
            records[uid]=[int(counts[k]),start+sy.start,start+sy.stop,sx.start,sx.stop,
                          start+sy.start==0 or start+sy.stop==h or sx.start==0 or sx.stop==w,
                          (start+sy.start,px)]

        def root(i):
            while parents[i]!=i:
                parents[i]=parents[parents[i]]
                i=parents[i]
            return i

        both=(previous>0)&(labels[0]>0)
        if np.any(both):
            pairs=np.unique(np.column_stack((previous[both],ids[labels[0,both]])),axis=0)
            for a,b in pairs:
                a,b=root(int(a)),root(int(b))
                if a==b:
                    continue
                ra,rb=records[a],records[b]
                parents[b]=a
                records[a]=[ra[0]+rb[0],min(ra[1],rb[1]),max(ra[2],rb[2]),
                            min(ra[3],rb[3]),max(ra[4],rb[4]),ra[5] or rb[5],ra[6]]
                del records[b]
        boundary=ids[labels[-1]]
        for uid in np.unique(boundary):
            if uid:
                boundary[boundary==uid]=root(int(uid))
        frontier=set(int(i) for i in np.unique(boundary) if i)
        for uid,record in records.items():
            if uid not in frontier:
                finish(record)
        active={uid:records[uid] for uid in frontier}
        previous=boundary
    for record in active.values():
        finish(record)
    stats['regions']=regions
    return stats


def automatically_repair_mosaic(path, source_paths, filter_code='', progress_cb=None,
                                cancel_cb=None, report_dir=None, quality_paths=None):
    """Inspect and repair newly generated output, atomically replacing it."""
    from tsdp2.astronomy.seam_repair import _fast_repair_fits_holes
    path=Path(path)
    if progress_cb:
        progress_cb(0,1,'Checking mosaic for internal holes and invalid pixelsÃ¢â‚¬Â¦')
    with fits.open(path,memmap=True,lazy_load_hdus=True) as hdul:
        hdu=next(h for h in hdul if h.data is not None and h.data.ndim==2)
        if not WCS(hdu.header,naxis=2).has_celestial:
            raise ValueError('Mosaic quality check: missing celestial WCS.')
        before=inspect_mosaic_holes(hdu.data,cancel_cb)
        del hdu
    report=dict(filter=filter_code,automatic=True,detected_holes=before['internal_holes'],
                detected_pixels=before['internal_pixels'],repaired_pixels=0,
                exterior_pixels=before['exterior_pixels'],
                repair_regions_truncated=before['repair_regions_truncated'])
    eligible=[r for r in before['regions']
              if (r['box'][1]-r['box'][0]+64)*(r['box'][3]-r['box'][2]+64)<=2_000_000]
    report['deferred_large_holes']=len(before['regions'])-len(eligible)
    report['deferred_disk_holes']=0
    if eligible and shutil.disk_usage(path.parent).free < int(path.stat().st_size*1.08):
        report['deferred_disk_holes']=len(eligible)
        eligible=[]
    if eligible:
        repaired=path.with_name(path.stem+'_auto_repair.fits')
        with tempfile.TemporaryDirectory(prefix='repair-counts-',dir=path.parent) as work:
            updates=[]
            def update_coverage(box,fill,count):
                if not quality_paths or not Path(quality_paths[0]).is_file():
                    return
                patch=Path(work)/f'{len(updates)}.npz'
                np.savez(patch,box=box,indices=np.flatnonzero(fill).astype(np.int32),counts=count[fill])
                updates.append(patch)
            result=_fast_repair_fits_holes(str(path),source_paths,str(repaired),
                filter_override=filter_code,progress_cb=progress_cb,cancel_cb=cancel_cb,
                repair_regions=eligible,repair_cb=update_coverage)
            report['repaired_pixels']=int(result['repaired'])
            if cancel_cb and cancel_cb():
                raise RuntimeError('Automatic repair cancelled by user.')
            os.replace(repaired,path)
            if updates:
                with fits.open(quality_paths[0],mode='update',memmap=True) as maps:
                    for patch in updates:
                        with np.load(patch) as saved:
                            y0,y1,x0,x1=saved['box']
                            iy,ix=np.unravel_index(saved['indices'],(y1-y0,x1-x0))
                            maps[0].data[y0+iy,x0+ix]=saved['counts']
                    for key in ('CHECKSUM','DATASUM'):
                        maps[0].header.remove(key,ignore_missing=True)
                    maps.flush()
        if quality_paths and all(Path(p).is_file() for p in quality_paths):
            with fits.open(quality_paths[0],mode='update',memmap=True) as coverage, \
                    fits.open(quality_paths[1],mode='update',memmap=True) as weight:
                data=coverage[0].data
                maximum=max(1.0,max(float(np.max(data[y:y+256])) for y in range(0,data.shape[0],256)))
                coverage[0].header['COVMAX']=maximum
                weight[0].header['COVMAX']=maximum
                for y in range(0,data.shape[0],256):
                    weight[0].data[y:y+256]=data[y:y+256]/maximum
                for key in ('CHECKSUM','DATASUM'):
                    weight[0].header.remove(key,ignore_missing=True)
                del data
                coverage.flush()
                weight.flush()
    with fits.open(path,memmap=True) as hdul:
        index=next(i for i,h in enumerate(hdul) if h.header.get('NAXIS')==2)
        after=inspect_mosaic_holes(hdul[index].data,cancel_cb)
    del hdul
    with fits.open(path,mode='update',memmap=True) as hdul:
        hdu=hdul[index]  # Header-only edit: no live science mmap during resize.
        report['remaining_holes']=after['internal_holes']
        report['remaining_pixels']=after['internal_pixels']
        hdu.header['AUTOCHK']=(True,'Automatic no-data inspection completed')
        hdu.header['AUTOFIX']=(True,'Source-backed automatic hole repair enabled')
        hdu.header['HOLEPIX']=(int(after['internal_pixels']),'Unrecoverable internal no-data pixels')
        hdu.header['FIXPIX']=(report['repaired_pixels'],'Pixels recovered from valid original data')
        hdu.header.add_history(f"Automatic hole audit: {before['internal_pixels']} detected, "
                               f"{report['repaired_pixels']} repaired, {after['internal_pixels']} remain")
        for key in ('CHECKSUM','DATASUM'):
            hdu.header.remove(key,ignore_missing=True)
        hdul.flush()
    folder=Path(report_dir) if report_dir else path.parent
    folder.mkdir(parents=True,exist_ok=True)
    report_path=folder/(path.stem+'_quality.json')
    report_path.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    report['report_path']=str(report_path)
    print(f"[Mosaic quality] {filter_code}: {report['detected_holes']} internal holes; "
          f"{report['repaired_pixels']} pixels recovered, {report['remaining_pixels']} remain; "
          f"exterior no-data preserved",flush=True)
    return report


def cosmetically_fill_draft(path, mask_path=None, cancel_cb=None):
    """Interpolate only enclosed draft gaps, with a separate provenance mask.

    This does not recover measured data. Valid pixels and exterior gaps stay
    untouched. Science callers must never use this cosmetic draft operation.
    """
    path=Path(path)
    mask_path=Path(mask_path) if mask_path else path.with_name(path.stem+'_interpolated_mask.fits')
    mask_path.parent.mkdir(parents=True,exist_ok=True)
    stage=path.with_name(path.name+'.cosmetic-writing')
    mask_stage=mask_path.with_name(mask_path.name+'.writing')
    if shutil.disk_usage(path.parent).free < path.stat().st_size*1.4:
        return dict(cosmetic_pixels=0,cosmetic_deferred=True)
    shutil.copy2(path,stage)
    filled=0
    try:
        with tempfile.TemporaryDirectory(prefix='draft-fill-',dir=path.parent) as work:
            with fits.open(stage,mode='update',memmap=True) as hdul:
                hdu=next(h for h in hdul if h.data is not None and h.data.ndim==2)
                if not hdu.header.get('FASTDRFT'):
                    raise ValueError('Cosmetic interpolation is restricted to marked Fast Draft images.')
                data=hdu.data
                inspection=inspect_mosaic_holes(data,cancel_cb)
                mask=np.memmap(Path(work)/'mask.u8',mode='w+',dtype='uint8',shape=data.shape)
                try:
                    for y in range(0,data.shape[0],256):mask[y:y+256]=0
                    for region in inspection['regions']:
                        if cancel_cb and cancel_cb():
                            raise RuntimeError('Draft cosmetic filling cancelled by user.')
                        ry0,ry1,rx0,rx1=region['box']
                        y0,y1=max(0,ry0-16),min(data.shape[0],ry1+16)
                        x0,x1=max(0,rx0-16),min(data.shape[1],rx1+16)
                        if (y1-y0)*(x1-x0)>2_000_000:
                            continue
                        patch=np.array(data[y0:y1,x0:x1],dtype=np.float32,copy=True)
                        valid=np.isfinite(patch)
                        labels,_=label(~valid)
                        sy,sx=region['seed']
                        component=int(labels[sy-y0,sx-x0])
                        fill=labels==component if component else np.zeros(labels.shape,dtype=bool)
                        if not np.any(fill) or np.count_nonzero(valid)<16:
                            continue
                        sky=patch[valid]
                        centre=float(np.median(sky))
                        sigma=max(1e-6,1.4826*float(np.median(np.abs(sky-centre))))
                        anchors=valid & (patch<=centre+3.5*sigma)
                        if np.count_nonzero(anchors)<16:
                            anchors=valid
                        # Do not clone bright stars into unobserved detector
                        # gaps; cosmetic fills estimate the local background.
                        nearest=distance_transform_edt(~anchors,return_distances=False,return_indices=True)
                        seeded=patch.copy()
                        seeded[~anchors]=patch[nearest[0][~anchors],nearest[1][~anchors]]
                        smooth=gaussian_filter(seeded,sigma=2,mode='nearest')
                        block=data[y0:y1,x0:x1]
                        block[fill]=smooth[fill]
                        mask[y0:y1,x0:x1][fill]=1
                        filled+=int(np.count_nonzero(fill))
                        del block
                    header=WCS(hdu.header,naxis=2).to_header()
                    header['MASKTYPE']='DRAFT_INTERPOLATED'
                    header['BUNIT']='flag'
                    header['FILLPIX']=filled
                    header.add_history('1 = cosmetic interpolation, not measured science data; 0 = no interpolation')
                    fits.PrimaryHDU(mask,header=header).writeto(mask_stage,overwrite=True)
                finally:
                    mask.flush()
                    mask._mmap.close()
                    del mask
                remaining=inspect_mosaic_holes(data,cancel_cb)['internal_pixels']
                # Header growth may resize/remap the FITS file. Release every
                # external science-array view before Astropy flushes it.
                del data
                hdul.flush()
                del hdu
            del hdul
            with fits.open(stage,mode='update',memmap=True) as metadata:
                hdu=next(h for h in metadata if h.header.get('NAXIS')==2)
                hdu.header['COSMFILL']=(bool(filled),'Draft-only cosmetic interpolation applied')
                hdu.header['COSMPIX']=(filled,'Interpolated draft pixels, not measured data')
                hdu.header['COSMMASK']=mask_path.name
                hdu.header.add_history(f'DRAFT ONLY: {filled} missing pixels cosmetically interpolated; see mask')
                for key in ('CHECKSUM','DATASUM'):hdu.header.remove(key,ignore_missing=True)
                metadata.flush()
                del hdu
            if cancel_cb and cancel_cb():
                raise RuntimeError('Draft cosmetic filling cancelled by user.')
            os.replace(mask_stage,mask_path)
            os.replace(stage,path)
        print(f'[Draft cosmetics] {filled} pixels interpolated; provenance mask: {mask_path.name}',flush=True)
        return dict(cosmetic_pixels=filled,cosmetic_mask=str(mask_path),visible_gap_pixels=remaining,
                    cosmetic_deferred=bool(remaining))
    finally:
        import gc
        data=hdu=hdul=block=None
        gc.collect()
        stage.unlink(missing_ok=True)
        mask_stage.unlink(missing_ok=True)
