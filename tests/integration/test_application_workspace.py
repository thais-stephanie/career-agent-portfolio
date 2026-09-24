"""The candidate's half of the product, over its own HTTP surface.

These tests are mostly about what the workspace must NOT do. The interesting
failures of an application assistant are not crashes; they are a gap quietly
becoming a match, a rejected line reappearing as a fact, an edit rewriting the
document it cites, and a click turning into experience nobody has.

Every test below names the thing it is stopping.
"""

from __future__ import annotations

import base64
import pathlib
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.pipeline.demo_seed import seed_demo
from career_agent.storage.db import connect, migrate
from career_agent.web.api import JobsApi
from career_agent.web.server import ApiError, ServerConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
#: The committed configuration, copied without the owner's private
#: overrides. A test that read `search.local.yaml` would pass or fail on
#: what is in one person's gitignored file. See `tests/support.py`.
CONFIG_DIR = committed_config_dir()
DEMO_FILE = REPO_ROOT / "evaluation" / "demo" / "demo_postings.yaml"

#: A CV nobody has. Invented for this file, with headings the reader knows and
#: one line carrying a figure, because the figure has its own rule.
SYNTHETIC_CV = """Jordan Vale
Business Systems Analyst

Experience
Built automated deal-to-ticket workflows in HubSpot for the billing team
Owned the reporting stack and reduced manual reconciliation by 40%
Ran discovery with sales and support to map the order-to-cash process

Skills
Process mapping, requirements gathering, stakeholder interviews

Tools
HubSpot, Salesforce, SQL, Looker

Education
BSc Information Systems, 2016
"""


@pytest.fixture
def api() -> Iterator[JobsApi]:
    """A fresh database per test. These tests write, and a module-scoped one
    would make the order they run in part of what they assert."""
    db_path = pathlib.Path(tempfile.mkdtemp(prefix="career-workspace")) / "demo.db"
    conn = connect(db_path)
    try:
        migrate(conn)
        config, _ = load_search_config(CONFIG_DIR)
        seed_demo(conn, config, source=DEMO_FILE)
    finally:
        conn.close()
    yield JobsApi(ServerConfig(db_path=db_path, config_dir=CONFIG_DIR, port=0), quiet=True)


def _import(api: JobsApi, text: str = SYNTHETIC_CV, name: str = "jordan-vale.txt") -> dict:
    return api.handle_api(
        "POST",
        "/api/cv/import",
        {},
        {
            "filename": name,
            "content_base64": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        },
    )


def _proposals(review: dict) -> list[dict]:
    return [p for group in review["groups"] for p in group["proposals"]]


def _find(review: dict, needle: str) -> dict:
    for proposal in _proposals(review):
        if needle.lower() in proposal["text"].lower():
            return proposal
    raise AssertionError(f"no proposal mentioning {needle!r}")


def _a_job(api: JobsApi) -> str:
    return api.handle_api("GET", "/api/jobs", {"limit": ["1"]}, {})["items"][0]["job_id"]


# =========================================================================
# 1. READING A CV CONFIRMS NOTHING
# =========================================================================


def test_importing_a_cv_creates_no_claims(api: JobsApi) -> None:
    """The whole design in one assertion. A document is read; a person confirms."""
    review = _import(api)
    assert review["total"] > 0
    assert review["counts"]["PENDING"] == review["total"]

    ledger = api.handle_api("GET", "/api/evidence", {}, {})
    assert ledger["confirmed"] == 0, "reading a CV confirmed something"


def test_every_proposal_carries_the_line_it_came_from(api: JobsApi) -> None:
    """A reviewer compares the proposal against the source. That comparison is
    the entire reason this can be trusted without trusting the reader."""
    for proposal in _proposals(_import(api)):
        assert proposal["evidence"].strip(), proposal["claim_key"]
        assert proposal["text"] in proposal["evidence"] or proposal["evidence"] in SYNTHETIC_CV


def test_a_figure_is_flagged_and_never_lifted_out(api: JobsApi) -> None:
    """ "40%" alone is a metric this program invented and cannot attribute."""
    measured = [p for p in _proposals(_import(api)) if p["has_measurement"]]
    assert measured, "the 40% line was not flagged"
    for proposal in measured:
        assert proposal["text"].strip() != "40%"
        assert len(proposal["text"]) > 10


