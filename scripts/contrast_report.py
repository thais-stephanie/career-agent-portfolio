"""Re-measure every ink-and-ground pair in the stylesheet.

The palette is made of pastels, and a pastel is never a text colour: `#84E2CA`
on the paper is 1.47:1 against a floor of 4.5:1. So every candy family ships as
a WASH plus an INK chosen to clear on it, and "chosen to clear" is a claim that
has to be checkable rather than remembered.

This reads the tokens out of `app.css` itself, so it cannot drift from the file
it is describing. A colour edited by hand shows up here on the next run.

    uv run python scripts/contrast_report.py
    uv run python scripts/contrast_report.py --json

The browser suite measures the RENDERED pixels in both themes, which is the
stronger check and the slower one. This is the fast one, and it is what makes
the ratios written into the stylesheet comments true.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from functools import lru_cache
from pathlib import Path

CSS = Path(__file__).resolve().parents[1] / "src" / "career_agent" / "web" / "static" / "app.css"

#: A closing brace alone on its line, which is what ends the `:root` block.
ROOT_CLOSE = "\n}\n"

#: WCAG AA for normal text. Large text is allowed 3:1 and nothing here relies
#: on that, because a tag rendered at 11px is not large text by any reading.
FLOOR = 4.5

#: Decorative punctuation is allowed the 3:1 non-text floor. It is aria-hidden
#: and carries no word, which is exactly the condition that permits it.
DECORATIVE = {"--sep", "--d-sep"}
DECORATIVE_FLOOR = 3.0


def _srgb(component: float) -> float:
    return component / 12.92 if component <= 0.04045 else ((component + 0.055) / 1.055) ** 2.4


def luminance(colour: str) -> float:
    value = colour.lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    red, green, blue = (int(value[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * _srgb(red) + 0.7152 * _srgb(green) + 0.0722 * _srgb(blue)


def ratio(one: str, two: str) -> float:
    first, second = luminance(one), luminance(two)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def read_tokens(path: Path | None = None) -> dict[str, str]:
    """`--name -> #rrggbb`, resolved through one level of `var()`.

    Only hex values are returned. `rgba()` veils and the shape tokens are not
    colours anything is measured against.
    """
    text = (path or CSS).read_text(encoding="utf-8")
    # ONLY the `:root` block. The two theme-switch blocks further down assign
    # `--mint-ink: var(--d-mint-ink)`, and reading the whole file lets those
    # overwrite the light literals -- which reported `--mint-ink on --mint` as
    # 1.00:1, a colour measured against itself. That was this function being
    # wrong, not the palette.
    start = text.index(":root {")
    end = text.index(ROOT_CLOSE, start)
    root = text[start:end]
    raw: dict[str, str] = {}
    for name, value in re.findall(r"^\s*(--[a-z0-9-]+):\s*([^;]+);", root, re.M):
        raw[name] = value.strip()

    resolved: dict[str, str] = {}
    for name, value in raw.items():
        seen = 0
        while value.startswith("var(") and seen < 4:
            inner = value[4:].split(")")[0].split(",")[0].strip()
            value = raw.get(inner, "")
            seen += 1
        if re.fullmatch(r"#[0-9a-fA-F]{3,8}", value):
            resolved[name] = value
    return resolved


#: Which ink sits on which ground, by name. Written out rather than inferred,
#: because "these two are used together" is a fact about the RULES in the file
#: and guessing it from the token names would be a different, weaker claim.
LIGHT_PAIRS: tuple[tuple[str, str], ...] = (
    ("--ink", "--paper"),
    ("--ink", "--paper-2"),
    ("--ink", "--paper-3"),
    ("--ink", "--paper-raised"),
    ("--ink-soft", "--paper"),
    ("--ink-soft", "--paper-2"),
    ("--ink-soft", "--paper-3"),
    ("--ink-faint", "--paper"),
    ("--ink-faint", "--paper-2"),
    ("--ink-faint", "--paper-3"),
    ("--ink-faint", "--paper-sunk"),
    ("--sep", "--paper"),
    ("--focus", "--paper"),
    # THE PRIMARY ACTION. Measured since the coral button was removed: it is
    # the design's dark ground with light text now, in both themes, and it is
    # the only ink/ground pair in the product that INVERTS between them.
    ("--btn-fg", "--btn-bg"),
)

#: The design's accents, by their design names. `lime`, `butter` and `coral`
#: were the old palette's families and survive only as aliases pointing at
#: `mint-soft`, `yellow` and `pink`; measuring them here would be measuring
#: three tokens twice under names the design does not use.
FAMILIES = ("mint", "yellow", "pink", "lilac", "orange", "blue", "sand", "stone")


def pairs_for(theme: str) -> list[tuple[str, str]]:
    """Every pair to measure, for `light` or `dark`."""
    prefix = "--d-" if theme == "dark" else "--"

    def token(name: str) -> str:
        return f"{prefix}{name}"

    out: list[tuple[str, str]] = []
    for ink, ground in LIGHT_PAIRS:
        out.append((token(ink[2:]), token(ground[2:])))
    # THE DESIGN'S RULE, AND IT IS WHY THIS LOOP IS SHORTER THAN IT WAS.
    #
    # Every accent is a GROUND, and the text on a ground is `--ink` or, for a
    # quiet second line, `--body`. There is no per-family ink to measure any
    # more, and `--muted` is deliberately NOT in this set: it clears the floor
    # on paper and on the alternate surface and does not clear it on an accent
    # -- 4.46:1 on soft mint, 3.88 on soft pink. That is a fact about where a
    # quiet grey may be used, so it is enforced here by absence rather than by
    # softening a token until every combination happens to pass.
    for family in FAMILIES:
        # A STRONG accent takes `--ink` and nothing else. In the dark theme the
        # accents are deep -- mint is a forest at `#2f7a5a` -- and `--body`
        # there is 3.20:1. That is not a token to soften; it is a combination
        # the design does not permit, so it is not measured and must not be
        # written.
        out.append((token("ink"), token(family)))
        # A SOFT accent is a quiet ground and takes the quiet second line too.
        soft = f"{family}-soft"
        if f"{prefix}{soft}" in _declared():
            out.append((token("ink"), token(soft)))
            out.append((token("body"), token(soft)))
    # The tints, which are whole-card grounds rather than chips.
    for tint in ("tint-warm", "tint-red", "tint-metric", "surface", "surface-alt"):
        for ink in ("ink", "body", "muted", "faint"):
            out.append((token(ink), token(tint)))
    return out


@lru_cache(maxsize=2)
def _declared() -> frozenset[str]:
    """Token names that actually exist, so a family with no soft variant is
    skipped rather than reported as a missing colour."""
    return frozenset(read_tokens())


def report(path: Path | None = None) -> tuple[list[dict], list[dict]]:
    tokens = read_tokens(path)
    rows: list[dict] = []
    failures: list[dict] = []
    for theme in ("light", "dark"):
        for ink, ground in pairs_for(theme):
            if ink not in tokens or ground not in tokens:
                continue
            floor = DECORATIVE_FLOOR if ink in DECORATIVE else FLOOR
            value = ratio(tokens[ink], tokens[ground])
            row = {
                "theme": theme,
                "ink": ink,
                "ink_hex": tokens[ink],
                "ground": ground,
                "ground_hex": tokens[ground],
                "ratio": round(value, 2),
                "floor": floor,
                "ok": value >= floor,
            }
            rows.append(row)
            if not row["ok"]:
                failures.append(row)
    return rows, failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    rows, failures = report()
    if args.json:
        print(json.dumps({"rows": rows, "failures": failures}, indent=2))
        return 1 if failures else 0

    theme = ""
    for row in rows:
        if row["theme"] != theme:
            theme = row["theme"]
            print(f"\n{theme.upper()}")
        mark = "ok  " if row["ok"] else "FAIL"
        print(
            f"  {mark} {row['ink']:20s} on {row['ground']:20s}"
            f" {row['ratio']:6.2f}:1  (floor {row['floor']})"
        )

    print(f"\n{len(rows)} pairs measured, {len(failures)} below their floor")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
