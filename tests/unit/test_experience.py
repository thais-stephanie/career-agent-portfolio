"""What a posting asks of somebody who is starting out, or moving into a field.

WHY THIS FILE EXISTS
--------------------
The product could say how SENIOR a role reads and could not say whether the
employer had asked for experience at all. Three different sentences produced
the same thing downstream:

    "3+ years of experience required"      a wall
    "3 years of experience preferred"      a wish
    "No previous experience necessary"     an invitation

For somebody entering a profession, returning to work or changing career, the
difference between them is the whole question -- and reading all three as "a
requirement you do not meet" manufactures a refusal out of a sentence that was
the opposite.

WHAT IS ASSERTED HERE, AND WHAT IS NOT
---------------------------------------
The READING only. Whether a person qualifies is not a question this module
answers and no test here implies one: the reading is candidate independent, so
the same posting produces the same reading for every reader (invariant 4).

The hardest cases in this file are the ones that must return NOT_STATED. A
wrong reading is worse than an absent one, because an absent one is visibly
absent -- the same trade `seniority.required_years` makes and for the same
reason.
"""

from __future__ import annotations

import pytest

from career_agent.match.experience import (
    EntrySignal,
    ExperienceRequirement,
    read_experience,
)

# =========================================================================
# 1. THE FIVE READINGS
# =========================================================================


@pytest.mark.parametrize(
    ("description", "expected", "years"),
    [
        # -- a stated minimum -------------------------------------------
        (
            "We require at least 3 years of experience in accounting.",
            ExperienceRequirement.REQUIRED_MINIMUM,
            3,
        ),
        (
            "Minimum 5 years in a clinical setting is required.",
            ExperienceRequirement.REQUIRED_MINIMUM,
            5,
        ),
        ("Exigimos no minimo 2 anos de experiencia.", ExperienceRequirement.REQUIRED_MINIMUM, 2),
        # -- a wish, which is NOT a minimum ------------------------------
        ("3 years of experience preferred.", ExperienceRequirement.PREFERRED, 3),
        ("2 anos de experiencia sao desejaveis.", ExperienceRequirement.PREFERRED, 2),
        # -- a bonus, which is weaker again ------------------------------
        ("2 years in hospitality is a plus.", ExperienceRequirement.NICE_TO_HAVE, 2),
        ("1 ano na area e um diferencial.", ExperienceRequirement.NICE_TO_HAVE, 1),
        # -- asked for, with no figure -----------------------------------
        ("Proven experience in a similar role.", ExperienceRequirement.REQUIRED_UNQUANTIFIED, None),
        (
            "Experiencia comprovada na area juridica.",
            ExperienceRequirement.REQUIRED_UNQUANTIFIED,
            None,
        ),
        # -- the employer saying none is needed ---------------------------
        ("No previous experience is required.", ExperienceRequirement.NONE_REQUIRED, 0),
        ("Sem experiencia previa necessaria.", ExperienceRequirement.NONE_REQUIRED, 0),
        ("Nao exigimos experiencia.", ExperienceRequirement.NONE_REQUIRED, 0),
        # -- silence ------------------------------------------------------
        ("We are hiring a nurse for the night shift.", ExperienceRequirement.NOT_STATED, None),
        ("", ExperienceRequirement.NOT_STATED, None),
    ],
)
def test_the_reading_and_the_figure(
    description: str, expected: ExperienceRequirement, years: int | None
) -> None:
    reading = read_experience(description)
    assert reading.requirement is expected, f"{description!r} -> {reading.requirement}"
    assert reading.min_years == years


# =========================================================================
# 2. THE REFUSALS
# =========================================================================


def test_two_different_minima_are_not_one_minimum() -> None:
    """Which of them is THE minimum is a judgement about which skill matters
    most, and this layer does not make judgements.

    `seniority.required_years` reaches the same answer for the same reason. A
    reading that picked the first, the smallest or the largest would be an
    arithmetic rule standing in for an editorial one.
    """
    text = "At least 5 years of engineering is required. A minimum of 2 years of QA is required."
    assert read_experience(text).requirement is ExperienceRequirement.NOT_STATED