def test_a_scanned_cv_is_refused_rather_than_read_as_empty(api: JobsApi) -> None:
    with pytest.raises(ApiError) as caught:
        _import(api, text="short", name="scan.txt")
    assert caught.value.status == 422


def test_a_file_kind_this_does_not_read_is_refused(api: JobsApi) -> None:
    with pytest.raises(ApiError) as caught:
        _import(api, name="cv.exe")
    assert caught.value.status == 400


def test_the_upload_never_stores_a_filesystem_path(api: JobsApi) -> None:
    """A browser may send a full path. What is stored and displayed is a name."""
    review = _import(api, name="C:\\Users\\someone\\Documents\\cv.txt")
    assert review["source_name"] == "cv.txt"


# =========================================================================
# 2. THREE ANSWERS, AND EDIT IS THE ONE THAT MATTERS
# =========================================================================


def test_accepting_a_proposal_creates_exactly_one_confirmed_claim(api: JobsApi) -> None:
    review = _import(api)
    target = _find(review, "HubSpot")
    after = api.handle_api(
        "POST",
        f"/api/cv/imports/{review['import_id']}/decide",
        {},
        {"claim_key": target["claim_key"], "decision": "ACCEPTED"},
    )
    assert after["counts"]["ACCEPTED"] == 1

    ledger = api.handle_api("GET", "/api/evidence", {}, {})
    assert ledger["confirmed"] == 1
    stored = ledger["claims"][0]
    assert stored["text"] == target["text"]
    assert stored["verified"] is True
    assert stored["source"] == "RESUME"


def test_editing_stores_the_correction_and_keeps_the_original_line(api: JobsApi) -> None:
    """The claim may be the person's wording. The evidence stays the document's.

    This is section 10 of the V1.3 brief, and it is the assertion that stops an
    edit from quietly rewriting the citation to agree with it.
    """
    review = _import(api)
    target = _find(review, "HubSpot")
    corrected = "Built and maintained HubSpot workflow automation for billing"

    api.handle_api(
        "POST",
        f"/api/cv/imports/{review['import_id']}/decide",
        {},
        {"claim_key": target["claim_key"], "decision": "EDITED", "text": corrected},
    )

    claim = api.handle_api("GET", "/api/evidence", {}, {})["claims"][0]
    assert claim["text"] == corrected
    assert claim["evidence"] == target["evidence"]
    assert claim["evidence"] != corrected
    assert claim["evidence_differs"] is True


def test_rejecting_creates_nothing_and_does_not_ask_again(api: JobsApi) -> None:
    review = _import(api)
    target = _find(review, "HubSpot")
    after = api.handle_api(
        "POST",
        f"/api/cv/imports/{review['import_id']}/decide",
        {},
        {"claim_key": target["claim_key"], "decision": "REJECTED"},
    )
    assert after["counts"]["REJECTED"] == 1
    assert api.handle_api("GET", "/api/evidence", {}, {})["confirmed"] == 0

    again = api.handle_api("GET", f"/api/cv/imports/{review['import_id']}", {}, {})
    decided = _find(again, "HubSpot")
    assert decided["decision"] == "REJECTED"


def test_an_edit_with_no_text_is_refused(api: JobsApi) -> None:
    review = _import(api)
    target = _find(review, "HubSpot")
    with pytest.raises(ApiError) as caught:
        api.handle_api(
            "POST",
            f"/api/cv/imports/{review['import_id']}/decide",
            {},
            {"claim_key": target["claim_key"], "decision": "EDITED"},
        )
    assert caught.value.status == 400


# =========================================================================
# 3. A REVIEW SURVIVES A CLOSED TAB
# =========================================================================


