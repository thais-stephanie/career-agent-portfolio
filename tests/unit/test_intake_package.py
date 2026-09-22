"""The Candidate Intake Package contract, and every refusal in it.

Every package here is SYNTHETIC. The two documents this contract was designed
against are the owner's own CV and LinkedIn export, and neither their content
nor anything derived from it appears in a tracked test.

The tests that matter are the refusals. A parser that accepts a well-formed
file produced by any assistant is a parser through which invented facts about
somebody's career arrive looking exactly like real ones.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from career_agent.intake import parse_package
from career_agent.intake.conflicts import reconcile
from career_agent.intake.models import (
    CURRENT_SCHEMA_VERSION,
    MAX_QUOTE,
    IntakeParseError,
)
from career_agent.intake.parse import claim_identity, package_digest


def _package(**overrides: Any) -> dict[str, Any]:
    """A minimal valid package. Invented; no real career is described here."""
    base: dict[str, Any] = {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "generator": {"kind": "EXTERNAL_AI", "name": "an assistant"},
        "sources": [{"ref": "cv", "kind": "RESUME", "title": "cv.docx"}],
        "claims": [
            {
                "type": "EMPLOYMENT",
                "text": "Integration Engineer at Acme",
                "source_ref": "cv",
                "employer": "Acme",
                "period": {"start": {"original": "Jan 2020", "normalized": "2020-01"}},
                "evidence": {"quote": "Integration Engineer, Acme, Jan 2020"},
            }
        ],
    }
    base.update(overrides)
    return base


# =========================================================================
# it reads a valid package
# =========================================================================


def test_a_well_formed_package_parses() -> None:
    package = parse_package(_package())
    assert package.schema_version == CURRENT_SCHEMA_VERSION
    assert len(package.claims) == 1
    assert package.claims[0].employer == "Acme"


def test_bytes_and_text_and_mapping_all_parse_to_the_same_thing() -> None:
    document = _package()
    as_text = json.dumps(document)
    assert (
        package_digest(parse_package(document))
        == package_digest(parse_package(as_text))
        == package_digest(parse_package(as_text.encode("utf-8")))
    )


# =========================================================================
# the refusals
# =========================================================================


def test_an_unsupported_schema_version_is_refused() -> None:
    """A field name that changed meaning between versions is invisible to a
    parser that shrugs at the version number."""
    with pytest.raises(IntakeParseError) as exc:
        parse_package(_package(schema_version="9.9"))
    assert "9.9" in str(exc.value)


def test_a_package_may_not_claim_a_claim_is_verified() -> None:
    """Confirmation is an act by a person. A file asserting it asserts it for
    somebody else."""
    document = _package()
    document["claims"][0]["verified"] = True

    with pytest.raises(IntakeParseError) as exc:
        parse_package(document)
    assert "verified" in str(exc.value)


@pytest.mark.parametrize(
    "key",
    [
        "eligibility",
        "work_authorization",
        "salary_expectation",
        "willing_to_relocate",
        "citizenship",
    ],
)
def test_a_package_may_not_answer_a_candidate_profile_question(key: str) -> None:
    """A CV can say where somebody has WORKED. It cannot say where they may
    work, or would like to, and an assistant will conflate the two."""
    document = _package()
    document["claims"][0][key] = "anything at all"

    with pytest.raises(IntakeParseError) as exc:
        parse_package(document)
    assert key in str(exc.value)


def test_a_forbidden_key_is_refused_however_deeply_it_is_buried() -> None:
    document = _package()
    document["claims"][0]["evidence"] = {
        "quote": "Integration Engineer, Acme",
        "confirmed": True,
    }

    with pytest.raises(IntakeParseError) as exc:
        parse_package(document)
    assert "confirmed" in str(exc.value)


def test_a_claim_citing_an_undeclared_source_is_refused() -> None:
    """An unattributable claim makes every conflict unresolvable."""
    document = _package()
    document["claims"][0]["source_ref"] = "somewhere-else"

    with pytest.raises(IntakeParseError) as exc:
        parse_package(document)
    assert "somewhere-else" in str(exc.value)


def test_a_claim_with_no_evidence_at_all_is_refused() -> None:
    """Otherwise a model's assertion is indistinguishable from the
    candidate's own words."""
    document = _package()
    document["claims"][0]["evidence"] = {}

    with pytest.raises(IntakeParseError):
        parse_package(document)


