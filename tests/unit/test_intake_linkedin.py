"""Reading a LinkedIn PDF export's Experience section.

The document here is SYNTHETIC, written to the template LinkedIn's PDF renderer
produces, read first-party from the owner's own export on 2026-09-07. No line
of anybody's real profile is committed.

The tests that matter are the ones about a WRONG employer. A missing one takes
a role out of conflict detection; a wrong one anchors a disagreement to a role
that was never in dispute and asks the candidate to resolve it.
"""

from __future__ import annotations

from career_agent.intake.build import read_period
from career_agent.intake.linkedin import (
    looks_like_a_company,
    read_positions,
)

#: The template's shape: a company on its own line, a title that may wrap, a
#: machine-written date line, a location, then prose. Several roles at one
#: employer are grouped under a total-duration line.
EXPORT = """Contact
someone@example.com

Top Skills
Automation

Experience

Northwind
Principal Logistics Engineer | Routing Systems and Data
Stewardship
November 2016 - Present
Somewhere, XX
Owned the routing pipeline end to end.
- Rebuilt the depot sync as two workflows.
Skills and Tools: iPaaS, SQL.

Contoso
4 years 1 month
Program Manager | Fleet Telemetry and Partner
Reporting
January 2016 - October 2016 (10 months)
Somewhere Else
Moved into program work when the depot team was merged.
Owned discovery for dispatch workflows.

Senior Support Engineer | Scheduling Tools,
Routing and Process Design
October 2012 - January 2016 (3 years 4 months)
Somewhere Else
Built schedules that cut overtime planning time.
Keywords: Scheduling, Routing, Delivery

Education
Some University
"""


def test_it_finds_every_position_the_export_states() -> None:
    positions = read_positions(EXPORT)
    assert len(positions) == 3


def test_a_company_on_its_own_line_is_read_as_the_employer() -> None:
    first = read_positions(EXPORT)[0]
    assert first.employer == "Northwind"
    assert "Principal Logistics Engineer" in first.title


def test_a_group_duration_line_names_the_employer_above_it() -> None:
    """The template's own grouping. `4 years 1 month` says the line above is
    the company for the roles beneath."""
    second = read_positions(EXPORT)[1]
    assert second.employer == "Contoso"


def test_a_grouped_role_inherits_the_employer_of_its_group() -> None:
    third = read_positions(EXPORT)[2]
    assert third.employer == "Contoso"
    assert "Senior Support Engineer" in third.title


def test_a_wrapped_title_is_never_read_as_an_employer() -> None:
    """The failure this reader was rewritten to stop.

    Measured on a real export: without positive identification, a wrapped
    title line became the employer on most positions.
    """
    employers = {p.employer for p in read_positions(EXPORT)}
    assert employers == {"Northwind", "Contoso"}
    assert not any("|" in (e or "") for e in employers)


def test_the_date_line_is_kept_verbatim_for_one_period_reader() -> None:
    """A LinkedIn date and a CV date must go through identical code, or two
    documents can disagree because two parsers did."""
    first = read_positions(EXPORT)[0]
    period = read_period(first.period_line)
    assert period is not None
    assert period.start is not None
    assert period.start.normalized == "2016-11"
    assert period.current is True


def test_a_role_that_ended_keeps_both_ends() -> None:
    second = read_positions(EXPORT)[1]
    period = read_period(second.period_line)
    assert period is not None
    assert period.start is not None and period.end is not None
    assert (period.start.normalized, period.end.normalized) == ("2016-01", "2016-10")
    assert period.current is False


def test_the_original_wording_of_a_date_is_never_discarded() -> None:
    """`normalized` is a reading; `original` is the evidence for it."""
    period = read_period(read_positions(EXPORT)[1].period_line)
    assert period is not None and period.start is not None
    assert period.start.original == "January 2016"


def test_education_dates_are_not_read_as_roles() -> None:
    """The section is bounded by the next heading. Without that, a degree's
    dates become a job."""
    assert all("University" not in p.title for p in read_positions(EXPORT))


def test_an_export_with_no_experience_section_yields_nothing() -> None:
    assert read_positions("Contact\nsomeone@example.com\n") == []


# -- the identification rule itself ----------------------------------------


def test_a_bare_noun_phrase_is_a_company() -> None:
    assert looks_like_a_company("Northwind")
    assert looks_like_a_company("Northwind Traders")


def test_a_compound_title_is_not_a_company() -> None:
    assert not looks_like_a_company("Senior Support Engineer | Routing Platforms")


def test_a_keyword_line_is_not_a_company() -> None:
    assert not looks_like_a_company("Keywords: Project Management, Delivery")


def test_a_fragment_of_a_sentence_is_not_a_company() -> None:
    """A body line that happens not to end in a full stop still starts mid
    sentence, and the template never starts a company name lower case."""
    assert not looks_like_a_company("validate requirements and change impacts")


def test_a_line_too_long_to_be_a_name_is_not_a_company() -> None:
    assert not looks_like_a_company("A" * 200)


def test_a_group_boundary_resets_an_employer_it_could_not_read() -> None:
    """The correction that mattered most.

    A group-duration line proves a NEW employer starts. When its name cannot
    be read the answer is None, never the previous company -- on the owner's
    own export, carrying it forward attributed several roles at other
    companies to one employer.
    """
    export = EXPORT.replace("Contoso\n4 years 1 month", "Keywords: things\n4 years 1 month")

    positions = read_positions(export)

    assert positions[0].employer == "Northwind"
    assert positions[1].employer is None
    assert positions[2].employer is None


def test_one_positions_body_never_swallows_the_next_positions_header() -> None:
    """The defect that put one employer's title into another's claims.

    A body that ran to the next DATE line included the next role's company and
    title, so on the owner's own export one employer's title was filed as a claim
    about the previous employer -- and landed in that employer's disagreement
    group, where it made no sense at all.
    """
    first, second, _third = read_positions(EXPORT)

    body = " ".join(first.body)
    assert "Contoso" not in body
    assert "Program Manager" not in body
    assert "Owned the routing pipeline" in body
    assert second.employer == "Contoso"
