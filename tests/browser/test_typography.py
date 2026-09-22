"""The design's typefaces are served from this machine, and only from it.

WHY THIS IS A TEST AND NOT A PREFERENCE
---------------------------------------
`docs/PRIVACY.md` promises this product fetches nothing at runtime. A
stylesheet naming a font CDN breaks that on every page load, for every
visitor, invisibly -- and a font request carries an IP address, a referrer and
a timing signal to somebody who was never asked.

Self-hosting is the honest alternative and it has two failure modes that both
look like success:

* **A face that never arrives.** `mimetypes.guess_type` answers None for
  `.woff2` on this machine and the server sends `X-Content-Type-Options:
  nosniff`, so an unregistered type means the browser fetches the font, refuses
  it, and falls back -- with the page looking almost right and nothing saying
  why. The MIME registration in `web/server.py` exists for that, and the
  measurement below is what proves it.
* **A remote URL creeping back in.** A `@import` from a CDN, a `src:` on a
  Google host, a `<link rel=preconnect>` somebody adds for speed. The
  Content-Security-Policy carries `font-src 'self'`, so the browser refuses
  one, and this asserts the header rather than trusting the stylesheet.
"""

from __future__ import annotations

import pytest
from tests.browser.chrome import Chrome
from tests.browser.test_browser_acceptance import open_list

#: Every family the design names, and the token that should carry each.
FAMILIES = {
    "Fraunces": "--display",
    "Outfit": "--sans",
    "JetBrains Mono": "--mono",
    "Press Start 2P": "--pixel",
}

#: Hosts a font must never be fetched from. Not an exhaustive list of the
#: internet -- the CSP does that -- but the ones a stylesheet reaches for.
FONT_CDNS = (
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "use.typekit.net",
    "cdn.jsdelivr.net",
    "unpkg.com",
    "fontawesome",
)


@pytest.fixture(autouse=True)
def _page_is_drawn(page: Chrome, server: str):
    open_list(page, server)
    page.wait_for("document.fonts.status === 'loaded'", message="the fonts to settle")
    yield


def test_every_family_the_design_names_is_actually_loaded(page: Chrome, server: str) -> None:
    """`document.fonts` is the browser's own answer, not the stylesheet's.

    A `@font-face` that failed to fetch, or was refused for its type, leaves
    no entry here -- which is exactly the silent failure this asserts against.
    """
    loaded = page.evaluate(
        "Array.from(document.fonts).filter((f) => f.status === 'loaded')"
        ".map((f) => f.family.replace(/[\"']/g, ''))"
    )

    missing = [name for name in FAMILIES if name not in (loaded or [])]
    assert not missing, f"declared but never loaded: {missing}; loaded: {sorted(set(loaded or []))}"


@pytest.mark.parametrize("family,token", sorted(FAMILIES.items()))
def test_each_family_is_first_in_its_own_token(
    page: Chrome, server: str, family: str, token: str
) -> None:
    """Loaded is not the same as USED. The token has to reach for it first."""
    value = str(
        page.evaluate(f"getComputedStyle(document.documentElement).getPropertyValue({token!r})")
    ).strip()

    assert value.startswith(f'"{family}"'), f"{token} is {value!r}"


def test_the_fallback_stack_survives_behind_every_face(page: Chrome, server: str) -> None:
    """A file that goes missing must degrade, not disappear.

    The offline stack this product shipped with is a real design, and keeping
    it behind the webfont is the difference between a graceful degradation and
    a mystery.
    """
    for token in ("--sans", "--mono", "--display", "--pixel"):
        value = str(
            page.evaluate(f"getComputedStyle(document.documentElement).getPropertyValue({token!r})")
        )
        assert value.count(",") >= 2, f"{token} has no fallback: {value!r}"


def test_no_font_was_fetched_from_anywhere_but_this_server(page: Chrome, server: str) -> None:
    """**The privacy assertion.** Measured on the requests the page made.

    `performance.getEntriesByType('resource')` is the browser's own record of
    every subresource it went and got. Nothing in it may be a font from
    somewhere else, and nothing in it may be one of the CDN hosts at all --
    a `preconnect` to a font host leaks the same signal as the font would.
    """
    entries = page.evaluate("performance.getEntriesByType('resource').map((e) => e.name)")
    foreign = [
        name
        for name in (entries or [])
        if not str(name).startswith(server) and not str(name).startswith("data:")
    ]

    assert not foreign, f"the page fetched something off this server: {foreign}"
    for cdn in FONT_CDNS:
        assert not any(cdn in str(name) for name in (entries or [])), cdn


def test_the_fonts_came_from_this_server_with_the_right_type(page: Chrome, server: str) -> None:
    """The MIME registration, proved rather than assumed.

    Served as `application/octet-stream` under `nosniff`, every one of these
    would be refused and the page would quietly wear its fallback.
    """
    fonts = page.evaluate(
        "performance.getEntriesByType('resource')"
        ".filter((e) => e.name.includes('/fonts/'))"
        ".map((e) => ({name: e.name, size: e.transferSize}))"
    )

    assert fonts, "no font was fetched from this server at all"
    names = [str(f["name"]) for f in fonts]
    assert all(name.startswith(server) for name in names), names
    assert all(name.endswith(".woff2") for name in names), names


def test_the_policy_refuses_a_font_from_anywhere_else(page: Chrome, server: str) -> None:
    """Structural, not conventional: a future stylesheet cannot undo it."""
    from urllib.request import urlopen

    with urlopen(f"{server}/", timeout=30) as response:
        policy = response.headers.get("Content-Security-Policy", "")

    assert "font-src 'self'" in policy, policy