def test_the_same_figure_twice_is_still_one_minimum() -> None:
    """The refusal above is about DISAGREEMENT, not about repetition. A posting
    that states its minimum in the summary and again in the requirements has
    said one thing twice."""
    text = "Requires at least 4 years of experience. Candidates must have 4 years minimum."
    reading = read_experience(text)
    assert reading.requirement is ExperienceRequirement.REQUIRED_MINIMUM
    assert reading.min_years == 4


def test_a_preferred_cue_beats_a_required_cue_on_the_same_figure() -> None:
    """Two contradicting cues about one number is exactly where guessing is
    unaffordable, and the SAFE direction is the weaker claim: reading a wish as
    a wall hides work from the people who most need to see it."""
    text = "3 years of experience is required, though ideally more."
    assert read_experience(text).requirement is not ExperienceRequirement.REQUIRED_MINIMUM


def test_an_explicit_no_beats_a_figure_elsewhere_in_the_posting() -> None:
    """An employer who writes "no previous experience is required" has answered
    the question outright, and a number in a later paragraph does not reopen
    it."""
    text = (
        "No previous experience is required for this role. "
        "Our team has at least 10 years of combined experience supporting new starters."
    )
    reading = read_experience(text)
    assert reading.requirement is ExperienceRequirement.NONE_REQUIRED
    assert reading.min_years == 0


# =========================================================================
# 3. HOW THE MARKET ACTUALLY WRITES IT
# =========================================================================


@pytest.mark.parametrize(
    "description",
    [
        # Measured on the real corpus 2026-09-10. These two are what Brazilian
        # employers actually write, and the first version of this reader matched
        # NEITHER -- it expected the English sentence translated,
        # `sem experiencia previa NECESSARIA`, which nobody writes.
        "Contratamos pessoas sem experiencia profissional.",
        "Estamos buscando pessoas com ou sem experiencia, entao fique tranquilo.",
        "Aceitamos candidatos sem experiencia.",
        "Nao e preciso ter experiencia.",
        "Nao precisa ter experiencia anterior.",
        # And the English forms beside them.
        "Open to candidates with or without experience.",
        "We hire people with no experience.",
    ],
)
def test_the_forms_this_market_writes_are_read_as_an_invitation(description: str) -> None:
    """Not one posting in two samples of two thousand read `NONE_REQUIRED`
    before these anchors existed, in the one language this product exists to
    serve. After them, 75 of 115 postings that mention the phrase do."""
    assert read_experience(description).requirement is ExperienceRequirement.NONE_REQUIRED


def test_an_invitation_beats_a_figure_in_the_same_advert() -> None:
    """The inversion this reading exists to prevent, found in the corpus.

    `Contratamos pessoas sem experiencia profissional mas sera um diferencial
    ter experiencia em vendas` sat in the same advert as a stated minimum, and
    the whole posting read REQUIRED_MINIMUM -- an explicit invitation reported
    as a wall. The explicit-none check runs first for exactly this.
    """
    text = (
        "Contratamos pessoas sem experiencia profissional, mas sera um "
        "diferencial ter experiencia em vendas. Exigimos no minimo 1 ano em atendimento."
    )
    reading = read_experience(text)
    assert reading.requirement is ExperienceRequirement.NONE_REQUIRED
    assert reading.min_years == 0


def test_a_sentence_about_one_tool_is_not_an_invitation() -> None:
    """The opposite error, and the reason every anchor is verb-led.

    `mesmo sem experiencia previa especifica NELA` is about a piece of
    software, not about the job. A bare `sem experiencia` would match it and
    manufacture an invitation nobody extended -- which is the same failure as
    reading a wish as a wall, pointed the other way.
    """
    text = (
        "Disposicao para aprender a ferramenta usada no time, mesmo sem "
        "experiencia previa especifica nela. Exigimos no minimo 3 anos em bancos relacionais."
    )
    reading = read_experience(text)
    assert reading.requirement is ExperienceRequirement.REQUIRED_MINIMUM
    assert reading.min_years == 3


# =========================================================================
# 4. SILENCE IS NOT AN INVITATION
# =========================================================================