def test_decisions_persist_and_the_rest_stay_reviewable(api: JobsApi) -> None:
    """Somebody answers three proposals and closes the browser."""
    review = _import(api)
    proposals = _proposals(review)
    for proposal, decision in zip(proposals[:3], ("ACCEPTED", "REJECTED", "ACCEPTED"), strict=True):
        api.handle_api(
            "POST",
            f"/api/cv/imports/{review['import_id']}/decide",
            {},
            {"claim_key": proposal["claim_key"], "decision": decision},
        )

    reopened = api.handle_api("GET", f"/api/cv/imports/{review['import_id']}", {}, {})
    assert reopened["counts"]["ACCEPTED"] == 2
    assert reopened["counts"]["REJECTED"] == 1
    assert reopened["counts"]["PENDING"] == review["total"] - 3
    assert reopened["status"] == "OPEN"
    assert api.handle_api("GET", "/api/evidence", {}, {})["confirmed"] == 2


def test_a_finished_review_closes_itself(api: JobsApi) -> None:
    review = _import(api)
    for proposal in _proposals(review):
        api.handle_api(
            "POST",
            f"/api/cv/imports/{review['import_id']}/decide",
            {},
            {"claim_key": proposal["claim_key"], "decision": "REJECTED"},
        )
    assert (
        api.handle_api("GET", f"/api/cv/imports/{review['import_id']}", {}, {})["status"]
        == "CLOSED"
    )


def test_importing_the_same_cv_twice_is_recognised(api: JobsApi) -> None:
    """Identity is the extracted TEXT. The same CV exported twice has different
    bytes and the same words, and it is the words a review is about."""
    first = _import(api)
    second = _import(api)
    assert second["seen_before"], "a repeated import was not recognised"
    assert second["seen_before"][0]["import_id"] == first["import_id"]


def test_discarding_a_review_keeps_the_claims_it_already_produced(api: JobsApi) -> None:
    review = _import(api)
    target = _find(review, "HubSpot")
    api.handle_api(
        "POST",
        f"/api/cv/imports/{review['import_id']}/decide",
        {},
        {"claim_key": target["claim_key"], "decision": "ACCEPTED"},
    )
    api.handle_api("POST", f"/api/cv/imports/{review['import_id']}/discard", {}, {})

    # "Discard" ARCHIVES now (Career Evidence V2). It hard-deleted the read
    # and every proposal row, including the provenance of what she had
    # confirmed. Archived, it is kept whole and waits nowhere.
    imports = api.handle_api("GET", "/api/cv/imports", {}, {})
    assert [item["archived"] for item in imports["imports"]] == [True]
    assert imports["counts"]["waiting"] == 0
    assert api.handle_api("GET", "/api/evidence", {}, {})["confirmed"] == 1


# =========================================================================
# 4. THE EVIDENCE LEDGER
# =========================================================================


def test_a_claim_written_by_hand_is_self_attested_and_cites_nothing(api: JobsApi) -> None:
    """There is no document behind it. Copying the text into the evidence field
    would manufacture a citation that cites itself."""
    ledger = api.handle_api(
        "POST",
        "/api/evidence",
        {},
        {"claim_type": "EMPLOYMENT", "text": "Ran the order-to-cash redesign at Acme"},
    )
    claim = ledger["claims"][0]
    assert claim["source"] == "SELF_ATTESTED"
    assert claim["evidence"] is None
    assert claim["verified"] is True


def test_a_metric_cannot_be_written_as_a_claim_on_its_own(api: JobsApi) -> None:
    with pytest.raises(ApiError) as caught:
        api.handle_api("POST", "/api/evidence", {}, {"claim_type": "METRIC", "text": "40%"})
    assert caught.value.status == 400


def test_editing_a_claim_makes_a_revision_and_keeps_the_evidence(api: JobsApi) -> None:
    review = _import(api)
    target = _find(review, "HubSpot")
    api.handle_api(
        "POST",
        f"/api/cv/imports/{review['import_id']}/decide",
        {},
        {"claim_key": target["claim_key"], "decision": "ACCEPTED"},
    )
    ledger = api.handle_api(
        "PATCH",
        f"/api/evidence/{target['claim_key']}",
        {},
        {"text": "Designed and built the HubSpot billing automation"},
    )
    claim = next(c for c in ledger["claims"] if c["claim_key"] == target["claim_key"])
    assert claim["revision"] == 2
    assert claim["revisions"] == 2
    assert claim["evidence"] == target["evidence"], "an edit rewrote the source line"

    history = api.handle_api("GET", f"/api/evidence/{target['claim_key']}/history", {}, {})
    assert [r["revision"] for r in history["revisions"]] == [1, 2]


