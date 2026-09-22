"""Finding a board on a company's own careers page, and refusing to invent one.

WHY THIS FILE EXISTS
--------------------
Two provider families cannot be found the way the other four are, and both
reasons are about what an EMPTY ANSWER means.

One addresses a board by a tenant, a site and a numbered data centre, none of
which is derivable from a domain. Guessing them is a namespace walk in three
dimensions.

The other addresses a board by a slug, and **its host answers for a slug nobody
registered** -- so a probe that returns no openings is indistinguishable from a
real board with nothing open, and promoting on that would create a board that
does not exist.

So identity comes from evidence a company published. The assertions that matter
here are therefore the REFUSALS: a vendor's own marketing host is not a
customer board, a widget names a family and not a board, and a link that names
two thirds of a composite identity resolves nothing.

EVERY PAGE HERE IS INVENTED. No company is named, no real careers page was
fetched to write this file, and every host uses a company name that does not
exist.
"""

from __future__ import annotations

import pytest

from career_agent.providers.site_detect import SignalKind, detect, families

# =========================================================================
# 1. WHAT IS RECOGNISED
# =========================================================================


def test_a_published_board_link_resolves_an_identity() -> None:
    signals = detect('<a href="https://acmehealth.recruitee.com/o/nurse">Open roles</a>')
    assert [(s.provider, s.identity) for s in signals] == [("recruitee", "acmehealth")]
    assert signals[0].kind is SignalKind.LINK


def test_an_api_reference_is_read_as_stronger_than_a_marketing_link() -> None:
    """A page that calls the machine surface is pointing at where the openings
    actually live, rather than at a page that talks about them."""
    signals = detect('<script>fetch("https://acmehealth.recruitee.com/api/offers/")</script>')
    assert signals[0].kind is SignalKind.API
    assert signals[0].identity == "acmehealth"


def test_a_three_part_identity_is_read_whole() -> None:
    """Tenant, data centre and site. None of the three is derivable from a
    company domain, which is the entire reason this module exists."""
    signals = detect('<a href="https://northwind.wd5.myworkdayjobs.com/en-US/External">Careers</a>')
    assert len(signals) == 1
    assert dict(signals[0].parts) == {"tenant": "northwind", "shard": "wd5", "site": "External"}
    assert signals[0].identity == "northwind.wd5/External"


def test_the_locale_segment_is_not_mistaken_for_the_site() -> None:
    """`/en-US/External` names a language and then a site.

    Without this every board found through a localised link would carry the
    same wrong identity, and they would all look like the same board.
    """
    signals = detect('<a href="https://northwind.wd5.myworkdayjobs.com/en-GB/Campus">x</a>')
    assert dict(signals[0].parts)["site"] == "Campus"


def test_the_same_board_twice_on_one_page_is_one_signal() -> None:
    """A careers page links its board from the header, the body and the footer.
    Three findings would read as three boards."""
    html = (
        '<a href="https://acmehealth.recruitee.com/">Jobs</a>'
        '<a href="https://acmehealth.recruitee.com/o/nurse">Nurse</a>'
        '<a href="https://acmehealth.recruitee.com/">Jobs</a>'
    )
    assert len(detect(html)) == 1


# =========================================================================
# 2. THE REFUSALS, WHICH ARE THE POINT
# =========================================================================


@pytest.mark.parametrize(
    "html",
    [
        '<a href="https://www.recruitee.com/pricing">Powered by Recruitee</a>',
        '<link rel="stylesheet" href="https://cdn.recruitee.com/style.css">',
        '<script src="https://app.recruitee.com/loader.js"></script>',
    ],
)
def test_a_vendors_own_host_is_never_read_as_a_customer_board(html: str) -> None:
    """`www`, `cdn` and `app` belong to the vendor.

    Reading one as a board would file the vendor itself as an employer, and
    then collect its marketing pages as somebody's openings.
    """
    assert detect(html) == ()


def test_a_widget_names_a_family_and_not_a_board() -> None:
    """The state a simpler design would not have had, and the one that keeps
    this honest.

    A careers page that loads a vendor's widget proves the vendor. Reporting it
    as a board would invent an identity; reporting it as nothing would throw
    away a true finding.
    """
    signals = detect('<div data-recruitee-widget id="jobs"></div>')
    assert len(signals) == 1
    assert signals[0].kind is SignalKind.EMBED
    assert signals[0].identity is None


def test_two_thirds_of_a_composite_identity_resolves_nothing() -> None:
    """A link naming the tenant and the data centre and no site cannot be
    called, and completing it by picking a likely site name is exactly the
    invention this module exists to avoid."""
    signals = detect('<a href="https://northwind.wd5.myworkdayjobs.com">Careers</a>')
    assert len(signals) == 1
    assert signals[0].identity is None
    assert dict(signals[0].parts) == {"tenant": "northwind", "shard": "wd5"}


def test_an_endpoint_with_no_data_centre_resolves_nothing_either() -> None:
    """The machine surface names the tenant and the site and NOT the numbered
    data centre, and a board cannot be reached without all three. Recorded as
    parts rather than guessed into an identity."""
    signals = detect('fetch("/wday/cxs/northwind/External/jobs")')
    assert signals[0].identity is None
    assert dict(signals[0].parts) == {"tenant": "northwind", "site": "External"}


def test_a_page_about_hiring_that_names_nobody_is_nothing() -> None:
    assert detect("<h1>We are hiring</h1><p>Email jobs@example.invalid</p>") == ()
    assert detect("") == ()


# =========================================================================
# 3. THE SHAPE OF THE ANSWER
# =========================================================================


def test_the_strongest_evidence_comes_first() -> None:
    """A caller taking the first signal for a family has to get the one most
    likely to name a real board."""
    html = (
        "<div data-recruitee-widget></div>"
        '<a href="https://acmehealth.recruitee.com/o/nurse">Nurse</a>'
    )
    signals = detect(html)
    assert signals[0].kind is SignalKind.LINK


def test_every_signal_quotes_the_text_that_produced_it() -> None:
    """Checkable rather than trusted, which is the rule ADR-0002 applies to a
    posting's quotes and which applies here for the same reason: this claim is
    about somebody else's website."""
    html = '<a href="https://acmehealth.recruitee.com/o/nurse">Nurse</a>'
    signal = detect(html)[0]
    assert signal.evidence in html


def test_the_recognised_families_are_named_once() -> None:
    """A caller reporting "we can recognise these" must not keep its own list."""
    assert families() == ("recruitee", "rippling", "teamtailor", "workday")


def test_teamtailor_and_rippling_boards_are_read_from_links() -> None:
    """Two families added 2026-09-11. The vendor's marketing host is never a
    tenant, and a Rippling slug is only ever read from an `ats.` board link."""
    from career_agent.providers.site_detect import detect

    html = (
        '<a href="https://payfit.teamtailor.com/jobs/1-x">Jobs</a>'
        '<a href="https://www.teamtailor.com/">the product</a>'
        '<a href="https://ats.rippling.com/rippling/jobs/abc">apply</a>'
        '<a href="https://www.rippling.com/careers">careers</a>'
    )
    found = {(s.provider, s.identity) for s in detect(html)}
    assert ("teamtailor", "payfit") in found
    assert ("rippling", "rippling") in found
    assert ("teamtailor", "www") not in found
    assert all(identity is not None for _, identity in found)
