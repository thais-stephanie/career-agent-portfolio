"""The seniority reading: what establishes a level, and what must not.

Half of this file is negative. That is deliberate and it is the design: the
detector answers through POSITIVE role-level anchors, so every trap below fails
to match rather than being caught by a rule that names it. If any of them ever
starts classifying, the fix is a narrower anchor, never a longer list of things
to ignore.

The traps are not invented. `lead routing` is the defect this module replaced,
measured on 94 postings in the real corpus; `Account Manager` is the shape that
makes "manager means leadership" wrong; `Middle East` and `principal component
analysis` are grade words owned by another meaning entirely.
"""

from __future__ import annotations

import pytest

from career_agent.domain.enums import AnalysisConfidence, Seniority, SenioritySource
from career_agent.match.seniority import read_seniority, required_years

# =========================================================================
# 1. THE TITLE STATES A LEVEL
# =========================================================================


@pytest.mark.parametrize(
    ("title", "level"),
    [
        ("Senior Business Systems Analyst", Seniority.SENIOR),
        ("Sr. Integration Engineer", Seniority.SENIOR),
        # Above senior, and separately from it since 2026-09-07. Reading these
        # as SENIOR put post-senior architecture roles at the top of a list
        # somebody had asked to fill with senior execution work.
        ("Staff Software Engineer", Seniority.STAFF),
        ("Principal Solutions Architect", Seniority.PRINCIPAL),
        ("Distinguished Engineer", Seniority.PRINCIPAL),
        ("Analista de Sistemas Sênior", Seniority.SENIOR),
        ("Lead Data Analyst", Seniority.LEAD),
        ("Lead Systems Engineer", Seniority.LEAD),
        ("Analista Pleno de Dados", Seniority.MID),
        ("Mid-Level Backend Engineer", Seniority.MID),
        ("Junior Data Analyst", Seniority.JUNIOR),
        ("Jr. Operations Analyst", Seniority.JUNIOR),
        ("Analista Júnior de Integrações", Seniority.JUNIOR),
        ("Graduate Software Engineer", Seniority.JUNIOR),
        ("Data Science Intern", Seniority.INTERN),
        ("Estagiário de Dados", Seniority.INTERN),
        ("Programa de Estágio em Tecnologia", Seniority.INTERN),
    ],
)
def test_an_explicit_grade_in_the_title_is_read(title: str, level: Seniority) -> None:
    reading = read_seniority(title, "You will own our internal tools.")
    assert reading.value is level
    assert reading.source is SenioritySource.TITLE_GRADE
    assert reading.confidence is AnalysisConfidence.HIGH


def test_the_highest_grade_in_a_title_wins_rather_than_the_first() -> None:
    """`Senior Staff Engineer` is one role, not an argument between two rungs.

    The rule did not change; what it resolves to did. `TITLE_GRADES` is
    ordered by precedence and STAFF now sits above SENIOR, so a title naming
    both still means the higher one -- and the higher one is now a value that
    can be told apart from senior.
    """
    assert read_seniority("Senior Staff Engineer", "").value is Seniority.STAFF
    assert read_seniority("Staff Senior Engineer", "").value is Seniority.STAFF
    assert read_seniority("Senior Principal Engineer", "").value is Seniority.PRINCIPAL


def test_staff_is_not_lead_and_lead_is_not_staff() -> None:
    """Two ways of being past senior, and they are not the same way.

    A staff engineer is an individual contributor with post-senior scope. A
    lead is an organisational position. Collapsing either into the other would
    put people management back into a scale that deliberately does not carry
    it -- see `Seniority` and ADR-0014.
    """
    assert read_seniority("Staff Engineer", "").value is Seniority.STAFF
    assert read_seniority("Tech Lead", "").value is Seniority.LEAD
    assert read_seniority("Engineering Manager", "").value is Seniority.LEAD


def test_a_staff_statement_in_the_body_reads_above_senior_too() -> None:
    """The title is not the only place a grade is stated, and the body's
    precedence has to match the title's or one posting reads two ways."""
    reading = read_seniority("Operations Wizard", "We are hiring a Staff Engineer.")
    assert reading.value is Seniority.STAFF
    assert reading.source is SenioritySource.DESCRIPTION_STATEMENT


