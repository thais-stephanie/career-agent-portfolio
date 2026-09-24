"""Portuguese in the interface, and English left alone in the jobs.

The catalogue tests read the message files as data. These drive the real page
and ask the only two questions that matter about a translation once it renders:
did OUR words change, and did THEIRS stay exactly as the employer wrote them.

The second is the one with teeth. ADR-0002 makes an evidence quote evidence
only while it is a contiguous substring of the posting, so a translated quote
is not a weaker quote -- it is not a quote.
"""

from __future__ import annotations

import pytest
from tests.browser.chrome import Chrome
from tests.browser.test_browser_acceptance import RENDERED_COUNT, open_list

LOCALE_KEY = "careerAgent.locale.v1"

#: A posting the demo corpus carries in Portuguese, and one it carries in
#: English. Neither may move in either direction.
PORTUGUESE_POSTING = "Especialista Sênior em Automação de Processos"
ENGLISH_POSTING = "Business Systems Engineer"


#: The filter panel's open/closed state, which this file reads as its canary.
RAIL_KEY = "careerAgent.rail.v1"


def close_the_filters(page: Chrome) -> None:
    """Put the filter panel back to closed, which is its default.

    This file's language canary is the filters toggle, and the toggle says
    "Show" or "Hide" depending on the panel. Chrome is session scoped and the
    `page` fixture does not clear `localStorage`, so one earlier test opening
    the panel left every later assertion here reading "Hide filters" -- which
    is the correct English for the wrong state.

    The same leak the colour scheme has, closed the same way: by resetting the
    thing rather than by asking the assertion to accept either answer.
    """
    page.evaluate(f"window.localStorage.removeItem('{RAIL_KEY}')")


def in_locale(page: Chrome, server: str, locale: str) -> None:
    """Open the list with a locale already stored, as a returning reader has.

    Stored before navigation rather than clicked afterwards, so the assertions
    describe a page that RENDERED in that language rather than one repainted
    into it. Both paths matter and the click has its own tests.
    """
    page.navigate(server)
    close_the_filters(page)
    page.evaluate(f"window.localStorage.setItem('{LOCALE_KEY}', '{locale}')")
    open_list(page, server)


def fresh(page: Chrome, server: str) -> None:
    """A reader who has never chosen anything."""
    page.navigate(server)
    close_the_filters(page)
    page.evaluate(f"window.localStorage.removeItem('{LOCALE_KEY}')")
    open_list(page, server)


def click_locale(page: Chrome, locale: str) -> None:
    page.evaluate(f"document.querySelector('.localeswitch__btn[data-locale=\"{locale}\"]').click()")


def stored(page: Chrome):
    return page.evaluate(f"window.localStorage.getItem('{LOCALE_KEY}')")


def texts(page: Chrome, selector: str) -> list[str]:
    """The text of every matching element.

    Element by element rather than `document.body.innerText`: the DevTools
    protocol truncates a long string value with an ellipsis, so a whole-page
    read silently loses its middle and an assertion against it passes or fails
    on where the cut happened to land.
    """
    return list(
        page.evaluate(f"[...document.querySelectorAll('{selector}')].map(n => n.textContent)")
    )


def rail(page: Chrome) -> list[str]:
    """The filters control's own word, which is this file's language canary.

    It used to be the summary of a `<details>` in the filter rail. The rail is
    gone -- the filters are a panel above the results now -- and the toggle
    that opens it carries the same word. `.railtoggle__word` rather than the
    button, because the button also holds the active-filter count and a
    hidden element still contributes to `textContent`: reading the button
    gave "Mostrar filtros0".
    """
    return texts(page, ".railtoggle__word")


def nav(page: Chrome) -> list[str]:
    """The destinations, in the reader's language.

    Read from `.topnav__label` rather than from the button. Each destination
    carries an icon beside its word in the Workspace V2 rail, so the button's
    own `textContent` is the icon glyph and the label run together with the
    whitespace the markup was written with. Asserting on that would be
    asserting a layout decision from a test about words.
    """
    return [text.strip() for text in texts(page, ".topnav__link .topnav__label")]


@pytest.fixture(autouse=True)
def _restore_english(page: Chrome, server: str):
    """Hand the next test the default rather than this file's leftovers.

    BEFORE and after, and the "before" half is not belt and braces. The browser
    fixture is session-scoped, `localStorage` outlives a test, and the locale is
    read once at module load -- so a single leaked key makes every later test in
    the whole suite read a Portuguese page while asserting English labels. Three
    acceptance tests failed that way before this cleared on entry too.

    The removal is verified rather than assumed: `localStorage` throws in some
    contexts and a silent failure here is a suite-wide mystery.
    """
    page.navigate(server)
    page.evaluate(f"window.localStorage.removeItem('{LOCALE_KEY}')")
    yield
    page.navigate(server)
    page.evaluate(f"window.localStorage.removeItem('{LOCALE_KEY}')")
    assert page.evaluate(f"window.localStorage.getItem('{LOCALE_KEY}') === null"), (
        "the locale key survived cleanup and will follow the next test"
    )


