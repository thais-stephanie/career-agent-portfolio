"""Components, penalties and confidence -- and the things that must never happen.

A missing salary must not cost a job anything beyond the two points the
configuration prices silence at, and a salary in a currency nobody wrote a rate
for must not be converted at a guessed one. Salesforce mentioned among other
tools must score positively; Salesforce in the title must not. Confidence must
report the items it did NOT award, because an unknown that leaves no row reads
as an absence of problems.
"""

from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.domain.enums import (
    AnalysisConfidence,
    EmploymentRelationship,
    FitBand,
    Seniority,
    SenioritySource,
)
from career_agent.domain.matching import DEFAULT_SENIORITY
from career_agent.match.employment import read_employment
from career_agent.match.engine import JobFacts
from career_agent.match.lexicon import body_only, observe
from career_agent.match.score import (
    LENGTH_CONFIDENCE_ITEM,
    MEASURABLE_CONFIDENCE_ITEMS,
    confidence_band_for,
    data_confidence,
    fit_band_for,
    match_score_from,
    score_components,
    soft_penalties,
)
from career_agent.match.seniority import read_seniority
from career_agent.storage.mvp_repo import SALARY_CONFIDENCE_ITEM

#: The committed worked example, never the real `config/`: that directory
#: resolves `search.local.yaml` when one exists, so a test reading it passed
#: on the owner's machine against her private search and failed on a fresh
#: clone against the neutral starter. `committed_config_dir` is the copy with
#: every local override removed and the worked example in its place.
CONFIG, _ = load_search_config(committed_config_dir())

TOOLS_BODY = """Responsibilities
- Own the business systems the company runs on
- Keep Stripe, Airtable and Salesforce talking to each other

Requirements
- 5 years of experience
"""


def _scored(title: str, description: str, **facts):
    """Exactly what the engine does, including the body-only view.

    `body_only` is not an optimisation to skip in a test: it is the rule that
    the title buys no compatibility, and a helper that scored the union would
    quietly test a product that does not exist.
    """
    job = JobFacts(title=title, description=description, **facts)
    observed = body_only(CONFIG, observe(CONFIG, title, description))
    components = score_components(
        CONFIG,
        observed_body=observed,
        seniority=read_seniority(title, description),
        # READ, never handed in. A helper that accepted an engagement as a
        # keyword would let a test assert against a value no pipeline can
        # produce -- which is exactly how the contract preference stayed broken
        # for the whole life of this configuration while its test was green.
        employment=read_employment(
            description,
            declared_relationship=job.declared_relationship,
            declared_regime=job.declared_contract_regime,
        ),
        job_facts=job,
    )
    return components, soft_penalties(CONFIG, observed)


def _component(components, component_id):
    return next(c for c in components if c.component_id == component_id)


# --- seniority is a reading, and it says where it came from -----------------


def test_seniority_reads_an_explicit_grade_from_the_title() -> None:
    """SUPERSEDES `test_the_title_alone_never_sets_seniority`.

    That test asserted the title could never set seniority, on the reasoning
    that "Senior" is a recruiting adjective. It was protecting the right thing
    at the wrong altitude: what a title must not buy is FIT, and it no longer
    does. What a title states rather well is the LEVEL of the role it names,
    and refusing to read it threw away the best evidence available.
    """
    reading = read_seniority("Senior Automation Engineer", "You will own our internal tools.")
    assert reading.value is Seniority.SENIOR
    assert reading.source is SenioritySource.TITLE_GRADE
    assert reading.evidence is not None
    assert reading.evidence in "Senior Automation Engineer"


def test_seniority_reads_a_role_level_statement_from_the_body() -> None:
    reading = read_seniority("Operations Wizard", "This is a senior individual contributor role.")
    assert reading.value is Seniority.SENIOR
    assert reading.source is SenioritySource.DESCRIPTION_STATEMENT


def test_seniority_falls_back_to_an_unambiguous_required_minimum() -> None:
    """SUPERSEDES `test_read_seniority_falls_back_to_years_of_experience`.

    The old test asserted a band off ANY years figure it could find, so
    "preferably 8+ years" and "you bring 6+ years" were the same input. Only a
    figure marked as REQUIRED bands now, and the bands themselves moved: 3-5 is
    mid rather than 3+, and nothing reaches LEAD at any number.
    """
    senior = read_seniority("Analyst", "Required: at least 6 years of operations work.")
    assert (senior.value, senior.source) == (Seniority.SENIOR, SenioritySource.REQUIRED_YEARS)

    mid = read_seniority("Analyst", "Minimum of 3 years of operations work is required.")
    assert mid.value is Seniority.MID

    junior = read_seniority("Analyst", "We require at least 2 years of operations work.")
    assert junior.value is Seniority.JUNIOR