def test_a_posting_that_says_nothing_does_not_open_to_beginners() -> None:
    """The whole of invariant 2, pointed at experience instead of at geography.

    A posting that never mentions experience has not invited anybody; it has
    failed to say. Reading silence as an invitation would put thousands of
    senior roles in front of somebody looking for their first job -- the exact
    inversion of what these filters are for.
    """
    reading = read_experience("Join our growing team of specialists in central Lisbon.")
    assert reading.requirement is ExperienceRequirement.NOT_STATED
    assert reading.entry_signals == ()
    assert reading.opens_to_beginners is False


def test_an_explicit_none_required_does_open_to_beginners() -> None:
    reading = read_experience("No previous experience needed. We will teach you.")
    assert reading.opens_to_beginners is True


# =========================================================================
# 4b. AN AGE IS NOT EXPERIENCE
# =========================================================================
#
# The defect these exist for was measured on the corpus on 2026-09-10: 863
# postings stored a required minimum of 18 years, 176 stored 21 and 41 stored 24,
# and almost none of them had mentioned experience at all. An employer stating a
# legal minimum age writes `minimo 18 anos` and `must be 21 years of age`, and
# `minimo` and `must` are the two cues this module reads as a requirement.
#
# It landed hardest on the people these readings exist for. `Ter entre 14 e 22
# anos, conforme estabelecido pela Lei da Aprendizagem` is a Brazilian
# apprenticeship -- the most entry-level category this market has -- and it was
# being reported as a job demanding twenty-two years of experience, in adverts
# that said `Experiencia: Nao e necessario` in the same breath.


@pytest.mark.parametrize(
    "description",
    [
        "Faixa etaria: minimo 18 anos. Escolaridade: ensino medio completo.",
        "Requirements: Must be 21 years of age. Warehouse background a plus.",
        "Candidates must be at least 18 years old and authorised to work.",
        "Ter entre 14 e 22 anos, conforme estabelecido pela Lei da Aprendizagem.",
        "Necessario ter de 17 a 21 anos incompletos.",
        "E necessario ter entre 14 e 24 anos de idade.",
    ],
)
def test_a_minimum_age_is_not_a_minimum_of_experience(description: str) -> None:
    reading = read_experience(description)
    assert reading.requirement is ExperienceRequirement.NOT_STATED
    assert reading.min_years is None


def test_an_age_bullet_does_not_silence_a_real_requirement_elsewhere() -> None:
    """The veto is per FIGURE and clipped to its sentence, not per posting.

    An advert may state a legal minimum age in one bullet and a genuine
    experience requirement in the next, and the first must not erase the second
    -- which the first version of this veto did, because an 80-character reach
    backwards from `5 years` landed inside `18 years of age`.
    """
    reading = read_experience(
        "Must be 18 years of age. We require a minimum of 5 years of experience in sales."
    )
    assert reading.requirement is ExperienceRequirement.REQUIRED_MINIMUM
    assert reading.min_years == 5


def test_the_word_age_inside_an_ordinary_word_vetoes_nothing() -> None:
    """`age`, and the boundary is the whole point.

    `age` sits inside language, manager, package, average, coverage, storage,
    percentage, stage, message and engagement. Without the anchor this veto
    would not remove ages -- it would silence the reading on every advert that
    mentioned a language, and silently, because the result is a legitimate
    NOT_STATED.
    """
    reading = read_experience(
        "We manage a package of languages across our storage coverage. "
        "Minimum 6 years of experience required."
    )
    assert reading.requirement is ExperienceRequirement.REQUIRED_MINIMUM
    assert reading.min_years == 6


def test_the_field_label_form_is_read_as_an_invitation() -> None:
    """`Experiencia: Nao e necessario`, which is how a Brazilian board prints it.

    Every other Portuguese form in this module expects the noun LAST. This one
    puts it first, in a structured field an employer filled in, and it was the
    plainest statement in the market that the reader could not see.
    """
    reading = read_experience("Requisitos: Ter entre 16 e 22 anos; Experiencia: Nao e necessario.")
    assert reading.requirement is ExperienceRequirement.NONE_REQUIRED
    assert reading.min_years == 0
    assert EntrySignal.NO_EXPERIENCE_REQUIRED in reading.entry_signals


