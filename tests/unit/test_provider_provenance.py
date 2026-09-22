"""Provider identity is pipeline provenance, not model output.

M2 Stage 0 established this the expensive way. 55 live Gemini calls, 50
schema-valid answers, and zero fingerprints -- every one of 26 postings died in
assembly with:

    PROVIDER_FIELD evidence requires provider and source_field

Four layers had disagreed with each other in silence:

    provider_v3.md      asks for `source_field` and `source_value`. Never
                        mentions `provider`.
    TEvidence           `provider: str | None = None` -- omitting it is valid
    domain Evidence     requires `provider` for PROVIDER_FIELD
    _convert_evidence   passed the model's value straight through

Gemini did exactly what the prompt asked. The prompt was right to ask for
nothing more: the ATS a payload came from is not an observation about the
posting, it is context the pipeline already holds on `JobSource` and even
renders into the block the model reads.

The assisted development run never caught it because the assisting agent could
see the whole job and helpfully supplied a field no real model was ever asked
for -- which is precisely the class of defect a live run exists to find.

These fixtures carry the semantic *shape* Gemini produced, not its raw output.
"""

import pytest

from career_agent.domain.enums import EvidenceSourceKind
from career_agent.domain.fingerprint import FingerprintMeta
from career_agent.llm.assemble import AssemblyError, assemble
from career_agent.llm.prompts import PROMPT_DIR
from career_agent.llm.transport import (
    ALL_DIMENSIONS,
    TDescriptionFamily,
    TEvidence,
    TObservation,
    TProviderFamily,
)

POSTING = (
    "Business Technology Analyst\n\n"
    "You will own our internal business systems end to end.\n"
    "We hire anywhere in the world.\n"
)


def description_family() -> TDescriptionFamily:
    """A complete description answer. Not what this module is testing."""
    return TDescriptionFamily(
        observed_title="Business Technology Analyst",
        observations=[
            TObservation(dimension=name, status="NOT_STATED", value=None, confidence=0.0)
            for name in ALL_DIMENSIONS
        ],
        responsibilities=[],
        software=[],
        languages=[],
        evidence=[],
    )


def provider_family(provider_value: str | None = None) -> TProviderFamily:
    """The shape Gemini actually returned under `provider_v3`.

    `source_field` and `source_value` copied from the observation block, an
    evidence id, and -- unless a test asks otherwise -- **no `provider`**,
    because the prompt never asks for one.
    """
    return TProviderFamily(
        observations=[
            TObservation(
                dimension="hiring_location_hint",
                status="EXPLICIT",
                value="COUNTRY_LIST:US",
                confidence=1.0,
                evidence_id="ev_01",
            )
        ],
        evidence=[
            TEvidence(
                id="ev_01",
                source_kind=EvidenceSourceKind.PROVIDER_FIELD,
                provider=provider_value,
                source_field="country",
                source_value="US",
            )
        ],
    )


def meta() -> FingerprintMeta:
    return FingerprintMeta(
        prompt_version="description_v5+provider_v3",
        model="gemini-3.1-flash-lite",
        content_hash="sha256:abc",
        payload_hash="sha256:def",
    )


# =========================================================================
# THE STAGE 0 DEFECT
# =========================================================================


def test_the_transport_accepts_provider_evidence_with_no_provider() -> None:
    """The first half of the mismatch, pinned.

    This response is *valid transport* under `provider_v3`. It has to stay
    valid: the prompt does not ask for `provider`, so a model that omits it has
    complied, and making the transport reject it would be blaming the model for
    following instructions.
    """
    family = provider_family()
    assert family.evidence[0].provider is None

    # Round-trips through the schema the vendor is actually given.
    assert TProviderFamily.model_validate(family.model_dump()).evidence[0].provider is None


