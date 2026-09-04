#!/usr/bin/env python3
"""Render the Boord home-screen icons (crate-and-leaf mark) for each app.

Regenerates frontend/<app>/icons/icon-{512,192,180}.png. Run after changing
the mark or the brand colours:

    python3 scripts/render_icons.py

The Owner app lives in its own repo (BoordOwner), so its icon is only
rendered when you say where to put it:

    python3 scripts/render_icons.py --owner ../BoordOwner/frontend/icons

Needs Pillow. The mark is drawn at 4x and downsampled, so no vector tooling
is required on the machine that builds it.
"""

import argparse
import math
import os

from PIL import Image, ImageDraw

# --- brand palette (frontend/shared/styles.css) ---------------------------
BLUE = (10, 47, 107, 255)     # --boord-blue   #0A2F6B
RED = (200, 16, 46, 255)      # --boord-red    #C8102E
GREEN = (22, 163, 74, 255)    # --boord-green  #16a34a
YELLOW = (234, 179, 8, 255)   # --boord-yellow #eab308
WHITE = (255, 255, 255, 255)

# Same crate for every app; the leaf colour tells them apart on a home
# screen, the way the old "LW"/"PH"/"AD" initials used to. The palette has
# exactly three accents, so Owner - which ships from its own repo anyway -
# takes the monochrome leaf instead of a fourth colour we would have to
# invent. See KEYLINE for what that costs.
APPS = {
    "field": GREEN,
    "packhouse": RED,
    "admin": YELLOW,
}
OWNER_LEAF = WHITE

SIZES = (512, 192, 180)
SS = 4          # supersampling factor
BASE = 512      # design canvas
CORNER = 112    # tile corner radius, design units

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# --- artwork geometry -----------------------------------------------------
# Laid out in the reference drawing's own coordinates, then fitted into the
# design canvas with a margin.
SCALE = 370.0 / 495.0
OX, OY = 114.75, 71.0


def T(p):
    """Reference coords -> supersampled device pixels."""
    x, y = p
    return ((x - 205.0) * SCALE + OX) * SS, ((y - 325.0) * SCALE + OY) * SS


def poly(pts):
    return [T(p) for p in pts]


CRATE_OUTER = [(205, 522), (583, 578), (578, 792), (208, 792)]
CRATE_INNER = [(240, 562), (549, 608), (543, 756), (243, 756)]
FEET = [
    [(208, 792), (258, 792), (258, 822), (216, 822)],
    [(528, 792), (578, 792), (570, 822), (528, 822)],
    [(378, 792), (424, 792), (424, 808), (378, 808)],
]
LATTICE_STEP = 128      # intercept step; line spacing is this over sqrt(2)
LATTICE_WIDTH = 23
RIB_WIDTH = 15
# A white leaf is the same colour as the crate, so the two would fuse into
# one blob where the leaf passes behind the top rail. This cuts a tile-
# coloured gap around the leaf, which is invisible everywhere except there.
KEYLINE = 6  # design units, per side

LEAF_BASE = (380.0, 555.0)
LEAF_TIP = (480.0, 335.0)
LEAF_WIDTH = 135.0
RIB_FOOT = (370.0, 578.0)   # tucks under the top rail rather than showing through the lattice


def quad(p0, p1, p2, steps=72):
    out = []
    for i in range(steps + 1):
        t = i / steps
        u = 1 - t
        out.append((
            u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
            u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1],
        ))
    return out


def leaf_shape():
    bx, by = LEAF_BASE
    tx, ty = LEAF_TIP
    vx, vy = tx - bx, ty - by
    length = math.hypot(vx, vy)
    nx, ny = -vy / length, vx / length          # unit normal
    mx, my = (bx + tx) / 2, (by + ty) / 2
    # A quadratic's peak deviation is half the control offset, so offset by
    # the full width to get a lens LEAF_WIDTH across.
    right = (mx + nx * LEAF_WIDTH, my + ny * LEAF_WIDTH)
    left = (mx - nx * LEAF_WIDTH, my - ny * LEAF_WIDTH)
    outline = quad(LEAF_BASE, right, LEAF_TIP) + quad(LEAF_TIP, left, LEAF_BASE)

    tip_end = (bx + vx * 0.74, by + vy * 0.74)
    rib_mid = ((RIB_FOOT[0] + tip_end[0]) / 2, (RIB_FOOT[1] + tip_end[1]) / 2)
    rib_ctrl = (rib_mid[0] - nx * LEAF_WIDTH * 0.30,
                rib_mid[1] - ny * LEAF_WIDTH * 0.30)
    rib = quad(RIB_FOOT, rib_ctrl, tip_end)
    return outline, rib