# =========================================================================
# 1. OUR WORDS CHANGE
# =========================================================================


def test_the_interface_renders_in_portuguese_when_asked(page: Chrome, server: str) -> None:
    in_locale(page, server, "pt-BR")

    # The rail filters a list, so what remains in it is about that list. The
    # preference editor and the source matrix are asserted below, on the
    # Settings destination they moved to: a test that kept asserting them here
    # would be describing a product that no longer exists.
    assert rail(page) == ["Mostrar filtros"], rail(page)
    # The profile and the evidence ledger are DESTINATIONS now, not panels
    # beside the results, so their words are asserted where they live. They
    # were in the rail until the shell existed; a test that kept asserting
    # them there would be describing a product that no longer exists.
    assert nav(page) == [
        "Início",
        "Descobrir vagas",
        "Candidaturas",
        "Perfil de carreira",
        "Evidências de carreira",
        "Configurações e fontes",
    ], nav(page)
    assert texts(page, "#view-cards") == ["Cartões"]
    # The tagline moved into the rail with the brand it belongs to.
    assert texts(page, "#sidenav-tag") == ["roda no seu computador"]
    # ...and so did the section headings above the destinations, which are
    # product-authored text like any other.
    assert texts(page, ".sidenav__section") == ["Busca", "Perfil", "Sistema"]
    assert texts(page, ".sidenav__label") == ["Aparência", "Idioma"]
    # The page header is the one place every screen states what it is, so it
    # is the one place a missed string is most visible. `in_locale` lands on
    # the job list, so this is the job list's title.
    assert texts(page, "#pagehead-title") == ["Vagas para você"]
    assert texts(page, "#pagehead-eyebrow") == ["Descobrir"]

    # SETTINGS, where the two panels that left the rail now live. Reached the
    # way a reader reaches it, so the assertion covers the navigation as well
    # as the words.
    page.evaluate("document.querySelector('.topnav__link[data-page=\"settings\"]').click()")
    page.wait_for(
        "document.getElementById('page-settings')"
        " && !document.getElementById('page-settings').hidden",
        message="the Settings page",
    )
    heads = texts(page, "#page-settings .settings__head")
    assert "Preferências de busca" in heads, heads
    assert "De onde elas vêm" in heads, heads
    assert texts(page, "#pagehead-title") == ["Configurações e fontes"]


def test_a_fresh_reader_gets_english(page: Chrome, server: str) -> None:
    """Nobody has chosen, so English. Not a guess, not a negotiation."""
    fresh(page, server)
    assert "Show filters" in rail(page)
    assert "Mostrar filtros" not in rail(page)
    assert nav(page)[0] == "Home", nav(page)
    assert page.evaluate("document.documentElement.lang") == "en"


def test_a_portuguese_browser_does_not_change_the_default(page: Chrome, server: str) -> None:
    """The correction that removed `navigator.language`, asserted directly.

    The machine running this suite reports `pt-BR`, so this test would have
    failed before the inference was removed -- which is precisely the point.
    A browser setting is not a request, and a UI that decided from one had to
    be argued with rather than used.
    """
    fresh(page, server)
    assert str(page.evaluate("navigator.language")).lower().startswith("pt"), (
        "this machine no longer reports a Portuguese browser, so this test proves nothing"
    )
    assert "Show filters" in rail(page), "the browser's language chose the interface language"


def test_nothing_is_stored_until_somebody_chooses(page: Chrome, server: str) -> None:
    """A written-down guess is indistinguishable from a decision, and would pin
    a reader to a language they never picked."""
    fresh(page, server)
    assert stored(page) is None, f"a locale was persisted without a choice: {stored(page)!r}"


def test_choosing_pt_switches_and_is_remembered(page: Chrome, server: str) -> None:
    fresh(page, server)
    click_locale(page, "pt-BR")
    page.wait_for(
        "document.querySelector('.railtoggle__word').textContent === 'Mostrar filtros'",
        message="the interface to switch to Portuguese",
    )
    assert stored(page) == "pt-BR"
    assert page.evaluate("document.documentElement.lang") == "pt-BR"


def test_choosing_en_again_is_remembered_as_a_choice(page: Chrome, server: str) -> None:
    """Going back is a decision too, and must not fall through to "unset" --
    which would leave the default in charge and look identical until the
    default ever changed."""
    in_locale(page, server, "pt-BR")
    click_locale(page, "en")
    page.wait_for(
        "document.querySelector('.railtoggle__word').textContent === 'Show filters'",
        message="the interface to switch back to English",
    )
    assert stored(page) == "en"


def test_the_toggle_is_two_letters_each(page: Chrome, server: str) -> None:
    """A compact `EN | PT`. No flags: a flag is a country and a language is
    not, and Portuguese is not the property of one."""
    fresh(page, server)
    assert texts(page, ".localeswitch__btn") == ["EN", "PT"]