def test_assembly_stamps_the_provider_the_pipeline_already_knows() -> None:
    """The Stage 0 regression itself. This is the assertion that was missing.

    26 of 26 live extractions failed on exactly this input. It must never fail
    again for this reason.
    """
    fingerprint, _ = assemble(
        description_family(), provider_family(), meta(), source_provider="greenhouse"
    )

    citation = next(
        e for e in fingerprint.evidence if e.source_kind is EvidenceSourceKind.PROVIDER_FIELD
    )
    assert citation.provider == "greenhouse"
    assert citation.source_field == "country"
    assert citation.source_value == "US"


def test_the_provider_is_never_asked_for_in_the_live_prompt() -> None:
    """The contract the fix rests on, asserted against the prompt itself.

    If a future prompt version starts asking the model for `provider`, this
    fails and the ownership decision gets revisited deliberately rather than
    drifting back.
    """
    from career_agent.llm.prompts import PROVIDER_PROMPT_VERSION, load_prompt

    text = load_prompt(PROVIDER_PROMPT_VERSION)
    assert "`provider`" not in text


# =========================================================================
# WHOSE VALUE WINS
# =========================================================================


def test_a_matching_model_supplied_provider_is_accepted_and_discarded() -> None:
    """The assisted development answers volunteered one. They still work.

    The stored value is the deterministic one either way -- agreement is not a
    reason to start trusting model output for provenance, it is merely not a
    contradiction.
    """
    fingerprint, _ = assemble(
        description_family(),
        provider_family("greenhouse"),
        meta(),
        source_provider="greenhouse",
    )
    citation = next(
        e for e in fingerprint.evidence if e.source_kind is EvidenceSourceKind.PROVIDER_FIELD
    )
    assert citation.provider == "greenhouse"


def test_a_differently_spelled_provider_is_not_treated_as_a_contradiction() -> None:
    """`Greenhouse` and `greenhouse` are one answer written two ways."""
    fingerprint, _ = assemble(
        description_family(),
        provider_family("Greenhouse"),
        meta(),
        source_provider="greenhouse",
    )
    citation = next(
        e for e in fingerprint.evidence if e.source_kind is EvidenceSourceKind.PROVIDER_FIELD
    )
    assert citation.provider == "greenhouse"


def test_a_conflicting_model_supplied_provider_is_rejected_not_overwritten() -> None:
    """Silently preferring the deterministic value would hide a real disagreement.

    A model naming `lever` for a posting collected from Greenhouse is not a
    formatting slip -- it is a claim about which source the citation came from,
    and choosing either value for it would be a guess. This matches how the same
    function already treats a family citing the wrong source kind.
    """
    with pytest.raises(AssemblyError) as exc:
        assemble(
            description_family(),
            provider_family("lever"),
            meta(),
            source_provider="greenhouse",
        )

    assert "claims provider 'lever'" in str(exc.value)
    assert "greenhouse" in str(exc.value)


def test_a_description_citation_still_cannot_carry_provider_fields() -> None:
    """The stamping applies to one channel only. The two never blend."""
    description = description_family()
    description.evidence = [
        TEvidence(
            id="ev_01",
            source_kind=EvidenceSourceKind.PROVIDER_FIELD,
            source_field="country",
            source_value="US",
        )
    ]

    with pytest.raises(AssemblyError) as exc:
        assemble(description, TProviderFamily(), meta(), source_provider="greenhouse")

    assert "may only cite" in str(exc.value)


def test_assembly_cannot_be_called_without_naming_the_source_provider() -> None:
    """Keyword-only and no default: forgetting it is a TypeError, not a document.

    A fingerprint whose provider channel named nobody is exactly what Stage 0
    produced, and the signature is what stops it being producible again.
    """
    with pytest.raises(TypeError):
        assemble(description_family(), provider_family(), meta())  # type: ignore[call-arg]


# =========================================================================
# WHAT WAS AUDITED AND DELIBERATELY NOT CHANGED
# =========================================================================


