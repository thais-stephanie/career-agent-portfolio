"""The Arbeitnow `description` field, decoded into the text this corpus stores.

THE DEFECT, MEASURED FROM DISK ON 2026-09-19. 94 of 600 open Arbeitnow
postings carried the employer's markup as TEXT: `<p>`, `<li>`, `<strong>`
sitting inside `job_raw.description_text`, at five or more tags a body. Three
postings in a consumed Search Fit holdout read as having NO DUTIES because of
it. Teamtailor and Get on Board, under the same query, had none.

The cause is the vendor's field shape, and it is a MIX. Every record ends in
the vendor's own raw-html link-back footer; before it, 506 records carry the
employer's advert as raw html and 94 carry it ENTITY-ESCAPED (`&lt;p&gt;`).
The adapter's old stripper removed the raw tags first and decoded `&lt;p&gt;`
into a literal `<p>` afterwards; the shared `html_to_text` never unescapes a
string that holds a real `<` anywhere. Both were wrong for the same input.

The cases below are the vendor's own records, archived verbatim in
`job_provider_payload` by the 2026-09-09 production run and extracted from
disk. No request was made to build them. See `archived_payloads.json`.
"""

from __future__ import annotations

import json
import re
from html import unescape
from pathlib import Path

import pytest

from career_agent.domain.normalize import content_hash, html_to_text
from career_agent.net.fetcher import HttpFetcher
from career_agent.providers.arbeitnow import (
    ArbeitnowProvider,
    advert_html,
    advert_text,
    to_stub,
)
from career_agent.providers.base import PostingStub

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "arbeitnow"
ARCHIVED = json.loads((FIXTURES / "archived_payloads.json").read_text(encoding="utf-8"))
CASES = {name: case["record"] for name, case in ARCHIVED["cases"].items()}
LIVE_ROWS = [
    row
    for page in ("page1.json", "page2.json")
    for row in json.loads((FIXTURES / page).read_text(encoding="utf-8"))["data"]
]

#: The exact query the 2026-09-18 production measurement used.
TAG = re.compile(r"<[a-zA-Z/][^>]{0,40}>")
ENTITY = re.compile(r"&[#a-zA-Z0-9]+;")


def _residue(text: str) -> int:
    return len(TAG.findall(text))


def _legacy_strip(html: str) -> str:
    """What the adapter did until 2026-09-19, kept here as the BEFORE side.

    Tags first, then six entities by string replacement. It is the order that
    turns an escaped advert into literal markup, and it is reproduced so the
    tests can show the defect rather than describe it.
    """
    text = re.sub(r"(?i)<br\s*/?>", "\n", html)
    text = re.sub(r"(?i)</(p|div|li|ul|ol|h[1-6]|tr)>", "\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    for entity, char in (
        ("&nbsp;", " "),
        ("&amp;", "&"),
        ("&lt;", "<"),
        ("&gt;", ">"),
        ("&quot;", '"'),
        ("&#39;", "'"),
    ):
        text = text.replace(entity, char)
    return text


# -- the fixture is what it claims to be --------------------------------------


def test_the_archived_cases_are_whole_feed_records() -> None:
    for name, record in CASES.items():
        for key in ("slug", "company_name", "title", "description", "url", "created_at"):
            assert key in record, f"{name} lacks {key}"
        assert to_stub(record) is not None, name


def test_the_escaped_cases_are_the_measured_shape() -> None:
    """An escaped advert, then the vendor's RAW footer: the mix itself."""
    for name in ("escaped_shortest", "escaped_with_list_and_nbsp"):
        field = CASES[name]["description"]
        cut = field.find("<")
        assert field.lstrip().startswith("&lt;"), name
        assert cut > 0, name
        assert "&lt;" not in field[cut:], f"{name}: escaped markup after the raw part"
        assert field.endswith("on Arbeitnow</a>"), name


def test_the_raw_cases_are_raw() -> None:
    for name in ("raw_with_list", "raw_with_hex_entity", "raw_with_malformed_anchor"):
        field = CASES[name]["description"]
        assert not field.lstrip().startswith("&lt;"), name
        assert "<" in field, name