def test_retiring_a_claim_keeps_it_and_stops_it_answering(api: JobsApi) -> None:
    """Not a DELETE. A revision chain whose earlier links can vanish is not a
    provenance, and the claim may already have prepared an application."""
    api.handle_api("POST", "/api/evidence", {}, {"claim_type": "SKILL", "text": "Process mapping"})
    ledger = api.handle_api("GET", "/api/evidence", {}, {})
    key = ledger["claims"][0]["claim_key"]

    after = api.handle_api("POST", f"/api/evidence/{key}/retire", {}, {})
    assert after["confirmed"] == 0
    assert after["retired"] == 1
    retired = next(c for c in after["claims"] if c["claim_key"] == key)
    assert retired["verified"] is False
    assert retired["revision"] == 2

    with pytest.raises(ApiError) as caught:
        api.handle_api("POST", f"/api/evidence/{key}/retire", {}, {})
    assert caught.value.status == 409


def test_an_unknown_claim_key_is_a_404_not_a_new_claim(api: JobsApi) -> None:
    with pytest.raises(ApiError) as caught:
        api.handle_api("PATCH", "/api/evidence/not-a-claim", {}, {"text": "invented"})
    assert caught.value.status == 404


# =========================================================================
# 5. PREPARATION: GAPS ARE FIRST-CLASS
# =========================================================================


def test_preparation_reports_every_requirement_including_the_gaps(api: JobsApi) -> None:
    """No requirement disappears for being unmet. There is no parameter that
    would filter to the matches, and that absence is the design."""
    payload = api.handle_api("GET", f"/api/jobs/{_a_job(api)}/preparation", {}, {})
    assert payload["total"] == len(payload["requirements"])
    assert set(payload["counts"]) == {"MATCHED", "PARTIAL", "GAP", "UNRESOLVED"}
    assert sum(payload["counts"].values()) == payload["total"]


def test_with_nothing_confirmed_every_answerable_requirement_is_a_gap(api: JobsApi) -> None:
    payload = api.handle_api("GET", f"/api/jobs/{_a_job(api)}/preparation", {}, {})
    assert payload["evidence_available"] == 0
    assert payload["counts"]["MATCHED"] == 0
    assert payload["counts"]["PARTIAL"] == 0


def test_every_readiness_state_carries_its_own_definition(api: JobsApi) -> None:
    """Section 6: these four must be precise in code, not only in a docstring."""
    payload = api.handle_api("GET", f"/api/jobs/{_a_job(api)}/preparation", {}, {})
    assert set(payload["readiness_meaning"]) == {"MATCHED", "PARTIAL", "GAP", "UNRESOLVED"}
    for meaning in payload["readiness_meaning"].values():
        assert len(meaning) > 40


def test_a_matched_requirement_shows_the_evidence_that_answered_it(api: JobsApi) -> None:
    """Never an assertion of a match without the sentence behind it."""
    api.handle_api(
        "POST",
        "/api/evidence",
        {},
        {
            "claim_type": "EMPLOYMENT",
            "text": "Built automated deal-to-ticket workflows in HubSpot for billing",
        },
    )
    for job in api.handle_api("GET", "/api/jobs", {"limit": ["40"]}, {})["items"]:
        payload = api.handle_api("GET", f"/api/jobs/{job['job_id']}/preparation", {}, {})
        answered = [r for r in payload["requirements"] if r["readiness"] in {"MATCHED", "PARTIAL"}]
        for requirement in answered:
            assert requirement["evidence_text"], requirement["signal_id"]
            assert requirement["matched_on"], requirement["signal_id"]
            assert requirement["matched_on"].lower() in (requirement["evidence_text"].lower())


