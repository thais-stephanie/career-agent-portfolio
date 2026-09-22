"""Evidence verification: tolerate formatting, never tolerate facts.

An earlier M0 revision allowed `similarity >= 0.95` as a proof tier. It was
superseded during M2 implementation review, and this file is the argument for
why, written as executable cases.

Every pair below is *mostly identical*. Each differs in one token that a later
gate depends on -- a negation, a country, a requirement level, an amount, a
frequency. Embedded in real posting context they all score far above 0.95. If
similarity were proof, the system would confirm the opposite of what a posting
says and attach a citation to it, which is worse than having no citation at all:
a wrong claim with evidence is one nobody thinks to check.

The positive cases matter equally. A verifier so strict that a curly apostrophe
breaks it would push every real extraction to NOT_STATED and make the corpus
useless. The line is drawn at typography.
"""

import pytest

from career_agent.domain.enums import VERIFYING_MATCH_KINDS, MatchKind
from career_agent.domain.verify import normalise, verify_quote

# `normalise` is imported for the anti-vacuity check below, which compares the
# adversarial pairs after canonicalisation to prove they really are similar.

#: Enough surrounding context that a one-token change is a tiny fraction of the
#: string. That is the point: this is where similarity scoring is most dangerous.
CONTEXT = (
    "We are a distributed team building infrastructure for modern businesses, and we "
    "care deeply about craft, autonomy and clear written communication. As part of "
    "this role you will work closely with engineering, revenue operations and support. "
)
TAIL = (
    " We review applications on a rolling basis and aim to respond within two weeks. "
    "We are proud to be an equal opportunity employer and welcome applicants from every "
    "background, and we are happy to make accommodations throughout the process."
)


def sentence(core: str) -> str:
    return f"{CONTEXT}{core}{TAIL}"


# --- the adversarial pairs --------------------------------------------------

ADVERSARIAL = [
    pytest.param(
        "German is required for this role.",
        "German is not required for this role.",
        id="negation",
    ),
    pytest.param(
        "This position is open to candidates residing in Brazil.",
        "This position is open to candidates residing in the United States.",
        id="geography",
    ),
    pytest.param(
        "HubSpot experience is required.",
        "HubSpot experience is preferred.",
        id="requirement-level",
    ),
    pytest.param(
        "The base salary range for this role is $120,000 to $150,000.",
        "The base salary range for this role is $100,000 to $150,000.",
        id="compensation-amount",
    ),
    pytest.param(
        "The base salary range for this role is USD 120,000 to 150,000.",
        "The base salary range for this role is EUR 120,000 to 150,000.",
        id="compensation-currency",
    ),
    pytest.param(
        "You should expect to travel up to 10% of the time.",
        "You should expect to travel up to 50% of the time.",
        id="travel-frequency",
    ),
    pytest.param(
        "We are able to sponsor visas for this position.",
        "We are unable to sponsor visas for this position.",
        id="sponsorship",
    ),
    pytest.param(
        "This role is open to contractors as well as employees.",
        "This role is open to employees only.",
        id="engagement-type",
    ),
]


@pytest.mark.parametrize(("posting_says", "model_claims"), ADVERSARIAL)
def test_a_semantically_different_quote_never_verifies(
    posting_says: str, model_claims: str
) -> None:
    """The whole reconciliation, in one assertion.

    The posting says one thing; the model cites something that differs by a
    single decisive token. Whatever the similarity score, this is not proof.
    """
    result = verify_quote(model_claims, sentence(posting_says), "ev_01")

    assert not result.verified
    assert result.match_kind not in VERIFYING_MATCH_KINDS


@pytest.mark.parametrize(("posting_says", "model_claims"), ADVERSARIAL)
def test_these_pairs_really_are_highly_similar(posting_says: str, model_claims: str) -> None:
    """Guards the test above from becoming vacuous.

    If these strings were obviously different, the parametrised test would pass
    for the wrong reason and would keep passing even if a similarity proof tier
    were reintroduced. Asserting the similarity is genuinely high is what keeps
    the adversarial cases adversarial.
    """
    from difflib import SequenceMatcher

    score = SequenceMatcher(
        None, normalise(sentence(posting_says)), normalise(sentence(model_claims))
    ).ratio()

    assert score > 0.95, (
        f"context is too short for {posting_says!r} vs {model_claims!r} to be a real "
        f"trap (similarity {score:.3f}); lengthen CONTEXT/TAIL"
    )


def test_the_exact_sentence_in_the_same_posting_does_verify() -> None:
    """The control. Without it, a verifier that rejected everything would pass
    every adversarial test above."""
    posting = sentence("German is required for this role.")

    result = verify_quote("German is required for this role.", posting, "ev_01")

    assert result.verified
    assert result.match_kind is MatchKind.EXACT


# --- typography is forgiven -------------------------------------------------

