"""End to end: real postings, the committed configuration, one `MatchResult`.

The fixtures below are written as job descriptions rather than as pattern
soup, because the behaviours worth protecting only appear in a whole posting.
"GTM Engineer" is the standing example: the same title, the same rules, and two
descriptions that must land on opposite sides of the taxonomy.

Everything here also holds `match_job` to being a pure function. It takes
`computed_at` as an argument precisely so that it consults no clock, and the
purity test is what stops that promise decaying into a comment.
"""

from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.domain.enums import (
    EligibilityStatus,
    GateResult,
    ScreeningState,
    Seniority,
    SenioritySource,
)
from career_agent.domain.matching import MatchResult, TitleAdjustment, TitleClass
from career_agent.match import JobFacts, match_job

# The COMMITTED configuration, never `config/` itself.
#
# `config/` resolves `search.local.yaml` when one exists, so reaching for it
# here means this module tests the owner's private search on her machine and
# the shipped worked example on everybody else's -- two different suites
# wearing one name, and the second one is what a fresh clone runs.
# `committed_config_dir` is the copy with every local override removed.
CONFIG, _ = load_search_config(committed_config_dir())

COMPUTED_AT = "2026-09-04T00:00:00Z"


#: The same search with no answer about ways of working.
CONFIG_NO_WORK_MODEL = CONFIG.model_copy(
    update={
        "preferences": CONFIG.preferences.model_copy(
            update={
                "remote": CONFIG.preferences.remote.model_copy(
                    update={
                        "accepted_work_models": [],
                        "avoided_work_models": [],
                        "excluded_work_models": [],
                    }
                )
            }
        )
    }
)


def run_without_work_model(title: str, description: str, **facts: object) -> MatchResult:
    return match_job(
        CONFIG_NO_WORK_MODEL,
        JobFacts(title=title, description=description, **facts),  # type: ignore[arg-type]
        computed_at=COMPUTED_AT,
    )


def run(title: str, description: str, **facts: object) -> MatchResult:
    return match_job(
        CONFIG,
        JobFacts(title=title, description=description, **facts),  # type: ignore[arg-type]
        computed_at=COMPUTED_AT,
    )


def gate(result: MatchResult, name: str):
    return next(g for g in result.gates if g.gate == name)


# =========================================================================
# FIXTURE POSTINGS
# =========================================================================

GTM_SYSTEMS = """About the role
You will own the systems the revenue team operates in.

Responsibilities
- Own the CRM architecture and the CRM data model end to end
- Design workflow automation across every GTM system we run
- Build API integration work using REST API endpoints and webhooks
- Keep data quality honest across the revenue stack

Requirements
- 5+ years of experience owning internal tools for operations teams
"""

GTM_SALES = """About the role
This is a quota-carrying seat on our outbound team.

Responsibilities
- Cold calling and cold outreach into net-new accounts every day
- Carry a sales quota and close deals month after month
- Outbound prospecting into your named territory

Requirements
- 3 years of sales development experience
"""

FORWARD_DEPLOYED = """About the role
You sit beside customers and make the platform fit how they work.

Responsibilities
- Own customer implementation and customer onboarding projects
- Deliver solution configuration and solution design per account
- Build API integration work using REST API endpoints and webhooks
- Design workflow automation that removes manual steps

Requirements
- 5+ years of implementation experience
"""

FORWARD_DEPLOYED_WITH_TRAVEL = (
    FORWARD_DEPLOYED + "\nLogistics\nYou will travel up to 50% to customer sites.\n"
)

SHARED_SERVICES_INTERNAL = """About the role
Our shared services group runs the platforms the rest of the company works in.

Responsibilities
- Build and own internal tools and the internal platform
- Deliver system integration between our business systems
- Own API integration work with REST API endpoints
- Replace manual steps with workflow automation

Requirements
- 4 years in a similar internal platform role
"""

SHARED_SERVICES_BACKEND = """About the role
We run the company's core product platform.

Responsibilities
- Design distributed systems and a microservices architecture
- Operate kubernetes clusters running production services at scale
- Ship product features on our core product codebase

Requirements
- A computer science degree
"""

MODEST_SYSTEMS = """About the role
We keep the back office running.

Responsibilities
- Own business process design across the company
- Keep documentation and monitoring in order

Requirements
- 4 years of experience in an operations team
"""

MODEST_SYSTEMS_WITH_TOOL = MODEST_SYSTEMS + "- Comfortable orchestrating flows in n8n\n"

PORTUGUESE = """Sobre a vaga
Buscamos alguem para cuidar dos nossos sistemas internos.

Responsabilidades
- Integração de sistemas e automação de processos
- Documentação e monitoramento dos fluxos

Requisitos
- 5 anos de experiência
"""

RICH = GTM_SYSTEMS + "\n" + "We hire globally and you can work from anywhere in the world. " * 12