def test_a_retired_claim_stops_answering_requirements(api: JobsApi) -> None:
    ledger = api.handle_api(
        "POST",
        "/api/evidence",
        {},
        {
            "claim_type": "EMPLOYMENT",
            "text": "Built automated deal-to-ticket workflows in HubSpot for billing",
        },
    )
    key = ledger["claims"][0]["claim_key"]

    def answered() -> int:
        total = 0
        for job in api.handle_api("GET", "/api/jobs", {"limit": ["40"]}, {})["items"]:
            payload = api.handle_api("GET", f"/api/jobs/{job['job_id']}/preparation", {}, {})
            total += payload["counts"]["MATCHED"] + payload["counts"]["PARTIAL"]
        return total

    before = answered()
    api.handle_api("POST", f"/api/evidence/{key}/retire", {}, {})
    assert answered() < before or before == 0


# =========================================================================
# 6. A CORRECTION IS AN OPINION, NEVER A FACT
# =========================================================================


def test_a_requirement_verdict_creates_no_claim(api: JobsApi) -> None:
    """`EVIDENCE_MISSING` is the dangerous one: it says "I have done this and it
    is not in my profile", and turning that into a claim would be this system
    inventing experience from a click."""
    job_id = _a_job(api)
    payload = api.handle_api("GET", f"/api/jobs/{job_id}/preparation", {}, {})
    signal = payload["requirements"][0]["signal_id"]

    after = api.handle_api(
        "PATCH",
        f"/api/jobs/{job_id}/requirements/{signal}",
        {},
        {"verdict": "EVIDENCE_MISSING", "note": "I did this at Acme"},
    )
    recorded = next(r for r in after["requirements"] if r["signal_id"] == signal)
    assert recorded["review"]["verdict"] == "EVIDENCE_MISSING"
    assert api.handle_api("GET", "/api/evidence", {}, {})["confirmed"] == 0


def test_a_verdict_does_not_change_the_readiness_it_disagrees_with(api: JobsApi) -> None:
    """The mapping stays visible beside the disagreement. A verdict that
    rewrote the verdict would destroy what was being disagreed with."""
    job_id = _a_job(api)
    payload = api.handle_api("GET", f"/api/jobs/{job_id}/preparation", {}, {})
    first = payload["requirements"][0]

    after = api.handle_api(
        "PATCH",
        f"/api/jobs/{job_id}/requirements/{first['signal_id']}",
        {},
        {"verdict": "DOES_NOT_SUPPORT"},
    )
    recorded = next(r for r in after["requirements"] if r["signal_id"] == first["signal_id"])
    assert recorded["readiness"] == first["readiness"]
    assert recorded["posting_quote"] == first["posting_quote"]


def test_a_verdict_can_be_withdrawn(api: JobsApi) -> None:
    job_id = _a_job(api)
    signal = api.handle_api("GET", f"/api/jobs/{job_id}/preparation", {}, {})["requirements"][0][
        "signal_id"
    ]
    api.handle_api(
        "PATCH", f"/api/jobs/{job_id}/requirements/{signal}", {}, {"verdict": "SUPPORTS"}
    )
    after = api.handle_api(
        "PATCH", f"/api/jobs/{job_id}/requirements/{signal}", {}, {"verdict": None}
    )
    assert next(r for r in after["requirements"] if r["signal_id"] == signal)["review"] is None


def test_an_absent_verdict_is_not_a_withdrawal(api: JobsApi) -> None:
    """Absence is never permission, and it is not an instruction either."""
    job_id = _a_job(api)
    signal = api.handle_api("GET", f"/api/jobs/{job_id}/preparation", {}, {})["requirements"][0][
        "signal_id"
    ]
    with pytest.raises(ApiError) as caught:
        api.handle_api("PATCH", f"/api/jobs/{job_id}/requirements/{signal}", {}, {})
    assert caught.value.status == 400


def test_an_invented_verdict_is_refused(api: JobsApi) -> None:
    job_id = _a_job(api)
    signal = api.handle_api("GET", f"/api/jobs/{job_id}/preparation", {}, {})["requirements"][0][
        "signal_id"
    ]
    with pytest.raises(ApiError) as caught:
        api.handle_api(
            "PATCH", f"/api/jobs/{job_id}/requirements/{signal}", {}, {"verdict": "PROBABLY"}
        )
    assert caught.value.status == 400


# =========================================================================
# 7. THE SURFACE ITSELF
# =========================================================================