@pytest.mark.parametrize(
    "title",
    [
        "Business Systems Analyst",
        "Integration Specialist",
        "Especialista em Automação",
        "Solutions Architect",
        "Salesforce Administrator",
        "Software Developer",
        "Consultor de Sistemas",
        # Explicitly refused by the product direction: these are ranks in some
        # industries and job families in others, and neither reading is safe.
        "Assistant Product Analyst",
        "Associate Systems Engineer",
        "Assistente de Operações",
    ],
)
def test_a_neutral_professional_title_establishes_no_grade(title: str) -> None:
    reading = read_seniority(title, "You will own our internal tools.")
    assert reading.source is SenioritySource.DEFAULT


# =========================================================================
# 2. LEADERSHIP, AND THE MANAGER PROBLEM
# =========================================================================


@pytest.mark.parametrize(
    "title",
    [
        "Head of Business Systems",
        "Director of Engineering",
        "Diretora de Tecnologia",
        "VP of Revenue Operations",
        "Vice President, Data",
        "Chief Technology Officer",
        "Engineering Manager",
        "Manager of Business Systems",
        "Sales Team Manager",
        "Gerente de Engenharia",
        "Tech Lead, Platform",
        "Líder Técnico de Integrações",
    ],
)
def test_an_organisational_title_reads_as_lead(title: str) -> None:
    reading = read_seniority(title, "You will own our internal tools.")
    assert reading.value is Seniority.LEAD
    assert reading.source is SenioritySource.TITLE_LEADERSHIP
    assert reading.evidence is not None


@pytest.mark.parametrize(
    "title",
    [
        "Account Manager",
        "Customer Success Manager",
        "Product Manager",
        "Project Manager",
        "Program Manager",
        "Partner Manager",
        "Vendor Manager",
        "Community Manager",
        "Brand Manager",
        "Gerente de Contas",
        "Gerente de Produto",
    ],
)
def test_a_job_family_ending_in_manager_is_not_leadership(title: str) -> None:
    """The single most common way to get this wrong.

    Every one of these is an individual contributor in most companies. Reading
    `manager` as leadership here would tell somebody a job runs a team when it
    runs a book of business.
    """
    reading = read_seniority(title, "You will own our internal tools.")
    assert reading.value is not Seniority.LEAD
    assert reading.source is SenioritySource.DEFAULT


def test_seniority_and_people_management_are_separate_dimensions() -> None:
    """The lock the product direction asked for by name.

    `Lead Data Analyst` is LEAD and manages nobody. Nothing in this module
    writes people management, which lives in the lexicon and is read from the
    body; the two answers are produced by different code over different text.
    """
    reading = read_seniority("Lead Data Analyst", "You will build dashboards for the team.")
    assert reading.value is Seniority.LEAD
    assert not hasattr(reading, "people_management")


@pytest.mark.parametrize(
    "title",
    [
        "Demand Generation Manager",
        "Lead Generation Specialist",
        "Enterprise Account Executive (Middle East & Africa)",
        "Member of Technical Staff",
        "Staff Augmentation Consultant",
    ],
)
def test_a_grade_word_owned_by_another_meaning_is_masked(title: str) -> None:
    reading = read_seniority(title, "You will own our internal tools.")
    assert reading.source is SenioritySource.DEFAULT


def test_a_masked_phrase_does_not_swallow_a_real_grade_beside_it() -> None:
    """Masking replaces a phrase with spaces of the same width, so the words
    around it keep their boundaries and their offsets."""
    reading = read_seniority("Senior Demand Generation Manager", "")
    assert reading.value is Seniority.SENIOR
    assert reading.source is SenioritySource.TITLE_GRADE


# =========================================================================
# 3. THE DESCRIPTION, THROUGH ROLE-LEVEL STATEMENTS ONLY
# =========================================================================


@pytest.mark.parametrize(
    ("body", "level"),
    [
        ("This is a senior individual contributor role.", Seniority.SENIOR),
        ("We are looking for a senior engineer to own this.", Seniority.SENIOR),
        ("This is a mid-level position.", Seniority.MID),
        ("We are looking for a Lead Engineer.", Seniority.LEAD),
        ("This is a lead role on the platform team.", Seniority.LEAD),
        ("An entry-level opportunity for recent graduates.", Seniority.JUNIOR),
        ("A summer internship in our São Paulo office.", Seniority.INTERN),
        ("Vaga sênior para o time de dados.", Seniority.SENIOR),
        ("Posição pleno na equipe de integrações.", Seniority.MID),
        ("Vaga júnior para quem está começando.", Seniority.JUNIOR),
    ],
)
def test_a_role_level_statement_in_the_body_is_read(body: str, level: Seniority) -> None:
    reading = read_seniority("Operations Wizard", body)
    assert reading.value is level
    assert reading.source is SenioritySource.DESCRIPTION_STATEMENT
    assert reading.evidence is not None


