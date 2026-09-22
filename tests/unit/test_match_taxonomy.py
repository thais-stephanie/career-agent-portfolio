"""Title classification, and the two orderings that decide what it means.

Excluded is consulted before everything else, so a title that reads as
off-target cannot be rescued by also containing a primary rule's words.
Demotion is evaluated before promotion, so a description whose responsibilities
section is cold calling wins over two passing mentions of an API.

Neither ordering is arbitrary and neither is documented anywhere but here and
in the module itself, which is why they get named tests rather than a passing
assertion inside a bigger one.
"""

from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.domain.matching import TitleAdjustment, TitleClass
from career_agent.match.lexicon import observe
from career_agent.match.taxonomy import classify_title

#: The committed worked example, never the real `config/`: that directory
#: resolves `search.local.yaml` when one exists, so a test reading it passed
#: on the owner's machine against her private search and failed on a fresh
#: clone against the neutral starter. `committed_config_dir` is the copy with
#: every local override removed and the worked example in its place.
CONFIG, _ = load_search_config(committed_config_dir())

SYSTEMS_BODY = """Responsibilities
- Own the CRM architecture and the CRM data model
- Design workflow automation across every system we run
- Build API integration work with REST API endpoints and webhooks
"""

SALES_BODY = """Responsibilities
- Cold calling and cold outreach into net-new accounts
- Carry a sales quota and close deals every month
"""


def classify(title: str, description: str = "Nothing much."):
    return classify_title(CONFIG, title, observe(CONFIG, title, description))


# --- rule matching ---------------------------------------------------------


def test_an_unrecognised_title_is_unclassified_not_excluded() -> None:
    """`docs/product/principles.md` forbids depending on a predefined title
    list, so a title no rule knows still scores its description normally."""
    result = classify("Chief Happiness Officer")

    assert result.base_class is TitleClass.UNCLASSIFIED
    assert result.adjustment is TitleAdjustment.NONE
    assert result.reason


def test_excluded_is_consulted_before_primary() -> None:
    """ "Salesforce Automation Engineer" matches `automation_engineer` too. The
    exclusion has to win, or every off-target title with the right noun in it
    would be rescued by a primary rule."""
    result = classify("Salesforce Automation Engineer")

    assert result.resolved_class is TitleClass.EXCLUDED
    assert result.rule_id == "salesforce_specialist"


def test_integrations_developer_is_primary_and_not_excluded() -> None:
    """The word "Developer" does not make it product engineering. Whether it is
    genuinely integration work is settled by the description."""
    result = classify("Integrations Developer", SYSTEMS_BODY)

    assert result.resolved_class is TitleClass.PRIMARY
    assert result.rule_id == "integration_engineer"


def test_a_none_of_guard_keeps_a_management_title_out_of_an_ic_rule() -> None:
    result = classify("Business Systems Manager")
    assert result.rule_id != "business_systems"


# --- ambiguity -------------------------------------------------------------


def test_a_rule_with_no_ambiguity_rule_reports_no_adjustment() -> None:
    result = classify("Business Systems Analyst", SYSTEMS_BODY)

    assert result.adjustment is TitleAdjustment.NONE
    assert result.ambiguity_rule is None


def test_demotion_is_evaluated_before_promotion() -> None:
    """A strong off-target signal beats a weak on-target one. Both branches are
    satisfiable here; the demotion must be the one that fires."""
    both = SALES_BODY + "\nWe also do some workflow automation and API integration work.\n"
    result = classify("GTM Engineer", both)

    assert result.adjustment is TitleAdjustment.DEMOTED
    assert result.resolved_class is TitleClass.EXCLUDED
    assert "cold_outbound" in result.supporting_signals


def test_a_promotion_names_the_signals_that_justified_it() -> None:
    result = classify("GTM Engineer", SYSTEMS_BODY)

    assert result.adjustment is TitleAdjustment.PROMOTED
    assert result.resolved_class is TitleClass.PRIMARY
    assert len(result.supporting_signals) >= 2
    assert result.ambiguity_rule == "gtm_engineer"


def test_a_conditional_title_that_was_not_promoted_stays_conditional() -> None:
    """There is no silent upgrade. The rule looked and found nothing, and that
    is recorded as a different thing from never having looked."""
    result = classify("Solutions Engineer", "We are a fast growing company.")

    assert result.base_class is TitleClass.CONDITIONAL
    assert result.resolved_class is TitleClass.CONDITIONAL
    assert result.adjustment is TitleAdjustment.UNCHANGED_INSUFFICIENT_EVIDENCE


def test_a_single_incidental_mention_does_not_reach_the_promotion_threshold() -> None:
    """`min_prominence: SECONDARY` is what stops one passing word flipping a
    classification."""
    result = classify("GTM Engineer", "One day you might touch a rest api.")

    assert result.adjustment is TitleAdjustment.UNCHANGED_INSUFFICIENT_EVIDENCE
    assert result.resolved_class is TitleClass.PRIMARY
