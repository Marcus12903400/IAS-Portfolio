"""Generate a realistic synthetic boat-deck scan for development and testing.

AutoDeck's only checked-in fixture is a 768-face cockpit, which is too small to
profile against and far too small to exercise seams (a real deck is metres long
and must be split across 2 m sheets).  This writes a scan that behaves like the
real thing:

  * a boat-shaped deck sole, wide at the stern and tapering to a bow point, so
    the longitudinal axis is unambiguous for `detect_boat_frame`
  * steep floor-to-wall transitions around the perimeter -- the wall line is
    what AutoDeck actually detects
  * raised obstacles (a console pad and two hatches) that become internal
    obstacle contours
  * optional scanner noise
  * a UV map and a matching texture: teak-coloured deck with plank lines,
    pale gelcoat walls, and a deliberate exposure gradient plus a baked shadow
    so texture-assisted detection is tested against realistic nuisance signal,
    not a clean synthetic image

The mesh is a heightfield on a regular grid, so it is manifold by construction
and has no stitching artefacts.  Wall "verticality" is a steep ramp: 350 mm of
rise over a 45 mm band is an ~83 deg slope, which reads as a wall to the
slope-based segmentation while keeping the surface a clean function of (x, y).

Usage
-----
    python tools/make_test_boat.py --out inputs/boats/testboat
    python tools/make_test_boat.py --out .../big --resolution 4 --texture-size 4096
    python tools/make_test_boat.py --out .../small --resolution 25 --no-noise

Writes scan.obj, scan.mtl and scan_diffuse.png into the output directory.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np


# --------------------------------------------------------------------------
# plan shape

def half_width(t: np.ndarray, beam_mm: float) -> np.ndarray:
    """Half-width of the deck at fractional station t (0 = stern, 1 = bow).

    Full beam aft, tapering to a point forward.  The 0.35 exponent keeps the
    stern quarters full and pulls the taper into the forward third, which is
    what makes the bow sides long, gentle, near-constant-radius curves -- the
    feature the auto-fitter is meant to recognise as single multi-metre arcs.
    """

    t = np.clip(t, 0.0, 1.0)
    return 0.5 * beam_mm * np.power(np.clip(1.0 - np.power(t, 2.6), 0.0, 1.0), 0.35)


def signed_distance(x: np.ndarray, y: np.ndarray, length_mm: float, beam_mm: float,
                    stern_round_mm: float) -> np.ndarray:
    """Approximate signed distance into the deck polygon (positive = inside).

    Exact for the straight stern edge and close enough along the gently curved
    sides, which is all the heightfield needs: the ramp only cares about
    distance within a few tens of millimetres of the boundary.
    """

    t = np.clip(x / length_mm, 0.0, 1.0)
    w = half_width(t, beam_mm)
    d_side = w - np.abs(y)
    # Rounded stern: fillet the two aft corners rather than a hard square end.
    d_stern = x - stern_round_mm * (1.0 - np.clip(np.abs(y) / max(beam_mm * 0.5, 1e-6), 0.0, 1.0) ** 2)
    d_bow = length_mm - x
    return np.minimum(np.minimum(d_side, d_stern), d_bow)


def rounded_rect_distance(x: np.ndarray, y: np.ndarray, cx: float, cy: float,
                          half_x: float, half_y: float, radius: float) -> np.ndarray:
    """Signed distance into a rounded rectangle (positive = inside)."""

    dx = np.abs(x - cx) - (half_x - radius)
    dy = np.abs(y - cy) - (half_y - radius)
    outside = np.sqrt(np.maximum(dx, 0.0) ** 2 + np.maximum(dy, 0.0) ** 2)
    inside = np.minimum(np.maximum(dx, dy), 0.0)
    return radius - (outside + inside)


def smoothstep(edge0: float, edge1: float, values: np.ndarray) -> np.ndarray:
    t = np.clip((values - edge0) / max(edge1 - edge0, 1e-9), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


# --------------------------------------------------------------------------
# obstacles

def obstacles(length_mm: float, beam_mm: float) -> list[dict]:
    """Raised pads on the sole.  Positions are fractions of length/beam so the
    layout stays sensible at any boat size."""

    return [
        {"name": "console", "cx": 0.62 * length_mm, "cy": 0.0,
         "hx": 0.085 * length_mm, "hy": 0.20 * beam_mm, "r": 90.0, "height": 210.0},
        {"name": "hatch_port", "cx": 0.30 * length_mm, "cy": -0.21 * beam_mm,
         "hx": 0.055 * length_mm, "hy": 0.10 * beam_mm, "r": 55.0, "height": 26.0},
        {"name": "hatch_stbd", "cx": 0.30 * length_mm, "cy": 0.21 * beam_mm,
         "hx": 0.055 * length_mm, "hy": 0.10 * beam_mm, "r": 55.0, "height": 26.0},
    ]


def surface_height(x: np.ndarray, y: np.ndarray, args: argparse.Namespace) -> np.ndarray:
    """Deck height in mm: sole with camber, steep walls, raised obstacles."""

    d = signed_distance(x, y, args.length, args.beam, args.stern_radius)

    # Sole: slight athwartships camber so it drains, plus a gentle sheer aft-to-fore.
    camber = -args.camber * (np.clip(np.abs(y) / (0.5 * args.beam), 0.0, 1.0) ** 2)
    sheer = args.sheer * np.clip(x / args.length, 0.0, 1.0) ** 2
    sole = camber + sheer

    # Wall: rises as the point crosses outside the deck polygon.
    wall = args.wall_height * (1.0 - smoothstep(0.0, args.wall_band, d))
    z = sole + wall

    for pad in obstacles(args.length, args.beam):
        pd = rounded_rect_distance(x, y, pad["cx"], pad["cy"], pad["hx"], pad["hy"], pad["r"])
        z = z + pad["height"] * smoothstep(0.0, args.obstacle_band, pd)
    return z


# --------------------------------------------------------------------------
# mesh

def build_mesh(args: argparse.Namespace) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Regular-grid heightfield -> (vertices Nx3, uv Nx2, faces Mx3)."""

    margin = args.wall_band * 3.0 + 60.0
    x0, x1 = -margin, args.length + margin
    y0, y1 = -(0.5 * args.beam + margin), (0.5 * args.beam + margin)

    nx = max(int(round((x1 - x0) / args.resolution)) + 1, 4)
    ny = max(int(round((y1 - y0) / args.resolution)) + 1, 4)
    xs = np.linspace(x0, x1, nx)
    ys = np.linspace(y0, y1, ny)
    gx, gy = np.meshgrid(xs, ys, indexing="ij")

    gz = surface_height(gx, gy, args)
    if args.noise > 0.0:
        rng = np.random.default_rng(args.seed)
        # Correlated noise reads like scanner error; pure white noise does not.
        raw = rng.normal(0.0, 1.0, gz.shape)
        kernel = np.array([1.0, 4.0, 6.0, 4.0, 1.0])
        kernel /= kernel.sum()
        smooth = np.apply_along_axis(lambda m: np.convolve(m, kernel, mode="same"), 0, raw)
        smooth = np.apply_along_axis(lambda m: np.convolve(m, kernel, mode="same"), 1, smooth)
        gz = gz + smooth * (args.noise / max(float(smooth.std()), 1e-9))

    vertices = np.column_stack([gx.ravel(), gy.ravel(), gz.ravel()])
    uv = np.column_stack([
        (gx.ravel() - x0) / (x1 - x0),
        (gy.ravel() - y0) / (y1 - y0),
    ])

    # Two triangles per grid cell, consistent counter-clockwise winding seen
    # from +Z so the face normals point up on the sole.
    i, j = np.meshgrid(np.arange(nx - 1), np.arange(ny - 1), indexing="ij")
    a = (i * ny + j).ravel()
    b = a + ny          # +1 in x
    c = b + 1           # +1 in x, +1 in y
    d = a + 1           # +1 in y
    faces = np.empty((len(a) * 2, 3), dtype=np.int64)
    faces[0::2] = np.column_stack([a, b, c])
    faces[1::2] = np.column_stack([a, c, d])
    return vertices, uv, faces