def test_a_preferred_years_figure_is_never_a_minimum() -> None:
    reading = read_seniority("Analyst", "Preferably 8+ years of operations work.")
    assert reading.source is SenioritySource.DEFAULT


def test_seniority_defaults_without_claiming_the_posting_said_so() -> None:
    """SUPERSEDES `test_read_seniority_is_unclear_when_the_body_says_nothing`.

    `UNCLEAR` was a value standing in for the absence of one, and the scorer
    paid it five points. The default is MID now -- an operational choice, not a
    reading -- and everything that matters hangs off the SOURCE.
    """
    reading = read_seniority("Analyst", "You will own our internal tools.")
    assert reading.value is Seniority.MID
    assert reading.source is SenioritySource.DEFAULT
    assert reading.is_evidence is False
    assert reading.evidence is None
    assert "does not state a level" in reading.sentence


def test_a_defaulted_seniority_earns_no_points() -> None:
    """Absence is never permission, in the dimension it is easiest to miss."""
    components, _ = _scored("Analyst", "You will own our internal tools.")
    component = _component(components, "seniority")
    assert component.points == CONFIG.scoring.components.seniority.unevidenced
    assert component.points == 0


def test_an_evidenced_mid_outscores_a_defaulted_one() -> None:
    stated, _ = _scored("Analyst", "This is a mid-level position. You own internal tools.")
    silent, _ = _scored("Analyst", "You own internal tools.")
    assert _component(stated, "seniority").points > _component(silent, "seniority").points


# --- the budget ------------------------------------------------------------


def test_the_component_budget_is_positive_and_the_score_is_a_percentage_of_it() -> None:
    """SUPERSEDES `test_component_maxima_sum_to_exactly_one_hundred`.

    The budget was pinned at 100 because the score was a bare sum of points.
    Removing `role_family` took 25 off the top; the score is a percentage of
    whatever the components declare now, so the invariant that matters is that
    there is a budget to be a percentage OF.
    """
    assert sum(CONFIG.scoring.components.maxima.values()) > 0
    assert "role_family" not in CONFIG.scoring.components.maxima


def test_no_component_ever_exceeds_its_configured_maximum() -> None:
    """The saturating posting: every weighted signal the configuration prices,
    all at PRIMARY prominence. Each component must cap rather than overflow."""
    everything = "Responsibilities\n" + "\n".join(
        f"- {signal.patterns[0]}" for signal in CONFIG.lexicon.values() if signal.patterns
    )
    components, _ = _scored("Business Systems Engineer", everything)

    for component in components:
        assert component.points <= component.max_points, component.component_id
    assert _component(components, "responsibilities").capped is True


# --- compensation is a preference ------------------------------------------


def test_a_posting_with_no_salary_scores_salary_unknown_and_is_not_rejected() -> None:
    components, _ = _scored("Business Systems Analyst", TOOLS_BODY)
    compensation = _component(components, "compensation_contract")

    assert compensation.points > 0
    assert [c.signal_id for c in compensation.contributions] == [
        "salary_unknown",
        "contract_unknown",
    ]


def test_a_salary_in_an_unpriced_currency_is_unknown_not_converted() -> None:
    """`conversion_rates` is empty in the shipped config on purpose. Converting
    at a rate nobody wrote down would manufacture a comparison, and a rate that
    exists without a date is no better -- see
    `tests/unit/test_compensation_hints.py` for both halves."""
    components, _ = _scored(
        "Business Systems Analyst",
        TOOLS_BODY,
        salary_min=20000,
        salary_currency="BRL",
        salary_period="MONTH",
    )
    compensation = _component(components, "compensation_contract")

    assert compensation.contributions[0].signal_id == "salary_unknown"
    assert compensation.note is not None and "currencies were not compared" in compensation.note


def test_a_declared_engagement_scores_the_contract_preference() -> None:
    """THE TEST THAT USED TO PASS WITHOUT THE PRODUCT WORKING.

    Its previous form handed `JobFacts.employment_type` the string
    `FULL_TIME_EMPLOYEE`, took `contract_preferred` and called it proved. But
    `employment_type` is filled from a provider's structured field through
    `employment_type_from`, whose whole vocabulary is `FULL_TIME` /
    `PART_TIME` / `CONTRACT` / `TEMPORARY` / `INTERNSHIP` / `VOLUNTEER`. No
    pipeline has ever produced `FULL_TIME_EMPLOYEE`, or could. The fixture knew
    more than its source did -- the same defect the demo corpus was fixed for
    in V1.4 -- and the comparison it proved was one that never ran.

    Measured 2026-09-09: the intersection of what the configuration asks for
    and what the scorer could answer was EMPTY, so `contract_preferred` had
    never fired on a single posting in 60,000.

    The fixture now supplies what a BOARD supplies: the contract type an
    employer picked, which is where a real posting's engagement comes from.
    """
    components, _ = _scored(
        "Business Systems Analyst",
        TOOLS_BODY,
        declared_relationship=EmploymentRelationship.EMPLOYEE.value,
    )
    compensation = _component(components, "compensation_contract")
    assert compensation.contributions[1].signal_id == "contract_preferred"


