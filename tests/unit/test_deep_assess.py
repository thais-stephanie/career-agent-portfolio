"""Three answers from one stored reading: alignment, history, to confirm.

Every requirement and evidence line is invented, in the SHAPE the pilot
showed: a listed skill cited for "deep experience", a list of technologies
half of which the evidence names, a required certification nobody sent.
"""

from __future__ import annotations

from pathlib import Path

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
    deep = status("Deep experience building integrations in Python", ids=("E9",))
    assert deep["status"] == "partial" and "level_not_confirmed" in deep["notes"]


@pytest.mark.parametrize(
    "quote",
    [
        "Strong experience building integrations in Python",
        "Solid experience building integrations in Python",
        "Excellent hands-on experience building integrations in Python",
        "Exceptional track record building integrations in Python",
        "Forte experiência construindo integrações em Python",
        "Sólida experiência construindo integrações em Python",
    ],
)
def test_intensity_words_do_not_cap_described_experience(quote):
    r = status(quote, ids=("E9",))
    assert r["status"] == "shown" and "level_not_confirmed" not in r["notes"]


@pytest.mark.parametrize(
    "quote",
    [
        "Advanced experience building integrations in Python",
        "Expert-level experience building integrations in Python",
        "Expertise building integrations in Python",
        "Senior experience building integrations in Python",
        "Extensive experience building integrations in Python",
        "In-depth experience building integrations in Python",
        "Mastery of building integrations in Python",
        "Experiência avançada construindo integrações em Python",
        "Strong, deep experience building integrations in Python",
    ],
)
def test_words_that_ask_a_level_still_cap_it(quote):
    r = status(quote, ids=("E9",))
    assert r["status"] == "partial" and "level_not_confirmed" in r["notes"]


def test_intensity_still_asks_more_than_a_listed_skill_shows():
    """`declared_only` is unchanged: "strong knowledge of X" is not met by
    having listed X, although "knowledge of X" alone would be."""
    assert status("Knowledge of PostgreSQL", ids=("E4",))["status"] == "shown"
    strong = status("Strong knowledge of PostgreSQL", ids=("E4",))
    assert strong["status"] == "declared" and "declared_only" in strong["notes"]


def test_intensity_does_not_lift_a_requirement_the_reading_did_not_support():
    assert status("Strong experience with Kubernetes", "not_shown", ())["status"] == "not_shown"
    r = status("Strong experience building integrations in Python", "partial", ("E9",))
    assert r["status"] == "partial"


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


def test_a_requirement_cited_by_one_word_is_to_review_and_credits_nothing():
    checked = {**req("React", support="not_shown", ids=()), "check": "short_quote"}
    base = assess(review(req("Python")), EVIDENCE)
    a = assess(review(req("Python"), checked, tools=("React",)), EVIDENCE)
    item = a.requirements[1]
    assert item["status"] == "review" and item["notes"] == ["short_quote"]
    assert a.counts["review"] == 1 and a.counts["mandatory"] == 2
    assert base.history == "SHOWN" and a.history == "PARTLY"
    # The central tool is open: the recommendation cannot read STRONG.
    assert base.recommendation == "STRONG" and a.recommendation == "CHECK_FIRST"


def test_a_supported_claim_made_by_one_word_gets_no_credit():
    claimed = {**req("Python", support="supported", ids=("E1",)), "check": "short_quote"}
    a = assess(review(claimed), EVIDENCE)
    assert a.requirements[0]["status"] == "review"
    assert a.history == "PARTLY"
    assert a.recommendation != "STRONG"


def test_one_agent_does_not_show_sub_agents():
    evidence = {
        "E1": {"type": "PROJECT", "text": "Built a tool-calling agent in Python", "tools": ""}
    }
    asked = "Experience building agent-based workflows, including sub-agents and orchestration"
    a = assess(review(req(asked, "supported", ("E1",))), evidence)
    item = a.requirements[0]
    assert item["status"] == "partial" and "specific_not_shown" in item["notes"]
    named = {
        "E1": {"type": "PROJECT", "text": "Ran sub-agents under one orchestrator", "tools": ""}
    }
    again = assess(review(req(asked, "supported", ("E1",))), named)
    assert again.requirements[0]["status"] == "shown"


