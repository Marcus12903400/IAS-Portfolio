# AutoDeck V0.2 flattening

All flattened output is labeled **TEST / VERIFICATION GEOMETRY — NOT CNC READY**. No kerf, foam-gap, border, seam-gap, or other manufacturing offset is applied.

## Required planar strategy

`PlanarFlattening` uses accepted interior deck raster samples, excluding the transition edge with a one-pixel interior erosion. It robustly trims surface-to-plane outliers, fits a physical best-fit plane, and builds a local frame:

- the normal points toward world +Z;
- U is the projection of world +X onto the plane;
- V is `normal × U` and corresponds toward world +Y;
- the U/V/normal determinant is measured and must remain right-handed.

Conditioned—not raw—3-D curve points are orthogonally projected to the plane and expressed in U/V millimetres. This differs from dropping Z and preserves dimensions on a sloped plane. Synthetic tests check sloped-plane length and prevent starboard/port mirroring.

Planarity reporting includes RMS, P50, P95, and maximum accepted-surface distance plus broad normal variation. Distortion compares representative neighboring 3-D surface samples with their 2-D projected lengths and reports mean strain, RMS strain, P95 absolute strain, and maximum absolute strain. Curve reports separately compare every important conditioned 3-D length with its 2-D length.

Warning/failure limits are configurable independently for P95, RMS, and maximum strain. Conservative defaults warn at 1% P95, 1% RMS, or 10% maximum. A warning remains visible even when TEST output is explicitly allowed.

## Strategy interface and nonplanar status

The subsystem exposes `FlatteningStrategy`, `PlanarFlattening`, and `MeshSurfaceFlattening`. No reliable libigl/LSCM/ARAP implementation is installed in the current environment. `MeshSurfaceFlattening` therefore fails explicitly as unavailable; AutoDeck does not substitute a custom finite-element solver or independently project points while pretending they were mapped through source triangles.

A future true mesh implementation must store a source triangle ID plus barycentric coordinates for each curve point, flatten the surface without flipped triangles, and map curves through those triangles while preserving holes and topology.

## Output contract

- `flattened_preview.3dm` contains only flat feature curves on class layers and every coordinate has Z=0.
- `flattened_curves.dxf` declares millimetres with `$INSUNITS=4`, excludes calibration/debug geometry, retains closure and class layers, and uses circles/lines when their configured fit error is met or lightweight polylines otherwise.
- `scale_check.dxf` contains a separate 100 mm check square.
- `flatten_report.md` and `feature_summary.json` expose coordinate frame, deviations, planarity, strain, orientation, topology, entities, bounding boxes, lineage, and PASS/WARN/FAIL evidence.