def test_payload_identity_is_carried_by_the_document_not_by_each_citation() -> None:
    """Audited alongside `provider`, and found already correct.

    `Evidence.payload_hash` exists on the domain model and is read by nothing:
    the verifier checks a citation against the payload it is handed, and
    `FingerprintRepo._insert_evidence` writes the evidence row's `payload_hash`
    from `fp.meta.payload_hash`. Payload identity therefore has exactly one
    authoritative home, at the document level, and populating a second copy per
    citation would create a value that could disagree with it.

    `providers/base.py` says the same thing from the other end: a
    `ProviderMetadataObservation` carries "everything a PROVIDER_FIELD evidence
    row needs except payload_hash".
    """
    fingerprint, _ = assemble(
        description_family(), provider_family(), meta(), source_provider="greenhouse"
    )

    citation = next(
        e for e in fingerprint.evidence if e.source_kind is EvidenceSourceKind.PROVIDER_FIELD
    )
    assert citation.payload_hash is None
    assert fingerprint.meta.payload_hash == "sha256:def"


# =========================================================================
# THE FIX MUST NOT INVALIDATE WHAT STAGE 0 ALREADY PAID FOR
# =========================================================================


def test_the_cache_identity_covers_the_bytes_the_model_is_shown() -> None:
    """Every question Take 3 will ask, pinned as the digest it asks under.

    These six lines are the whole cache contract, and each one is a decision:

    * the two families move independently -- the description prompt moved to
      v7 and `provider_v3` did not, so a re-run re-asks the description family
      and reuses every stored provider answer;
    * the arm separates -- a development answer cannot reach a vendor arm;
    * and the key now covers the model-visible BYTES, not two labels standing
      in for them. `test_editing_the_prompt_text_moves_the_key` proves the
      mechanism; this proves the values Take 3 will actually run under.

    A moved digest here is 25 or 27 more live calls. Do not update one to make
    a test pass -- work out which input moved, and whether it should have.
    """
    from career_agent.llm.cache import observation_block, provider_key, static_digest
    from career_agent.llm.client import ModelConfig
    from career_agent.llm.prompts import PROVIDER_PROMPT_VERSION, load_prompt
    from career_agent.llm.requests import build_description_request, provider_schema
    from career_agent.llm.transport import PROVIDER_TRANSPORT_VERSION

    gemini = ModelConfig(vendor="google", identifier="gemini-3.1-flash-lite")
    cowork = ModelConfig(vendor="cowork", identifier="cowork-development")

    # MOVED, deliberately, by the description_v7 -> description_v8 bump, and
    # for the second time by the same input. The docstring says not to update
    # this to make a test pass, so: the input that moved is `static_digest`,
    # which covers the system prompt bytes, and v8 is 405 bytes v7 did not
    # contain -- an unfenced skeleton and one terminal rule. That is exactly
    # what a bump is for: the 18 description answers stored under v7's bytes
    # were answers to a prompt that ended differently, and none of them can be
    # served to a v8 request. `provider_v3` is untouched and the provider pin
    # below has not moved, which is why a re-run re-asks one family and not two.
    description = build_description_request("sha256:PIN", "a posting", gemini).cache_key.key
    assert description == (
        "sha256:c5511a8ab5d57e0c612af622217e1a4a3dfe15d488cd8fdd8237c5bfa77c75d8"
    )

    provider = provider_key(
        gemini,
        PROVIDER_PROMPT_VERSION,
        PROVIDER_TRANSPORT_VERSION,
        observation_block("greenhouse", ()),
        "sha256:map",
        static_digest(load_prompt(PROVIDER_PROMPT_VERSION), provider_schema(), "STRICT_SCHEMA"),
    ).key
    assert provider == "sha256:f9e70c05d6d1424ade12f5b91f8fe886c9ec58a4c87643c436613591e1c43171"

    # The two families are different questions and can never collide.
    assert description != provider

    # A development answer is a different question from a vendor one.
    development = build_description_request("sha256:PIN", "a posting", cowork).cache_key.key
    assert development != description


