"""A scope phrase only opens the geography gate when the sentence says so.

The geography gate is the only gate that can declare somebody ELIGIBLE, which
makes it the only place where a phrase found in prose GRANTS something rather
than withholding it. It granted a great deal: 598 of 1,030 `VERIFIED_ELIGIBLE`
postings in the real corpus were made eligible by a scope word in a sentence
that was not about hiring, and 129 of those postings were located in the United
States.

The sentences below are real, taken from the corpus, and each one used to make
a posting eligible for a candidate living in Brazil.
"""

from __future__ import annotations

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.domain.enums import EligibilityStatus, GateResult
from career_agent.match.gates import HIRING_INTENT, eligibility_status_from, evaluate_gates
from career_agent.match.lexicon import observe
from career_agent.match.text import split_sections

# The COMMITTED configuration, never `config/` itself.
#
# `config/` resolves `search.local.yaml` when one exists, so reaching for it
# here means this module tests the owner's private search on her machine and
# the shipped worked example on everybody else's -- two different suites
# wearing one name, and the second one is what a fresh clone runs.
# `committed_config_dir` is the copy with every local override removed.
CONFIG, _ = load_search_config(committed_config_dir())


def gates_for(title: str, description: str):
    """Through the real loader and the real lexicon, as the product runs it."""
    sections = split_sections(
        description,
        CONFIG.prominence.primary_headings,
        CONFIG.prominence.secondary_headings,
    )
    observed = observe(CONFIG, title, description)
    return evaluate_gates(CONFIG, observed, title, description, sections)


#: Sentences that DO state where a job hires.
STATES_A_SCOPE = [
    "This position is available for candidates based in EMEA, LATAM or North America.",
    "You are located in LATAM or the US and can comfortably work within US time zones.",
    "Latin America and North America time zones welcome. We hire across the Americas.",
    "We are a fully remote team and hire from anywhere in the world.",
    "Open to applicants residing in Brazil.",
    "Vaga remota para candidatos no Brasil.",
    "Contratacao para profissionais da America Latina.",
    "Buscamos pessoas residentes no Brasil para trabalho remoto.",
]

#: Sentences that do NOT, every one of them from a posting that used to be
#: eligible because of it.
STATES_NOTHING = [
    # Figma's product marketing.
    "From idea to product, Figma empowers teams to streamline workflows, move "
    "faster, and work together in real time from anywhere in the world",
    # A list of privacy statutes, in a Remote-USA privacy counsel posting.
    ", LGPD (Brazil), PIPEDA (Canada), APPI (Japan)",
    # A sales territory, in a San Francisco role.
    "Adyen is seeking a Manager, Rewards for the Americas Region "
    "(Brazil, Canada, Mexico and the United States)",
    # Where the company operates, in a Boulder, Colorado role.
    ", South America, and Australia",
    # The team's remit, in a Mexico City role.
    "LATAM regional leadership - become a critical member of the LATAM regional leadership team",
]


@pytest.mark.parametrize("sentence", STATES_A_SCOPE)
def test_a_sentence_about_hiring_is_recognised(sentence: str) -> None:
    assert HIRING_INTENT.search(sentence.lower()) is not None


@pytest.mark.parametrize("sentence", STATES_NOTHING)
def test_a_sentence_about_anything_else_is_not(sentence: str) -> None:
    assert HIRING_INTENT.search(sentence.lower()) is None


def test_portuguese_is_not_an_afterthought() -> None:
    """An English-only anchor fails hardest in the market this product serves.

    Half the Brazilian postings this system will ever read are written in
    Portuguese, and an anchor that recognised none of them would have made
    every one UNRESOLVED while every English posting resolved normally. The
    demo corpus caught exactly that, on `demo-012`.
    """
    assert HIRING_INTENT.search("vaga remota para candidatos no brasil") is not None
    assert HIRING_INTENT.search("contratacao clt para residentes no brasil") is not None


def test_a_scope_word_in_marketing_copy_does_not_make_a_posting_eligible() -> None:
    """End to end through the real gate, on the real Figma sentence.

    The location is the one the board printed. Before the anchor this posting
    was VERIFIED_ELIGIBLE for a candidate in Brazil.
    """
    description = (
        "About Figma. From idea to product, Figma empowers teams to streamline "
        "workflows, move faster, and work together in real time from anywhere in "
        "the world.\n\nWhat you will do:\n- Lead a product area.\n"
    )
    gates = gates_for("Manager, Product Management", description)
    geography = next(g for g in gates if g.gate == "geography")
    assert geography.result is GateResult.UNRESOLVED
    assert eligibility_status_from(gates) is EligibilityStatus.UNRESOLVED


def test_a_real_hiring_sentence_still_makes_a_posting_eligible() -> None:
    """The other half, which matters just as much.

    An anchor that let nothing through would be no better than the bug: it
    would trade false eligibility for a status column that never resolves.
    """
    description = (
        "About us. We build payment infrastructure.\n\n"
        "This position is available for candidates based in EMEA, LATAM or "
        "North America.\n"
    )
    gates = gates_for("Solutions Engineer", description)
    geography = next(g for g in gates if g.gate == "geography")
    assert geography.result is GateResult.PASS
    assert eligibility_status_from(gates) is EligibilityStatus.VERIFIED_ELIGIBLE


def test_a_region_in_the_title_names_a_market_not_a_hiring_scope() -> None:
    """`Commercial Account Executive - LATAM`, based in Denver, was eligible.

    A region in a title says which market the role sells into. The accepted
    invariant is already explicit that a title may name a role and inform
    retrieval but may never establish compatibility, and eligibility is
    compatibility -- so the title is not consulted here at all.
    """
    gates = gates_for(
        "Commercial Account Executive - LATAM (Portuguese Speaking)",
        "You will sell to mid-market accounts. Denver-based hybrid role.",
    )
    geography = next(g for g in gates if g.gate == "geography")
    assert geography.result is GateResult.UNRESOLVED