TYPOGRAPHY = [
    pytest.param(
        "You'll own our HubSpot instance end to end.",
        "You’ll own our HubSpot instance end to end.",
        id="curly-apostrophe",
    ),
    pytest.param(
        'We call this "async by default".',
        "We call this “async by default”.",
        id="curly-quotes",
    ),
    pytest.param(
        "Salary: 120,000 USD",
        "Salary: 120,000 USD",
        id="non-breaking-space",
    ),
    pytest.param(
        "This role is fully remote.",
        "This    role\tis  fully   remote.",
        id="collapsed-whitespace",
    ),
    pytest.param(
        "We hire across EMEA and the Americas.",
        "We hire across EMEA\nand the Americas.",
        id="line-break",
    ),
    pytest.param(
        "Travel is 10-20% of the time.",
        "Travel is 10–20% of the time.",  # punctuation-check: allow: employer-written fixture
        id="en-dash",
    ),
    pytest.param(
        "REMOTE - WORLDWIDE",
        "Remote - Worldwide",
        id="heading-case",
    ),
]


@pytest.mark.parametrize(("posting_renders", "model_transcribes"), TYPOGRAPHY)
def test_formatting_differences_still_verify(posting_renders: str, model_transcribes: str) -> None:
    """A model transcribing a curly apostrophe as a straight one has not lied.

    Rejecting these would push every real extraction to NOT_STATED and make the
    corpus useless -- the over-caution failure that the false-NOT_STATED metric
    exists to catch from the other side.
    """
    result = verify_quote(model_transcribes, sentence(posting_renders), "ev_01")

    assert result.verified
    assert result.match_kind in (MatchKind.EXACT, MatchKind.NORMALISED)


# --- there is no third tier -------------------------------------------------


def test_a_near_miss_is_simply_not_found() -> None:
    """The mildest possible failure: the model added a comma the posting lacks.

    It does not verify, and that is the trade stated plainly. Strictness costs
    some genuinely honest citations; the alternative costs correctness on the
    exact tokens the gates depend on. The false-NOT_STATED metric watches this
    cost from the other side, which is why both metrics exist.
    """
    posting = sentence("You will own our HubSpot instance end to end.")

    result = verify_quote("You will own our HubSpot instance, end to end.", posting, "ev_01")

    assert result.match_kind is MatchKind.NOT_FOUND
    assert not result.verified


def test_a_near_miss_and_an_invention_are_treated_identically() -> None:
    """Both are unverified, and neither may keep a field EXPLICIT.

    Telling a paraphrase apart from an invention is genuinely useful for prompt
    diagnostics, and it now happens in the evaluation harness against stored raw
    output -- not in the module that decides what counts as proof.
    """
    posting = sentence("You will own our HubSpot instance end to end.")

    near = verify_quote("You will own our HubSpot instance, end to end.", posting, "ev_01")
    invented = verify_quote("You will manage a team of twelve engineers.", posting, "ev_02")

    assert near.match_kind is invented.match_kind is MatchKind.NOT_FOUND
    assert near.verified == invented.verified is False


def test_the_enum_has_no_similarity_tier() -> None:
    """Pins the reconciliation itself.

    Reintroducing a FUZZY member would be the first step back towards
    similarity-as-proof, and it should fail here by name rather than be noticed
    later in a gate.
    """
    assert not hasattr(MatchKind, "FUZZY")
    assert not hasattr(MatchKind, "NEAR_MATCH")
    assert set(VERIFYING_MATCH_KINDS) == {
        MatchKind.EXACT,
        MatchKind.NORMALISED,
        MatchKind.FIELD_MATCH,
    }


def test_verified_cannot_be_asserted_independently_of_match_kind() -> None:
    """`verified` is derived, not stored.

    There is deliberately no way to construct a result claiming proof from a
    kind that does not provide it.
    """
    from career_agent.domain.verify import VerificationResult

    assert not VerificationResult("ev_01", MatchKind.NOT_FOUND, 0.99).verified
    assert VerificationResult("ev_01", MatchKind.EXACT).verified
    assert VerificationResult("ev_01", MatchKind.NORMALISED).verified
    assert VerificationResult("ev_01", MatchKind.FIELD_MATCH).verified


def test_an_empty_quote_cites_nothing() -> None:
    assert not verify_quote("   ", sentence("anything at all"), "ev_01").verified


# --- provider evidence is stricter still ------------------------------------


def test_provider_evidence_requires_an_exact_payload_value() -> None:
    from career_agent.domain.verify import verify_provider_citation

    payload = {"location": {"name": "Remote - United States"}}

    good = verify_provider_citation("location.name", "Remote - United States", payload, "ev_p01")
    wrong_value = verify_provider_citation("location.name", "Remote - Brazil", payload, "ev_p02")
    wrong_path = verify_provider_citation("office.city", "Remote", payload, "ev_p03")

    assert good.verified and good.match_kind is MatchKind.FIELD_MATCH
    assert not wrong_value.verified
    assert not wrong_path.verified


def test_provider_evidence_has_no_tolerance_tier() -> None:
    """No fuzzy tier, no typography tolerance, no provider-specific exception.

    A payload either held that value at that path or it did not, and 'almost'
    is not a thing a JSON document can be.
    """
    from career_agent.domain.verify import verify_provider_citation

    payload = {"location": {"name": "Remote - United States"}}

    off_by_a_word = verify_provider_citation(
        "location.name", "Remote - United State", payload, "ev_p01"
    )

    assert off_by_a_word.match_kind is MatchKind.NOT_FOUND
    assert not off_by_a_word.verified
