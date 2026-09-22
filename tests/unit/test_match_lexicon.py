"""Pattern matching, negation and prominence, against the committed vocabulary.

Two behaviours here are worth more than the rest. Token boundaries have to hold
for the names this profile actually cares about -- `n8n`, `make.com`, `c++` --
which is why the matcher uses lookarounds rather than `\\b`. And negation has to
survive a real sentence: "no cold calling required" is one of the best sentences
a posting can contain for this candidate, and a matcher that read it as a cold
calling job would be worse than one that never opened the description.
"""

from tests.support import committed_config_dir

from career_agent.config.search_config import LexiconSignal, load_search_config
from career_agent.domain.enums import Prominence
from career_agent.match.lexicon import compile_patterns, find_hits, observe
from career_agent.match.text import split_sections

# The COMMITTED configuration, never `config/` itself.
#
# `config/` resolves `search.local.yaml` when one exists, so reaching for it
# here means this module tests the owner's private search on her machine and
# the shipped worked example on everybody else's -- two different suites
# wearing one name, and the second one is what a fresh clone runs.
# `committed_config_dir` is the copy with every local override removed.
CONFIG, _ = load_search_config(committed_config_dir())


def _sections(description: str) -> tuple[tuple[int, int, str | None], ...]:
    return tuple(
        split_sections(
            description,
            CONFIG.prominence.primary_headings,
            CONFIG.prominence.secondary_headings,
        )
    )


def _hits(signal_id: str, description: str) -> list:
    return find_hits(
        signal_id,
        CONFIG.lexicon[signal_id],
        "description",
        description,
        _sections(description),
        CONFIG.negation,
    )


# --- boundaries ------------------------------------------------------------


def test_a_pattern_only_matches_at_token_boundaries() -> None:
    regex = compile_patterns(("ops",)).regex
    assert regex.search("we run ops for the company") is not None
    assert regex.search("we run operations for the company") is None


def test_punctuated_tool_names_still_match() -> None:
    """`\\b` mishandles every one of these, which is why the matcher does not use it."""
    for phrase in ("n8n", "make.com", "c++"):
        regex = compile_patterns((phrase,)).regex
        assert regex.search(f"we use {phrase} daily") is not None, phrase

    assert compile_patterns(("make.com",)).regex.search("remake.combine") is None


def test_the_longest_matching_phrase_wins() -> None:
    """Otherwise the recorded evidence is a phrase the posting does not contain."""
    hits = _hits("cold_outbound", "Daily cold calling into new accounts.")
    assert [hit.pattern for hit in hits] == ["cold calling"]


def test_a_signal_with_no_patterns_matches_nothing() -> None:
    empty = LexiconSignal(label="empty", patterns=[])
    assert find_hits("empty", empty, "description", "anything at all", ()) == []


# --- negation --------------------------------------------------------------


def test_a_cue_before_the_match_marks_the_hit_negated() -> None:
    hits = _hits("cold_outbound", "This is a systems role: no cold calling required.")

    assert len(hits) == 1
    assert hits[0].negated is True


def test_a_requirement_is_not_negated() -> None:
    hits = _hits("cold_outbound", "Cold calling is required in this role.")

    assert len(hits) == 1
    assert hits[0].negated is False


def test_notable_is_not_the_cue_not() -> None:
    """Cues are whole tokens. A substring cue would negate half the corpus."""
    hits = _hits("cold_outbound", "A notable amount of cold calling.")
    assert hits[0].negated is False


def test_the_negation_window_does_not_reach_into_the_previous_sentence() -> None:
    """ "No travel" in the bullet above says nothing about this bullet."""
    hits = _hits("cold_outbound", "There is no travel.\nCold calling is the core of the day.")
    assert hits[0].negated is False


def test_a_negation_sensitive_signal_only_negates_when_configured() -> None:
    """`api_integration` is not negation-sensitive, so no cue is looked for."""
    hits = _hits("api_integration", "This role has no rest api work at all.")
    assert hits and all(hit.negated is False for hit in hits)


# --- prominence ------------------------------------------------------------


def test_a_hit_in_the_title_is_primary() -> None:
    observed = observe(CONFIG, "Workflow Automation Engineer", "We do things.")
    assert observed["workflow_automation"].prominence is Prominence.PRIMARY


def test_a_hit_under_a_responsibilities_heading_is_primary() -> None:
    description = "About us\nWe sell things.\n\nResponsibilities\n- Own workflow automation here\n"
    observed = observe(CONFIG, "Analyst", description)

    assert observed["workflow_automation"].prominence is Prominence.PRIMARY
    assert observed["workflow_automation"].hits[0].section == "PRIMARY"


def test_repeated_body_mentions_reach_secondary() -> None:
    description = "We do workflow automation.\nLater we mention process automation again.\n"
    observed = observe(CONFIG, "Analyst", description)

    assert observed["workflow_automation"].prominence is Prominence.SECONDARY


def test_a_single_passing_mention_is_incidental() -> None:
    observed = observe(CONFIG, "Analyst", "We occasionally do some workflow automation.")
    assert observed["workflow_automation"].prominence is Prominence.INCIDENTAL


def test_a_negated_hit_does_not_lend_prominence() -> None:
    observed = observe(CONFIG, "Analyst", "Responsibilities\n- No cold calling required here\n")
    signal = observed["cold_outbound"]

    assert signal.fired is False
    assert signal.prominence is Prominence.INCIDENTAL
    assert len(signal.negated_hits) == 1


def test_a_signal_that_fired_nowhere_is_still_returned() -> None:
    """So the caller never has to tell "absent from the dict" from "negated"."""
    observed = observe(CONFIG, "Analyst", "Nothing relevant here.")

    assert set(observed) == set(CONFIG.lexicon)
    assert observed["ipaas"].hits == ()
    assert observed["ipaas"].prominence is Prominence.INCIDENTAL


# --- bilingual -------------------------------------------------------------


def test_accented_portuguese_fires_the_same_signals_as_english() -> None:
    description = "Responsabilidades\n- Integração de sistemas e automação de processos\n"
    observed = observe(CONFIG, "Analista de Sistemas", description)

    assert observed["system_integration"].fired
    assert observed["workflow_automation"].fired
    assert observed["system_integration"].hits[0].quote in description