def test_english_and_a_workspace_are_to_confirm_never_missing():
    english = req("Advanced English: high-level written and verbal English", "not_shown", ())
    setup = req("Dedicated quiet workspace with a high-performance computer", "not_shown", ())
    a = assess(review(english, setup), EVIDENCE)
    assert [r["status"] for r in a.requirements] == ["unknown", "unknown"]
    assert a.counts["not_shown"] == 0 and a.central_gaps == ()
    assert a.languages == (english["quote"],)
    assert [c["quote"] for c in a.conditions] == [setup["quote"]]


def test_an_empty_comparison_is_the_readings_not_the_postings():
    # The sentence shown for "no technical requirement" must not claim the
    # posting has none: the reading may simply not have listed any.
    text = (Path(__file__).parents[2] / "src/career_agent/web/static/js/i18n.js").read_text(
        encoding="utf-8"
    )
    for line in text.splitlines():
        if "'deep.history.NONE_STATED'" in line:
            assert "reading" in line or "leitura" in line


@pytest.mark.parametrize(
    "quote",
    [
        "Sehr gute schriftliche Ausdrucksweise in Deutsch",
        "Sehr gute Deutschkenntnisse in Wort und Schrift (mindestens C1-Niveau)",
        "Fließende Englischkenntnisse",
        "Gute Sprachkenntnisse in Französisch",
        "Dominio del idioma inglés",
        "Fluent Dutch or Nederlands",
    ],
)
def test_a_spoken_language_in_any_language_is_a_condition_not_a_gap(quote):
    a = assess(
        review(
            req(quote, "not_shown", ()),
            req("Hands-on experience building integrations in Python", ids=("E9",)),
        ),
        EVIDENCE,
    )
    assert a.languages == (quote,)
    assert a.counts["mandatory"] == 1 and a.recommendation == "STRONG"


def test_a_programming_language_is_still_technical():
    a = assess(
        review(req("Proficiency in at least one programming language such as Go", "not_shown", ())),
        EVIDENCE,
    )
    assert a.languages == () and a.counts["not_shown"] == 1


# --- Ways of working are questions to confirm, not technical requirements ----


def category(quote):
    return assess(review(req(quote, "supported", ("E9",))), EVIDENCE).requirements[0]["category"]


@pytest.mark.parametrize(
    "quote",
    [
        "Strong analytical skills with the ability to leverage CRM data",
        "Strong analytical and problem-solving skills",
        "Excellent analytical thinking",
        "Strong problem-solving skills and a portfolio of completed projects",
        "Excellent project management and cross-functional stakeholder management skills.",
        "Strong organizational and project management skills",
        "Experience with stakeholder management across departments",
        "Strong troubleshooting instincts",
        "Strong analytical and commercial thinking.",
        "Excellent interpersonal and collaboration skills",
        "Capacidade de resolução de problemas",
    ],
)
def test_a_way_of_working_is_a_question_not_a_technical_gap(quote):
    a = assess(review(req(quote, "not_shown", ())), EVIDENCE)
    assert a.requirements[0]["category"] == "disposition"
    assert a.questions == (quote,) and a.counts["mandatory"] == 0 and a.central_gaps == ()


@pytest.mark.parametrize(
    "quote",
    [
        "Experience troubleshooting API integrations and webhooks",
        "Experience with project management tools such as Jira or Asana",
        "Hands-on experience with analytics tools such as Looker or Power BI",
        "Strong SQL skills for data analysis",
        "Experience solving integration problems in Python",
        "Experience managing CRM implementation projects end to end",
    ],
)
def test_the_work_itself_stays_technical(quote):
    assert category(quote) == "technical"


