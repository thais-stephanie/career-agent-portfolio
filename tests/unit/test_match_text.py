"""Folding, sectioning and quoting: the three things every quote depends on.

The offset map is the load-bearing part. Everything else in `career_agent.match`
finds phrases in a folded copy of the posting and then has to hand back a
sentence the employer actually wrote; if the map were off by one, every stored
quote in the system would be off by one and ADR-0002 verification would start
failing for a reason that has nothing to do with matching.
"""

from career_agent.match.text import (
    ELLIPSIS,
    MAX_QUOTE_CHARS,
    PRIMARY_SECTION,
    SECONDARY_SECTION,
    fold,
    section_kind_at,
    sentence_at,
    split_sections,
)

PRIMARY_HEADINGS = ["responsibilities", "what you'll do", "responsabilidades"]
SECONDARY_HEADINGS = ["requirements", "requisitos"]


# --- folding ---------------------------------------------------------------


def test_fold_casefolds_and_strips_accents() -> None:
    folded, _ = fold("Integração de Sistemas")
    assert folded == "integracao de sistemas"


def test_the_offset_map_points_at_the_original_characters() -> None:
    """Every folded character can name the character it came from, which is how
    a match in the folded copy becomes a quote from the original."""
    original = "Integração de Sistemas"
    folded, offsets = fold(original)

    assert len(offsets) == len(folded)
    # "sistemas" starts at folded index 14 and at original index 14.
    start = folded.index("sistemas")
    assert original[offsets[start] : offsets[start] + 8] == "Sistemas"
    # The accented character folded to one character and kept its own index.
    assert original[offsets[folded.index("integracao") + 7]] == "ç"


def test_fold_collapses_every_whitespace_run_including_newlines() -> None:
    folded, offsets = fold("  API\n\n  integration\t work  ")
    assert folded == "api integration work"
    assert offsets[0] == 2


def test_folding_an_empty_string_produces_an_empty_map() -> None:
    assert fold("") == ("", ())


# --- sections --------------------------------------------------------------


def test_split_sections_labels_primary_and_secondary_headings() -> None:
    text = "Intro line\n\nResponsibilities:\n- Own the CRM\n\nRequirements\n- 5 years\n"
    sections = split_sections(text, PRIMARY_HEADINGS, SECONDARY_HEADINGS)

    kinds = [kind for _, _, kind in sections]
    assert kinds == [None, PRIMARY_SECTION, SECONDARY_SECTION]


def test_text_before_the_first_heading_is_a_section_with_no_kind() -> None:
    text = "About us\nWe sell things.\n\nResponsibilities\n- Own the CRM\n"
    sections = split_sections(text, PRIMARY_HEADINGS, SECONDARY_HEADINGS)

    assert section_kind_at(sections, text.index("We sell")) is None
    assert section_kind_at(sections, text.index("Own the CRM")) == PRIMARY_SECTION


def test_headings_are_matched_after_folding_so_portuguese_works() -> None:
    text = "Responsabilidades\n- Automação de processos\n"
    sections = split_sections(text, PRIMARY_HEADINGS, SECONDARY_HEADINGS)

    assert section_kind_at(sections, text.index("Automação")) == PRIMARY_SECTION


def test_a_document_with_no_headings_is_one_unlabelled_section() -> None:
    text = "Just a paragraph about the job."
    assert split_sections(text, PRIMARY_HEADINGS, SECONDARY_HEADINGS) == [(0, len(text), None)]


# --- quoting ---------------------------------------------------------------


def test_sentence_at_returns_a_contiguous_substring_of_the_original() -> None:
    """ADR-0002: evidence is a quote that exists. The untruncated quote must be
    findable in the original text, byte for byte."""
    text = "We hire globally. You will own the CRM data model and its integrations. Apply soon."
    start = text.index("CRM data model")
    quote = sentence_at(text, start, start + len("CRM data model"))

    assert quote == "You will own the CRM data model and its integrations"
    assert quote in text


def test_a_newline_ends_a_sentence_so_a_bullet_quotes_as_itself() -> None:
    text = "Responsibilities\n- Own the CRM data model\n- Something else entirely\n"
    start = text.index("CRM")
    quote = sentence_at(text, start, start + 3)

    assert quote == "- Own the CRM data model"
    assert quote in text


def test_a_semicolon_ends_a_sentence() -> None:
    text = "Remote in Brazil; travel is not expected"
    start = text.index("travel")
    assert sentence_at(text, start, start + 6) == "travel is not expected"


def test_a_long_sentence_is_truncated_with_a_single_ellipsis() -> None:
    text = "x " * 400
    quote = sentence_at(text, 10, 11)

    assert len(quote) == MAX_QUOTE_CHARS
    assert quote.endswith(ELLIPSIS)
    assert quote[:-1] in text
