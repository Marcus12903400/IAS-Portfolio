"""Draw a run copy's panels (placed frame), its seams AS CUT (via sheetjob.plan) and
the resulting pieces with their ids, to a PNG.  No matplotlib: PIL only.

  python flatview.py <run_copy_dir> <out.png> [--weight W]

Re-run example:
  cd /d/AutoDeck && PYTHONIOENCODING=utf-8 PYTHONPATH="engine-v1/src;engine;app" \
    .venv/Scripts/python.exe <scratch>/wf/probe-optimiser/flatview.py <scratch>/wf/probe-optimiser/runs/main <scratch>/wf/probe-optimiser/out/main_flat.png
"""

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parent))
from common import config_with  # noqa: E402
from autodeck2 import sheetjob, sheets  # noqa: E402
from autodeck2.config import load_config  # noqa: E402


def main(run_dir: Path, out: Path, weight: float | None = None) -> None:
    config = load_config() if weight is None else config_with(seam_tidiness_weight=weight)
    options = sheets.settings(config)
    step = float(options["sample_step_mm"])
    loops, _pattern, _kind = sheets.read_fitted_dxf(sheetjob.source_dxf(run_dir))
    result = sheetjob.plan(run_dir, config, write_files=False)
    seam_list = [sheets.Seam.from_dict(item) for item in result["seams"]]
    frame, _w = sheetjob.resolve_frame(run_dir, options)
    rotation = sheets.sheet_transform(frame.axis)

    # Work in the SHEET frame (boat along +Y) so the picture reads bow-up.
    def to_sheet(points):
        return np.asarray(points, dtype=float) @ rotation.T

    rings = []
    for panel_id in sorted(loops):
        outer, holes = sheets.classify_loops(loops[panel_id], step)
        if outer is None:
            continue
        pieces, _pw = sheets.split_panel(panel_id, outer, holes, seam_list, options)
        for piece in pieces:
            pts, _s = sheets.sample_loop(piece.outer, 4.0)
            rings.append(("piece", piece.piece_id, to_sheet(pts)))
            for hole in piece.holes:
                hp, _s = sheets.sample_loop(hole, 4.0)
                rings.append(("hole", piece.piece_id, to_sheet(hp)))
    seams_xy = [to_sheet([[s.x1, s.y1], [s.x2, s.y2]]) for s in seam_list]

    allpts = np.vstack([r[2] for r in rings])
    lo = allpts.min(axis=0) - 60
    hi = allpts.max(axis=0) + 60
    scale = 0.22
    width = int((hi[0] - lo[0]) * scale) + 1
    height = int((hi[1] - lo[1]) * scale) + 1
    image = Image.new("RGB", (width, height), (250, 249, 246))
    draw = ImageDraw.Draw(image)

    def px(points):
        points = np.asarray(points, dtype=float)
        return [((p[0] - lo[0]) * scale, (hi[1] - p[1]) * scale) for p in points]

    for kind, piece_id, pts in rings:
        poly = px(pts)
        if kind == "piece":
            draw.polygon(poly, fill=(214, 228, 245), outline=(30, 90, 170))
        else:
            draw.polygon(poly, fill=(250, 249, 246), outline=(30, 90, 170))
    for kind, piece_id, pts in rings:
        if kind != "piece":
            continue
        centre = pts.mean(axis=0)
        x, y = px([centre])[0]
        draw.text((x - 12, y - 6), piece_id, fill=(20, 20, 20))
    for (a, b), seam in zip(seams_xy, seam_list):
        draw.line(px([a, b]), fill=(200, 40, 40), width=2)
        mid = (a + b) / 2.0
        x, y = px([mid])[0]
        draw.text((x + 4, y + 4), seam.seam_id, fill=(160, 20, 20))
    # Axis gridlines every 500 mm in the sheet frame for scale.
    for value in np.arange(np.floor(lo[0] / 500) * 500, hi[0], 500):
        x = (value - lo[0]) * scale
        draw.line([(x, 0), (x, height)], fill=(225, 225, 225))
    for value in np.arange(np.floor(lo[1] / 500) * 500, hi[1], 500):
        y = (hi[1] - value) * scale
        draw.line([(0, y), (width, y)], fill=(225, 225, 225))
    draw.text((6, 6), f"{run_dir.name}: {result['seam_count']} seams, {result['piece_count']} pieces, "
                      f"{len(result['sheets'])} sheets, status {result['status']} (sheet frame, bow = +Y up)",
              fill=(0, 0, 0))
    image.save(out)
    print("wrote", out, image.size)


if __name__ == "__main__":
    run = Path(sys.argv[1])
    out = Path(sys.argv[2])
    weight = float(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[3] == "--weight" else None
    main(run, out, weight)