def test_a_normalised_date_of_the_wrong_shape_is_refused() -> None:
    document = _package()
    document["claims"][0]["period"] = {"start": {"original": "Jan 2020", "normalized": "01/2020"}}

    with pytest.raises(IntakeParseError) as exc:
        parse_package(document)
    assert "YYYY-MM" in str(exc.value)


def test_a_period_that_ends_before_it_starts_is_refused() -> None:
    document = _package()
    document["claims"][0]["period"] = {
        "start": {"original": "Jan 2021", "normalized": "2021-01"},
        "end": {"original": "Jan 2020", "normalized": "2020-01"},
    }

    with pytest.raises(IntakeParseError):
        parse_package(document)


def test_a_role_cannot_be_current_and_also_have_an_end_date() -> None:
    document = _package()
    document["claims"][0]["period"] = {
        "start": {"original": "Jan 2020", "normalized": "2020-01"},
        "end": {"original": "Jan 2021", "normalized": "2021-01"},
        "current": True,
    }

    with pytest.raises(IntakeParseError):
        parse_package(document)


def test_there_is_nowhere_to_put_a_bare_number() -> None:
    """The structural half of "a figure is flagged and never lifted out".

    `MetricMention` has no numeric field, so a generator cannot supply one --
    which is a stronger guarantee than a rule saying not to.
    """
    document = _package()
    document["claims"][0]["metrics"] = [{"original": "cut the queue to 2-3", "value": 3}]

    with pytest.raises(IntakeParseError):
        parse_package(document)


def test_an_evidence_quote_longer_than_an_excerpt_is_refused() -> None:
    """A package proposes evidence; it is not a second copy of the CV."""
    document = _package()
    document["claims"][0]["evidence"] = {"quote": "x" * (MAX_QUOTE + 1)}

    with pytest.raises(IntakeParseError):
        parse_package(document)


def test_an_unknown_field_anywhere_is_refused() -> None:
    document = _package()
    document["claims"][0]["seniority"] = "Senior"

    with pytest.raises(IntakeParseError):
        parse_package(document)


def test_prose_around_the_json_is_refused_with_a_useful_message() -> None:
    """The single most likely failure: an assistant wrapped its answer."""
    wrapped = "Here is the file you asked for:\n```json\n" + json.dumps(_package()) + "\n```"

    with pytest.raises(IntakeParseError) as exc:
        parse_package(wrapped)
    assert "code fence" in str(exc.value)


def test_two_sources_sharing_a_ref_are_refused() -> None:
    document = _package(
        sources=[
            {"ref": "cv", "kind": "RESUME", "title": "cv.docx"},
            {"ref": "cv", "kind": "LINKEDIN", "title": "profile.pdf"},
        ]
    )

    with pytest.raises(IntakeParseError) as exc:
        parse_package(document)
    assert "cv" in str(exc.value)


def test_a_package_declaring_no_source_is_refused() -> None:
    with pytest.raises(IntakeParseError):
        parse_package(_package(sources=[]))


# =========================================================================
# identity
# =========================================================================


def test_the_same_claim_from_two_documents_gets_one_key() -> None:
    """Otherwise the candidate is asked the same question twice."""
    document = _package(
        sources=[
            {"ref": "cv", "kind": "RESUME", "title": "cv.docx"},
            {"ref": "li", "kind": "LINKEDIN", "title": "profile.pdf"},
        ],
        claims=[
            {
                "type": "SKILL",
                "text": "Workato",
                "source_ref": "cv",
                "evidence": {"locator": "Skills"},
            },
            {
                "type": "SKILL",
                "text": "Workato",
                "source_ref": "li",
                "evidence": {"locator": "Top Skills"},
            },
        ],
    )
    package = parse_package(document)

    assert claim_identity(package.claims[0]) == claim_identity(package.claims[1])


