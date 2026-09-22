"""Draw the Career Agent star, as data.

An ORIGINAL 8-bit sprite. Nothing here is traced, cropped or derived from any
reference image: it is a hand-placed pixel map in this file, and the SVG is
generated from it. That is deliberate. A sprite committed as an opaque blob is
a thing nobody can review; a sprite committed as sixteen rows of characters is
a thing anybody can read, argue with and edit.

    uv run python scripts/make_star.py
    uv run python scripts/make_star.py --show

**The map carries the SHAPE and nothing else.** The outline and the four
shading bands are derived, which is the second reason this is a script rather
than a file: the first version of this star was hand-shaded, character by
character, and it came out as two legs under a horizontal bar. Separating "what
shape is it" from "where does the light fall" made the mistake obvious and the
fix a one-line change.

Writes `star.svg` (the static frame) and `star-twinkle.svg` (a gentle glint).
Both are pure geometry with `shape-rendering: crispEdges`, so they scale to any
size with hard pixel edges and no antialiasing, and need no decoder.

WHERE IT IS USED, and deliberately not in many places: the empty state. A charm
that appears everywhere stops being one.
"""

from __future__ import annotations

import argparse
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "src" / "career_agent" / "web" / "static"

#: The palette, from the art direction. Warm gold with an orange shadow and a
#: cream highlight, over a dark outline that is nearly the ink of the page.
PALETTE: dict[str, str] = {
    "K": "#3a3448",  # outline
    "H": "#fff4c7",  # highlight
    "M": "#fbdd94",  # main
    "W": "#e9a23b",  # warm shadow
    "D": "#c97832",  # deep shadow
}

SIZE = 16

#: The SHAPE, and only the shape. `#` is star, `.` is nothing.
#:
#: A five-pointed star: one point up, two arms out at the shoulders, two legs
#: down. Read it as a picture, because that is the whole point of keeping the
#: sprite here instead of in a binary.
SHAPE: tuple[str, ...] = (
    ".......##.......",
    ".......##.......",
    "......####......",
    "......####......",
    ".....######.....",
    "################",
    ".##############.",
    "..############..",
    "...##########...",
    "...##########...",
    "..############..",
    "..####....####..",
    ".####......####.",
    ".###........###.",
    "##............##",
    "................",
)


def solid(grid: tuple[str, ...]) -> set[tuple[int, int]]:
    return {(x, y) for y, row in enumerate(grid) for x, char in enumerate(row) if char == "#"}


def shade(grid: tuple[str, ...]) -> list[list[str]]:
    """Outline the shape, then light it from the upper left.

    The outline is every solid pixel with at least one empty orthogonal
    neighbour, which is what gives a sprite its hard edge and what stops the
    star dissolving into the page behind it.

    The bands are a single diagonal ramp. A sprite this small cannot carry a
    lighting model and does not need one: four steps along one axis reads as a
    lit object, and anything more elaborate reads as noise at 48 pixels.
    """
    filled = solid(grid)
    out = [["." for _ in range(SIZE)] for _ in range(SIZE)]

    #: Where the ramp starts and ends, measured across the shape rather than
    #: across the canvas, so the full four bands are used whatever the shape is.
    values = {(x, y): (x * 0.62 + y * 0.78) for (x, y) in filled}
    low, high = min(values.values()), max(values.values())
    span = (high - low) or 1

    for x, y in filled:
        edge = any((x + dx, y + dy) not in filled for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))
        if edge:
            out[y][x] = "K"
            continue
        position = (values[(x, y)] - low) / span
        if position < 0.22:
            out[y][x] = "H"
        elif position < 0.52:
            out[y][x] = "M"
        elif position < 0.78:
            out[y][x] = "W"
        else:
            out[y][x] = "D"
    return out