# =========================================================================
# 5. THE INVITATIONS
# =========================================================================


@pytest.mark.parametrize(
    ("description", "signal"),
    [
        ("No prior experience required.", EntrySignal.NO_EXPERIENCE_REQUIRED),
        ("This is an entry-level position.", EntrySignal.ENTRY_LEVEL),
        ("Ideal para quem busca o primeiro emprego.", EntrySignal.ENTRY_LEVEL),
        ("Recent graduates welcome.", EntrySignal.RECENT_GRADUATE),
        ("Vaga aberta a recem-formados.", EntrySignal.RECENT_GRADUATE),
        ("Full training is provided.", EntrySignal.TRAINING_PROVIDED),
        ("Oferecemos treinamento completo.", EntrySignal.TRAINING_PROVIDED),
        ("Career changers are welcome to apply.", EntrySignal.CAREER_CHANGERS_WELCOME),
        ("Buscamos pessoas em transicao de carreira.", EntrySignal.CAREER_CHANGERS_WELCOME),
    ],
)
def test_each_invitation_is_read_in_both_languages(description: str, signal: EntrySignal) -> None:
    assert signal in read_experience(description).entry_signals


def test_an_invitation_is_not_inferred_from_a_junior_title_or_a_low_figure() -> None:
    """Every signal is a phrase an employer WROTE.

    A posting asking for one year of experience is asking for a year of
    experience; it has not said it will train anybody and it has not said
    recent graduates are welcome. Inferring either would put words in an
    employer's mouth and would do it in the direction that raises somebody's
    hopes.
    """
    reading = read_experience("Junior Analyst. We require at least 1 year of experience.")
    assert reading.entry_signals == ()
    assert reading.opens_to_beginners is False


def test_the_invitations_survive_a_refused_reading() -> None:
    """Two irreconcilable minima make the REQUIREMENT unreadable. They say
    nothing about the invitations, which are independent observations of
    different sentences, and discarding those with the figure would lose a
    fact the posting plainly stated."""
    text = (
        "Training is provided. At least 5 years of engineering is required "
        "and a minimum of 2 years of QA is required."
    )
    reading = read_experience(text)
    assert reading.requirement is ExperienceRequirement.NOT_STATED
    assert EntrySignal.TRAINING_PROVIDED in reading.entry_signals


# =========================================================================
# 6. THE QUOTE IS EVIDENCE (ADR-0002)
# =========================================================================


@pytest.mark.parametrize(
    "description",
    [
        "We require at least 3 years of experience in accounting.",
        "No previous experience is required for this role.",
        "Proven experience in a similar role is essential.",
        "Two years is preferred, though 2 years is not a hard rule.",
    ],
)
def test_every_quote_is_a_contiguous_substring_of_the_posting(description: str) -> None:
    """ADR-0002: a quote is evidence only while it is a contiguous substring of
    what the employer actually wrote. A composed sentence is not weaker
    evidence -- it is not evidence."""
    reading = read_experience(description)
    assert reading.quote is not None
    assert reading.quote in description


def test_silence_carries_no_quote() -> None:
    """There is nothing to quote, and an empty string would render as a blank
    blockquote under a heading."""
    assert read_experience("Join our team in Lisbon.").quote is None


# =========================================================================
# 7. THE READING IS CANDIDATE INDEPENDENT
# =========================================================================


def test_the_module_reads_nothing_about_a_candidate() -> None:
    """Invariant 4: the same posting must produce the same reading for every
    reader. Asserted structurally, because a reading that started consulting a
    profile would still pass every behavioural test above.
    """
    import ast
    import pathlib

    source = (
        pathlib.Path(__file__).resolve().parents[2]
        / "src"
        / "career_agent"
        / "match"
        / "experience.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    }
    forbidden = {"career_agent.domain.claims", "career_agent.config.search_config"}
    assert not (imported & forbidden), f"{imported & forbidden} would make the reading personal"

    # `read_experience` takes exactly one argument: the description. A second
    # parameter is where a candidate fact would arrive.
    functions = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    args = functions["read_experience"].args
    assert [a.arg for a in args.args] == ["description"]
    assert not args.kwonlyargs
