"""Three answers from one stored reading: alignment, history, to confirm.

Every requirement and evidence line is invented, in the SHAPE the pilot
showed: a listed skill cited for "deep experience", a list of technologies
half of which the evidence names, a required certification nobody sent.
"""

from __future__ import annotations

import pytest

from career_agent.semantic.deep_assess import assess, comparison

SKILLS = {
    "E1": {"type": "SKILL", "text": "Python", "tools": ""},
    "E2": {"type": "SKILL", "text": "JavaScript/Node.js", "tools": ""},
    "E3": {"type": "SKILL", "text": "HTML/CSS", "tools": ""},
    "E4": {"type": "SKILL", "text": "PostgreSQL", "tools": ""},
    "E5": {"type": "SKILL", "text": "Fictional Editor", "tools": ""},
}
EXPERIENCE = {
    "E9": {
        "type": "EMPLOYMENT",
        "text": "Built a billing integration in Python for a fictional retailer",
        "tools": "",
    },
}
EVIDENCE = {**SKILLS, **EXPERIENCE}


def req(quote, support="supported", ids=("E1",), kind="mandatory"):
    return {
        "quote": quote,
        "kind": kind,
        "support": support,
        "evidence_ids": list(ids),
        "reason": "",
    }


def review(*reqs, relation="central", tools=(), conditions=(), insufficient=False):
    return {
        "work_relation": {"value": relation},
        "requirements": list(reqs),
        "tools": [{"name": n, "importance": "core", "quotes": []} for n in tools],
        "conditions": list(conditions),
        "insufficient": insufficient,
    }


def status(quote, support="supported", ids=("E1",), evidence=EVIDENCE):
    return assess(review(req(quote, support, ids)), evidence).requirements[0]


def test_a_listed_skill_is_familiarity_not_deep_experience():
    r = status(
        "Deep hands-on experience with Fictional Editor as a primary environment", ids=("E5",)
    )
    assert r["status"] == "declared" and "declared_only" in r["notes"]


def test_a_listed_skill_does_not_show_designing_and_implementing():
    assert (
        status("Experience designing and implementing REST services.", ids=("E1",))["status"]
        == "declared"
    )


def test_a_listed_skill_meets_a_requirement_that_asks_only_familiarity():
    assert status("Familiarity with PostgreSQL", ids=("E4",))["status"] == "shown"


def test_described_experience_shows_practice_but_not_the_level_asked():
    assert (
        status("Hands-on experience building integrations in Python", ids=("E9",))["status"]
        == "shown"
    )
    strong = status("Strong experience building integrations in Python", ids=("E9",))
    assert strong["status"] == "partial" and "level_not_confirmed" in strong["notes"]


def test_years_are_never_shown_by_a_line():
    r = status("3+ years building integrations in Python", ids=("E9",))
    assert r["status"] == "partial" and "years_not_computed" in r["notes"]


def test_a_composite_says_which_parts_the_cited_evidence_names():
    r = status("Expert in HTML, CSS, JavaScript, and PHP", ids=("E3", "E2"))
    assert {p["text"]: p["linked"] for p in r["parts"]} == {
        "Expert in HTML": True,
        "CSS": True,
        "JavaScript": True,
        "PHP": False,
    }
    assert r["status"] == "declared" and "some_parts_unknown" in r["notes"]


def test_alternatives_are_one_requirement():
    r = status("Data storage: PostgreSQL, Redshift, or equivalent", ids=("E4",))
    assert r["parts"] == [] and r["status"] == "shown"


def test_a_composite_whose_parts_no_evidence_names_is_marked_for_review():
    r = status(
        "Knowledge of Alpha, Beta, Gamma, and Delta tracking.", support="partial", ids=("E1",)
    )
    assert r["status"] == "review" and r["notes"] == ["parts_not_named"]


def test_a_heading_whose_list_was_not_quoted_is_marked_for_review():
    a = assess(review(req("Advanced proficiency with:", "partial", ("E1",))), EVIDENCE)
    assert a.requirements[0]["status"] == "review" and a.to_review == 1


def test_not_sent_stays_unknown_never_absent():
    a = assess(review(req("Experience with Python", "unclear", ())), None)
    assert a.history == "NOT_SENT" and a.requirements[0]["status"] == "unknown"
    assert a.recommendation == "NOT_COMPARED"


def test_a_required_certification_not_shown_is_a_central_gap():
    a = assess(
        review(
            req("Fictional Tool Certification is required", "not_shown", ()),
            req("Hands-on experience building integrations in Python", ids=("E9",)),
        ),
        EVIDENCE,
    )
    assert a.central_gaps == (0,) and a.recommendation == "CHECK_FIRST"


def test_the_core_tool_of_the_job_not_shown_is_a_central_gap():
    a = assess(
        review(
            req("3-5 years of experience in Fictional CMS development", "not_shown", ()),
            tools=("Fictional CMS",),
        ),
        EVIDENCE,
    )
    assert a.central_gaps == (0,)


def test_language_and_dispositions_leave_the_technical_count():
    a = assess(
        review(
            req("Strong written English communication skills", "unclear", ()),
            req("Proactive problem-solver who works independently", "partial", ("E5",)),
            req("Hands-on experience building integrations in Python", ids=("E9",)),
        ),
        EVIDENCE,
    )
    assert a.counts["mandatory"] == 1 and a.history == "SHOWN" and a.recommendation == "STRONG"
    assert a.languages == ("Strong written English communication skills",)
    assert a.questions == ("Proactive problem-solver who works independently",)


def test_high_alignment_with_partly_shown_history_says_both():
    a = assess(
        review(
            req("Hands-on experience building integrations in Python", ids=("E9",)),
            req("Experience designing and implementing REST services.", ids=("E1",)),
            req("Experience with Fictional Queue", "not_shown", ()),
        ),
        EVIDENCE,
    )
    assert (a.alignment, a.history, a.recommendation) == ("CENTRAL", "PARTLY", "PREPARE")


@pytest.mark.parametrize(
    ("relation", "insufficient", "expected"),
    [
        ("unclear", False, "TO_CONFIRM"),
        ("central", True, "TO_CONFIRM"),
        ("unrelated", False, "OTHER_WORK"),
        ("neighbour", False, "LOW"),
    ],
)
def test_alignment_decides_before_history(relation, insufficient, expected):
    a = assess(
        review(
            req("Familiarity with PostgreSQL", ids=("E4",)),
            relation=relation,
            insufficient=insufficient,
        ),
        EVIDENCE,
    )
    assert a.recommendation == expected


def test_conditions_are_kept_verbatim_and_languages_separated():
    conditions = [
        {"quote": "Remote from a fictional region", "topic": "place"},
        {"quote": "Fluent English required", "topic": "language"},
    ]
    a = assess(review(conditions=conditions), EVIDENCE)
    assert a.conditions == (conditions[0],) and a.languages == ("Fluent English required",)


def test_comparison_names_disagreement_with_the_local_band():
    assert comparison("STRONG", "WEAK") == "raises"
    assert comparison("OTHER_WORK", "STRONG") == "lowers"
    assert comparison("CHECK_FIRST", "STRONG") == "agrees"
    assert comparison("TO_CONFIRM", "GOOD") == "open"
