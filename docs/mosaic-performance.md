# Mosaic performance checks for 3.2.2

Measured locally on Windows 11, a Ryzen 9 7900X3D (12 cores / 24 threads),
31.0 GiB usable RAM, and local storage. About 9 GiB RAM was available at
the initial inspection. An RTX 4070 was detected; these tests use the CPU.

The short 32 MiB storage test measured about 1,866 MiB/s writing and
3,198 MiB/s reading. Reads can be served from cache; this is not a sustained
storage or cold-read measurement.

## Full FITS workflow comparison

Compared the 3.2.1 tag with the optimized implementation using the same
Python 3.12 environment and dependency versions. Each run loads three
synthetic 1536 x 1536 float32 TAN-WCS FITS images, shifted by 100 pixels,
with noise, different sky levels, and NaN edges. It computes the output WCS,
reprojects with three workers, sky-matches, feather-blends, writes the final
1736 x 1536 FITS, and writes coverage and weight FITS maps. Seed: 123.

| Version | Run 1 | Run 2 | Mean | Highest sampled process RSS |
| --- | ---: | ---: | ---: | ---: |
| 3.2.1 | 1.8340 s | 1.9426 s | 1.8883 s | 1,062.4 MiB |
| 3.2.2 | 1.2846 s | 1.3497 s | 1.3172 s | 312.5 MiB |

This workload took about 30% less time and reached about 71% lower sampled
peak process memory. RSS was sampled every 20 ms, so short-lived peaks may
be missed. Two short runs do not establish sustained throughput or predict
the speed of arbitrary DECam datasets; compression, distortions, disk state,
other applications, and mosaic geometry affect performance.

Both optimized final arrays were compared against the old implementation
with rtol=3e-6, atol=3e-4, equal_nan=True. Coverage arrays matched exactly.
The tolerances cover float32 arithmetic rather than a change in the requested
output resolution or feather weighting. Regression tests additionally cover
subpixel shifts, holes, RA wrap, distorted/wide WCS checks, and low-free-RAM
worker/disk-mode decisions.

## Resource policy

Automatic workers leave two logical CPU threads outside the worker pool and
budget no more than 60% of currently available RAM. The planner accounts for
reprojected tiles and blend/median workspaces, limits workers, and chooses the
disk-backed path when needed. These estimates provide headroom, not an OS
enforced memory limit. Other processes may change available memory during a run.

## Parallel blocks inside large tiles

For a separate two-tile 2304 x 2304 synthetic TAN reprojection/feather check,
two tile workers with one block thread each took 2.337 s; four block threads
per tile took 1.728 s. The arrays matched at rtol=3e-6, atol=3e-5 including NaNs.
This is a single additional short check, not a sustained throughput claim.
Large in-memory tiles can now use up to four block threads each, bounded by
the requested total thread count, two reserved CPU threads, and the same RAM
budget. Small mosaics retain one block thread per tile to avoid overhead.