def test_intensity_gives_a_way_of_working_no_technical_credit():
    a = assess(
        review(
            req("Excellent project management and stakeholder management skills", ids=("E9",)),
            req("Hands-on experience building integrations in Python", "partial", ("E9",)),
        ),
        EVIDENCE,
    )
    assert a.counts["mandatory"] == 1 and a.history != "SHOWN"


# --- P1: years from confirmed dates, never inferred --------------------------


def dated(*items):
    """Evidence with dated work items E20, E21, ... (type, start, end)."""
    return {
        **EVIDENCE,
        **{
            f"E{20 + n}": {
                "type": kind,
                "text": "Built billing integrations in Python for a fictional retailer",
                "tools": "",
                "start": start,
                "end": end,
            }
            for n, (kind, start, end) in enumerate(items)
        },
    }


def years(quote, evidence, ids=("E20",), support="supported"):
    return assess(review(req(quote, support, ids)), evidence).requirements[0]


def test_dated_work_covering_the_years_shows_them():
    r = years(
        "3+ years building integrations in Python", dated(("EMPLOYMENT", "2020-01", "2023-06"))
    )
    assert r["status"] == "shown" and "years_covered" in r["notes"]


def test_dated_work_short_of_the_years_stays_partial():
    r = years(
        "3+ years building integrations in Python", dated(("EMPLOYMENT", "2021-01", "2023-01"))
    )
    assert r["status"] == "partial" and "years_not_computed" in r["notes"]


def test_overlapping_roles_are_counted_once():
    evidence = dated(("EMPLOYMENT", "2020-01", "2022-01"), ("PROJECT", "2021-01", "2023-01"))
    ids = ("E20", "E21")
    assert years("3+ years building integrations", evidence, ids)["status"] == "shown"
    assert years("4+ years building integrations", evidence, ids)["status"] == "partial"


def test_an_undated_item_adds_nothing_and_nothing_is_guessed():
    assert (
        years("1+ years building integrations in Python", EVIDENCE, ("E9",))["status"] == "partial"
    )
    evidence = dated(("EMPLOYMENT", None, None))
    assert years("1+ years building integrations in Python", evidence)["status"] == "partial"


def test_a_dated_degree_or_certificate_is_not_time_doing_the_work():
    for kind in ("EDUCATION", "CERTIFICATION"):
        r = years("2+ years building integrations", dated((kind, "2010-01", "2020-01")))
        assert r["status"] != "shown", kind


def test_the_most_years_named_are_the_years_asked():
    evidence = dated(("EMPLOYMENT", "2020-01", "2024-01"))
    r = years("6+ years of software engineering, with 2+ years building integrations", evidence)
    assert r["status"] == "partial"
    assert years("3-5 years building integrations", evidence)["status"] == "shown"


def test_dates_never_lift_what_the_reading_did_not_support():
    evidence = dated(("EMPLOYMENT", "2010-01", "2024-01"))
    assert (
        years("3+ years building integrations", evidence, support="partial")["status"] == "partial"
    )
    assert (
        years("3+ years building integrations", evidence, support="not_shown", ids=())["status"]
        == "not_shown"
    )


def test_dates_never_lift_a_listed_skill_or_a_level_word():
    evidence = dated(("EMPLOYMENT", "2010-01", "2024-01"))
    assert years("3+ years of Python", evidence, ids=("E1",))["status"] == "declared"
    r = years("3+ years of advanced integration work", evidence)
    assert r["status"] == "partial" and "level_not_confirmed" in r["notes"]


def test_an_open_role_runs_to_this_month():
    r = years("5+ years building integrations in Python", dated(("EMPLOYMENT", "2015-01", None)))
    assert r["status"] == "shown" and "years_covered" in r["notes"]


def test_a_requirement_that_asks_years_is_never_only_a_way_of_working():
    quote = "5+ years in a role requiring diagnostic thinking and problem-solving"
    assert category(quote) == "technical"