# =========================================================================
# 1. A DESCRIPTION-ONLY KEYWORD MOVES THE SCORE
# =========================================================================


def test_a_preferred_keyword_only_in_the_description_raises_the_score() -> None:
    """The title is identical. The only difference is one tool named in the body,
    which is the whole point of scoring the description rather than the title."""
    without = run("Business Systems Analyst", MODEST_SYSTEMS)
    with_tool = run("Business Systems Analyst", MODEST_SYSTEMS_WITH_TOOL)

    assert with_tool.match_score > without.match_score
    technologies = next(c for c in with_tool.components if c.component_id == "technologies")
    assert "ipaas" in {c.signal_id for c in technologies.contributions}


# =========================================================================
# 2. A GOOD TITLE DOES NOT OVERRIDE A GATE
# =========================================================================


def test_a_primary_title_with_a_us_only_statement_is_not_eligible() -> None:
    """Fit and eligibility are separate measurements (ADR-0004). A perfect
    role-family score cannot argue with a sentence saying where they hire."""
    description = GTM_SYSTEMS + "\nThis role is open to US residents only.\n"
    result = run("Business Systems Engineer", description)

    assert result.title is not None and result.title.resolved_class is TitleClass.PRIMARY
    assert result.eligibility_status is EligibilityStatus.VERIFIED_NOT_ELIGIBLE
    assert result.match_score > 0


# =========================================================================
# 3. AMBIGUOUS TITLES
# =========================================================================


def test_a_gtm_engineer_doing_systems_work_resolves_to_primary() -> None:
    result = run("GTM Engineer", GTM_SYSTEMS)

    assert result.title is not None
    assert result.title.resolved_class is TitleClass.PRIMARY
    assert result.title.adjustment is TitleAdjustment.PROMOTED


def test_a_gtm_engineer_doing_outbound_sales_resolves_to_excluded_and_scores_far_lower() -> None:
    systems = run("GTM Engineer", GTM_SYSTEMS)
    sales = run("GTM Engineer", GTM_SALES)

    assert sales.title is not None
    assert sales.title.resolved_class is TitleClass.EXCLUDED
    assert sales.title.adjustment is TitleAdjustment.DEMOTED
    assert systems.match_score - sales.match_score >= 40


def test_a_forward_deployed_ai_engineer_doing_integration_work_stays_eligible_and_promotes() -> (
    None
):
    result = run("Forward Deployed AI Engineer", FORWARD_DEPLOYED)

    assert result.title is not None
    assert result.title.base_class is TitleClass.STRONG_ADJACENT
    assert result.title.resolved_class is TitleClass.PRIMARY
    assert result.eligibility_status is not EligibilityStatus.VERIFIED_NOT_ELIGIBLE


def test_a_forward_deployed_engineer_with_heavy_travel_fails_the_travel_gate() -> None:
    result = run("Forward Deployed Engineer", FORWARD_DEPLOYED_WITH_TRAVEL)
    travel = gate(result, "travel")

    assert travel.result is GateResult.FAIL
    assert travel.quote in FORWARD_DEPLOYED_WITH_TRAVEL


def test_integrations_developer_is_primary_via_the_integration_engineer_rule() -> None:
    """The noun "Developer" does not make it product engineering."""
    result = run("Integrations Developer", SHARED_SERVICES_INTERNAL)

    assert result.title is not None
    assert result.title.rule_id == "integration_engineer"
    assert result.title.resolved_class is TitleClass.PRIMARY


def test_shared_services_promotes_on_internal_platform_work_and_demotes_on_backend_work() -> None:
    """Same title, opposite readings. "Shared services" is an internal business
    platform at one company and a distributed backend at another."""
    internal = run("Software Engineer, Shared Services", SHARED_SERVICES_INTERNAL)
    backend = run("Software Engineer, Shared Services", SHARED_SERVICES_BACKEND)

    assert internal.title is not None and backend.title is not None
    assert internal.title.base_class is TitleClass.CONDITIONAL
    assert internal.title.resolved_class is TitleClass.STRONG_ADJACENT
    assert internal.title.adjustment is TitleAdjustment.PROMOTED

    assert backend.title.resolved_class is TitleClass.EXCLUDED
    assert backend.title.adjustment is TitleAdjustment.DEMOTED


def test_a_salesforce_administrator_is_named_but_not_priced() -> None:
    """SUPERSEDES `test_a_salesforce_administrator_is_excluded_and_takes_the_penalty`.

    The taxonomy still recognises the role and still says so on the card. What
    it no longer does is subtract 15 points for the WORDS IN THE TITLE:
    measured, `Salesforce Administrator` scored 30 where the identical body
    under a plain title scored 48, which is a title deciding fit as surely as a
    bonus for one would be.
    """
    result = run("Salesforce Administrator", GTM_SYSTEMS)
    assert result.title is not None
    assert result.title.resolved_class is TitleClass.EXCLUDED
    assert "salesforce_centred" not in {p.signal_id for p in result.penalties}


