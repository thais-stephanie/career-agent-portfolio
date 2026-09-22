"""Deterministic evidence recognition, independent of posting score weights."""

import pytest

from career_agent.domain.claims import VerifiedClaim
from career_agent.domain.enums import ClaimSource, ClaimType
from career_agent.match.preparation import _answering


def evidence(text, **kwargs):
    return VerifiedClaim(
        claim_key="evidence",
        text=text,
        claim_type=ClaimType.PROJECT,
        source=ClaimSource.SELF_ATTESTED,
        verified=True,
        **kwargs,
    )


@pytest.mark.parametrize(
    ("phrase", "text", "expected"),
    [
        ("automação", "Desenvolvi AUTOMAÇÃO para a equipe", True),
        ("API", "Built a capital planning process", False),
        ("C++", "Developed C++ services", True),
        ("make.com", "Maintained remake.combine", False),
        ("internal tool", "Built internal tooling", False),
    ],
)
def test_normalization_and_boundaries(phrase, text, expected):
    assert bool(_answering((phrase,), [evidence(text)])) is expected


@pytest.mark.parametrize(
    "text",
    [
        "Built an internal app for shift handovers.",
        "Desenvolvi uma aplicação interna para atender solicitações.",
        "Criei aplicativos internos para a equipe.",
        "Shipped a second locally hosted internal app for shift handovers.",
        "Delivered an internal application for service requests.",
        "Deployed an internal app for colleagues.",
        "Entreguei um aplicativo interno para a equipe.",
        "Implantei uma aplicação interna para a equipe.",
    ],
)
def test_internal_apps_are_bounded_delivery_paraphrases(text):
    found = _answering(("internal tool",), [evidence(text)], signal_id="internal_tooling")
    assert found is not None
    assert found[0].text == text


def test_tool_use_and_disconnected_sentences_are_not_delivery():
    for text in [
        "Used an internal app.",
        "Built a shelf. Used an internal app.",
        "Built an internal apprentice training plan.",
        "Customers built an internal app.",
        "Used an internal app built by another team.",
        "Customers shipped an internal app.",
        "Shipped orders using an internal app.",
        "Delivered packages with an internal application.",
        "Entreguei encomendas usando um aplicativo interno.",
    ]:
        assert (
            _answering(("internal tool",), [evidence(text)], signal_id="internal_tooling") is None
        )
    assert _answering(("internal app",), [evidence("Built an internal", tools=["app"])]) is None


def test_unconfirmed_and_empty_vocabulary_never_supply_support():
    claim = evidence("Built an internal app.").model_copy(update={"verified": False})
    assert _answering(("internal app",), [claim], signal_id="internal_tooling") is None
    assert _answering((), [evidence("Built an internal app")], signal_id="internal_tooling") is None


def test_negated_delivery_does_not_supply_support():
    from types import SimpleNamespace

    from career_agent.config.search_config import Negation

    config = SimpleNamespace(
        negation=Negation(window_chars=40, cues_en=["not", "never"], cues_pt=["não", "nunca"]),
        lexicon={},
    )
    for text in [
        "I did not build an internal app.",
        "Nunca criei uma aplicação interna.",
        "I never shipped an internal app.",
    ]:
        assert (
            _answering(
                ("internal tool",), [evidence(text)], config=config, signal_id="internal_tooling"
            )
            is None
        )
