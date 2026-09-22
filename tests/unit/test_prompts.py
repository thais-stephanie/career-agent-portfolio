"""Prompts are reviewable files, and their versions are cache keys.

Two properties are worth pinning. The first is mechanical: the version names the
code uses must exist on disk, or extraction fails at runtime for a reason a test
could have caught in a second.

The second is the boundary that makes the two-call topology mean anything. The
provider prompt must never acquire instructions about reading a job description,
and the description prompt must never acquire instructions about provider
metadata. Prompts drift by accretion -- someone adds a clarifying sentence, and
a year later the isolation exists only in the architecture document.
"""

import pytest

from career_agent.llm.prompts import (
    DESCRIPTION_PROMPT_VERSION,
    PROVIDER_PROMPT_VERSION,
    PromptNotFound,
    available_prompts,
    load_prompt,
)

DESCRIPTION = load_prompt(DESCRIPTION_PROMPT_VERSION)
PROVIDER = load_prompt(PROVIDER_PROMPT_VERSION)


def test_the_versions_the_code_uses_exist() -> None:
    assert DESCRIPTION_PROMPT_VERSION in available_prompts()
    assert PROVIDER_PROMPT_VERSION in available_prompts()


def test_a_missing_version_fails_loudly_and_says_what_exists() -> None:
    with pytest.raises(PromptNotFound, match="available"):
        load_prompt("description_v99")


# --- the isolation boundary -------------------------------------------------


def test_every_mention_of_the_description_in_the_provider_prompt_is_a_denial() -> None:
    """The provider call is not given the posting text, and the prompt may only
    refer to the description in order to say so.

    The first version of this test forbade the phrase "the job description says"
    outright and immediately failed on the line *"You do not know what the job
    description says"* -- a sentence that is not only allowed but required by
    the test below it. Blunt substring matching cannot see negation, which is
    the identical blindness that removed the similarity tier from the verifier.

    So the check is on the sentence, not the phrase: every sentence referring to
    the posting's *content* must contain a negation. It matches "job
    description" and "posting text" rather than the bare word "description",
    because the prompt legitimately refers to its sibling *description prompt*
    when explaining why the two readings are kept apart -- a mention of a file,
    not a claim of access.
    """
    import re

    sentences = re.split(r"(?<=[.!?])\s+|\n", PROVIDER)
    mentions = [
        s for s in sentences if "job description" in s.lower() or "posting text" in s.lower()
    ]

    assert mentions, "provider_v1 should state that it lacks the description"

    negations = ("not", "never", "no ", "cannot", "n't", "without")
    for sentence in mentions:
        lowered = sentence.lower()
        assert any(word in lowered for word in negations), (
            f"provider_v1 refers to the description without denying access to it: "
            f"{sentence.strip()!r}"
        )


def test_the_provider_prompt_says_so_explicitly() -> None:
    """Stating the absence is load-bearing: without it a model will assume it
    simply was not given the description *this time* and reason about what it
    probably says."""
    assert "not been given the job description" in PROVIDER.lower()


def test_each_prompt_names_only_its_own_evidence_kind() -> None:
    """Cross-source citation is rejected at assembly. It should never get that
    far, because neither prompt describes the other's evidence as available."""
    assert "PROVIDER_FIELD" not in DESCRIPTION
    assert "JOB_DESCRIPTION" not in PROVIDER
    assert "PROVIDER_FIELD" in PROVIDER


def test_both_prompts_carry_the_remote_trap() -> None:
    """The single most expensive error the system could make, and it can arrive
    through either channel: a posting body saying "Remote" and an ATS location
    field saying "Remote" are the same mistake waiting in two places.

    The two prompts express the honest answer differently, and the difference
    is real. The description family reports a *status* -- the posting simply did
    not say where it hires. The provider family reports a *value* -- a location
    field exists, and what it holds names nowhere. Silence and "a field that
    says nothing" are not the same fact.
    """
    for name, text in (("description", DESCRIPTION), ("provider", PROVIDER)):
        assert "remote" in text.lower(), f"{name} prompt does not address 'remote'"

    assert "NOT_STATED" in DESCRIPTION
    assert "UNSTATED" in PROVIDER


def test_the_description_prompt_separates_work_model_from_hiring_scope() -> None:
    """One of the reasons this product exists.

    Remote describes the desk. Hiring scope describes the employment contract.
    A prompt that blurs them produces a confident wrong answer on the highest
    stakes dimension in the system, and no downstream check can recover the
    distinction once it has been lost.
    """
    assert "WORK MODEL" in DESCRIPTION
    assert "HIRING SCOPE" in DESCRIPTION

    lowered = DESCRIPTION.lower()
    assert "work from anywhere" in lowered, "the trap phrase that looks geographic"
    assert "hiring-geography evidence" in lowered or "hiring geography" in lowered


# --- the rules the assembler will enforce anyway -----------------------------


