"""Admission by title OR description, and why neither alone is enough.

The requirement these exist for: "a vacancy with an unknown or unusual title
must remain eligible when its full description strongly matches", and equally
"a listed title with unrelated responsibilities is not automatically ranked
highly". Those pull in opposite directions, and the union is what satisfies
both without collapsing into "admit everything".
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.domain.enums import ScreeningState
from career_agent.match.engine import ADMITTING_TITLE_CLASSES, evaluate_screening, observe

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def config():
    # The committed worked example, never the real `config/`, which resolves
    # the owner's private `search.local.yaml` when one exists.
    cfg, _ = load_search_config(committed_config_dir())
    return cfg


def _screen(config, title: str, description: str, title_class: str):
    observed = observe(config, *_fields(config, title, description))
    return evaluate_screening(config, observed, title_class)


def _fields(config, title: str, description: str):
    from career_agent.match.text import fold_field

    return fold_field(title), fold_field(description)


STRONG_BODY = (
    "You will own our HubSpot platform and build integrations across the "
    "revenue stack. Day to day you will design workflow automation, wire up "
    "REST APIs and webhooks between systems, and own the CRM architecture "
    "that our go-to-market teams depend on. Experience with iPaaS tooling "
    "such as Workato or n8n is valuable, as is data synchronisation and "
    "migration between business systems."
)

THIN_BODY = "We are hiring. Competitive salary. Apply on our website."


def test_an_unknown_title_with_a_strong_description_is_admitted(config) -> None:
    """The headline requirement. `Internal Systems Builder` is in no taxonomy
    and never will be; the body is unmistakably the work."""
    state, _, channel = _screen(config, "Internal Systems Builder", STRONG_BODY, "UNCLASSIFIED")
    assert state is ScreeningState.NOT_BLOCKED
    assert channel in ("signals", "title_and_signals")


@pytest.mark.parametrize(
    "title",
    [
        "Business Applications Engineer",
        "Operations Technology Specialist",
        "Internal Systems Builder",
        "Solutions Architect - Automation",
    ],
)
def test_the_titles_the_specification_names_qualify_on_their_bodies(config, title: str) -> None:
    state, _, channel = _screen(config, title, STRONG_BODY, "UNCLASSIFIED")
    assert state is ScreeningState.NOT_BLOCKED, f"{title} was blocked"
    assert channel != "title", f"{title} was admitted by title, not by its body"


def test_a_known_title_with_a_thin_body_is_still_admitted(config) -> None:
    """The other direction, and the one that was broken.

    Screening reads only the description, so a posting titled exactly what
    this search is looking for could be blocked because its body was short or
    phrased unusually. The title said what the job was and nothing listened.
    """
    state, reason, channel = _screen(config, "Integration Engineer", THIN_BODY, "PRIMARY")
    assert state is ScreeningState.NOT_BLOCKED
    assert channel == "title"
    # And it says the body did not back the title up, rather than pretending.
    assert "title" in reason.lower()


def test_a_thin_body_and_an_unknown_title_is_blocked(config) -> None:
    """Neither channel fired. The union must still be able to say no, or it
    is not admission control -- it is a pass-through."""
    state, _, channel = _screen(config, "Regional Sales Manager", THIN_BODY, "UNCLASSIFIED")
    assert state is ScreeningState.BLOCKED
    assert channel == "none"


def test_one_generic_word_does_not_admit(config) -> None:
    """`automation` on its own is a marketing word.

    The co-occurrence rule lives in the configuration -- every required group
    must fire, and a group names a family rather than a word -- so this is a
    test that the configuration is doing its job, not that the code is.
    """
    for word in ("automation", "data", "API", "systems", "operations", "AI"):
        body = f"We value {word} in everything we do. Join our growing team."
        state, _, channel = _screen(config, "Growth Associate", body, "UNCLASSIFIED")
        assert state is ScreeningState.BLOCKED, f"{word!r} alone admitted a posting"
        assert channel == "none"


def test_a_conditional_title_does_not_admit_on_its_own(config) -> None:
    """`CONDITIONAL` is absent from the admitting classes on purpose: those
    titles mean different work at different employers, which is exactly the
    case the description has to settle."""
    assert "CONDITIONAL" not in ADMITTING_TITLE_CLASSES
    state, _, channel = _screen(config, "GTM Engineer", THIN_BODY, "CONDITIONAL")
    assert state is ScreeningState.BLOCKED
    assert channel == "none"


def test_an_excluded_title_does_not_admit_on_its_own(config) -> None:
    assert "EXCLUDED" not in ADMITTING_TITLE_CLASSES
    state, _, _ = _screen(config, "Account Executive", THIN_BODY, "EXCLUDED")
    assert state is ScreeningState.BLOCKED


def test_both_channels_firing_is_reported_as_both(config) -> None:
    """Worth distinguishing: a posting the taxonomy predicted AND the body
    confirms is the strongest kind of result, and the channel says so."""
    state, _, channel = _screen(config, "Integration Engineer", STRONG_BODY, "PRIMARY")
    assert state is ScreeningState.NOT_BLOCKED
    assert channel == "title_and_signals"