# --------------------------------------------------------------------------
# texture

def build_texture(args: argparse.Namespace) -> "np.ndarray":
    """Top-view diffuse map matching the UV layout: teak sole, pale gelcoat
    walls, darker obstacles, plus an exposure gradient and a baked shadow so
    the image is not trivially separable by a single global threshold."""

    size = args.texture_size
    margin = args.wall_band * 3.0 + 60.0
    x0, x1 = -margin, args.length + margin
    y0, y1 = -(0.5 * args.beam + margin), (0.5 * args.beam + margin)

    # Pixel centres -> world mm, matching build_mesh's UV mapping exactly.
    u = (np.arange(size) + 0.5) / size
    v = (np.arange(size) + 0.5) / size
    gx = x0 + u[:, None] * (x1 - x0)
    gy = y0 + v[None, :] * (y1 - y0)
    gx, gy = np.broadcast_arrays(gx, gy)

    d = signed_distance(gx, gy, args.length, args.beam, args.stern_radius)
    inside = smoothstep(0.0, args.wall_band * 0.55, d)     # 1 on the sole, 0 on the wall

    teak = np.array([0.482, 0.333, 0.184])
    gelcoat = np.array([0.878, 0.882, 0.867])
    caulk = np.array([0.129, 0.122, 0.114])

    # Plank lines run along the boat axis, on centre at teak_spacing.
    phase = np.abs(((gy / args.teak_spacing) % 1.0) - 0.5) * 2.0
    seam = 1.0 - smoothstep(0.0, 0.16, phase)
    grain = 0.045 * np.sin(gx / 21.0) * np.sin(gy / 6.5)

    rgb = teak[None, None, :] * (1.0 + grain[..., None])
    rgb = rgb * (1.0 - seam[..., None]) + caulk[None, None, :] * seam[..., None]
    rgb = rgb * inside[..., None] + gelcoat[None, None, :] * (1.0 - inside[..., None])

    for pad in obstacles(args.length, args.beam):
        pd = rounded_rect_distance(gx, gy, pad["cx"], pad["cy"], pad["hx"], pad["hy"], pad["r"])
        mask = smoothstep(0.0, args.obstacle_band, pd)[..., None]
        rgb = rgb * (1.0 - mask) + np.array([0.216, 0.227, 0.239])[None, None, :] * mask

    # Nuisance signal: a diagonal exposure gradient and one soft baked shadow.
    # Detection must survive these; a naive global colour threshold will not.
    exposure = 0.80 + 0.34 * (gx - x0) / (x1 - x0) + 0.12 * (gy - y0) / (y1 - y0)
    shadow_r = np.sqrt((gx - 0.42 * args.length) ** 2 + (gy + 0.28 * args.beam) ** 2)
    shadow = 1.0 - 0.32 * (1.0 - smoothstep(0.0, 0.30 * args.beam, shadow_r))
    rgb = rgb * (exposure * shadow)[..., None]

    rng = np.random.default_rng(args.seed + 7)
    rgb = rgb + rng.normal(0.0, 0.012, rgb.shape)
    return np.clip(rgb, 0.0, 1.0)