def crate_mask(px):
    """Rim + lattice + feet, as a single alpha mask."""
    mask = Image.new("L", (px, px), 0)
    d = ImageDraw.Draw(mask)
    d.polygon(poly(CRATE_OUTER), fill=255)
    d.polygon(poly(CRATE_INNER), fill=0)
    for foot in FEET:
        d.polygon(poly(foot), fill=255)

    inner = Image.new("L", (px, px), 0)
    ImageDraw.Draw(inner).polygon(poly(CRATE_INNER), fill=255)

    lattice = Image.new("L", (px, px), 0)
    ld = ImageDraw.Draw(lattice)
    xs = [p[0] for p in CRATE_INNER]
    ys = [p[1] for p in CRATE_INNER]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    w = int(round(LATTICE_WIDTH * SCALE * SS))
    for sign in (1, -1):
        lo = int(min(x0 - sign * y0, x0 - sign * y1,
                     x1 - sign * y0, x1 - sign * y1))
        hi = int(max(x0 - sign * y0, x0 - sign * y1,
                     x1 - sign * y0, x1 - sign * y1))
        c = lo
        while c <= hi:
            a = T((c + sign * (y0 - 40), y0 - 40))
            b = T((c + sign * (y1 + 40), y1 + 40))
            ld.line([a, b], fill=255, width=w)
            c += LATTICE_STEP

    lattice = Image.composite(lattice, Image.new("L", (px, px), 0), inner)
    return Image.composite(Image.new("L", (px, px), 255), mask, lattice)


def render(leaf_colour, keyline=False):
    px = BASE * SS
    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    tile = Image.new("L", (px, px), 0)
    ImageDraw.Draw(tile).rounded_rectangle(
        [0, 0, px - 1, px - 1], radius=CORNER * SS, fill=255)
    img.paste(Image.new("RGBA", (px, px), BLUE), (0, 0), tile)

    outline, rib = leaf_shape()

    # Leaf and its rib go down first: the crate's top rail overlaps them.
    leaf = Image.new("L", (px, px), 0)
    ImageDraw.Draw(leaf).polygon([T(p) for p in outline], fill=255)
    img.paste(Image.new("RGBA", (px, px), leaf_colour), (0, 0), leaf)

    # The midrib has to contrast with the leaf, not with the crate, so a
    # white (Owner) leaf gets a tile-coloured one.
    rib_colour = BLUE if leaf_colour == WHITE else WHITE
    ribs = Image.new("L", (px, px), 0)
    ImageDraw.Draw(ribs).line(
        [T(p) for p in rib], fill=255,
        width=int(round(RIB_WIDTH * SCALE * SS)), joint="curve")
    img.paste(Image.new("RGBA", (px, px), rib_colour), (0, 0), ribs)

    img.paste(Image.new("RGBA", (px, px), WHITE), (0, 0), crate_mask(px))

    if keyline:
        # Stroke the leaf outline centred on the path, then drop the half
        # that falls inside the leaf, leaving a ring just outside it.
        ring = Image.new("L", (px, px), 0)
        closed = [T(p) for p in outline] + [T(outline[0])]
        ImageDraw.Draw(ring).line(
            closed, fill=255, width=int(2 * KEYLINE * SS), joint="curve")
        ring = Image.composite(Image.new("L", (px, px), 0), ring, leaf)
        img.paste(Image.new("RGBA", (px, px), BLUE), (0, 0), ring)

    return img


def write(art, out_dir):
    for size in SIZES:
        path = os.path.join(out_dir, "icon-%d.png" % size)
        art.resize((size, size), Image.LANCZOS).save(path, optimize=True)
        print("wrote", path)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--owner", metavar="DIR",
                    help="also render the Owner icon into DIR "
                         "(BoordOwner's frontend/icons)")
    args = ap.parse_args()

    for app, leaf_colour in APPS.items():
        write(render(leaf_colour), os.path.join(ROOT, "frontend", app, "icons"))

    if args.owner:
        write(render(OWNER_LEAF, keyline=True), args.owner)


if __name__ == "__main__":
    main()