def test_the_description_prompt_states_the_completeness_requirement() -> None:
    """The compact transport turned dimensions into rows, which means a
    forgotten dimension no longer looks different from a stated NOT_STATED.
    The assembler rejects an incomplete family; the prompt has to ask for
    completeness or every extraction fails its first attempt."""
    lowered = DESCRIPTION.lower()

    assert "every dimension" in lowered
    assert "not_stated" in lowered


def test_the_description_prompt_forbids_tidying_a_quote() -> None:
    """Verification is exact containment, so a helpfully corrected typo costs
    the observation its EXPLICIT status. The model has to be told, because
    tidying a quote is otherwise the obviously helpful thing to do."""
    lowered = DESCRIPTION.lower()

    assert "character-for-character" in lowered
    assert "typos" in lowered


def test_the_description_prompt_lists_the_permitted_not_applicable_cases() -> None:
    """Default-deny only works if the four permitted cases are stated. A model
    told 'almost never' without a list will invent its own."""
    for dimension in (
        "relocation_support",
        "travel_expenses_covered",
        "business_visa_support",
        "worksite_requirement",
    ):
        assert dimension in DESCRIPTION


def test_the_description_prompt_teaches_centrality_by_example() -> None:
    """Software centrality is the hardest field and the one with the lowest
    accuracy target. The same tool across all five levels is the clearest way
    to show that centrality is about the role, not the word count."""
    for level in ("CORE", "REQUIRED", "PREFERRED", "MENTIONED", "ALTERNATIVE"):
        assert level in DESCRIPTION
    assert DESCRIPTION.lower().count("hubspot") >= 5


def test_the_description_prompt_keeps_worksite_and_hiring_scope_apart() -> None:
    """A required office is not a hiring restriction.

    An employer may hire internationally and require relocation, and postings
    that say exactly that exist. The prompt has to teach the distinction,
    because no downstream check can recover it once the model has collapsed the
    two into one answer.
    """
    assert "WHERE MAY THE EMPLOYER HIRE?" in DESCRIPTION
    assert "WHERE MUST THE PERSON WORK?" in DESCRIPTION
    assert "worksite_requirement" in DESCRIPTION

    lowered = DESCRIPTION.lower()
    assert "a named office is not a hiring restriction" in lowered
    # The Berlin row: an office abroad must not become a country restriction.
    assert "does **not** mean germany-only hiring" in lowered


# --- the prompt must not carry the benchmark's answer key -------------------

#: A quoted illustration short enough to be a connective (`e.g.`, `such as`)
#: rather than an example. Below this, a collision with a posting says nothing.
_ILLUSTRATION_MIN_WORDS = 4


def _illustrations(prompt: str) -> list[str]:
    """Every *"quoted"* worked example in a prompt, whitespace-normalised."""
    import re

    found = (" ".join(m.split()) for m in re.findall(r'\*"(.+?)"\*', prompt, re.S))
    return [q for q in found if len(q.split()) >= _ILLUSTRATION_MIN_WORDS]


def test_no_worked_example_is_a_sentence_from_a_scored_posting() -> None:
    """A prompt that quotes the benchmark's own postings measures nothing.

    The first `description_v6` draft was written straight from the Stage 0
    Take 2 diagnosis and picked up three sentences verbatim from golden
    postings -- the clearance line, the named in-office days, the
    individual-contributor requirement. Each one sat next to the correct answer
    for an assertion the same posting is scored on, which turns three model
    errors into three lookups and reports the result as accuracy.

    The rule generalises past those three: an instruction must teach on an
    example the extractor will never be graded against, or the grade is
    partly a measurement of the prompt.

    Short quotes are skipped -- `*"e.g."*` and `*"such as"*` are the vocabulary
    a rule is about, and finding them in a posting is the point of the rule.
    """
    from pathlib import Path

    from career_agent.evaluation.golden import load_cases

    root = Path(__file__).resolve().parents[2] / "evaluation" / "golden"
    if not root.exists():
        import pytest

        pytest.skip("Private golden corpus is intentionally excluded from the public edition")
    postings = {case.case_id: " ".join(case.raw_text.split()) for case in load_cases(root)}

    leaks = [
        (quote, case_id)
        for quote in _illustrations(DESCRIPTION)
        for case_id, text in postings.items()
        if quote in text
    ]

    assert not leaks, (
        "these worked examples are copied out of postings the benchmark scores: "
        + "; ".join(f"{case_id}: {quote!r}" for quote, case_id in leaks)
    )


def test_no_prompt_names_a_golden_case() -> None:
    """Case ids are how we talk about the benchmark, never how we teach.

    A prompt naming `gc-23` would be overfitting made literal, and it is the
    single easiest way for a diagnosis document to leak into the thing it is
    diagnosing.
    """
    import re

    for name, prompt in (("description", DESCRIPTION), ("provider", PROVIDER)):
        assert not re.search(r"\bgc-\d\d\b", prompt), f"{name} prompt names a golden case"
