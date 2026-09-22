"""The two properties that keep `match_job` a UI action rather than an evening.

Editing preferences and re-scoring the corpus is the product's central claim, and
it is a claim about a number: 18,550 postings at a second each is five and a half
hours, and at six milliseconds each it is two minutes.

There are two tests here and they are not the same test. The wall-clock one is a
smoke alarm -- generous, timing-based, and worth little on a loaded machine. The
fold-counting one is the real guard: it pins the STRUCTURE that produced the
speed, which is that the description is folded once per `match_job` and shared,
rather than once per lexicon signal. A refactor that quietly returns to folding
inside `find_hits` would still pass a slack time limit on a fast laptop, and
would put the five and a half hours straight back.
"""

from __future__ import annotations

import time

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.match import text as text_module
from career_agent.match.engine import JobFacts, match_job

# The COMMITTED configuration, never `config/` itself.
#
# `config/` resolves `search.local.yaml` when one exists, so reaching for it
# here means this module tests the owner's private search on her machine and
# the shipped worked example on everybody else's -- two different suites
# wearing one name, and the second one is what a fresh clone runs.
# `committed_config_dir` is the copy with every local override removed.
CONFIG, _ = load_search_config(committed_config_dir())

COMPUTED_AT = "2026-09-04T00:00:00Z"

#: A body of roughly the size the real corpus actually holds: its median
#: description is 5,811 characters and its mean 6,129. Accented Portuguese is in
#: here on purpose -- folding is where an accent costs the most, and a posting
#: that is ASCII except for four characters is the common case, not the corner.
_BLOCK = """Responsibilities
- Own the CRM data model and the integrations that feed it
- Build automations in n8n and Zapier across Stripe, Airtable and HubSpot
- Manutenção e documentação dos processos de integração de sistemas
- Partner with revenue operations on reporting and data quality
- Write SQL against the warehouse and keep the dashboards honest

Requirements
- 5 years of experience in business systems or revenue operations
- Comfortable with APIs, webhooks and a scripting language
- Experiência com automação de processos e ferramentas no-code
"""
POSTING = (_BLOCK * (6000 // len(_BLOCK) + 1))[:6000]

JOB = JobFacts(
    title="Senior Business Systems Engineer",
    description=POSTING,
    location_raw="Remote - Brazil",
    employment_type="FULL_TIME",
    posted_at="2026-09-01",
)


def test_a_posting_is_matched_in_single_digit_milliseconds() -> None:
    """Mean wall-clock per posting, warmed up first.

    The ceiling is 60 ms against a measured 5-7 ms on the corpus median, which
    is roughly ten times the headroom. That is deliberate: a shared CI box, a
    cold cache or a debug build can lose a factor of three without anything
    being wrong, while the regression this exists to catch -- re-folding the
    description once per signal -- cost a factor of a hundred and fifty. A
    tighter bound would fail on a busy afternoon and teach the team to ignore it.
    """
    for _ in range(3):
        match_job(CONFIG, JOB, computed_at=COMPUTED_AT)

    runs = 20
    start = time.perf_counter()
    for _ in range(runs):
        match_job(CONFIG, JOB, computed_at=COMPUTED_AT)
    mean_ms = (time.perf_counter() - start) / runs * 1000

    assert mean_ms < 60.0, f"match_job averaged {mean_ms:.1f} ms/posting"


def test_the_description_is_folded_once_per_match(monkeypatch: pytest.MonkeyPatch) -> None:
    """Folding is per FIELD, not per signal.

    `_fold` is the single door onto folding -- `fold`, `fold_field` and the
    heading scan all go through it -- so counting its calls counts every fold in
    the package, whoever asked for it.

    Before `FoldedText` existed this ran 49 lexicon signals plus a dozen blocker
    patterns over the description and folded the whole thing for each, about
    four hundred times per posting. The bound below is 2, and the slack is there
    only so a caller may hold a plain string; it is not room to grow into.
    """
    folded_texts: list[str] = []
    original_fold = text_module._fold

    def counting_fold(text: str):  # type: ignore[no-untyped-def]
        folded_texts.append(text)
        return original_fold(text)

    monkeypatch.setattr(text_module, "_fold", counting_fold)

    # Warm the heading cache, so what is counted is one posting's work and not
    # the one-off folding of the configured headings.
    match_job(CONFIG, JOB, computed_at=COMPUTED_AT)
    folded_texts.clear()
    match_job(CONFIG, JOB, computed_at=COMPUTED_AT)

    whole_description = folded_texts.count(JOB.description)
    assert whole_description <= 2, f"the description was folded {whole_description} times"

    # The title is short and folded by a couple of callers that do not share a
    # `FoldedText`; the rest are candidate heading lines, which are bounded by
    # the number of lines, not by the number of signals.
    assert folded_texts.count(JOB.title) <= 4
    assert len(folded_texts) < 100, f"{len(folded_texts)} folds for one posting"