#: The twinkle. Four glints around the star, each lit for a fifth of a 1.1s
#: loop, and every frame is the SAME star: only the glints change, which is
#: why it reads as a sparkle rather than as movement. Nothing spins, nothing
#: bounces, nothing blurs, nothing flashes.
SPARKLES: tuple[tuple[int, int, int], ...] = (
    (13, 2, 1),
    (2, 8, 2),
    (14, 10, 3),
    (5, 14, 4),
)

FRAME_SECONDS = 0.22
LOOP_SECONDS = round(FRAME_SECONDS * 5, 2)


def _rects(pixels: list[list[str]]) -> list[str]:
    """One rect per RUN of identical pixels, not per pixel.

    A naive sprite is 256 rects. Merging horizontal runs takes it to about
    sixty, which matters only because a person reads this file as often as a
    browser does.
    """
    out: list[str] = []
    for y, row in enumerate(pixels):
        x = 0
        while x < SIZE:
            char = row[x]
            if char == ".":
                x += 1
                continue
            run = 1
            while x + run < SIZE and row[x + run] == char:
                run += 1
            out.append(f'<rect x="{x}" y="{y}" width="{run}" height="1" fill="{PALETTE[char]}"/>')
            x += run
    return out


def _glint(x: int, y: int) -> str:
    """A four-pixel sparkle: a plus, one pixel thick."""
    colour = PALETTE["H"]
    return (
        f'<rect x="{x}" y="{y - 1}" width="1" height="3" fill="{colour}"/>'
        f'<rect x="{x - 1}" y="{y}" width="3" height="1" fill="{colour}"/>'
    )


def _svg(body: str, title: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SIZE} {SIZE}"'
        f' width="{SIZE}" height="{SIZE}" shape-rendering="crispEdges" role="img">'
        f"<title>{title}</title>{body}</svg>"
    )


def static_svg() -> str:
    return _svg("".join(_rects(shade(SHAPE))), "Career Agent")


def twinkle_svg() -> str:
    """The same star, with four glints taking turns.

    SMIL rather than CSS, because this is served as a standalone `.svg` and a
    stylesheet inside it would be a second place the palette has to be kept in
    step.

    `prefers-reduced-motion` is honoured by the PAGE, which asks for the static
    file instead. A file that decided for itself would already have been
    downloaded and parsed before it could decide.
    """
    body = "".join(_rects(shade(SHAPE)))
    glints = []
    for x, y, frame in SPARKLES:
        # Five discrete steps over the loop, one of which is this glint's.
        # `calcMode="discrete"` is what makes it a pixel sprite rather than a
        # fade: the value jumps, it does not tween.
        steps = ";".join("1" if step == frame else "0" for step in range(1, 6))
        glints.append(
            f'<g opacity="0">{_glint(x, y)}'
            f'<animate attributeName="opacity" values="{steps}"'
            f' dur="{LOOP_SECONDS}s" repeatCount="indefinite" calcMode="discrete"/></g>'
        )
    return _svg(body + "".join(glints), "Career Agent")


def main() -> int:
    parser = argparse.ArgumentParser(description="Draw the Career Agent star.")
    parser.add_argument("--show", action="store_true", help="print the sprite as text")
    args = parser.parse_args()

    pixels = shade(SHAPE)

    if args.show:
        blocks = {".": "  ", "K": "##", "H": "``", "M": "oo", "W": "xx", "D": "@@"}
        for row in pixels:
            print("".join(blocks[c] for c in row))
        return 0

    for name, svg in (("star.svg", static_svg()), ("star-twinkle.svg", twinkle_svg())):
        (STATIC / name).write_text(svg, encoding="utf-8")
        print(f"static/{name}  {len(svg):,} bytes")

    assert len(SHAPE) == SIZE and all(len(row) == SIZE for row in SHAPE), "the grid is not square"
    assert solid(SHAPE), "the shape is empty"
    used = {char for row in pixels for char in row} - {"."}
    assert used <= set(PALETTE), f"derived a colour the palette does not define: {used}"
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
