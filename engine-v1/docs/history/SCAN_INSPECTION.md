# Scan inspection

> Historical pre-real-scan V0.1 note. See `V0_2_FEATURE_EXTRACTION.md` for the inspected real-cockpit evidence and current architecture.

Inspection date: 2026-08-21

## Supplied data

No Einstar Vega scan was supplied in the task workspace or its attachment directory. The only supplied attachment is the 39,144-byte text specification. In particular, there is currently no OBJ, MTL, texture image, point cloud, Rhino file, or marker-paper geometry to inspect.

Consequently, the following properties are **unknown and deliberately not guessed**:

- mesh vertex and triangle counts;
- world units and scan scale;
- UV coverage, texture paths, image sizes, and missing texture assets;
- connected-component, degeneracy, manifold, and winding characteristics;
- whether marker paper or a machine-readable tag is present in the geometry/texture;
- the scan's physical noise floor and useful decimation resolution.

## Unit policy

AutoDeck calculations are normalized to millimeters. OBJ does not standardize a unit. A recognized comment such as `# units: mm` or explicit `--units mm|cm|m|in` takes priority. For the simplified click-to-run workflow, an undeclared OBJ uses the configurable `mesh.default_input_units` value (`mm` initially) and emits a prominent warning in the report. The input coordinates are never changed on disk. Derived verification geometry is converted back to the input coordinate system before export.

## Texture/marker policy

The OBJ loader records vertex texture coordinates, per-corner texture indices, `mtllib`, and `usemtl` references. ArUco detection is an optional adapter because OpenCV is not part of the core runtime. Until a textured scan is supplied and calibrated UV-to-face mapping is validated, V0.1 accepts explicit three-dimensional seed and marker-paper centers. This is intentional: inventing marker detections would make the geometry experiment invalid.

## First real-data inspection command

```powershell
python -m autodeck inspect --input path\to\scan.obj --units mm --output output\cockpit-01-inspection
```

Review the emitted `scan_inspection.json` and `SCAN_INSPECTION.md` before selecting analysis resolution or tuning any threshold.

## Concrete V0.1 plan based on available evidence

1. Establish a conservative OBJ/MTL/UV inspector and explicit physical-unit boundary.
2. Preserve the original mesh and create a separate cleaned/optionally vertex-clustered analysis mesh with source-vertex provenance.
3. Calculate face/vertex normals, one-ring principal curvature estimates, multi-scale normal variation, and face-transition normal-turn concentration.
4. Keep conformability and structural salience as distinct fields; segment by seeded graph traversal over the latter.
5. Support explicit temporary-paper regions that suppress crossing cost while warning when they overlap strong structural evidence.
6. Extract outer and internal 3-D loops separately, retain on-scan master points, and create a normal-lifted display copy.
7. Export JSON, colored PLY, Rhino-importable OBJ polylines, and optional 3DM when `rhino3dm` is installed.
8. Validate flat, sloped, gradual bend, tight bend, seam, obstruction, and paper-suppression behavior with synthetic meshes before processing a real scan.

## Blocked evidence

Real-data acceptance criteria—marker invariance on the cockpit scan, actual ArUco recovery, paper relief suppression, hatch recognition, and Rhino overlay accuracy—cannot be measured until the referenced OBJ/MTL/textures are supplied. The implementation reports this honestly rather than treating synthetic success as real-scan validation.