def test_a_prompt_that_has_been_used_live_cannot_change_under_the_same_name() -> None:
    """The runtime half of the guarantee, and the reason it is not a test pin.

    The cache key names a prompt version. That is only meaningful if a version
    is one specific set of bytes forever, and until `PROMPT_DIGESTS` existed
    nothing enforced it -- `description_v6` was compressed by 685 tokens without
    its identity moving, which was safe only because it had never been sent.

    `static_digest` and this check divide the work, and the division is the
    point. The digest answers *which bytes is this the answer to*, so an edit
    produces a miss rather than a wrong hit. The pin answers *has this named
    prompt mutated*, so an edit to a version already used live produces a
    refusal at load time rather than a silent re-spend of the quota.

    A mismatch raises before a request exists, which is what makes it a runtime
    guarantee rather than a regression test: nothing reaches a vendor, and
    nothing reaches the cache.
    """
    import hashlib

    from career_agent.llm.prompts import (
        DESCRIPTION_PROMPT_VERSION,
        PROMPT_DIGESTS,
        PROVIDER_PROMPT_VERSION,
        PromptChanged,
        load_prompt,
    )

    # Every prompt on disk is pinned, not only the two in force. An unpinned
    # file is a name that could mean anything the next time it is loaded.
    on_disk = {path.stem for path in PROMPT_DIR.glob("*.md")}
    assert on_disk == set(PROMPT_DIGESTS), "a prompt file exists with no pinned digest"

    for version in (DESCRIPTION_PROMPT_VERSION, PROVIDER_PROMPT_VERSION):
        raw = (PROMPT_DIR / f"{version}.md").read_bytes()
        assert "sha256:" + hashlib.sha256(raw).hexdigest() == PROMPT_DIGESTS[version]

    # And drift is refused rather than reported.
    load_prompt.cache_clear()
    original = PROMPT_DIGESTS[DESCRIPTION_PROMPT_VERSION]
    PROMPT_DIGESTS[DESCRIPTION_PROMPT_VERSION] = "sha256:" + "0" * 64
    try:
        with pytest.raises(PromptChanged, match="does not match its pinned digest"):
            load_prompt(DESCRIPTION_PROMPT_VERSION)
    finally:
        PROMPT_DIGESTS[DESCRIPTION_PROMPT_VERSION] = original
        load_prompt.cache_clear()


def test_an_edited_prompt_cannot_serve_the_answer_to_the_old_one() -> None:
    """The invariant both mechanisms exist for, stated end to end.

    Same bytes and same posting must produce the same identity, or a re-run
    re-pays for work it already has. Different bytes under the SAME version name
    must produce a different identity, or a stored answer to the old
    instructions is served as an answer to the new ones.
    """
    from career_agent.llm.cache import description_key, static_digest
    from career_agent.llm.client import ModelConfig
    from career_agent.llm.requests import description_schema

    gemini = ModelConfig(vendor="google", identifier="gemini-3.1-flash-lite")
    schema = description_schema()

    def key(prompt_text: str) -> str:
        return description_key(
            gemini, "description_v6", 3, "sha256:posting", static_digest(prompt_text, schema, "S")
        ).key

    assert key("the instructions") == key("the instructions")
    assert key("the instructions") != key("the instructions, revised")


def test_the_fixes_live_downstream_of_everything_the_model_is_shown() -> None:
    """Structural, so the claim above cannot rot.

    Neither changed file is on the path that builds a request. `requests.py`
    assembles what is sent and `cache.py` decides what makes two sends the same
    question; `assemble.py` and `vendors/google.py` only ever run on an answer
    that has already come back.
    """
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "src" / "career_agent"
    for changed in ("llm/assemble.py", "llm/vendors/google.py"):
        tree = ast.parse((src / changed).read_text(encoding="utf-8"))
        imported = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert "career_agent.llm.requests" not in imported, changed
        assert "career_agent.llm.cache" not in imported, changed
