"""The selection, written out. Layout only, and never a word about her.

`resume.py` decides which of her own confirmed sentences a posting argues for
and in what order. This is what happens when she takes that away: a file she
can work from, with the gaps beneath the strengths.

THE PROPERTY THAT MATTERS
-------------------------
Every line of the export is one of three things: a heading this product wrote,
a sentence SHE confirmed word for word, or a label out of her own
configuration. There is no fourth kind, and in particular there is no sentence
this program wrote about her.

That is asserted twice, because one way would not be enough. Structurally, by
walking the module's syntax tree and checking that nothing composes a string
outside a marker table. And by VALUE: every claim line, with its marker
stripped, equals a stored claim's text exactly -- which is the check a reading
of the formatted output could never give.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.match import resume_export
from career_agent.match.resume_export import WORDS, LineKind, as_text, lines
from career_agent.pipeline.demo_seed import seed_demo
from career_agent.storage.db import connect, migrate
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = committed_config_dir()
DEMO_FILE = REPO_ROOT / "evaluation" / "demo" / "demo_postings.yaml"

#: Sentences a person confirmed about herself. Invented for this file, and
#: written the way somebody actually writes about their own work.
CONFIRMED = (
    "Built workflow automation across HubSpot and the billing systems",
    "Owned the CRM data model and its custom objects",
    "Ran a data quality programme across three business systems",
)


@pytest.fixture
def api() -> Iterator[JobsApi]:
    db_path = pathlib.Path(tempfile.mkdtemp(prefix="resume-export")) / "demo.db"
    conn = connect(db_path)
    try:
        migrate(conn)
        loaded, _ = load_search_config(CONFIG_DIR)
        seed_demo(conn, loaded, source=DEMO_FILE)
    finally:
        conn.close()
    yield JobsApi(ServerConfig(db_path=db_path, config_dir=CONFIG_DIR, port=0), quiet=True)


def confirm(api: JobsApi, text: str) -> None:
    api.handle_api("POST", "/api/evidence", {}, {"claim_type": "EMPLOYMENT", "text": text})


def a_job_with_requirements(api: JobsApi) -> str:
    """The first posting the confirmed claims actually speak to.

    Not simply the best-scoring one: an export whose `lead_with` is empty
    would satisfy every assertion below by containing nothing, which is the
    shape of a test that passes and proves nothing.
    """
    for job in api.handle_api("GET", "/api/jobs", {"limit": ["40"]}, {})["items"]:
        built = api.handle_api("GET", f"/api/jobs/{job['job_id']}/resume", {}, {})
        if built["lead_with"]:
            return str(job["job_id"])
    raise AssertionError("no posting in the demo corpus was answered by the claims")


def any_job(api: JobsApi) -> str:
    """Any scored posting, for the assertions that do not need an answer."""
    payload = api.handle_api("GET", "/api/jobs", {"limit": ["5"]}, {})
    assert payload["items"], "the fixture served no jobs"
    return str(payload["items"][0]["job_id"])


# =========================================================================
# 1. STRUCTURALLY: NOTHING COMPOSES A SENTENCE
# =========================================================================


def test_the_only_literals_this_module_joins_are_its_own_markers() -> None:
    """The narrower rule that lets a renderer exist at all.

    `resume.py` may not compose a string AT ALL, and a test walks its tree to
    keep it that way. A renderer has to join a bullet to a line, so it lives
    here instead -- and here the rule is that the only literal it may join is
    one of its own markers, which are punctuation.
    """
    tree = ast.parse(inspect.getsource(resume_export))
    for node in ast.walk(tree):
        # No f-string anywhere. That is the shape a composed sentence takes.
        assert not isinstance(node, ast.JoinedStr), "the export formats a string"
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            for side in (node.left, node.right):
                if not (isinstance(side, ast.Constant) and isinstance(side.value, str)):
                    continue
                # A LITERAL WITH A LETTER IN IT IS A WORD. One without is
                # punctuation: a bullet, an indent, a line break. Joining a
                # newline to a list of lines is layout; joining "expert in "
                # to a claim would be this module writing her CV, and the
                # difference between the two is exactly whether a reader
                # would find new words in the file.
                assert not any(char.isalpha() for char in side.value), (
                    "the export concatenates a word into somebody's sentence: " + repr(side.value)
                )


def test_every_word_this_module_contributes_is_in_one_table() -> None:
    """So a reader can see the whole of what it says, in one place.

    A heading written inline would be a sentence this product added to a
    document about her career, in a file nobody thinks to re-read.
    """
    assert WORDS, "the export contributes no words at all, which cannot be right"
    for value in WORDS.values():
        assert value.strip() == value
        assert value

    # And every MARKER is punctuation. A marker with a letter in it would be a
    # word joined to her sentence through the one door this module leaves
    # open, and the structural check above would allow it.
    from career_agent.match.resume_export import _MARKER

    for marker in _MARKER.values():
        assert not any(char.isalpha() for char in marker), repr(marker)


# =========================================================================
# 2. BY VALUE: EVERY CLAIM LINE IS HER OWN SENTENCE
# =========================================================================


def test_each_claim_line_equals_a_confirmed_sentence_exactly(api: JobsApi) -> None:
    for text in CONFIRMED:
        confirm(api, text)
    job_id = a_job_with_requirements(api)

    payload = api.resume(job_id=job_id, query={}, body={})
    built = _plan_for(api, job_id)
    rendered = lines(built, title="A title", company="A company")

    claim_lines = [line.text for line in rendered if line.kind is LineKind.CLAIM]
    stored = {suggestion["text"] for suggestion in payload["lead_with"]}
    stored |= {claim["text"] for claim in payload["not_relevant"]}
    for line in claim_lines:
        assert line in stored, line


def test_nothing_is_truncated_rephrased_or_recased(api: JobsApi) -> None:
    """The three ways a renderer quietly rewrites somebody.

    A trimmed sentence is a different claim; a re-cased one asserts a style
    she did not choose; and an ellipsis in a document she is about to send
    somewhere is worse than a long line.
    """
    for text in CONFIRMED:
        confirm(api, text)
    job_id = a_job_with_requirements(api)
    built = _plan_for(api, job_id)

    exported = as_text(built, title="A title", company="A company")
    for text in CONFIRMED:
        assert text in exported, text


def test_the_gaps_travel_with_the_strengths(api: JobsApi) -> None:
    """A document listing only what she has is the flattering view the whole
    preparation surface exists to refuse, and it is worst on the way into an
    interview."""
    confirm(api, CONFIRMED[0])
    job_id = a_job_with_requirements(api)
    built = _plan_for(api, job_id)

    exported = as_text(built, title="A title", company="A company")
    assert WORDS["gaps"] in exported
    assert WORDS["unresolved"] in exported


def test_an_empty_section_says_nothing_rather_than_disappearing(api: JobsApi) -> None:
    """A section that vanished when empty would let a reader believe there
    were no gaps when the truth is that nobody looked."""
    job_id = any_job(api)
    built = _plan_for(api, job_id)

    exported = as_text(built, title="A title", company="A company")
    assert WORDS["gaps"] in exported
    assert WORDS["nothing"] in exported


def test_the_file_says_what_it_is_not(api: JobsApi) -> None:
    """It is a selection and an order. Somebody opening it in six months must
    not mistake it for a resume this program wrote."""
    job_id = any_job(api)
    exported = as_text(_plan_for(api, job_id), title="A title", company="A company")
    assert WORDS["not_a_resume"] in exported
    assert WORDS["note"] in exported


def test_the_employer_words_are_carried_verbatim(api: JobsApi) -> None:
    """A job title and a company name are the employer's, and ADR-0002's rule
    about quoted text does not stop applying because the quote is a heading."""
    job_id = any_job(api)
    exported = as_text(
        _plan_for(api, job_id), title="Especialista Sênior", company="Ativa Sistemas"
    )
    assert "Especialista Sênior" in exported
    assert "Ativa Sistemas" in exported


# =========================================================================
# 3. THE ROUTE AND THE FILE
# =========================================================================


def test_the_route_returns_the_same_text_as_the_plan(api: JobsApi) -> None:
    confirm(api, CONFIRMED[0])
    job_id = a_job_with_requirements(api)

    payload = api.resume(job_id=job_id, query={}, body={})

    assert payload["export_text"].endswith("\n")
    assert WORDS["title"] in payload["export_text"]
    for suggestion in payload["lead_with"]:
        assert suggestion["text"] in payload["export_text"]


def test_an_unscored_posting_refuses_rather_than_exporting_an_empty_file(
    api: JobsApi,
) -> None:
    """A file full of "Nothing." would look like an answer about her."""
    from career_agent.web.api import ApiError

    with pytest.raises(ApiError) as caught:
        api.resume(job_id="01M0000000000000000000000X", query={}, body={})
    assert caught.value.status in {404, 409}


def test_the_export_is_stable_across_two_calls(api: JobsApi) -> None:
    """An unstable order would make the selection look like an opinion that
    changes rather than a consequence of what the posting asked for."""
    for text in CONFIRMED:
        confirm(api, text)
    job_id = a_job_with_requirements(api)

    first = api.resume(job_id=job_id, query={}, body={})["export_text"]
    second = api.resume(job_id=job_id, query={}, body={})["export_text"]
    assert first == second


# =========================================================================
# helpers
# =========================================================================


def _plan_for(api: JobsApi, job_id: str):
    from career_agent.match.preparation import prepare
    from career_agent.match.resume import plan
    from career_agent.storage.mvp_repo import MatchRepo
    from career_agent.storage.repositories import ClaimRepo
    from career_agent.storage.workspace_repo import candidate_id_of

    config = api.search_config()
    config_id, config_version = api._identity()
    conn = connect(api.config.db_path)
    try:
        result = MatchRepo(conn).get(job_id, config_id, config_version)
        assert result is not None
        candidate_id = candidate_id_of(conn)
        claims = ClaimRepo(conn).current(candidate_id) if candidate_id else []
    finally:
        conn.close()
    return plan(prepare(config, result, claims), claims)