# -- the defect, shown -------------------------------------------------------


@pytest.mark.parametrize("name", ["escaped_shortest", "escaped_with_list_and_nbsp"])
def test_the_old_stripper_left_the_employers_markup_as_text(name: str) -> None:
    before = _legacy_strip(CASES[name]["description"])
    assert _residue(before) >= 5, "the defect this file exists for did not reproduce"


@pytest.mark.parametrize("name", ["escaped_shortest", "escaped_with_list_and_nbsp"])
def test_the_shared_contract_alone_also_left_it(name: str) -> None:
    """`html_to_text` is right for the escaped case and right for the raw
    case, and wrong for the mix. This is why the adapter decodes the vendor's
    shape BEFORE handing the document over rather than after."""
    assert _residue(html_to_text(CASES[name]["description"])) >= 5


# -- the fix -----------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(CASES))
def test_no_case_leaves_a_tag_in_the_stored_text(name: str) -> None:
    assert _residue(advert_text(CASES[name]["description"])) == 0


@pytest.mark.parametrize("name", sorted(CASES))
def test_no_case_leaves_an_entity_in_the_stored_text(name: str) -> None:
    text = advert_text(CASES[name]["description"])
    assert not ENTITY.search(text), ENTITY.findall(text)[:5]


def test_every_live_row_is_clean_too() -> None:
    for row in LIVE_ROWS:
        text = advert_text(row["description"])
        assert _residue(text) == 0, row["slug"]
        assert not ENTITY.search(text), row["slug"]


def test_an_escaped_advert_is_decoded_once_and_the_footer_is_left_raw() -> None:
    field = CASES["escaped_shortest"]["description"]
    doc = advert_html(field)
    cut = field.find("<")
    assert doc == unescape(field[:cut]) + field[cut:]
    # One html document now: real tags where the entities were, and the
    # employer's own `&nbsp;` back to ONE level of escaping for the parser.
    assert doc.lstrip().startswith("<")
    assert "&lt;" not in doc
    assert "&amp;nbsp;" not in doc


def test_a_raw_advert_passes_through_untouched() -> None:
    for name in ("raw_with_list", "raw_with_hex_entity", "raw_with_malformed_anchor"):
        field = CASES[name]["description"]
        assert advert_html(field) == field, name


def test_a_wholly_escaped_field_with_no_raw_part_is_one_document() -> None:
    assert advert_html("&lt;p&gt;Hello &amp;amp; welcome&lt;/p&gt;") == "<p>Hello &amp; welcome</p>"
    assert advert_text("&lt;p&gt;Hello &amp;amp; welcome&lt;/p&gt;") == "Hello & welcome"


def test_an_empty_field_is_empty() -> None:
    assert advert_html("") == ""
    assert advert_text("") == ""


def test_the_employers_own_nbsp_survives_two_levels_of_escaping_as_a_space() -> None:
    """`&amp;nbsp;` in the field is the employer's `&nbsp;` escaped once by the
    vendor. Decoded once by the adapter and once by the parser, it is a space,
    and the stored text carries neither spelling."""
    text = advert_text(CASES["escaped_with_list_and_nbsp"]["description"])
    assert "&nbsp;" not in text
    assert "&amp;" not in text
    assert " " not in text


def test_a_hex_entity_the_six_entry_table_did_not_know_is_decoded() -> None:
    field = CASES["raw_with_hex_entity"]["description"]
    assert "&#x26;" in field
    assert "&#x26;" in _legacy_strip(field), "the old table would have left it"
    text = advert_text(field)
    assert "&#x26;" not in text
    assert "&" in text


# -- structure kept, content kept ----------------------------------------------


def test_list_items_in_an_escaped_advert_come_out_as_separate_marked_lines() -> None:
    field = CASES["escaped_with_list_and_nbsp"]["description"]
    items = re.findall(r"&lt;li&gt;(.*?)&lt;/li&gt;", field, flags=re.S)
    assert len(items) >= 3
    lines = advert_text(field).splitlines()
    marked = [line for line in lines if line.startswith("- ")]
    assert len(marked) >= len(items)