# --------------------------------------------------------------------------
# writing

def write_obj(path: Path, vertices: np.ndarray, uv: np.ndarray, faces: np.ndarray,
              mtl_name: str, material: str) -> None:
    """OBJ with v/vt/f, written in vectorised blocks (a per-line Python loop
    over a million faces is unusably slow)."""

    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write("# units: mm\n")
        stream.write(f"# generated by tools/make_test_boat.py\n")
        stream.write(f"mtllib {mtl_name}\n")

        block = 200_000
        for start in range(0, len(vertices), block):
            chunk = vertices[start:start + block]
            stream.write("\n".join(
                "v %.4f %.4f %.4f" % (p[0], p[1], p[2]) for p in chunk
            ))
            stream.write("\n")
        for start in range(0, len(uv), block):
            chunk = uv[start:start + block]
            stream.write("\n".join("vt %.6f %.6f" % (p[0], p[1]) for p in chunk))
            stream.write("\n")

        stream.write(f"usemtl {material}\n")
        one = faces + 1
        for start in range(0, len(one), block):
            chunk = one[start:start + block]
            stream.write("\n".join(
                "f %d/%d %d/%d %d/%d" % (f[0], f[0], f[1], f[1], f[2], f[2]) for f in chunk
            ))
            stream.write("\n")