@pytest.mark.parametrize(
    "body",
    [
        # The measured defect, and its whole family.
        "You will own lead routing between marketing and sales.",
        "You will build our lead generation engine.",
        "Own lead scoring and lead qualification in HubSpot.",
        "You will enrich sales leads before they reach an AE.",
        "Keep the lead data clean across both systems.",
        # Verbs. A person who leads something is not a person at the lead grade.
        "You will lead the implementation across three teams.",
        "You will lead a project end to end.",
        "You will lead the workstream and report weekly.",
        "You will lead the team of five engineers.",
        # Other people's levels.
        "You will collaborate with senior management.",
        "You will work with senior leadership on the roadmap.",
        "You will support senior managers across the business.",
        "Mentor junior colleagues across the org.",
        "We care deeply about our staff and our culture.",
        "You will report to the Head of Product.",
        # A grade word inside a term of art.
        "Experience with principal component analysis is welcome.",
        # A grade word about something that is not the role.
        "We expect a senior level of commitment.",
    ],
)
def test_incidental_prose_never_establishes_a_grade(body: str) -> None:
    reading = read_seniority("Operations Wizard", body)
    assert reading.source is SenioritySource.DEFAULT, reading


def test_the_title_outranks_the_body() -> None:
    """Precedence is by evidence quality, never by position in the text.

    The old reader took the earliest word anywhere, so a `Senior Engineer`
    posting mentioning `junior colleagues` in its second sentence read JUNIOR.
    """
    reading = read_seniority(
        "Senior Systems Engineer", "You will mentor junior colleagues. This is a lead role."
    )
    assert reading.value is Seniority.SENIOR
    assert reading.source is SenioritySource.TITLE_GRADE


# =========================================================================
# 4. REQUIRED YEARS
# =========================================================================


@pytest.mark.parametrize(
    ("body", "level"),
    [
        ("We require at least 1 year of experience.", Seniority.JUNIOR),
        ("Minimum of 2 years of experience required.", Seniority.JUNIOR),
        ("Required: at least 3 years of operations work.", Seniority.MID),
        ("You must have 5 years of experience.", Seniority.MID),
        ("Required: 6+ years of experience.", Seniority.SENIOR),
        ("No mínimo 8 anos de experiência é obrigatório.", Seniority.SENIOR),
        ("No prior experience is required.", Seniority.JUNIOR),
    ],
)
def test_an_unambiguous_required_minimum_bands(body: str, level: Seniority) -> None:
    reading = read_seniority("Analyst", body)
    assert reading.value is level
    assert reading.source is SenioritySource.REQUIRED_YEARS
    assert reading.confidence is AnalysisConfidence.MEDIUM
    assert reading.evidence is not None


def test_years_never_reach_lead_at_any_figure() -> None:
    """A decade of experience says how long somebody worked. It says nothing
    about whether the job runs a team."""
    for count in (6, 10, 15, 20):
        reading = read_seniority("Analyst", f"Required: at least {count} years of experience.")
        assert reading.value is Seniority.SENIOR


@pytest.mark.parametrize(
    "body",
    [
        # A wish, not a requirement.
        "Preferably 8+ years of experience.",
        "Nice to have: 10 years of experience.",
        "5+ years of experience is a plus.",
        "Desejável 6 anos de experiência.",
        # No cue at all. This is 42.6% of the real corpus.
        "What we're looking for: - 5+ years of experience using data.",
        "You bring 6+ years of operations work.",
        # Contradicting cues about one figure.
        "Required or preferred: 4 years of experience.",
    ],
)
def test_a_figure_that_is_not_a_stated_minimum_is_not_used(body: str) -> None:
    assert read_seniority("Analyst", body).source is SenioritySource.DEFAULT


def test_two_different_required_minima_resolve_to_nothing() -> None:
    """Which minimum is THE minimum is a judgement about which skill matters
    most, and 610 postings in the real corpus pose it."""
    body = (
        "Requirements: at least 3 years of experience with Python. "
        "You must have 5 years of experience with distributed systems."
    )
    assert required_years(body) is None
    assert read_seniority("Analyst", body).source is SenioritySource.DEFAULT


# =========================================================================
# 5. THE DEFAULT IS NOT EVIDENCE
# =========================================================================


