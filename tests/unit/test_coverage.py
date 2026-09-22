"""The sourcing funnel and the unsupported-ATS tally.

This section exists to answer one product question -- is a single unsupported
ATS common enough to justify a fourth provider milestone? -- and the first
attempt at answering it was wrong in a way worth pinning down.

The original probe accepted any vendor endpoint that returned HTTP 200. Control
slugs no company owns proved that BambooHR answers 200 with its own marketing
page for every unknown subdomain, so the probe reported "BambooHR: 49 of 79",
Salesforce and Shopify included. A second attempt required the vendor to name
the company, and every Workable account it matched turned out to hold zero jobs:
correctly named, long dormant. Only the third attempt -- discriminating vendor,
matching name, and at least one live posting -- produced numbers small enough to
be believable.

The lesson these tests encode: an unidentified vendor must stay visible as
unidentified. Rolling UNKNOWN into the leader is exactly how a plausible,
confident, wrong answer gets manufactured.
"""

from career_agent.pipeline.coverage import summarise_candidates


def rows(*specs: tuple[str, str | None]) -> list[dict]:
    return [
        {"name": f"company-{index}", "status": status, **({"ats": ats} if ats else {})}
        for index, (status, ats) in enumerate(specs)
    ]


def test_no_queue_reports_that_it_could_not_measure() -> None:
    """Silence is not zero. A missing queue must not render as an empty tally
    that looks like a finished measurement."""
    summary = summarise_candidates(None)

    assert summary["available"] is False
    assert "cannot be reported" in summary["note"]
    assert "unsupported_ats" not in summary


def test_the_funnel_counts_every_status() -> None:
    summary = summarise_candidates(
        rows(
            ("PROMOTED", None),
            ("PROMOTED", None),
            ("NO_BOARD_FOUND", None),
            ("UNSUPPORTED_ATS", "Workable"),
        )
    )

    assert summary["total"] == 4
    assert summary["by_status"] == {
        "PROMOTED": 2,
        "NO_BOARD_FOUND": 1,
        "UNSUPPORTED_ATS": 1,
    }


def test_only_confirmed_vendors_are_tallied() -> None:
    """A company with no supported board and no identified vendor contributes
    to the denominator and to no vendor's count."""
    summary = summarise_candidates(
        rows(
            ("UNSUPPORTED_ATS", "SmartRecruiters"),
            ("UNSUPPORTED_ATS", "SmartRecruiters"),
            ("UNSUPPORTED_ATS", "Workable"),
            ("NO_BOARD_FOUND", None),
            ("NO_BOARD_FOUND", None),
            ("NO_BOARD_FOUND", None),
        )
    )

    assert summary["unsupported_ats"] == {"SmartRecruiters": 2, "Workable": 1}
    assert summary["ats_identified"] == 3
    assert summary["ats_unidentified"] == 3
    assert "3 of 6" in summary["note"]


def test_vendors_are_ordered_by_size() -> None:
    summary = summarise_candidates(
        rows(
            ("UNSUPPORTED_ATS", "Recruitee"),
            ("UNSUPPORTED_ATS", "Workable"),
            ("UNSUPPORTED_ATS", "Workable"),
        )
    )

    assert list(summary["unsupported_ats"]) == ["Workable", "Recruitee"]


def test_an_inconclusive_probe_counts_as_unidentified_not_absent() -> None:
    """A probe that failed for network reasons says nothing about whether the
    company has an ATS, so it must not quietly become evidence of absence."""
    summary = summarise_candidates(rows(("INCONCLUSIVE", None), ("UNSUPPORTED_ATS", "Workable")))

    assert summary["ats_unidentified"] == 1
    assert summary["unsupported_ats"] == {"Workable": 1}


def test_a_row_marked_unsupported_without_a_vendor_is_not_silently_dropped() -> None:
    """Whatever produced the row, the report must show that something was
    counted as unsupported with nothing to attribute it to."""
    summary = summarise_candidates(rows(("UNSUPPORTED_ATS", None)))

    assert summary["unsupported_ats"] == {"UNIDENTIFIED": 1}