def write_mtl(path: Path, material: str, texture_name: str) -> None:
    path.write_text(
        f"newmtl {material}\n"
        "Ka 1.000 1.000 1.000\n"
        "Kd 1.000 1.000 1.000\n"
        "Ks 0.050 0.050 0.050\n"
        "Ns 12.0\n"
        "d 1.0\n"
        "illum 2\n"
        f"map_Kd {texture_name}\n",
        encoding="utf-8",
    )


def write_png(path: Path, rgb: np.ndarray) -> None:
    from PIL import Image

    # Texture rows are V; OBJ V runs bottom-up, PNG rows run top-down.
    image = (np.transpose(rgb, (1, 0, 2))[::-1] * 255.0 + 0.5).astype(np.uint8)
    Image.fromarray(image, mode="RGB").save(path, optimize=True)


# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a synthetic textured boat-deck scan.")
    parser.add_argument("--out", required=True, type=Path, help="output directory")
    parser.add_argument("--length", type=float, default=5200.0, help="deck length mm (default 5200)")
    parser.add_argument("--beam", type=float, default=2300.0, help="deck beam mm (default 2300)")
    parser.add_argument("--resolution", type=float, default=7.0, help="grid spacing mm (default 7)")
    parser.add_argument("--wall-height", type=float, default=360.0)
    parser.add_argument("--wall-band", type=float, default=45.0, help="floor-to-wall transition width mm")
    parser.add_argument("--obstacle-band", type=float, default=18.0)
    parser.add_argument("--stern-radius", type=float, default=180.0)
    parser.add_argument("--camber", type=float, default=14.0)
    parser.add_argument("--sheer", type=float, default=26.0)
    parser.add_argument("--teak-spacing", type=float, default=63.5)
    parser.add_argument("--noise", type=float, default=0.45, help="scanner noise sigma mm (0 disables)")
    parser.add_argument("--no-noise", dest="noise", action="store_const", const=0.0)
    parser.add_argument("--texture-size", type=int, default=2048)
    parser.add_argument("--no-texture", action="store_true", help="write an untextured OBJ (no vt/mtl)")
    parser.add_argument("--seed", type=int, default=20260831)
    args = parser.parse_args()

    out = args.out.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)

    print(f"Building mesh at {args.resolution} mm resolution...")
    vertices, uv, faces = build_mesh(args)
    print(f"  {len(vertices):,} vertices, {len(faces):,} faces")

    if args.no_texture:
        obj = out / "scan.obj"
        with obj.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write("# units: mm\n")
            for start in range(0, len(vertices), 200_000):
                stream.write("\n".join("v %.4f %.4f %.4f" % (p[0], p[1], p[2])
                                       for p in vertices[start:start + 200_000]))
                stream.write("\n")
            one = faces + 1
            for start in range(0, len(one), 200_000):
                stream.write("\n".join("f %d %d %d" % (f[0], f[1], f[2])
                                       for f in one[start:start + 200_000]))
                stream.write("\n")
        print(f"Wrote {obj} ({obj.stat().st_size / 1e6:.1f} MB)")
        return 0

    print(f"Painting {args.texture_size}x{args.texture_size} texture...")
    rgb = build_texture(args)
    write_png(out / "scan_diffuse.png", rgb)
    write_mtl(out / "scan.mtl", "deck", "scan_diffuse.png")

    print("Writing OBJ...")
    write_obj(out / "scan.obj", vertices, uv, faces, "scan.mtl", "deck")

    for name in ("scan.obj", "scan.mtl", "scan_diffuse.png"):
        size = (out / name).stat().st_size
        print(f"  {name:20s} {size / 1e6:8.2f} MB")
    print(f"\nDeck {args.length:.0f} x {args.beam:.0f} mm "
          f"-- needs at least {math.ceil(args.length / 2006.6)} pieces along the boat axis "
          f"at the 79 in sheet limit.")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
