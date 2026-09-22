"""A published posting URL names a board, family by family, and nothing else does.

The other half of ADR-0013. `identify_posting_url` answers "which posting is
this"; `identify_board_url` answers "which BOARD is this posting on", and only
for URL shapes the family builds itself. A family that cannot be guessed
(Workday, Recruitee) may be reached this way because the identity was
published, not derived.
"""

from __future__ import annotations

import pytest

from career_agent.providers.registry import (
    board_recogniser_families,
    identify_board_url,
    identify_posting_url,
)

UUID = "0fc20191-55cf-4cf9-85fd-d05496298112"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # Greenhouse, three hosts, and the board token in the path.
        (
            "https://job-boards.eu.greenhouse.io/ably30/jobs/4958779101",
            ("greenhouse", "ably30", "4958779101"),
        ),
        (
            "https://job-boards.greenhouse.io/midihealth/jobs/4412523005?gh_src=x",
            ("greenhouse", "midihealth", "4412523005"),
        ),
        ("https://boards.greenhouse.io/Acme/jobs/1234567", ("greenhouse", "acme", "1234567")),
        # Lever, US and EU hosts, with and without /apply.
        (
            f"https://jobs.lever.co/bluelightconsulting/{UUID}",
            ("lever", "bluelightconsulting", UUID),
        ),
        (f"https://jobs.eu.lever.co/acme/{UUID}/apply", ("lever", "acme", UUID)),
        # Ashby.
        (f"https://jobs.ashbyhq.com/livekit/{UUID}", ("ashby", "livekit", UUID)),
        # Workday: tenant, shard AND site, with or without a locale segment,
        # and the apply page is the posting page.
        (
            "https://mtb.wd5.myworkdayjobs.com/MTB/job/Remote-USA/Manager_R76072/apply",
            ("workday", "mtb.wd5/MTB", "/job/Remote-USA/Manager_R76072"),
        ),
        (
            "https://thermofisher.wd5.myworkdayjobs.com/en-US/thermofishercareers/job/Remote/Sr_R-1",
            ("workday", "thermofisher.wd5/thermofishercareers", "/job/Remote/Sr_R-1"),
        ),
        (
            "https://gdit.wd5.myworkdayjobs.com/External_Career_Site/job/USA-VA/Analyst_RQ1",
            ("workday", "gdit.wd5/External_Career_Site", "/job/USA-VA/Analyst_RQ1"),
        ),
        # Rippling, in the stored external-id form.
        (
            f"https://ats.rippling.com/rippling/jobs/{UUID}",
            ("rippling", "rippling", f"rippling-rippling-{UUID}"),
        ),
        # Teamtailor and Recruitee name the tenant and no posting id.
        ("https://payfit.teamtailor.com/jobs/123456-engineer", ("teamtailor", "payfit", None)),
        ("https://acme.recruitee.com/o/senior-engineer", ("recruitee", "acme", None)),
        # Workable's account form names the account; the feed covers it.
        (
            "https://apply.workable.com/huzzle/j/9A9ECD49CB/",
            ("workable", "huzzle", "workable-9A9ECD49CB"),
        ),
    ],
)
def test_a_hosted_posting_url_names_its_board(url: str, expected: tuple[str, str, str | None]):
    assert identify_board_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        # An embedded Greenhouse board proves the family and NOT the board.
        "https://careers.datadoghq.com/detail/1497543/?gh_jid=1497543",
        # A Workday host alone is two thirds of an identity.
        "https://gdit.wd5.myworkdayjobs.com/",
        "https://gdit.wd5.myworkdayjobs.com/en-US/",
        # The vendor's own hosts are never a tenant.
        "https://careers.teamtailor.com/jobs/1-x",
        "https://www.recruitee.com/o/anything",
        # Workable's short form names no account.
        "https://apply.workable.com/j/9A9ECD49CB",
        # Half the web.
        "https://example.com/jobs/12345",
        "",
        "not a url",
    ],
)
def test_a_url_that_names_no_board_is_refused(url: str) -> None:
    assert identify_board_url(url) is None


def test_no_two_families_claim_one_url() -> None:
    """The tie rule, exercised over every example above."""
    for url in (
        "https://job-boards.eu.greenhouse.io/ably30/jobs/4958779101",
        f"https://jobs.lever.co/acme/{UUID}",
        f"https://jobs.ashbyhq.com/acme/{UUID}",
        "https://acme.recruitee.com/o/x",
        "https://acme.teamtailor.com/jobs/1-x",
        f"https://ats.rippling.com/acme/jobs/{UUID}",
        "https://apply.workable.com/acme/j/9A9ECD49CB/",
    ):
        assert identify_board_url(url) is not None, url


def test_the_families_are_the_ones_that_recognise_themselves() -> None:
    assert board_recogniser_families() == (
        "ashby",
        "comeet",
        "greenhouse",
        "lever",
        "recruitee",
        "rippling",
        "teamtailor",
        "workable",
        "workday",
    )


# -- Workable's posting identity, the research's second finding ----------------


def test_a_workable_apply_url_resolves_to_the_stored_external_id() -> None:
    """The largest provider in the corpus had no recogniser, so a WWR row and
    its Workable original sat as two jobs. Both public forms resolve now."""
    assert identify_posting_url("https://apply.workable.com/j/9A9ECD49CB") == (
        "workable",
        "workable-9A9ECD49CB",
    )
    assert identify_posting_url("https://apply.workable.com/getellipsis/j/C4645A1DB1/apply/") == (
        "workable",
        "workable-C4645A1DB1",
    )
    # The view page carries a different id and is not claimed.
    assert identify_posting_url("https://jobs.workable.com/view/abc/def") is None
