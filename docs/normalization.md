# Mosaic normalization, 3.2.4

The former in-memory feather path compared whole-tile medians. Different galaxy
coverage could therefore become a false sky offset. The former disk/draft path
fit pairwise planes and could compare pixels across gaps at different sky
coordinates, introducing astronomical gradients into the correction.

## Relative overlap model

The new implementation compares finite pixels at the same reprojected sky
coordinates. Spatial cell medians suppress isolated cosmic rays and star/PSF
residuals. Iterative clipping rejects contaminated cells. A joint sparse least
squares fit connects all measured overlaps, with robust residual weights and a
weak penalty on slopes in local tile coordinates. One tile is fixed exactly
in each connected component. Tiles without usable overlap remain independent.

The corrections are additive planes. They do not rescale stellar flux or
attempt an absolute sky calibration. Real extended emission shared by inputs
cancels in overlap differences. A common sky gradient remains unconstrained.
Mean, median, feather, disk-backed combines, drafts, and seam models share this
solver. Sample storage is bounded to eight million float pixels (32 MB), with
at most 200,000 samples per tile; application of planes uses row blocks.
Combined-cache signatures include the algorithm identity.

## Optional source-masked draft sky

The draft cleanup option fits a detector sky plane from sigma-clipped spatial
medians, iteratively excluding bright source cells. This estimates background
across disconnected detectors, before blending, rather than subtracting the
old high-pass mesh from an already combined galaxy. Sparse detector coverage
falls back to a scalar sky estimate when possible. No-data pixels stay NaN
during normalization; existing cosmetic draft hole filling remains separate.

Independent sky subtraction is a cosmetic model. When astronomical emission
fills most of a detector, its background cannot be uniquely identified without
an external reference. Disable the draft sky option for overlap-only processing.
The full-resolution science path uses relative overlaps; separately named
cosmetic high-resolution copies retain their own cleanup operation.

## Validation

Regression tests cover a galaxy plus a real common sky gradient, additive
offsets/slopes, cosmic rays, mismatched stars, narrow overlaps, disconnected
pointings, invalid inputs, mask preservation, source-masked draft contrast,
and actual FITS output through all three combine modes in RAM and on disk.
The release smoke check also executes the sparse normalization solver.

The supplied draft manifest referenced 120 G-band CCD inputs across two
exposures. A bounded real-data test reprojected them onto a 361 x 638 preview
of the supplied WCS footprint. Independently measured median absolute
pair differences fell from about 34.45 with the previous draft matcher to
7.15 with the new model across 43 measured pairs (about 79% lower).
The reduced sampling grid supplied 38 usable model edges; 70 tiles had no
measurable overlap at that sampling, which is why independent draft sky
estimation is useful for this dataset. The reference files were unchanged.

The sampled process RSS was about 125 MiB during the initial reduced test.
This is a sampled measurement for this preview, not a full-resolution memory
ceiling or a throughput benchmark. Full-resolution processing continues to
use the application's RAM and worker planning.

Method references: [Montage background rectification](https://irsa.ipac.caltech.edu/Montage/docs/algorithms.html)
and [STScI sky matching](https://stcal.readthedocs.io/en/stable/stcal/skymatch/description.html).