def test_identity_survives_accents_and_punctuation() -> None:
    a = parse_package(
        _package(
            claims=[
                {
                    "type": "SKILL",
                    "text": "Gestao de Processos",
                    "source_ref": "cv",
                    "evidence": {"locator": "Skills"},
                }
            ]
        )
    ).claims[0]
    b = parse_package(
        _package(
            claims=[
                {
                    "type": "SKILL",
                    "text": "gestao, de processos",
                    "source_ref": "cv",
                    "evidence": {"locator": "Skills"},
                }
            ]
        )
    ).claims[0]

    assert claim_identity(a) == claim_identity(b)


def test_a_different_period_is_a_different_claim() -> None:
    """The pair `conflicts.py` then groups. If these collapsed, a
    disagreement between two documents would silently become one answer."""

    def claim(end: str) -> dict[str, Any]:
        return {
            "type": "EMPLOYMENT",
            "text": "Integration Engineer at Acme",
            "source_ref": "cv",
            "employer": "Acme",
            "period": {
                "start": {"original": "Jan 2020", "normalized": "2020-01"},
                "end": {"original": end, "normalized": end},
            },
            "evidence": {"locator": "Experience"},
        }

    package = parse_package(_package(claims=[claim("2021-03"), claim("2021-04")]))

    assert claim_identity(package.claims[0]) != claim_identity(package.claims[1])


def test_the_digest_does_not_move_when_key_order_does() -> None:
    """`package_sha256` is what makes a re-import a recognition."""
    document = _package()
    reordered = json.loads(json.dumps(document, sort_keys=True))

    assert package_digest(parse_package(document)) == package_digest(parse_package(reordered))


# =========================================================================
# reconciliation
# =========================================================================


def _two_document_package(cv_end: str, li_end: str) -> dict[str, Any]:
    def role(ref: str, end: str) -> dict[str, Any]:
        return {
            "type": "EMPLOYMENT",
            "text": "Integration Engineer at Acme",
            "source_ref": ref,
            "employer": "Acme",
            "period": {
                "start": {"original": "Jan 2020", "normalized": "2020-01"},
                "end": {"original": end, "normalized": end},
            },
            "evidence": {"locator": "Experience"},
        }

    return _package(
        sources=[
            {"ref": "cv", "kind": "RESUME", "title": "cv.docx"},
            {"ref": "li", "kind": "LINKEDIN", "title": "profile.pdf"},
        ],
        claims=[role("cv", cv_end), role("li", li_end)],
    )


def test_two_documents_disagreeing_about_a_date_become_a_conflict_group() -> None:
    result = reconcile(parse_package(_two_document_package("2021-03", "2021-04")))

    assert len(result.groups) == 2
    assert len(result.conflicts) == 1
    assert len(result.conflicted_keys) == 2
    assert all(g.conflict_group is not None for g in result.groups)


def test_two_documents_agreeing_are_one_claim_citing_both() -> None:
    result = reconcile(parse_package(_two_document_package("2021-03", "2021-03")))

    assert len(result.groups) == 1
    assert result.duplicates_collapsed == 1
    assert result.groups[0].sources == ("cv", "li")
    assert result.conflicts == {}


def test_nothing_picks_a_winner_between_two_documents() -> None:
    """The rule this whole module exists to keep.

    Neither ordering nor source kind may decide. Reconciling the same
    disagreement with the documents swapped must produce the same two claims,
    both still unanswered.
    """
    one = reconcile(parse_package(_two_document_package("2021-03", "2021-04")))
    other = reconcile(parse_package(_two_document_package("2021-04", "2021-03")))

    assert {g.claim_key for g in one.groups} == {g.claim_key for g in other.groups}
    assert one.conflicted_keys == other.conflicted_keys