def test_an_unanchored_phrase_leaves_the_posting_visible() -> None:
    """Losing a false PASS must never become a false FAIL.

    UNRESOLVED is what silence has always meant for hiring scope, and an
    UNRESOLVED posting is never hidden. The candidate sees it and decides.
    """
    gates = gates_for("Data Engineer", "We serve customers across South America.")
    assert eligibility_status_from(gates) is not EligibilityStatus.VERIFIED_NOT_ELIGIBLE
    assert eligibility_status_from(gates) is EligibilityStatus.UNRESOLVED


# -- the twelve that survived the workation fix ------------------------------
#
# Audited on the real corpus, 2026-09-09. Of the postings that resolved to the
# United States and nowhere else and still came out VERIFIED_ELIGIBLE for a
# candidate in Brazil, twelve mentioned a region for a reason that was not
# hiring. Every quote below is that posting's own, and every one of them clears
# `HIRING_INTENT` -- which is exactly why a third question was needed.


def test_a_sales_territory_is_not_a_place_you_may_live() -> None:
    """Three postings at one employer, all eligible on this sentence.

    It says plainly that the person must be based in one of four United States
    cities. `LATAM` is the territory the partnership covers. The same
    conflation already cost this product `Commercial Account Executive -
    LATAM`, based in Denver, which is why the TITLE was removed from this gate
    -- and a title is not the only place a market name appears.
    """
    from career_agent.match.gates import MARKET_SCOPE

    quote = (
        "Based in New York, Chicago, Austin, or San Francisco - Reports to "
        "SVP, Partnerships - Scope: AMER + LATAM"
    )
    assert MARKET_SCOPE.search(quote) is not None


def test_where_your_colleagues_sit_is_not_an_offer_to_employ_you_there() -> None:
    """`based in` belongs in `HIRING_INTENT` -- "this role is based in Sao
    Paulo" is the sentence the gate exists for. Here its SUBJECT is the teams.
    """
    from career_agent.match.gates import OTHER_PEOPLE

    quote = (
        "You will work closely with cross-functional teams across the "
        "organization, based in San Francisco, New York, Seattle, Vancouver, "
        "Brazil, and Argentina"
    )
    assert OTHER_PEOPLE.search(quote) is not None


def test_a_privacy_notice_is_not_a_hiring_statement() -> None:
    """The V1.2 pass caught this shape once already, as a list of privacy
    statutes on a Remote-USA role. This one clears `HIRING_INTENT` because a
    privacy notice for CANDIDATES genuinely is about candidates. What it is not
    about is where anybody may be employed."""
    from career_agent.match.gates import POLICY_DOCUMENT

    quote = (
        "For information on our data privacy policies, see Privacy, "
        "CA Candidate Privacy, and Brazil Transparency Report"
    )
    assert POLICY_DOCUMENT.search(quote) is not None


def test_a_region_you_must_know_about_is_not_a_region_you_may_live_in() -> None:
    """Somebody in Boston can be required to know Brazilian payroll law.
    Being required to know it is not permission to live there."""
    from career_agent.match.gates import A_DUTY

    for quote in (
        "Knowledge of regional tax, employment, and travel regulations (especially LATAM)",
        "In this role, you will support the payrolls either directly through Workday and ADP",
        "Partner with senior leaders to understand their hiring objectives",
    ):
        assert A_DUTY.search(quote) is not None, quote


def test_a_real_hiring_statement_survives_every_one_of_them() -> None:
    """THE DIRECTION THAT MATTERS MORE, and the reason each negation is written
    to require its own subject rather than a bare keyword.

    The risk is not symmetric. A false PASS shows a candidate a job she cannot
    take and wastes an afternoon. A false FAIL hides one she can and costs her
    the job. Every sentence here is from the corpus or from the demo, and every
    one of them must still open the gate.
    """
    from career_agent.match.gates import NOT_A_HIRING_STATEMENT

    for quote in (
        "You are located in LATAM or the US and can comfortably work within US time zones",
        "This remote position is based in Brazil",
        "We hire anywhere in Latin America",
        "Open to candidates residing in Brazil",
        "Vaga remota para candidatos no Brasil",
        "This role is based in Sao Paulo",
        "Contratamos em todo o Brasil",
        "Remote, anywhere in the Americas",
        # The one most at risk from OTHER_PEOPLE: the candidate IS placed, and
        # the colleagues are mentioned afterwards.
        "You will be based in Brazil, working with teams in London",
        # AND THE BOUNDARY V1.2 DREW, which an earlier draft of OTHER_PEOPLE
        # broke and `test_a_sentence_about_where_the_team_works_still_opens_
        # the_gate` caught. The reader is inside the "we" and the sentence is
        # about how the work happens; the version that refused it required only
        # a collective noun near a locator, and this one requires the collective
        # to be an object the reader works WITH.
        "We work remotely across the US, Europe, LatAm",
    ):
        blocked = [p for p in NOT_A_HIRING_STATEMENT if p.search(quote)]
        assert not blocked, f"{quote!r} was refused by {blocked}"


def test_each_negation_is_its_own_pattern_with_its_own_posting() -> None:
    """They are four constants rather than one alternation on purpose: each is
    a different objection with a different posting behind it, and a regex
    nobody can attribute to a real defect is one nobody can safely change."""
    from career_agent.match.gates import NOT_A_HIRING_STATEMENT

    assert len(NOT_A_HIRING_STATEMENT) == 4
