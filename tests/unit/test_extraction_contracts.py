"""Guards on the properties that would fail silently if they broke.

None of these test behaviour. They test the *absence* of behaviour: that the
extraction path cannot know which runner it is serving, that a development
answer cannot be served to a benchmark, and that two registries which must
agree still do. Each of them protects a claim made in the architecture that
nothing else would notice going wrong.
"""

import ast
from pathlib import Path

from career_agent.domain.fingerprint import FingerprintMeta
from career_agent.llm.cache import description_key, static_digest
from career_agent.llm.client import ModelConfig
from career_agent.llm.prompts import PROVIDER_PROMPT_VERSION, load_prompt
from career_agent.llm.transport import TRANSPORT_SCHEMA_VERSION
from career_agent.pipeline.cowork import COWORK_MODEL_IDENTIFIER, COWORK_VENDOR, cowork_config
from career_agent.providers.registry import available_providers, field_map_for

SRC = Path(__file__).resolve().parents[2] / "src" / "career_agent"

#: The extraction decision path. Nothing in these functions may consult the
#: runner, because a runner-aware extraction would stop being one pipeline.
DECISION_FUNCTIONS = {"extract_job", "extract_many", "_ask", "_interpret"}


def test_the_extraction_path_cannot_see_which_runner_it_is_serving() -> None:
    """The property the whole Cowork harness rests on.

    A vendor adapter, the replay client and the Cowork harness all reach the
    same code, and it cannot tell them apart. If extraction ever branched on
    the runner, development results would stop being evidence about the
    production path -- and the branch would look perfectly reasonable in the
    diff that introduced it.

    Accounting is not a branch: `_as_record` and `paid_calls` legitimately read
    the runner to price a call, and they are outside the decision path.
    """
    tree = ast.parse((SRC / "pipeline" / "extract.py").read_text(encoding="utf-8"))
    offenders: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name not in DECISION_FUNCTIONS:
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Name) and inner.id == "Runner":
                offenders.append(node.name)
            if isinstance(inner, ast.Attribute) and inner.attr.startswith("COWORK"):
                offenders.append(node.name)

    assert offenders == [], f"the extraction path consults the runner in {sorted(set(offenders))}"


def test_no_module_outside_the_harness_knows_cowork_exists() -> None:
    """Cowork is a runner, not a concept the system reasons about.

    Three files may name it: the enum that defines it, the harness that sets
    it, and the CLI that reports it back to a human. A fourth would mean the
    harness had started to leak into the product.
    """
    allowed = {
        Path("llm/client.py"),
        Path("pipeline/cowork.py"),
        Path("cli_extract.py"),
    }
    mentions = {
        path.relative_to(SRC)
        for path in SRC.rglob("*.py")
        if "COWORK" in path.read_text(encoding="utf-8")
    }

    assert mentions == allowed, f"unexpected Cowork awareness in {sorted(mentions - allowed)}"


def test_a_development_answer_can_never_be_served_to_a_benchmark() -> None:
    """Structural separation, not a convention someone has to remember.

    The model identity is part of the cache key, so the Cowork arm and any
    production arm are different questions about the same posting even when
    every other input is identical.
    """
    production = ModelConfig(vendor="anthropic", identifier="claude-sonnet-5")

    same_bytes = static_digest("identical prompt", {"type": "object"}, "STRICT_SCHEMA")
    development_key = description_key(
        cowork_config(), "description_v1", 1, "sha256:aaa", same_bytes
    )
    production_key = description_key(production, "description_v1", 1, "sha256:aaa", same_bytes)

    assert development_key.key != production_key.key
    assert COWORK_MODEL_IDENTIFIER == "cowork-development"
    assert COWORK_VENDOR == "cowork"


def test_the_cowork_identity_is_not_a_production_model_name() -> None:
    """It must stay obviously not-a-model wherever it is printed or stored.

    A development fingerprint that could be mistaken for a benchmark result
    would let an accuracy claim be made about a model that never ran.
    """
    for vendor_prefix in ("claude", "gpt", "gemini", "sonnet", "haiku", "opus"):
        assert vendor_prefix not in COWORK_MODEL_IDENTIFIER


def test_the_transport_version_recorded_on_a_document_tracks_the_transport() -> None:
    """`domain/` may not import `llm/`, so the default is mirrored.

    A mirrored constant is a constant that will drift. This is the test that
    makes the drift loud instead of producing documents that claim to have been
    assembled from a wire format they never saw.
    """
    default = FingerprintMeta.model_fields["transport_version"].default

    assert default == TRANSPORT_SCHEMA_VERSION


def test_every_provider_has_a_field_map_reachable_without_a_fetcher() -> None:
    """Two registries that must agree.

    Extraction resolves field maps offline; collection constructs adapters with
    an HTTP client. A provider registered in one and forgotten in the other
    would collect fine and then fail at extraction with a message about an
    unknown provider, which is a confusing way to learn about a typo.
    """
    for name in available_providers():
        assert field_map_for(name) is not None


def test_the_provider_prompt_describes_the_block_it_is_actually_given() -> None:
    """The prompt and `observation_block` are one contract.

    v2 documented two columns while the block rendered a vendor header and
    three. Describing an input the model does not receive produces a plausible
    wrong answer and no error anywhere, which is the worst shape a bug can
    take here.
    """
    prompt = load_prompt(PROVIDER_PROMPT_VERSION)

    assert "<vendor>" in prompt
    assert "<dimension>" in prompt
    assert "hiring_location_hint\tlocation.name" in prompt