def test_the_page_language_attribute_follows(page: Chrome, server: str) -> None:
    """A screen reader pronounces the page according to this attribute, and
    Portuguese read with English phonemes is harder to follow than English."""
    in_locale(page, server, "pt-BR")
    assert page.evaluate("document.documentElement.lang") == "pt-BR"

    in_locale(page, server, "en")
    assert page.evaluate("document.documentElement.lang") == "en"


def test_switching_repaints_without_reloading(page: Chrome, server: str) -> None:
    """A reload would lose the drawer, the scroll position and any unsaved
    status change, and none of those are language."""
    in_locale(page, server, "en")
    page.evaluate("window.__stillHere = 'yes'")

    page.evaluate("document.querySelector('.localeswitch__btn[data-locale=\"pt-BR\"]').click()")
    page.wait_for(
        "document.querySelector('.railtoggle__word').textContent === 'Mostrar filtros'",
        message="the interface to repaint in Portuguese",
    )

    assert page.evaluate("window.__stillHere") == "yes", "the page reloaded"


def test_an_explicit_choice_survives_a_reload(page: Chrome, server: str) -> None:
    fresh(page, server)
    click_locale(page, "pt-BR")
    page.wait_for(
        "document.querySelector('.railtoggle__word').textContent === 'Mostrar filtros'",
        message="the switch to Portuguese",
    )

    page.evaluate("window.location.href = window.location.pathname")
    page.wait_for(f"{RENDERED_COUNT} > 0", message="the list after reloading")
    # WAIT FOR THE THING THIS ASSERTS ABOUT. The list painting and the filter
    # toggle painting are different events, and under load this asserted on
    # the second while waiting for the first: it failed once in a full run and
    # never on its own, which is the signature.
    page.wait_for(
        "Boolean(document.querySelector('.railtoggle__word'))",
        message="the filter toggle after reloading",
    )
    assert "Mostrar filtros" in rail(page)
    assert stored(page) == "pt-BR"


# =========================================================================
# 2. THEIR WORDS DO NOT
# =========================================================================


@pytest.mark.parametrize("locale", ["en", "pt-BR"])
def test_a_posting_keeps_the_language_the_employer_wrote_it_in(
    page: Chrome, server: str, locale: str
) -> None:
    """The demo corpus holds both, which is what a real corpus looks like.

    A Portuguese posting stays Portuguese for an English reader and an English
    one stays English for a Portuguese reader. Neither is a defect: a job title
    is the employer's, and rendering it in the reader's language would be this
    product inventing a fact about somebody else's vacancy.
    """
    in_locale(page, server, locale)
    titles = texts(page, ".card__title, .jobrow__title, [data-job-title]")
    joined = " | ".join(titles)
    assert PORTUGUESE_POSTING in joined, f"a Portuguese posting was rewritten: {titles}"
    assert ENGLISH_POSTING in joined, f"an English posting was rewritten: {titles}"


def test_an_evidence_quote_is_byte_identical_in_both_languages(page: Chrome, server: str) -> None:
    """The strongest form of the rule, checked on the rendered quotes.

    ADR-0002 verifies a quote as a contiguous substring of the posting. A
    translated quote is not a weaker quote; it is not a quote, and the
    reasoning tab would be asserting something no text supports.
    """
    quotes = {}
    for locale in ("en", "pt-BR"):
        in_locale(page, server, locale)
        page.evaluate("document.querySelector('.card:not(.card--skeleton)').click()")
        page.wait_for("document.querySelector('.drawer')", message="the drawer")
        page.evaluate(
            "(() => { const tab = [...document.querySelectorAll('[role=tab]')]"
            ".find(t => /why|por que/i.test(t.textContent)); if (tab) tab.click(); })()"
        )
        page.wait_for(
            "document.querySelectorAll('.quote').length > 0", message="the quoted evidence"
        )
        quotes[locale] = page.evaluate(
            "[...document.querySelectorAll('.quote')].map(q => q.textContent)"
        )
        page.evaluate("document.querySelector('.drawer__close')?.click()")

    assert quotes["en"], "no quotes were rendered, so this test proved nothing"
    # `.quote` holds verbatim source text and nothing else. It used to also
    # carry `quote--absent`, our own sentence saying there was no quotable
    # line, which this test collected as employer evidence and then required
    # to stay English. That class is `quote-absent` now and is not a `.quote`.
    assert quotes["en"] == quotes["pt-BR"], "an evidence quote changed with the locale"


# =========================================================================
# 3. NOTHING STORED MOVED
# =========================================================================


def test_the_query_string_stays_in_the_canonical_vocabulary(page: Chrome, server: str) -> None:
    """`VERIFIED_NOT_ELIGIBLE` is a stored value and a query parameter. Only
    its label is translated, so a Portuguese reader's filters must produce the
    same URL an English reader's do."""
    in_locale(page, server, "pt-BR")
    page.navigate(f"{server}/?eligibility=UNRESOLVED&include_off_target=1#jobs")
    page.wait_for(f"{RENDERED_COUNT} > 0", message="the filtered list")

    assert "eligibility=UNRESOLVED" in str(page.evaluate("location.search"))
    badges = texts(page, ".badge, .badge__value, .card__elig")
    assert any("Não disse" in badge for badge in badges), badges