def test_the_same_body_scores_the_same_under_a_salesforce_title() -> None:
    """The title is not a fit input, and that includes the negative direction."""
    plain = run("Operations Wizard", GTM_SYSTEMS)
    branded = run("Salesforce Administrator", GTM_SYSTEMS)
    assert plain.match_score == branded.match_score


def test_no_cold_calling_required_produces_a_negated_hit_and_no_penalty() -> None:
    """The good sentence and the bad sentence contain the same phrase."""
    absent = run("GTM Engineer", GTM_SYSTEMS + "\nThere is no cold calling required here.\n")
    present = run("GTM Engineer", GTM_SYSTEMS + "\nCold calling is required in this role.\n")

    negated = next(s for s in absent.signals if s.signal_id == "cold_outbound")
    assert negated.fired is False
    assert len(negated.negated_hits) == 1
    assert "cold_outbound" not in {p.signal_id for p in absent.penalties}

    assert "cold_outbound" in {p.signal_id for p in present.penalties}


def test_an_accented_portuguese_posting_fires_the_same_signals() -> None:
    result = run("Analista de Sistemas", PORTUGUESE)
    fired = {s.signal_id for s in result.signals if s.fired}

    assert "system_integration" in fired
    assert "workflow_automation" in fired
    assert result.screening_state is ScreeningState.NOT_BLOCKED


# =========================================================================
# 5. EVIDENCE, PURITY AND THE THREE SEPARATE MEASUREMENTS
# =========================================================================


def test_every_evidence_quote_is_a_substring_of_the_posting() -> None:
    """ADR-0002 applies to the deterministic matcher exactly as it applies to
    the model: a quote is a quote that exists. Checked across every fixture, so
    one posting whose sentences happen to be short cannot carry the assertion."""
    postings = (
        ("GTM Engineer", GTM_SYSTEMS),
        ("GTM Engineer", GTM_SALES),
        ("Forward Deployed AI Engineer", FORWARD_DEPLOYED_WITH_TRAVEL),
        ("Software Engineer, Shared Services", SHARED_SERVICES_INTERNAL),
        ("Software Engineer, Shared Services", SHARED_SERVICES_BACKEND),
        ("Salesforce Administrator", MODEST_SYSTEMS_WITH_TOOL),
        ("Analista de Sistemas", PORTUGUESE),
        ("Business Systems Engineer", RICH + "\nCandidates must be located in the US.\n"),
    )
    # role_family and seniority quote their own reasoning; compensation quotes
    # nothing, because a preference is not an observation about the text.
    derived = {"role_family", "seniority", "compensation_contract", "work_model"}
    checked = 0

    for title, description in postings:
        result = run(title, description)

        def is_quoted(quote: str, title: str = title, description: str = description) -> bool:
            text = quote[:-1] if quote.endswith("…") else quote
            return text in title or text in description

        for signal in result.signals:
            for hit in signal.hits + signal.negated_hits:
                assert is_quoted(hit.quote), hit
                checked += 1
        for outcome in result.gates:
            if outcome.quote:
                assert is_quoted(outcome.quote), outcome
                checked += 1
        for component in result.components:
            if component.component_id in derived:
                continue
            for contribution in component.contributions:
                assert contribution.quote is not None
                assert is_quoted(contribution.quote), contribution
                checked += 1

    assert checked >= 90, "the fixtures stopped producing evidence to verify"


def test_match_job_is_pure() -> None:
    """No clock, no I/O, no randomness. Two calls, one answer."""
    job = JobFacts(
        title="GTM Engineer",
        description=GTM_SYSTEMS,
        location_raw="Remote - Brazil",
        employment_type="FULL_TIME_EMPLOYEE",
        posted_at="2026-09-01",
    )
    assert match_job(CONFIG, job, computed_at=COMPUTED_AT) == match_job(
        CONFIG, job, computed_at=COMPUTED_AT
    )


def test_confidence_reads_the_metadata_and_the_score_does_not() -> None:
    """Two measurements, never multiplied. Adding a location and a date tells us
    more about the posting without telling us more about the work."""
    # Without a way-of-working preference. With one, the posting's stated way
    # of working is exactly what she asked to be scored on; that is pinned in
    # `test_work_model_preference.py`.
    bare = run_without_work_model("GTM Engineer", GTM_SYSTEMS)
    with_metadata = run_without_work_model(
        "GTM Engineer", GTM_SYSTEMS, location_raw="Remote - Brazil", posted_at="2026-09-01"
    )

    assert with_metadata.match_score == bare.match_score
    assert with_metadata.data_confidence > bare.data_confidence

    thin = run("GTM Engineer", "GTM Engineer wanted. Own the CRM data model.")
    rich = run(
        "GTM Engineer",
        RICH,
        location_raw="Remote - Worldwide",
        employment_type="FULL_TIME_EMPLOYEE",
        salary_min=9000,
        salary_currency="USD",
        salary_period="MONTH",
        posted_at="2026-09-01",
    )
    assert rich.data_confidence - thin.data_confidence >= 40
    assert thin.unknowns