def test_no_route_here_can_submit_an_application(api: JobsApi) -> None:
    """Section 45. The absence has to be checkable, not merely intended."""
    import ast
    import inspect

    from career_agent.web import workspace_api

    forbidden = {"httpx", "requests", "urllib", "socket", "http", "smtplib", "ftplib"}
    imported: set[str] = set()
    for node in ast.walk(ast.parse(inspect.getsource(workspace_api))):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not (imported & forbidden), f"the workspace routes import {sorted(imported & forbidden)}"


def test_only_one_place_in_this_program_confirms_a_claim(api: JobsApi) -> None:
    """Two handlers confirm, and they are the two a person acts through:
    writing a fact about herself, and standing behind one she had retired.
    Anything else setting `verified=True` would be a click that became
    experience.

    The CV path is deliberately absent from this list. It confirms through
    `cv.propose.to_claim`, the single place in that whole package which sets
    the flag, asserted separately in `tests/unit/test_cv_intake.py`.
    """
    import ast
    import inspect

    from career_agent.web import workspace_api

    tree = ast.parse(inspect.getsource(workspace_api))
    confirming: set[str] = set()
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef):
            continue
        for node in ast.walk(function):
            if (
                isinstance(node, ast.keyword)
                and node.arg == "verified"
                and isinstance(node.value, ast.Constant)
                and node.value.value is True
            ):
                confirming.add(function.name)
    assert confirming == {"create_claim", "confirm_claim"}, confirming


def test_confirming_a_retired_claim_is_its_own_act(api: JobsApi) -> None:
    """An edit does not un-retire. Standing behind a fact again is a decision,
    and a typo fix must not make it for her."""
    api.handle_api("POST", "/api/evidence", {}, {"claim_type": "SKILL", "text": "Process mapping"})
    key = api.handle_api("GET", "/api/evidence", {}, {})["claims"][0]["claim_key"]
    api.handle_api("POST", f"/api/evidence/{key}/retire", {}, {})

    edited = api.handle_api(
        "PATCH", f"/api/evidence/{key}", {}, {"text": "Process mapping and redesign"}
    )
    still_retired = next(c for c in edited["claims"] if c["claim_key"] == key)
    assert still_retired["verified"] is False, "an edit silently re-confirmed a retired claim"
    assert still_retired["text"] == "Process mapping and redesign"

    back = api.handle_api("POST", f"/api/evidence/{key}/confirm", {}, {})
    assert back["confirmed"] == 1
    assert next(c for c in back["claims"] if c["claim_key"] == key)["revision"] == 4

    with pytest.raises(ApiError) as caught:
        api.handle_api("POST", f"/api/evidence/{key}/confirm", {}, {})
    assert caught.value.status == 409


# =========================================================================
# 8. WHAT TO LEAD WITH -- selection and order, never new words
# =========================================================================


def _confirm(api: JobsApi, text: str, claim_type: str = "EMPLOYMENT") -> str:
    ledger = api.handle_api("POST", "/api/evidence", {}, {"claim_type": claim_type, "text": text})
    return ledger["claims"][0]["claim_key"]


def _resume_for_a_job_with_evidence(api: JobsApi) -> dict:
    """The first posting the confirmed claims actually speak to."""
    for job in api.handle_api("GET", "/api/jobs", {"limit": ["40"]}, {})["items"]:
        built = api.handle_api("GET", f"/api/jobs/{job['job_id']}/resume", {}, {})
        if built["lead_with"]:
            return built
    raise AssertionError("no posting in the demo corpus was answered by the claims")


def test_the_resume_plan_only_ever_returns_sentences_she_confirmed(api: JobsApi) -> None:
    """The whole guarantee. Every string is hers or a label from her own
    settings; nothing is rewritten, summarised or strengthened."""
    said = "Built automated deal-to-ticket workflows in HubSpot for the billing team"
    _confirm(api, said)
    built = _resume_for_a_job_with_evidence(api)
    for suggestion in built["lead_with"]:
        assert suggestion["text"] == said, "a suggestion said something she did not"


