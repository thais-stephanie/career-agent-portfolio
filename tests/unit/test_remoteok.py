"""The Remote OK adapter, against the window captured on 2026-09-05.

Nothing here opens a socket, and that is a policy rather than a testing habit:
`remoteok.com/robots.txt` names `ClaudeBot` with `Disallow: /`, the agent that
maintains this file is ClaudeBot, and the fixture predates the adapter. The
first live call belongs to whoever runs `collect-remoteok`.

Three themes:

* the first element of every response is the LICENCE, and a reader that took it
  for a posting would file terms of service as a job advert;
* the link back is the licence, so the Apply target is asserted rather than
  trusted;
* a salary with no currency is not read at all.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from career_agent.providers.remoteok import (
    HOST,
    RemoteOkError,
    RemoteOkProvider,
    assert_trusted,
    company_of,
    is_posting,
    licence_text,
    public_url,
    strip_html,
    to_stub,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "remoteok"
FEED = json.loads((FIXTURES / "feed.json").read_text(encoding="utf-8"))
POSTINGS = [record for record in FEED if is_posting(record)]


# -- the licence row ---------------------------------------------------------


def test_the_first_element_is_the_licence_and_never_a_posting() -> None:
    """It carries `legal` and no `id`. A reader that took element zero as a job
    would store the terms of service as an advert, and would be unlikely ever
    to notice the terms exist."""
    assert not is_posting(FEED[0])
    assert to_stub(FEED[0]) is None
    assert "legal" in FEED[0]


def test_the_licence_is_readable_from_the_response() -> None:
    """Recorded per run rather than trusted from a docstring: the grant is
    conditional and the vendor restates it in every response."""
    text = licence_text(FEED)
    assert text is not None
    assert "link back" in text.lower()
    assert "remote ok" in text.lower()


def test_every_other_element_is_a_posting() -> None:
    assert len(POSTINGS) == len(FEED) - 1
    assert POSTINGS


# -- the link back, which is half the licence --------------------------------


def test_the_apply_target_is_the_remote_ok_url() -> None:
    """The licence asks for a link back with follow. `job.url` is the only
    Apply target this adapter produces, so honouring it is structural rather
    than a habit somebody could change later."""
    for record in POSTINGS:
        stub = to_stub(record)
        assert stub is not None
        assert stub.url.lower().startswith(f"https://{HOST}/")


def test_a_url_off_the_vendor_is_refused() -> None:
    """A feed that started pointing elsewhere would take the link back with
    it, and the link back is what the access is conditioned on."""
    record = dict(POSTINGS[0])
    record["url"] = "https://evil.example/job/1"
    assert public_url(record) is None
    assert to_stub(record) is None


def test_a_plain_http_url_is_refused() -> None:
    record = dict(POSTINGS[0])
    record["url"] = f"http://{HOST}/remote-jobs/x"
    assert public_url(record) is None


def test_nothing_off_the_host_is_ever_fetched() -> None:
    with pytest.raises(RemoteOkError):
        assert_trusted(f"https://{HOST}.evil.example/api")
    with pytest.raises(RemoteOkError):
        assert_trusted(f"http://{HOST}/api")


# -- addressing --------------------------------------------------------------


def test_every_captured_posting_is_addressable() -> None:
    for record in POSTINGS:
        stub = to_stub(record)
        assert stub is not None
        assert stub.external_id.startswith("remoteok-")
        assert stub.title
        assert stub.description_html
        assert stub.posted_at is not None and stub.posted_at.endswith("+00:00")


@pytest.mark.parametrize("missing", ["id", "position", "url"])
def test_a_row_missing_a_required_field_is_refused(missing: str) -> None:
    record = dict(POSTINGS[0])
    record[missing] = None
    assert to_stub(record) is None


def test_the_employer_comes_from_the_company_field() -> None:
    assert company_of(POSTINGS[0])
    assert company_of({"company": "   "}) is None


# -- what it refuses to read -------------------------------------------------


def test_a_salary_with_no_currency_is_not_read() -> None:
    """`salary_min` and `salary_max` are bare integers and no field anywhere in
    the record names a currency. They are almost certainly dollars and this
    adapter does not say so: `min_salary` compares within one currency and
    nothing here converts, so a number with an invented unit is worse than no
    number.
    """
    assert RemoteOkProvider.capabilities.exposes_compensation is False
    priced = [r for r in POSTINGS if r.get("salary_min")]
    assert priced, "the premise: the feed does state amounts"
    assert all("currency" not in r for r in priced)


def test_this_adapter_does_not_claim_to_publish_a_hiring_scope() -> None:
    """`location` is where the work is, and it is empty on most rows. Empty is
    stored as nothing rather than as `Remote`: a remote posting with no stated
    place resolves to no country, which is the answer invariant 3 wants."""
    assert RemoteOkProvider.capabilities.publishes_hiring_scope is False
    blank = [r for r in POSTINGS if not (r.get("location") or "").strip()]
    assert blank, "the premise: most rows say nothing about where"
    assert all(to_stub(r).location_raw is None for r in blank)  # type: ignore[union-attr]


def test_the_field_map_is_empty_on_purpose() -> None:
    """An empty map is a statement about the vendor: `location` is free text,
    `tags` are the board's categories and `original` is a boolean. None of them
    is a work model, an employment type or a hiring scope."""
    assert RemoteOkProvider.field_map.mappings == ()


def test_it_is_an_aggregator_rather_than_an_employers_own_record() -> None:
    """It republishes work advertised elsewhere and composes its own page.
    `original` is True on a minority of rows and is archived rather than read:
    a boolean is not a provenance chain."""
    from career_agent.providers.base import ProviderKind

    assert RemoteOkProvider.kind is ProviderKind.AGGREGATOR


# -- the advert --------------------------------------------------------------


def test_the_whole_advert_arrives_in_the_feed() -> None:
    assert RemoteOkProvider.capabilities.full_description_in_list is True
    assert RemoteOkProvider.capabilities.obtains_full_description is True
    assert all(len(r.get("description") or "") > 100 for r in POSTINGS)


def test_list_items_survive_as_separate_lines() -> None:
    text = strip_html("<ul><li>Support</li><li>Success</li></ul>")
    assert "Support" in text.splitlines()
    assert "Success" in text.splitlines()
    assert "<" not in text


def test_the_window_carries_work_outside_engineering() -> None:
    """The reason this source is worth having at all. A corpus meant to serve a
    career changer needs roles an engineering-heavy one does not carry, and
    this window has a hospitality job in Ipojuca beside a support role."""
    titles = " ".join((r.get("position") or "") for r in POSTINGS).lower()
    tags = {tag for r in POSTINGS for tag in (r.get("tags") or [])}
    assert "non tech" in tags or "support" in titles