def test_screening_and_eligibility_are_answered_separately() -> None:
    """A posting can fail screening while its eligibility is wide open, and the
    reverse. Collapsing them would hide one behind the other (ADR-0004)."""
    off_target = run("Account Executive", GTM_SALES)

    assert off_target.screening_state is ScreeningState.BLOCKED
    assert "core_work" in off_target.screening_reason
    assert off_target.eligibility_status is EligibilityStatus.UNRESOLVED


def test_the_result_carries_its_configuration_provenance() -> None:
    result = run("GTM Engineer", GTM_SYSTEMS)

    assert result.config_id == CONFIG.config_id
    assert result.config_version == CONFIG.config_version
    assert result.computed_at == COMPUTED_AT


# =========================================================================
# THE TITLE BUYS NO COMPATIBILITY
#
# The approved correction, and the one thing about it that must never quietly
# come back. Every assertion below holds the DESCRIPTION fixed and changes only
# the title, so a difference in score can only have come from the name.
# =========================================================================

#: Two titles this search feels very differently about, and one it has never
#: heard of. Under the old scoring these spanned 25 points of `role_family`
#: before the body was read at all.
TITLES_THE_SEARCH_LOVES = ("Business Systems Analyst", "Integration Engineer")
TITLES_THE_SEARCH_REJECTS = ("Backend Engineer", "Salesforce Administrator")
TITLES_THE_SEARCH_HAS_NEVER_SEEN = ("Operations Wizard", "Business Applications Builder")


def test_a_title_the_search_wants_buys_no_points_over_one_it_has_never_seen() -> None:
    baseline = run(TITLES_THE_SEARCH_HAS_NEVER_SEEN[0], GTM_SYSTEMS).match_score
    for title in TITLES_THE_SEARCH_LOVES:
        assert run(title, GTM_SYSTEMS).match_score == baseline, title


def test_a_title_the_search_rejects_costs_no_points_either() -> None:
    """The correction is symmetric, and the negative half was the larger one:
    `Salesforce Administrator` used to lose 15 points for its name alone."""
    baseline = run(TITLES_THE_SEARCH_HAS_NEVER_SEEN[0], GTM_SYSTEMS).match_score
    for title in TITLES_THE_SEARCH_REJECTS:
        assert run(title, GTM_SYSTEMS).match_score == baseline, title


def test_an_unrelated_body_is_not_rescued_by_a_title_the_search_wants() -> None:
    """The measurement that started this. A distribution-centre posting titled
    `Business Systems Analyst` scored 39 and read MODERATE; the identical body
    under `Operations Wizard` scored 14."""
    warehouse = (
        "You will supervise the loading bay, coordinate pallet movements with "
        "the forklift team, and keep the cold storage logs up to date. You will "
        "walk the floor daily and count returned crates against the manifests."
    )
    plain = run("Operations Wizard", warehouse)
    dressed = run("Business Systems Analyst", warehouse)
    assert dressed.match_score == plain.match_score
    assert dressed.fit_band is plain.fit_band


def test_only_seniority_may_move_when_the_title_changes() -> None:
    """The one dimension a title is still allowed to decide, and the proof that
    it is decided SEPARATELY: the score does not follow it."""
    plain = run("Business Systems Analyst", GTM_SYSTEMS)
    senior = run("Senior Business Systems Analyst", GTM_SYSTEMS)

    assert senior.seniority.value is Seniority.SENIOR
    assert senior.seniority.source is SenioritySource.TITLE_GRADE
    # This body states a required minimum, so the plain title falls through to
    # the years band rather than to DEFAULT. Either way it is not the title.
    assert plain.seniority.source is SenioritySource.REQUIRED_YEARS

    # Seniority is a scored dimension, so the totals may differ -- but only by
    # the seniority component, and never by the title's resemblance to a
    # preferred one.
    def _without_seniority(result):
        return sum(c.points for c in result.components if c.component_id != "seniority")

    assert _without_seniority(plain) == _without_seniority(senior)


def test_the_taxonomy_still_names_the_role_it_no_longer_prices() -> None:
    """Removing the points is not removing the parsing. The class still reaches
    the card, the screening channel and the stored row."""
    result = run("Business Systems Analyst", GTM_SYSTEMS)
    assert result.title is not None
    assert result.title.resolved_class is TitleClass.PRIMARY
    assert "role_family" not in {c.component_id for c in result.components}