def test_a_claim_that_answers_more_comes_first(api: JobsApi) -> None:
    _confirm(api, "Owned HubSpot CRM architecture and built workflow automation for billing")
    _confirm(api, "Wrote runbooks for the finance team", claim_type="PROJECT")
    built = _resume_for_a_job_with_evidence(api)
    counts = [len(s["answers"]) for s in built["lead_with"]]
    assert counts == sorted(counts, reverse=True)


def test_having_done_the_work_outranks_having_named_the_tool(api: JobsApi) -> None:
    """MATCHED before PARTIAL. A document that led with the weaker one would
    be overstating it."""
    _confirm(api, "Built workflow automation across HubSpot and billing")
    _confirm(api, "n8n for orchestration", claim_type="TOOL")
    built = _resume_for_a_job_with_evidence(api)
    order = [s["strength"] for s in built["lead_with"]]
    assert order == sorted(order, key=lambda s: 0 if s == "MATCHED" else 1)


def test_every_suggestion_says_what_it_answers(api: JobsApi) -> None:
    """A reader must be able to disagree with the order rather than accept it."""
    _confirm(api, "Built workflow automation across HubSpot and billing")
    for suggestion in _resume_for_a_job_with_evidence(api)["lead_with"]:
        assert suggestion["answers"], suggestion["claim_key"]


def test_the_gaps_come_back_in_the_same_object(api: JobsApi) -> None:
    """A resume workspace returning only the strengths would be the flattering
    view the whole preparation surface exists to refuse."""
    _confirm(api, "Built workflow automation across HubSpot and billing")
    built = _resume_for_a_job_with_evidence(api)
    assert "gaps" in built
    assert built["counts"]["gaps"] == len(built["gaps"])
    assert built["gaps"], "a posting answered by one claim had no gaps at all"


def test_a_claim_this_posting_does_not_argue_for_is_kept(api: JobsApi) -> None:
    """Still true, and this posting does not argue for it. What belongs in a
    document is the person's decision, not this module's."""
    _confirm(api, "Built workflow automation across HubSpot and billing")
    _confirm(api, "Coached two junior analysts through their first quarter")
    built = _resume_for_a_job_with_evidence(api)
    kept = [claim["text"] for claim in built["not_relevant"]]
    assert "Coached two junior analysts through their first quarter" in kept


def test_a_retired_claim_is_not_suggested(api: JobsApi) -> None:
    key = _confirm(api, "Built workflow automation across HubSpot and billing")
    before = _resume_for_a_job_with_evidence(api)
    assert before["lead_with"]
    api.handle_api("POST", f"/api/evidence/{key}/retire", {}, {})
    for job in api.handle_api("GET", "/api/jobs", {"limit": ["40"]}, {})["items"]:
        built = api.handle_api("GET", f"/api/jobs/{job['job_id']}/resume", {}, {})
        assert not built["lead_with"], "a retired claim was suggested for a document"


def test_the_order_is_the_same_every_time(api: JobsApi) -> None:
    """An unstable order would make the suggestion look like an opinion that
    changes rather than a rule anybody can check."""
    _confirm(api, "Built workflow automation across HubSpot and billing")
    _confirm(api, "Owned the CRM data model and its custom objects")
    first = _resume_for_a_job_with_evidence(api)
    second = _resume_for_a_job_with_evidence(api)
    assert [s["claim_key"] for s in first["lead_with"]] == [
        s["claim_key"] for s in second["lead_with"]
    ]


def test_nothing_in_the_resume_module_can_write_a_sentence(api: JobsApi) -> None:
    """Asserted structurally. `claim.text` is read and never built: a f-string
    or a concatenation over it would be this module writing somebody's CV."""
    import ast
    import inspect

    from career_agent.match import resume

    tree = ast.parse(inspect.getsource(resume))
    for node in ast.walk(tree):
        # An f-string is the shape a composed sentence takes. There is none in
        # this module and there must not be one: every string it returns has to
        # arrive from a claim or a label, whole.
        assert not isinstance(node, ast.JoinedStr), "the resume planner formats a string"
        # And a `+` with a string literal on either side is the other shape.
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            for side in (node.left, node.right):
                assert not (isinstance(side, ast.Constant) and isinstance(side.value, str)), (
                    "the resume planner concatenates a literal into somebody's sentence"
                )