def test_the_engagement_may_also_come_from_the_employers_own_words() -> None:
    """No board field, and the posting says it in a sentence. Both routes end
    at the same preference, which is the point of scoring the READING rather
    than one provider's spelling of it."""
    components, _ = _scored(
        "Analista de Sistemas",
        "Contratacao PJ para atuar no time de dados.\n\n" + TOOLS_BODY,
    )
    compensation = _component(components, "compensation_contract")
    contract = compensation.contributions[1]
    assert contract.signal_id == "contract_preferred"
    assert contract.quote is not None, "a preference earned on prose must show the prose"


def test_a_posting_that_says_nothing_about_engagement_scores_unknown() -> None:
    components, _ = _scored("Business Systems Analyst", TOOLS_BODY)
    compensation = _component(components, "compensation_contract")
    assert compensation.contributions[1].signal_id == "contract_unknown"


def test_a_preference_nobody_can_answer_says_so_on_the_card() -> None:
    """The failure mode that hid this for a year: an unreadable preference
    scored every posting `unknown` and printed nothing about why."""
    contract = CONFIG.preferences.contract.model_copy(update={"preferred": ["WIZARD"]})
    preferences = CONFIG.preferences.model_copy(update={"contract": contract})
    config = CONFIG.model_copy(update={"preferences": preferences})
    job = JobFacts(title="Analyst", description=TOOLS_BODY)
    components = score_components(
        config,
        observed_body=body_only(config, observe(config, job.title, job.description)),
        seniority=read_seniority(job.title, job.description),
        employment=read_employment(job.description),
        job_facts=job,
    )
    compensation = _component(components, "compensation_contract")
    assert compensation.note is not None
    assert "WIZARD" in compensation.note


# --- Salesforce ------------------------------------------------------------


def test_salesforce_among_other_tools_is_not_penalised() -> None:
    """It scores positively through `tool_stack`. The penalty is about a role
    built around Salesforce, which is a prominence judgement, not a word count."""
    components, penalties = _scored("Business Systems Analyst", TOOLS_BODY)

    assert "salesforce_centred" not in {p.signal_id for p in penalties}
    technologies = _component(components, "technologies")
    assert "tool_stack" in {c.signal_id for c in technologies.contributions}


# --- penalties are proportional --------------------------------------------


def test_a_penalty_scales_with_prominence() -> None:
    primary = _scored("Analyst", "Responsibilities\n- Manage a team of eight\n")[1]
    incidental = _scored("Analyst", "You may occasionally manage a team of interns.")[1]

    primary_points = next(p.points for p in primary if p.signal_id == "people_management")
    incidental_points = next(p.points for p in incidental if p.signal_id == "people_management")
    assert primary_points > incidental_points


def test_the_total_floors_at_zero_rather_than_going_negative() -> None:
    components, penalties = _scored(
        "Account Executive",
        "Responsibilities\n- Cold calling all day\n- Carry a sales quota and close deals\n",
    )
    assert match_score_from(components, penalties) == 0


# --- confidence ------------------------------------------------------------


def test_confidence_reports_every_configured_item_including_the_unawarded() -> None:
    value, items, unknowns = data_confidence(
        CONFIG, JobFacts(title="Analyst", description="Short."), DEFAULT_SENIORITY
    )

    assert {item.item_id for item in items} == set(CONFIG.confidence.components)
    assert all(item.note for item in items)
    assert len(unknowns) == len([i for i in items if not i.awarded])
    assert 0 <= value <= 100


def test_every_item_the_matcher_measures_is_an_item_the_configuration_declares() -> None:
    """The check that was missing, and the reason the test above cannot be it.

    Item ids live in two independent places: the key in `search.worked-example.yaml`
    and the key in the matcher's own mapping. Nothing compared them. A key
    misspelled on the Python side made `awarded_by_item.get(item_id)` return
    None, which produces `awarded=False` and the note "<label> is not measurable
    by the deterministic matcher" -- forever, on every posting, with the item
    still reported so nothing looked missing.

    The assertion above is tautological against exactly that bug: it builds its
    expectation FROM the configuration, so it passes with any spelling on the
    Python side. Renaming `seniority_determinable` to `seniority_known` in
    `score.py` broke no test in the suite. This one fails, because
    `MEASURABLE_CONFIDENCE_ITEMS` is the matcher's mapping's own key set,
    derived from the function that builds it.
    """
    configured = set(CONFIG.confidence.components)

    unknown_to_the_config = MEASURABLE_CONFIDENCE_ITEMS - configured
    assert not unknown_to_the_config, (
        f"score.py measures {sorted(unknown_to_the_config)}, which no configured "
        "confidence item asks for. Either the id is misspelled here or the "
        "configuration dropped the item; both are silent today."
    )

    unmeasurable = configured - MEASURABLE_CONFIDENCE_ITEMS - {LENGTH_CONFIDENCE_ITEM}
    assert not unmeasurable, (
        f"the configuration declares {sorted(unmeasurable)}, which the matcher "
        "cannot measure, so every posting will report it un-awarded with a "
        "'not measurable' note. Add the measurement or remove the item."
    )


