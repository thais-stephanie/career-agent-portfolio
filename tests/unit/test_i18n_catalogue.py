"""The message catalogues, read as data.

Parsed from `js/i18n.js` rather than executed, because this repository has no
JavaScript test runner and does not want one -- `scripts/frontend_check.mjs`
parses and resolves every module, and this file asks the questions a catalogue
raises that a parser cannot.

The question that matters most is not "is everything translated". It is
**"did anything that belongs to an employer get translated"**, because ADR-0002
verifies every evidence quote as a contiguous substring of the posting, and a
translated quote is not a substring of anything.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "src" / "career_agent" / "web" / "static"
I18N = STATIC / "js" / "i18n.js"
SOURCE = I18N.read_text(encoding="utf-8")


def catalogue(name: str) -> dict[str, str]:
    """One catalogue object, read out of the module text.

    A deliberately small parser: it takes the `'key': value` pairs of one
    `const NAME = { ... };` block, joining the `+` continuations the file uses
    for long sentences. It is not a JavaScript engine and does not need to be.
    """
    start = SOURCE.index(f"const {name} = {{")
    depth, index = 0, SOURCE.index("{", start)
    for position in range(index, len(SOURCE)):
        if SOURCE[position] == "{":
            depth += 1
        elif SOURCE[position] == "}":
            depth -= 1
            if depth == 0:
                body = SOURCE[index + 1 : position]
                break
    else:  # pragma: no cover - the file would not parse
        raise AssertionError(f"{name} is not a closed object")

    entries: dict[str, str] = {}
    for match in re.finditer(r"'([\w.\-]+)':\s*((?:'(?:[^'\\]|\\.)*'\s*\+?\s*)+),", body):
        pieces = re.findall(r"'((?:[^'\\]|\\.)*)'", match.group(2))
        entries[match.group(1)] = "".join(pieces).replace("\\'", "'")
    return entries


EN = catalogue("EN")
PT_BR = catalogue("PT_BR")


# =========================================================================
# 1. THE CATALOGUES AGREE ON WHAT EXISTS
# =========================================================================


def test_the_catalogues_were_actually_read() -> None:
    """A guard on every test below: a parser that found nothing proves nothing."""
    assert len(EN) > 40, f"only {len(EN)} English entries were parsed"
    assert len(PT_BR) > 40, f"only {len(PT_BR)} Portuguese entries were parsed"


def test_portuguese_defines_no_key_english_does_not() -> None:
    """English is the fallback, so it is the one catalogue that must be
    complete. A key only Portuguese defines is a typo that would render a raw
    identifier at an English reader and never fail anywhere."""
    orphans = sorted(set(PT_BR) - set(EN))
    assert not orphans, f"keys in pt-BR with no English entry: {orphans}"


def test_every_english_key_is_translated() -> None:
    """Not required by the fallback, and asserted anyway: a half-translated
    interface reads worse than an untranslated one, because the reader cannot
    tell which language a missing sentence was supposed to be in."""
    missing = sorted(set(EN) - set(PT_BR))
    assert not missing, f"keys with no pt-BR translation: {missing}"


def test_no_entry_is_empty() -> None:
    for name, entries in (("EN", EN), ("PT_BR", PT_BR)):
        blank = sorted(key for key, value in entries.items() if not value.strip())
        assert not blank, f"{name} has empty entries: {blank}"


@pytest.mark.parametrize("key", sorted(EN))
def test_a_translation_keeps_every_placeholder_its_english_has(key: str) -> None:
    """`{count}` missing from a translation is a sentence that silently loses
    its number, and a `{cont}` typo is one that prints a brace at somebody."""
    holes = re.findall(r"\{(\w+)\}", EN[key])
    if key not in PT_BR:
        pytest.skip("covered by the completeness test")
    assert sorted(re.findall(r"\{(\w+)\}", PT_BR[key])) == sorted(holes), key


def _state_families() -> list[str]:
    """`STATE_FAMILIES`, read out of `i18n.js`.

    The list lives in the module because that is where `tVocab` uses it. A
    second copy here is a fact about the same thing, and a second copy is
    what went stale.
    """
    import re

    source = (STATIC / "js" / "i18n.js").read_text(encoding="utf-8")
    match = re.search(r"const STATE_FAMILIES = \[(.*?)\];", source, re.S)
    assert match, "STATE_FAMILIES is not where this test expects it"
    return re.findall(r"'([^']+)'", match.group(1))


# =========================================================================
# 2. STORED VALUES STAY ENGLISH
# =========================================================================

#: The families a bare enum value is looked up in.
#:
#: It said it mirrored `STATE_FAMILIES` and named four of the six, so
#: `work_model` and `contract` were outside the disjointness check they were
#: written to be inside. Read from the catalogue itself now, so the two can
#: no longer drift apart in silence.
FAMILIES = tuple(_state_families())


def test_the_state_families_are_disjoint() -> None:
    """`tVocab` searches the families in order and returns the first hit, so a
    value belonging to two of them would resolve by accident of ordering."""
    seen: dict[str, str] = {}
    for key in EN:
        family, _, value = key.partition(".")
        if family not in FAMILIES:
            continue
        assert value not in seen, f"{value} is in both {seen[value]} and {family}"
        seen[value] = family


def test_a_label_never_reads_as_a_DIFFERENT_states_value() -> None:
    """The collision that would actually hurt.

    `band.STRONG` reading "Strong" is fine and unavoidable: an English label
    for a value usually IS that value in sentence case, and the first version
    of this test failed on exactly that and was measuring nothing useful.

    What must never happen is a label that reads as some OTHER state's value.
    A Portuguese label spelled `APPLIED` on a band, or `HIRED` on an
    eligibility, would be a sentence in one place and a stored value in
    another, and somebody debugging a filter would chase it for an afternoon.
    """
    values = {key.partition(".")[2] for key in EN if key.partition(".")[0] in FAMILIES}
    assert values, "no state values were parsed"

    for name, entries in (("EN", EN), ("PT_BR", PT_BR)):
        for key, label in entries.items():
            family, _, own = key.partition(".")
            if family not in FAMILIES:
                continue
            shaped = label.upper().replace(" ", "_").replace("-", "_")
            assert shaped == own or shaped not in values, (
                f"{name} {key} reads as another state's stored value: {label!r} -> {shaped}"
            )


def test_the_canonical_values_are_the_ones_the_domain_defines() -> None:
    """The catalogue cannot drift from the enums it labels."""
    from career_agent.domain.application import ApplicationStatus
    from career_agent.domain.enums import EligibilityStatus, FitBand, Seniority

    for family, enum in (
        ("eligibility", EligibilityStatus),
        ("band", FitBand),
        ("seniority", Seniority),
        ("status", ApplicationStatus),
    ):
        labelled = {key.partition(".")[2] for key in EN if key.startswith(f"{family}.")}
        defined = {member.value for member in enum}
        assert not (labelled - defined), (
            f"{family} labels a value the domain does not define: {sorted(labelled - defined)}"
        )
        assert not (defined - labelled), (
            f"{family} leaves a real value unlabelled: {sorted(defined - labelled)}"
        )


# =========================================================================
# 3. NOTHING AN EMPLOYER WROTE IS TRANSLATABLE
# =========================================================================


def test_no_catalogue_key_names_a_posting_field() -> None:
    """A key called `job.title` or `posting.description` would be an invitation
    to translate somebody else's words. There is no such key and there must not
    be one: ADR-0002 makes a quote evidence only while it remains a contiguous
    substring of the original.

    `evidence.` is banned because in this codebase EVIDENCE means the
    employer's own sentence. The candidate's side of the product needed a
    namespace for its headings -- "Confirmed about you", "Stop using this" --
    and those really are ours to translate, so they live under `ledger.`
    instead of widening this list. One word, two meanings, and the ban stays
    on the one that must never be translated.
    """
    forbidden = ("job.title", "job.description", "posting.", "quote.", "company.name", "evidence.")
    offenders = [key for key in EN for bad in forbidden if key.startswith(bad)]
    assert not offenders, f"catalogue keys that would translate employer text: {offenders}"


def test_the_renderers_do_not_pass_posting_text_through_the_catalogue() -> None:
    """Read from the modules themselves.

    `t()` and `tVocab()` take a KEY, and a key is a literal in our own source.
    A call whose first argument is a posting field would be translating an
    employer, and the shapes that would take are searched for by name.
    """
    leaks: list[str] = []
    for module in sorted((STATIC / "js").glob("*.js")):
        text = module.read_text(encoding="utf-8")
        for call in re.finditer(r"\bt(?:Vocab)?\(\s*([^,)]+)", text):
            argument = call.group(1).strip()
            if re.match(r"^['\"`]", argument):
                continue  # a literal key, which is the only correct form
            if re.search(
                r"\b(job|posting|row|result)\.(title|description|snippet|company)", argument
            ):
                leaks.append(f"{module.name}: t({argument})")
    assert not leaks, leaks


# =========================================================================
# 4. THE LOCALE IS A UI PREFERENCE
# =========================================================================


def test_the_locale_is_not_a_search_configuration_field() -> None:
    """It changes how the page LOOKS and nothing about how a posting is read.

    Putting it in `search.local.yaml` would also mean a language change bumped
    `config_version` and invalidated every stored score, which is an absurd
    consequence and a good sign the fact is in the wrong place.
    """
    from career_agent.config.search_config import SearchConfig

    fields = set(SearchConfig.model_fields)
    for forbidden in ("locale", "language", "lang", "ui"):
        assert forbidden not in fields


def test_nothing_is_inferred_from_the_browser_or_the_machine() -> None:
    """The correction, enforced where it cannot drift back.

    Not `navigator.language`, not a timezone, not the candidate's country. A
    browser setting is not a request, and the version that read one made this
    entire suite depend on the operating system's language -- the machine here
    reports `pt-BR`, so every English assertion in the browser tests passed or
    failed on a setting nobody in this repository controls.
    """
    body = SOURCE[SOURCE.index("export function initialLocale") :]
    body = body[: body.index("export function setLocale")]
    # The comments EXPLAIN what was removed and why, so scanning them would
    # fail on the explanation. Only executable lines are searched.
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    body = re.sub(r"//.*", "", body)
    for inferred in ("navigator", "language", "timeZone", "Intl", "country"):
        assert inferred not in body, (
            f"the starting locale is inferred from {inferred!r}: {body[:200]}"
        )
    assert "return DEFAULT_LOCALE" in body, "there is no plain English default"


def test_the_toggle_reads_en_and_pt() -> None:
    """A compact two-letter selector. `pt-BR` stays the INTERNAL value, because
    that is the tag `<html lang>` needs and a screen reader reads."""
    assert EN["locale.en"] == "EN"
    assert EN["locale.pt-BR"] == "PT"
    assert PT_BR["locale.en"] == "EN"
    assert PT_BR["locale.pt-BR"] == "PT"
    assert "'pt-BR'" in SOURCE, "the internal tag stopped being a real language tag"


def test_only_an_explicit_choice_is_remembered() -> None:
    """The first paint calls `setLocale` with whatever the browser suggested.

    Writing that guess down would make it indistinguishable from a choice: the
    reader would be pinned to a language they never picked, and changing their
    browser afterwards would do nothing. `persist` is off by default and the
    click is the only caller that turns it on.
    """
    assert "persist = false" in SOURCE, "the default persists a guess"
    main = (STATIC / "js" / "main.js").read_text(encoding="utf-8")
    assert "setLocale(locale, { persist: true })" in main, "the click does not remember"
    assert "setLocale(initialLocale())" in main, "the first paint persists a guess"


def test_the_locale_is_remembered_in_the_browser_and_sent_nowhere() -> None:
    assert "localStorage" in SOURCE
    assert "careerAgent.locale" in SOURCE
    # No fetch, no request, no query parameter. The server never learns it.
    assert "fetch(" not in SOURCE
    assert "XMLHttpRequest" not in SOURCE


def test_the_page_language_attribute_follows_the_locale() -> None:
    """A screen reader pronounces the page according to `lang`, and Portuguese
    read with English phonemes is harder to follow than English is."""
    assert "documentElement.lang" in SOURCE


def test_english_is_the_fallback_and_a_missing_key_returns_itself() -> None:
    """The worst case a reader sees is an untranslated sentence, never a blank
    and never a crash."""
    body = SOURCE[SOURCE.index("export function t(") :]
    assert "EN[key]" in body, "there is no English fallback"
    assert "return key" in body, "a missing key does not degrade to its own name"


# =========================================================================
# A LIKELIHOOD MUST NOT READ AS A REFUSAL
# =========================================================================


def test_the_domestic_reading_never_tells_the_reader_she_is_ineligible() -> None:
    """`domestic.*` describes the POSTING. `eligibility.*` describes the answer.

    The reading fires on 3,321 postings in the owner's corpus, almost all of
    them UNRESOLVED on eligibility, which means the employer has said nothing
    about who may apply. A label that read "you are not eligible" would be this
    system answering on the employer's behalf, from a benefits list.
    """
    forbidden = (
        "not eligible",
        "cannot apply",
        "ineligible",
        "rules you out",
        "nao elegivel",
        "não elegível",
        "exclui voce",
        "exclui você",
    )
    for catalogue in ({"en": EN, "pt-BR": PT_BR}).values():
        for key, text in catalogue.items():
            if not key.startswith("domestic."):
                continue
            lowered = str(text).lower()
            for phrase in forbidden:
                assert phrase not in lowered, f"{key} reads as an eligibility verdict: {text!r}"


def test_the_domestic_sentence_says_it_is_not_a_refusal() -> None:
    """Not a style preference. The sentence is the only place a reader is told
    what the reading is NOT, and every language has to say it."""
    for locale, catalogue in {"en": EN, "pt-BR": PT_BR}.items():
        sentence = catalogue.get("domestic.sentence")
        assert sentence, f"{locale} has no domestic sentence"
        lowered = str(sentence).lower()
        assert (
            "not a refusal" in lowered
            or "nao e uma recusa" in lowered
            or "não é uma recusa" in lowered
        ), f"{locale} does not tell the reader this is not a refusal: {sentence!r}"


def test_an_employment_likelihood_is_labelled_as_one() -> None:
    """`employment.stated` and `employment.likely` are the whole distinction
    between an employer writing `contratacao CLT` and an employer offering a
    health plan. Both must exist, and they must not read the same."""
    for locale, catalogue in {"en": EN, "pt-BR": PT_BR}.items():
        stated = catalogue.get("employment.stated")
        likely = catalogue.get("employment.likely")
        assert stated and likely, f"{locale} is missing one half of the distinction"
        assert stated != likely, f"{locale} renders a statement and a likelihood identically"
