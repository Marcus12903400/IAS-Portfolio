# tests/regression/

Reserved for real-boat regression tests once a raw scan is available under
`inputs/boats/`. Keep expectation data compact (expected area/obstacle-count
ranges, expected geometry status, expected max join gap/tangent limits,
expected output file existence) rather than storing multi-gigabyte generated
artifacts here — see `reference-data/legacy/` for the preserved historical
Key West/SeaPro derived data this project currently has, which predates any
raw scan being available in this environment and is not runnable as a live
regression (see `reference-data/legacy/README.md`).