def test_a_skill_never_conflicts_with_another_skill() -> None:
    """ "Python" in one document and "SQL" in the other is two skills, not a
    disagreement. Only claims that pin a role to a span of time can be
    inconsistent."""
    document = _package(
        claims=[
            {"type": "SKILL", "text": "Python", "source_ref": "cv", "evidence": {"locator": "S"}},
            {"type": "SKILL", "text": "SQL", "source_ref": "cv", "evidence": {"locator": "S"}},
        ]
    )

    result = reconcile(parse_package(document))

    assert result.conflicts == {}
    assert len(result.groups) == 2


def test_two_claims_about_one_employer_with_no_dates_do_not_conflict() -> None:
    """A CV's title line and one of its achievement bullets are two claims
    about the same employer, and neither contradicts the other."""
    document = _package(
        claims=[
            {
                "type": "EMPLOYMENT",
                "text": "Integration Engineer at Acme",
                "source_ref": "cv",
                "employer": "Acme",
                "evidence": {"locator": "Experience"},
            },
            {
                "type": "EMPLOYMENT",
                "text": "Built the billing reconciliation layer",
                "source_ref": "cv",
                "employer": "Acme",
                "evidence": {"locator": "Experience"},
            },
        ]
    )

    result = reconcile(parse_package(document))

    assert result.conflicts == {}


def test_what_the_package_did_not_contain_is_reported() -> None:
    """So a candidate can see their certifications did not come through,
    rather than concluding they have none."""
    result = reconcile(parse_package(_package()))

    assert "CERTIFICATION" in result.missing_kinds
    assert "EMPLOYMENT" not in result.missing_kinds


def test_consecutive_roles_at_one_employer_are_not_a_disagreement() -> None:
    """The correction that made conflict detection usable.

    The first rule grouped every dated claim at one employer whenever two
    distinct periods appeared, which reads a promotion as a contradiction.
    Measured on the owner's own documents: 174 of 334 claims flagged, almost
    none of it real, and the one genuine disagreement invisible inside it.
    """

    def role(title: str, start: str, end: str) -> dict[str, Any]:
        return {
            "type": "EMPLOYMENT",
            "text": title,
            "source_ref": "cv",
            "employer": "Acme",
            "period": {
                "start": {"original": start, "normalized": start},
                "end": {"original": end, "normalized": end},
            },
            "evidence": {"locator": "Experience"},
        }

    package = parse_package(
        _package(
            claims=[
                role("Engineer", "2022-04", "2023-04"),
                role("Senior Engineer", "2023-04", "2024-02"),
                role("Staff Engineer", "2024-02", "2024-07"),
            ]
        )
    )

    result = reconcile(package)

    assert result.conflicts == {}
    assert len(result.groups) == 3


def test_two_documents_overlapping_on_one_role_do_disagree() -> None:
    """The shape that IS a conflict: one stretch of time at one employer,
    described two ways."""

    def role(ref: str, end: str | None, current: bool) -> dict[str, Any]:
        period: dict[str, Any] = {"start": {"original": "Mar 2025", "normalized": "2025-03"}}
        if current:
            period["current"] = True
        else:
            period["end"] = {"original": end, "normalized": end}
        return {
            "type": "EMPLOYMENT",
            "text": "Staff Engineer",
            "source_ref": ref,
            "employer": "Acme",
            "period": period,
            "evidence": {"locator": "Experience"},
        }

    package = parse_package(
        _package(
            sources=[
                {"ref": "cv", "kind": "RESUME", "title": "cv.docx"},
                {"ref": "li", "kind": "LINKEDIN", "title": "profile.pdf"},
            ],
            claims=[role("cv", None, True), role("li", "2026-09", False)],
        )
    )

    result = reconcile(package)

    assert len(result.conflicts) == 1
    assert len(result.conflicted_keys) == 2


def test_an_employers_legal_suffix_does_not_hide_a_disagreement() -> None:
    """ "Acme LLC" on a CV and "Acme" on a LinkedIn export are one employer.

    An enumerated suffix list, not a similarity match: "Acme Tecnologia" and
    "Acme" stay apart, because that word is part of a name.
    """
    from career_agent.intake.conflicts import normalise_employer

    assert normalise_employer("Acme LLC") == normalise_employer("Acme")
    assert normalise_employer("Acme Tecnologia") != normalise_employer("Acme")
