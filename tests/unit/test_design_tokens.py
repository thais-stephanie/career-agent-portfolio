"""The stylesheet's colour system, checked rather than trusted.

Two guards, and both of them caught something real the day they were written.

**The token graph.** `border: var(--hair) solid var(--rule)` shipped in the V3
interface pass with no `--hair` declared anywhere. An undefined custom property
with no fallback does not fall back to a default: it makes the WHOLE
declaration invalid, so that border was never drawn and nothing said so. Three
more tokens were used with fallbacks that were therefore always taken, which is
the same defect wearing a disguise.

**The contrast.** Every tone in the candy palette is a pastel, and a pastel is
never a text colour: `#84E2CA` on the paper is 1.47:1 against a floor of 4.5:1.
So each family ships as a wash plus an ink measured to clear on it, and this is
that measurement, in both themes, over the tokens as they are declared today.

The browser suite measures the RENDERED pixels, which is a stronger claim and a
much slower one. These run in milliseconds and fail on the edit that caused the
problem rather than three files away.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CSS = REPO_ROOT / "src" / "career_agent" / "web" / "static" / "app.css"
SCRIPTS = REPO_ROOT / "scripts"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from contrast_report import ROOT_CLOSE, luminance, ratio, read_tokens, report  # noqa: E402

STYLESHEET = CSS.read_text(encoding="utf-8")
ROOT = STYLESHEET[: STYLESHEET.index(ROOT_CLOSE)]

DECLARED = set(re.findall(r"^\s*(--[a-z0-9-]+):", ROOT, re.M))
USED = set(re.findall(r"var\((--[a-z0-9-]+)", STYLESHEET))
LIGHT = {name for name in DECLARED if not name.startswith("--d-")}
DARK_MIRRORS = {f"--{name[4:]}" for name in DECLARED if name.startswith("--d-")}
ASSIGNED = set(
    re.findall(r"^\s*(--[a-z0-9-]+): var\(--d-", STYLESHEET[STYLESHEET.index(ROOT_CLOSE) :], re.M)
)


# =========================================================================
# the graph
# =========================================================================


def test_every_token_the_stylesheet_uses_is_declared() -> None:
    """An undefined custom property invalidates its whole declaration.

    It does not fall back to an initial value and it does not warn. The rule
    is simply dropped, so the effect is a border that is not drawn, a colour
    that is not applied, a radius that does nothing, and no error anywhere.

    `--hair` shipped that way. `--paper-raised`, `--paper-sunk` and
    `--radius-sm` shipped with fallbacks that were therefore taken every
    single time, which looks fine and means the token does not exist.
    """
    missing = sorted(USED - DECLARED)
    assert not missing, f"used and never declared: {missing}"


def test_no_declared_token_is_unused() -> None:
    """A palette carries five families. It used to carry eight.

    `--sage`, `--dusty-blue` and `--terracotta` were three more hues in a
    system that has exactly five jobs to do. A token nobody references is a
    colour nobody has to justify, and that is how a palette becomes a list.
    """
    # The dark mirrors are read through the switch blocks, not through `var()`
    # at a use site, so they are exempt by construction.
    unused = sorted(LIGHT - USED - {"--d-focus"})
    assert not unused, f"declared and never used: {unused}"


def test_every_dark_mirror_is_assigned_by_both_theme_switches() -> None:
    """The assignment list appears twice and the literals do not.

    That is the trade the stylesheet states: a duplicated assignment that
    drifts produces a MISSING token, which is visible immediately, while a
    duplicated hex that drifts produces two slightly different products and
    nobody notices for months. This is the half that makes it a good trade.
    """
    unassigned = sorted(DARK_MIRRORS - ASSIGNED)
    assert not unassigned, f"has a --d-* twin the switch never assigns: {unassigned}"

    orphaned = sorted(ASSIGNED - DARK_MIRRORS)
    assert not orphaned, f"the switch assigns a token with no --d-* twin: {orphaned}"

    media = STYLESHEET.count("@media (prefers-color-scheme: dark)")
    assert media >= 1, "the no-JavaScript path lost its media query"
    assert '[data-theme="dark"]' in STYLESHEET, "the explicit choice lost its selector"


# =========================================================================
# the contrast
# =========================================================================


def test_every_ink_clears_its_ground_in_both_themes() -> None:
    """WCAG AA for normal text, measured over the tokens as declared."""
    rows, failures = report(CSS)
    assert rows, "no pairs were measured at all, which is not a pass"
    described = [
        f"{row['theme']}: {row['ink']} {row['ink_hex']} on {row['ground']} "
        f"{row['ground_hex']} = {row['ratio']}:1 (floor {row['floor']})"
        for row in failures
    ]
    assert not failures, "\n".join(described)


@pytest.mark.parametrize("family", ["mint", "lime", "butter", "coral", "lilac"])
def test_no_candy_tone_is_ever_a_text_colour_on_the_page(family: str) -> None:
    """The constraint the whole wash-and-ink structure exists to satisfy.

    Somebody will eventually reach for `color: var(--mint)` because it is the
    brand colour and it looks right on a mockup. On this paper it is 1.47:1.
    """
    tokens = read_tokens(CSS)
    for name in (f"--{family}", f"--{family}-wash"):
        measured = ratio(tokens[name], tokens["--paper"])
        assert measured < 3.0, (
            f"{name} now clears {measured:.2f}:1 on paper. If that is deliberate the palette "
            "changed; if it is not, a pastel has been darkened into an ink by accident."
        )


def test_the_dark_theme_is_warm_rather_than_black() -> None:
    """A pure black ground beside a warm cream is two products, not two themes.

    Checked as a colour rather than as a comment: the channels have to differ,
    and none of them may be zero.
    """
    tokens = read_tokens(CSS)
    for name in ("--d-paper", "--d-paper-2", "--d-paper-3"):
        value = tokens[name].lstrip("#")
        channels = [int(value[i : i + 2], 16) for i in (0, 2, 4)]
        assert min(channels) > 0, f"{name} is pure black in at least one channel"
        assert max(channels) - min(channels) >= 2, f"{name} carries no warmth: {tokens[name]}"


#: The design's accents, by the design's own names.
ACCENTS = ("mint", "yellow", "pink", "lilac", "orange", "blue", "sand", "stone")


def test_an_accent_is_light_in_one_theme_and_deep_in_the_other() -> None:
    """The decision that makes the pair read as one product, and it REVERSED.

    The palette this replaced kept the same five hexes in both themes: a candy
    tone was a background on paper and the INK on charcoal. The Claude Design
    does the opposite -- every accent goes deep in the dark, mint from
    `#a8f0cc` to a `#2f7a5a` forest -- and the ink stays light on top of it.

    Either arrangement is coherent. What is not is half of each, so this pins
    the one now in force: a light accent must be light, its dark twin must be
    dark, and they must not be the same hex.
    """
    tokens = read_tokens(CSS)
    for family in ACCENTS:
        light = tokens[f"--{family}"]
        dark = tokens[f"--d-{family}"]
        assert light != dark, f"{family}: the two themes share one hex"
        assert luminance(light) > 0.45, f"{family}: the light accent is not light"
        assert luminance(dark) < 0.30, f"{family}: the dark accent is not deep"


def test_the_ink_on_an_accent_follows_the_page() -> None:
    """`--on-candy` follows `--ink` now, and that is the whole rule.

    It used to be pinned dark in BOTH themes, because the old accents stayed
    light in the dark theme and cream on `#84E2CA` is 1.31:1. Here the accents
    go deep, so a pinned dark ink would be `#1a1a1a` on `#2f7a5a` -- 2.4:1, and
    the exact defect the pinning existed to prevent, arriving from the other
    side.

    So the design's rule is the simple one: text on an accent is `--ink`. This
    asserts it holds as a RATIO in both themes rather than as a hex, because
    what matters is that the text can be read.
    """
    tokens = read_tokens(CSS)
    assert tokens["--on-candy"] == tokens["--ink"]
    assert tokens["--d-on-candy"] == tokens["--d-ink"]
    for family in ACCENTS:
        assert ratio(tokens["--on-candy"], tokens[f"--{family}"]) >= 4.5, family
        assert ratio(tokens["--d-on-candy"], tokens[f"--d-{family}"]) >= 4.5, family


# =========================================================================
# the anti-slop rules, as assertions
# =========================================================================


#: The gradients this design allows, and what each one is FOR.
#:
#: Declared the way the punctuation gate declares its exclusions: by name,
#: with a reason, so an exception has to be argued for rather than added. A
#: blanket ban would have been dishonest, because one of these is an
#: affordance rather than decoration.
ALLOWED_GRADIENTS: dict[str, str] = {
    ".tablescrollwrap::before": "the scroll edge: it says there is more table to the left",
    ".tablescrollwrap::after": "the scroll edge: it says there is more table to the right",
    ".select": (
        "the dropdown caret. Two 5px triangles, drawn with `linear-gradient` "
        "because a `<select>` is a replaced element and cannot carry a "
        "`::after`. It is an AFFORDANCE -- it says this opens -- and the "
        "alternative was a wrapper element at every call site or the "
        "platform's own caret, which is the one part of a native select that "
        "gives it away."
    ),
}


def test_the_stylesheet_carries_no_decorative_gradient_blur_or_glow() -> None:
    """The visual direction says so, and a direction nobody checks is a mood.

    These are the AI-dashboard tells: a gradient behind a heading, a blurred
    backdrop, a coloured glow standing in for depth. None of them belongs in a
    product made of paper, outlines and hard offsets.

    Gradients are allowed only where one is doing a JOB, and the two that are
    have to be listed above with what the job is.
    """
    for banned, reason in (
        ("backdrop-filter", "glassmorphism"),
        ("blur(", "a blur"),
        ("text-shadow", "a glow"),
        ("radial-gradient", "a decorative gradient"),
        ("conic-gradient", "a decorative gradient"),
    ):
        assert banned not in STYLESHEET, f"the stylesheet grew {reason}"

    # Every remaining gradient must belong to a declared exception. The rule
    # is found by walking back to the selector above it.
    for match in re.finditer(r"linear-gradient", STYLESHEET):
        head = STYLESHEET[: match.start()]
        selector = head[head.rindex("}") :] if "}" in head else head
        named = [name for name in ALLOWED_GRADIENTS if name in selector]
        assert named, (
            f"a gradient appeared outside the declared exceptions, in: {selector.strip()[:120]!r}"
        )


def _resolve(value: str, tokens_raw: dict[str, str]) -> str:
    """Expand `var(--x)` inside a declaration, so it can be read as lengths."""
    for _ in range(4):
        found = re.search(r"var\((--[a-z0-9-]+)\)", value)
        if not found:
            break
        value = value.replace(found.group(0), tokens_raw.get(found.group(1), "0"))
    return value


def test_no_shadow_is_soft() -> None:
    """Every shadow in this design is a HARD offset with no blur.

    `0 2px 8px rgba(...)` is the floating-card look the direction rules out. A
    hard offset reads as a physical object on a desk; a soft one reads as a
    card hovering over grey, which is every dashboard ever made.

    The declarations are resolved through their tokens first, because
    `box-shadow: var(--shadow)` says nothing on its own and checking the
    literal text would have passed whatever `--shadow` happened to hold.
    """
    raw = dict(re.findall(r"^\s*(--[a-z0-9-]+):\s*([^;]+);", ROOT, re.M))
    soft: list[str] = []
    for value in re.findall(r"box-shadow:\s*([^;]+);", STYLESHEET):
        if value.strip() == "none":
            continue
        resolved = _resolve(value, raw)
        # `[inset] X Y BLUR [SPREAD] colour`. The blur is the third length and
        # it has to be a bare zero.
        lengths = re.findall(r"(-?[\d.]+)(?:px)?", resolved.split("var(")[0])
        if len(lengths) >= 3 and float(lengths[2]) != 0:
            soft.append(f"{value.strip()}  ->  {resolved.strip()}")
    assert not soft, f"soft shadows: {soft}"


# =========================================================================
# The mistake the dark theme makes possible
# =========================================================================

#: Token names that are an ACCENT: a fill, a loud ground, a chip's background.
STRONG_TONES = frozenset(
    {
        "--mint",
        "--mint-soft",
        "--lilac",
        "--lilac-soft",
        "--yellow",
        "--yellow-soft",
        "--pink",
        "--pink-soft",
        "--orange",
        "--blue",
        "--emerald",
        "--sand",
        "--stone",
        # the old family names, still reaching these through the alias layer
        "--lime",
        "--butter",
        "--coral",
        # and the three measurements, whichever accent they currently wear
        "--match",
        "--detail",
        "--block",
        "--clear",
    }
)

#: The only inks the design permits on an accent.
#:
#: `--ink` and `--on-candy` are the same value now and both spellings are in
#: the file; `--body` is the quiet second line and the contrast report proves
#: it clears on the soft tones; the `--*-ink` aliases all resolve to `--ink`
#: and are retired view by view.
INKS_ALLOWED_ON_AN_ACCENT = frozenset(
    {
        "--ink",
        "--on-candy",
        "--body",
        "--btn-fg",
        "--match-ink",
        "--block-ink",
        "--clear-ink",
        "--detail-ink",
        "--mint-ink",
        "--lime-ink",
        "--butter-ink",
        "--coral-ink",
        "--lilac-ink",
    }
)


def _rules() -> list[tuple[str, str]]:
    """`(selector, body)` for every rule in the stylesheet.

    Deliberately a regex rather than a parser. There is no CSS parser in this
    project's dependencies, adding one to check a naming convention would be a
    dependency for a lint, and the shape being looked for is a declaration on
    its own line inside a flat rule, which is all this file contains.
    """
    return [
        (match.group(1).strip(), match.group(2))
        for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", STYLESHEET)
    ]


def test_only_the_permitted_ink_sits_on_an_accent() -> None:
    """The design's one colour rule, enforced over the rules that paint.

    Text on an accent is `--ink`. Not a family ink, not a grey, not a muted
    metadata colour: `--ink`, which is dark on the light pastels and light on
    the deep ones, so a chip needs no per-theme exception and no second
    decision.

    This replaces a guard that asserted the OPPOSITE, and that guard was right
    under the palette before this one. There `--lime-ink` and `--lime` were the
    same hex in the dark theme, so `background: var(--lime); color:
    var(--lime-ink)` rendered 1.00:1 -- it shipped as a solid green dot in the
    rail with an invisible number in it.

    That whole class of defect is gone with the wash-and-ink structure. What
    can go wrong NOW is the mirror image: a quiet grey landing on a fill, which
    measures 4.46:1 on soft mint and 3.88 on soft pink and is exactly as
    unreadable. So the rule flipped from "never this ink" to "only these".
    """
    offenders = []
    for selector, body in _rules():
        background = re.search(r"background(?:-color)?:\s*var\((--[a-z-]+)\)", body)
        foreground = re.search(r"(?<!-)color:\s*var\((--[a-z-]+)\)", body)
        if not background or not foreground:
            continue
        if (
            background.group(1) in STRONG_TONES
            and foreground.group(1) not in INKS_ALLOWED_ON_AN_ACCENT
        ):
            offenders.append(
                f"{selector.splitlines()[-1].strip()[:60]}:"
                f" {foreground.group(1)} on {background.group(1)}"
            )
    assert not offenders, (
        "text on an accent must be --ink; these are something else:\n" + "\n".join(offenders)
    )