def test_the_default_says_mid_without_claiming_the_posting_did() -> None:
    reading = read_seniority("Analyst", "You will own our internal tools.")
    assert reading.value is Seniority.MID
    assert reading.source is SenioritySource.DEFAULT
    assert reading.confidence is AnalysisConfidence.LOW
    assert reading.evidence is None
    assert reading.is_evidence is False


def test_the_default_sentence_never_asserts_the_level() -> None:
    """The wording is the product decision. "This is a mid-level role" about a
    posting that said nothing is the whole defect in one sentence."""
    default = read_seniority("Analyst", "You will own our internal tools.")
    assert (
        default.sentence == "The posting does not state a level. Search Fit treats it as mid-level."
    )

    stated = read_seniority("Analyst", "This is a mid-level position.")
    assert stated.sentence == "The posting states this is a mid-level role."


def test_every_reading_quotes_text_the_posting_actually_contains() -> None:
    """ADR-0002 holds here too: evidence is a substring that exists."""
    cases = [
        ("Senior Systems Engineer", "You will own tools."),
        ("Analyst", "This is a mid-level position."),
        ("Analyst", "Required: at least 7 years of experience."),
        ("Head of Data", "You will own tools."),
    ]
    for title, body in cases:
        reading = read_seniority(title, body)
        assert reading.evidence is not None, (title, body)
        assert reading.evidence in title or reading.evidence in body, (title, body)


def test_an_empty_posting_is_the_default_rather_than_an_error() -> None:
    reading = read_seniority("", "")
    assert reading.source is SenioritySource.DEFAULT
    assert reading.value is Seniority.MID


# =========================================================================
# a configuration written before a level existed
# =========================================================================


def test_a_level_the_configuration_does_not_price_scores_zero_rather_than_raising() -> None:
    """The crash that adding STAFF would otherwise have caused.

    `_seniority_component` used to subscript `points` directly, so the first
    real Staff posting to reach the scorer against a configuration written
    before 2026-09-07 would have raised `KeyError`. Refusing to LOAD such a
    file was the other option and it is worse: it makes every future
    vocabulary change a breaking change to somebody's private configuration.

    Zero is a reading rather than a shrug -- `points` is the list of levels
    this search asked for, and a level absent from it was not asked for.
    """
    from career_agent.config.search_config import SeniorityComponent

    component = SeniorityComponent(
        max=10,
        label="Seniority alignment",
        points={Seniority.MID: 9, Seniority.SENIOR: 10},
        unevidenced=0,
    )

    assert component.points_for(Seniority.SENIOR) == 10
    assert component.points_for(Seniority.STAFF) == 0
    assert set(component.unpriced_levels()) == {
        Seniority.INTERN,
        Seniority.JUNIOR,
        Seniority.STAFF,
        Seniority.PRINCIPAL,
        Seniority.LEAD,
    }


def test_the_shipped_configuration_prices_every_level() -> None:
    """The example is what somebody copies. A level missing from it would
    silently score zero for every reader who never noticed."""
    from pathlib import Path

    from career_agent.config.search_config import load_search_config

    root = Path(__file__).resolve().parents[2]
    import shutil
    import tempfile

    directory = Path(tempfile.mkdtemp())
    shutil.copy(root / "config" / "search.worked-example.yaml", directory)
    shutil.copy(root / "config" / "places.yaml", directory)
    config, _ = load_search_config(directory, use_example=True)

    assert config.scoring.components.seniority.unpriced_levels() == ()


def test_every_level_in_the_vocabulary_can_be_said_out_loud() -> None:
    """The 500 this file's sibling served, and the guard against the next one.

    Adding STAFF and PRINCIPAL to `Seniority` left `LEVEL_WORDS` behind, and
    the first real Principal posting to reach a card took the whole jobs list
    down with a `KeyError` -- on the personal corpus, because the demo has no
    such posting and every test passed.

    A vocabulary is allowed to grow. A display table falling behind it must
    degrade to the value's own name, and this is what notices when one has.
    """
    from career_agent.domain.enums import AnalysisConfidence, SenioritySource
    from career_agent.domain.matching import LEVEL_WORDS, SeniorityReading

    for level in Seniority:
        assert level in LEVEL_WORDS, f"{level.value} has no word a person reads"
        reading = SeniorityReading(
            value=level,
            source=SenioritySource.TITLE_GRADE,
            confidence=AnalysisConfidence.HIGH,
            evidence="x",
        )
        # Must not raise, and must name the level.
        assert LEVEL_WORDS[level] in reading.sentence