def test_the_salary_confidence_item_the_query_filters_on_is_a_real_item() -> None:
    """`has_salary=true` filters on the awarded tag for this exact id.

    It is a third independent spelling of the same string -- storage has its
    own constant -- and a rename in either of the other two places would leave
    the filter matching nothing at all, quietly.
    """
    assert SALARY_CONFIDENCE_ITEM in CONFIG.confidence.components
    assert SALARY_CONFIDENCE_ITEM in MEASURABLE_CONFIDENCE_ITEMS


def test_the_length_item_is_the_only_one_measured_outside_the_mapping() -> None:
    """A guard on the guard. `description_substantial` is special-cased in the
    loop because its rule is a NUMBER in the configuration rather than the
    presence of a field; if it ever stops being special-cased, the subset
    assertion above would start passing for the wrong reason."""
    assert LENGTH_CONFIDENCE_ITEM in CONFIG.confidence.components
    assert LENGTH_CONFIDENCE_ITEM not in MEASURABLE_CONFIDENCE_ITEMS


def test_a_substantial_description_earns_its_points() -> None:
    long_enough = "Own the CRM data model. " * 60
    thin, _, _ = data_confidence(
        CONFIG, JobFacts(title="Analyst", description="Short."), DEFAULT_SENIORITY
    )
    thick, _, _ = data_confidence(
        CONFIG, JobFacts(title="Analyst", description=long_enough), DEFAULT_SENIORITY
    )

    assert thick > thin


# --- bands -----------------------------------------------------------------


def test_fit_bands_follow_the_configured_thresholds() -> None:
    assert fit_band_for(75, CONFIG) is FitBand.STRONG
    assert fit_band_for(55, CONFIG) is FitBand.GOOD
    assert fit_band_for(35, CONFIG) is FitBand.MODERATE
    assert fit_band_for(34, CONFIG) is FitBand.WEAK


def test_confidence_bands_follow_the_configured_thresholds() -> None:
    assert confidence_band_for(70, CONFIG) is AnalysisConfidence.HIGH
    assert confidence_band_for(45, CONFIG) is AnalysisConfidence.MEDIUM
    assert confidence_band_for(44, CONFIG) is AnalysisConfidence.LOW


# --- the note is read by a person, so it may not be a list of identifiers ---


def test_the_missing_signal_note_names_signals_the_way_the_configuration_does() -> None:
    """It used to read: "The posting never mentioned crm_architecture,
    gtm_systems_work, custom_objects."

    Three of this system's own identifiers, in a sentence somebody reads in
    the drawer to find out why a job scored what it did. `observed` carries
    an entry for EVERY signal in the lexicon, fired or not, each with the
    label the configuration gave it, so the label was always one lookup away.
    """
    components, _ = _scored("Business Systems Engineer", TOOLS_BODY)

    notes = [c.note for c in components if c.note]
    assert notes, "no component reported a missing signal, so this proves nothing"

    for note in notes:
        assert "_" not in note, f"a raw identifier reached the note: {note!r}"
        assert note.startswith("No configured body phrase was recognized for ")
        assert "never mentioned" not in note
        assert note.endswith(".")

    # And the labels are the configuration's own, not a prettified id.
    labels = {signal.label for signal in observe(CONFIG, "x", "y").values()}
    named = " ".join(notes)
    assert any(label in named for label in labels), (
        f"none of the configured labels appears in {notes}"
    )


def test_a_weighted_signal_missing_from_the_lexicon_still_names_itself() -> None:
    """The fallback, and why it is the id rather than a blank.

    A signal weighted in `scoring` and absent from `lexicon` is a broken
    configuration. Printing its id is how somebody finds which line to fix;
    hiding it behind "something" would make the file silently wrong.
    """
    from career_agent.match.score import _weighted_component

    component = CONFIG.scoring.components.responsibilities
    observed = observe(CONFIG, "Business Systems Engineer", TOOLS_BODY)
    stripped = {key: value for key, value in observed.items() if key not in component.weights}

    result = _weighted_component("responsibilities", component, stripped)
    assert result.note is not None
    assert any(signal_id in result.note for signal_id in component.weights)
