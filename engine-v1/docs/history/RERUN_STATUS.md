# Cockpit rerun status

> Historical V0.1 report. See `V0_2_FEATURE_EXTRACTION.md` for current V0.2 real-cockpit runs.

## Real scan

The real 10,267,826-triangle cockpit OBJ/MTL/texture set is not present in this workspace. Only the user-reported metrics from the earlier run and the 768-triangle synthetic fixture are available. AutoDeck therefore did not claim or fabricate a real-cockpit rerun.

When the prepared cockpit OBJ is placed locally, run the baseline first:

```powershell
python -m autodeck analyze --input C:\path\to\cockpit.obj --output output\cockpit-orientation --segmentation-mode orientation
```

Do not proceed with structural tuning until that output has a non-null primary candidate and visible 3DM curve. Then run the controlled comparison:

```powershell
python -m autodeck analyze --input C:\path\to\cockpit.obj --output output\cockpit-advanced --segmentation-mode advanced
```

## Available synthetic end-to-end A/B run

The same 768-triangle millimeter-scale cockpit fixture was run through both modes after implementation.

| Metric | Orientation | Advanced |
|---|---:|---:|
| Stage A eligible faces | 596 | 596 |
| Stage A eligible area | 7,467.568 mm² | 7,467.568 mm² |
| Stage A components | 4 | 4 |
| Stage B components | 4 | 4 |
| Stage C components | 4 | 4 |
| Candidate regions | 2 | 1 |
| Primary area | 7,342.568 mm² | 7,161.008 mm² |
| Primary boundary points | 12 | 42 |
| OBJ curve objects | 6 | 12 |
| Verification 3DM objects | 7 | 13 |
| Runtime | 0.787 s | 0.810 s |
| V0.1 success contract | pass | pass |

These values validate the file/CLI/export path only. They are not evidence of real Vega cockpit accuracy. Detailed artifacts are in `output/synthetic-ab-orientation` and `output/synthetic-ab-advanced`.