def test_paragraphs_in_an_escaped_advert_are_separated() -> None:
    text = advert_text(CASES["escaped_shortest"]["description"])
    assert "\n\n" in text
    # and nothing glued: a paragraph never runs straight into the next word
    assert not re.search(r"[a-z]\.[A-Z]", text.replace("e.g.", "").replace("i.e.", ""))


def test_a_link_keeps_its_text_and_loses_its_markup() -> None:
    text = advert_text(CASES["escaped_shortest"]["description"])
    assert "on Arbeitnow" in text
    assert "href" not in text
    assert "<a" not in text


def test_a_malformed_anchor_no_longer_swallows_the_paragraphs_after_it() -> None:
    """The employer wrote `<a ---------- - +49 ...` with no closing `>`. The
    old regex read from that `<` to the NEXT `>` on the page, across three
    paragraphs, and dropped the diversity statement and the HR contact with
    the tag. That is content loss, not residue, and it is why a regex is not
    an html parser."""
    field = CASES["raw_with_malformed_anchor"]["description"]
    assert "<a ---" in field
    sentence = "Our top priority is that you fit us"
    assert sentence in field
    assert sentence not in _legacy_strip(field)
    assert sentence in advert_text(field)


def test_the_employers_words_survive_the_decode() -> None:
    """Every run of letters the vendor's field carries as text is in the
    stored text. Decoding removes markup and nothing else."""
    for name, record in CASES.items():
        doc = advert_html(record["description"])
        words = set(re.findall(r"[A-Za-z]{6,}", re.sub(r"<[^>]*>", " ", doc)))
        words -= {"nbsp", "strong", "class", "style", "target", "noreferrer", "noopener"}
        text = advert_text(record["description"])
        missing = {w for w in words if w not in text}
        assert not missing, f"{name}: {sorted(missing)[:10]}"


# -- what does not move --------------------------------------------------------


@pytest.mark.parametrize("name", sorted(CASES))
def test_nothing_but_the_body_is_touched(name: str) -> None:
    record = CASES[name]
    stub = to_stub(record)
    assert stub is not None
    assert stub.title == record["title"].strip()
    assert stub.url == record["url"]
    assert stub.location_raw == (record["location"].strip() or None)
    assert stub.payload == record
    posting = ArbeitnowProvider(HttpFetcher()).fetch_posting(None, stub)  # type: ignore[arg-type]
    assert posting.payload == record, "the archived payload is the vendor's record, untouched"
    assert posting.stub is stub


@pytest.mark.parametrize("name", sorted(CASES))
def test_a_rebuild_from_the_archive_equals_a_fresh_collection(name: str) -> None:
    """`pipeline.rebuild_bodies` hands the adapter a stub with NO html and the
    archived payload alone. The body it derives must be byte-identical to the
    one collection stores, or a rebuilt row and a re-collected row could be
    told apart. The first adapter read only the stub and rebuilt nothing."""
    record = CASES[name]
    adapter = ArbeitnowProvider(HttpFetcher())
    collected = adapter.fetch_posting(None, to_stub(record))  # type: ignore[arg-type]
    rebuilt = adapter.fetch_posting(
        None,  # type: ignore[arg-type]
        PostingStub(
            external_id=f"arbeitnow-{record['slug']}",
            title=record["title"],
            url=record["url"],
            location_raw=None,
            department=None,
            posted_at=None,
            description_html=None,
            payload=dict(record),
        ),
    )
    assert rebuilt.description_text == collected.description_text
    assert rebuilt.description_html == collected.description_html
    assert content_hash(rebuilt.description_text) == content_hash(collected.description_text)


def test_the_archived_html_renormalises_to_the_stored_text() -> None:
    """`pipeline.renormalize` re-derives every archived body through
    `html_to_text` and expects to find the stored text. For this provider that
    was never true; it is now, because what is archived is the decoded
    document and not the vendor's mixed field."""
    adapter = ArbeitnowProvider(HttpFetcher())
    for name, record in CASES.items():
        posting = adapter.fetch_posting(None, to_stub(record))  # type: ignore[arg-type]
        assert html_to_text(posting.description_html) == posting.description_text, name
