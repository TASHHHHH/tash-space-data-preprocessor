# Full-field visual normalization, 3.2.6

Relative overlap matching cannot determine a common sky gradient and has no
constraint between disconnected detector regions. The prior visual cleanup
also removed a high-pass component while retaining broad gradients. The new
global stage models the complete observed background of each visual product.

## Method

At most 500,000 image samples are used. Neighbor differences estimate the noise
in the image's own units. Compact and resolved structure is detected with two
Gaussian scale pairs. Spatial moments separate broad resolved emission from
stellar wings, while elongated boundary structures are excluded from source
detection. The expanded source masks protect stars and galaxies in the sky fit.

Spatial sky cells use asymmetric sigma clipping. Valid cells interpolate under
masked sources. A source-free median surface follows background discontinuities
and broad gradients; a small smoothing kernel suppresses noise in that model.
The additive background is subtracted to a common zero sky level. No local or
global multiplicative photometric gain is applied.

The model is applied in bounded row blocks without loading a full-resolution
correction image. Drafts receive it after the source-backed audit and before
cosmetic gap filling. The high-resolution operation writes a separate copy;
the original science mosaic remains available. WCS and NaN coverage are
preserved. FITS header edits are separate from live science memmaps to avoid
Windows remapping problems on header growth.

This is a visual sky model, not an absolute photometric or sky calibration.
Unmasked faint diffuse emission may enter a background estimate. The estimator
accepts an explicit source mask for programmatic use; measurements should use
the retained science mosaic with an appropriate independently determined sky.

The source-masking approach follows established iterative background estimation
practice described in the [Photutils background guide](https://photutils.readthedocs.io/en/stable/user_guide/background.html).
The implementation uses the project's existing NumPy/SciPy dependencies.

## Tests and real-data check

Synthetic tests combine a known galaxy, stars, noise, detector steps and a broad
sky gradient. They require substantial sky flattening while retaining galaxy
aperture contrast within 12% and stellar peak contrast within 5% of known truth.
Additional tests check invariance when data units change by a million-fold,
explicit masks, no-data preservation, the analysis cap, constant sky,
streamed FITS parity, unchanged originals and cancellation. The packaged smoke
check exercises both a detector step and a gradient.

The supplied 902 x 1595 G-band draft was normalized without feeding a manual
galaxy mask to the estimator. An independent validation divided the image into
40 x 40 cells, excluded the galaxy footprint only from the measurement, and
sigma-clipped stellar pixels within each cell. The P90 minus P10 spread of
cell sky medians fell from 50.53 to 3.50 in the same input units (93.1% lower).
This measures background consistency; it is not a real-data photometry proof.

The global pass plus marked interpolation of 22,769 enclosed draft-gap pixels
took about 2.6 seconds in that run. Analysis used 798 x 451 samples. Timings
depend on data and available resources; this is not a full-resolution benchmark.