# --- Work conditions are conditions to confirm, not technical requirements ---


@pytest.mark.parametrize(
    ("quote", "topic"),
    [
        ("Ability to work onsite in Winter Park five days per week.", "work_model"),
        ("Able to work on-site.", "work_model"),
        ("Comfortable working in-office 5 days per week at a high intensity", "work_model"),
        ("Ability and desire to work remotely", "work_model"),
        ("Ability to relocate to Sofia for this on-site opportunity.", "work_model"),
        ("Ability to travel up to 30%", "travel"),
        ("30\u201350% travel to assigned markets", "travel"),
        ("Comfort with approximately 10% to 20% travel each year.", "travel"),
        ("Ability to work effectively during standard U.S. business hours.", "schedule"),
        (
            "Ability to work full-time with a minimum of 5 hours overlap with US Central Time.",
            "schedule",
        ),
        ("Available to work weekends and evenings on the rotating schedule.", "schedule"),
        ("Availability to work U.S. Mountain Time (8:00 AM \u2013 5:00 PM).", "schedule"),
        ("Based in Latin America with at least 6 hours of overlap with US Pacific time.", "place"),
        ("Candidates MUST BE located in USA due to client's technical requirements.", "place"),
        (
            "Authorization to work in the US without current or future visa sponsorship.",
            "work_authorization",
        ),
        (
            "A right to work in the UK (we are not in a position to support visa sponsorship)",
            "work_authorization",
        ),
        ("Active TS/SCI clearance with Polygraph", "work_authorization"),
        ("Able to pass a background check & drug screen", "work_authorization"),
        ("A valid current Driver’s License and reliable vehicle", "work_authorization"),
    ],
)
def test_a_work_condition_is_a_condition_not_a_technical_gap(quote, topic):
    a = assess(review(req(quote, "not_shown", ())), EVIDENCE)
    assert a.requirements[0]["category"] == "setup" and a.requirements[0]["status"] == "unknown"
    assert a.counts["mandatory"] == 0 and a.central_gaps == ()
    assert {"quote": quote, "topic": topic} in [dict(c) for c in a.conditions]


@pytest.mark.parametrize(
    "quote",
    [
        "Ability to troubleshoot APIs and webhooks",
        "Ability to work with SQL and relational databases",
        "Experience operating production systems with on-call rotations",
        "Experience working with Salesforce",
        "Ability to configure integrations between SaaS tools",
        "Experience designing systems for high availability and failover",
        "4+ years of experience in travel & expense or accounts payable",
        "Experience in luxury travel or travel consulting",
        "Experience with Okta authentication/authorization.",
        "Exceptional communicator who earns executive sponsorship for change.",
        "Ability to define non-functional requirements including availability.",
        "2\u20135 years B2B sales (remote/outbound preferred).",
        "Experience working across globally distributed teams spanning multiple time zones.",
        "Commercial driver's license is required to operate commercial size vehicles.",
        "Valid Class A Commercial Driver’s License (CDL)",
        "Ran recurring enablement programs such as workshops and office hours",
        "Manages events end to end and on-site execution. Comfort with travel is required.",
        "Bachelor's degree, ability to travel 20-30% and 5+ years of experience",
    ],
)
def test_the_work_itself_is_not_a_condition(quote):
    assert category(quote) != "setup"


def test_a_condition_takes_no_technical_credit_and_leaves_no_gap():
    with_condition = assess(
        review(
            req("Hands-on experience building integrations in Python", ids=("E9",)),
            req("Ability to work onsite in Winter Park five days per week.", "not_shown", ()),
        ),
        EVIDENCE,
    )
    alone = assess(
        review(req("Hands-on experience building integrations in Python", ids=("E9",))), EVIDENCE
    )
    assert with_condition.recommendation == alone.recommendation == "STRONG"
    assert with_condition.counts == alone.counts
