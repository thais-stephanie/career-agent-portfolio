"""The deterministic publication gate, attacked.

Every provider is untrusted. A provider answer is text; nothing in it becomes
Search Fit evidence unless its shape parses, its intent ids were the ones we
sent, and its quotes are text the posting really contains. These cases are
what a careless, confused or adversarial model might send back.
"""

from __future__ import annotations

import json

import pytest

from career_agent.semantic.contract import (
    AnswerRejected,
    Strength,
    Verdict,
    contract_identity,
    parse_answer,
    posting_text,
    user_message,
)
from career_agent.semantic.gate import locate, publish
from career_agent.semantic.intent import Aspect, IntentItem, SearchIntent

POSTING = (
    "About us: we are a friendly company that loves automation.\n\n"
    "What you will do:\n"
    "- Design systems that remove manual handoffs across revenue\n  operations.\n"
    "- Maintain our HubSpot portal and its webhooks.\n"
    "Benefits: free lunch."
)
INTENT = SearchIntent(
    items=(
        IntentItem("W1", Aspect.WORK, "workflow_automation", "Workflow automation"),
        IntentItem("W2", Aspect.WORK, "crm_architecture", "CRM architecture"),
        IntentItem("T1", Aspect.TOOLS, "hubspot", "HubSpot"),
        IntentItem("T2", Aspect.TOOLS, "webhooks", "Webhooks"),
    )
)


def answer(**aspects: dict) -> str:
    empty = {"verdict": "unresolved", "matches": []}
    body = {"role_core": "x", "work": empty, "tools": empty, "other": empty}
    body.update(aspects)
    return json.dumps(body)


def match(intent_id: str, *quotes: str, strength: str = "strong") -> dict:
    return {"intent_id": intent_id, "strength": strength, "quotes": list(quotes)}


def published(raw: str):
    return publish(parse_answer(raw), INTENT, POSTING)


# =========================================================================
# quotes
# =========================================================================


def test_an_exact_quote_is_published_verbatim() -> None:
    raw = answer(
        tools={"verdict": "strong", "matches": [match("T1", "Maintain our HubSpot portal")]}
    )
    out = published(raw)
    (found,) = out.matches
    assert found.quotes == ("Maintain our HubSpot portal",)
    assert found.signal_id == "hubspot" and found.strength is Strength.STRONG
    assert out.report.total == 0


def test_a_collapsed_line_break_finds_and_stores_the_original_span() -> None:
    """Models fold a line break into a space. The stored quote is still the
    posting's own text, so it remains a verbatim substring."""
    quote = "remove manual handoffs across revenue operations."
    raw = answer(work={"verdict": "strong", "matches": [match("W1", quote)]})
    (found,) = published(raw).matches
    assert found.quotes[0] in POSTING
    assert found.quotes[0] != quote, "the original line break must survive"
    assert locate(quote, POSTING) == found.quotes[0]


@pytest.mark.parametrize(
    "quote",
    [
        "Designs systems that remove manual handoffs",  # paraphrase
        "design systems that remove manual handoffs",  # case changed
        "Maintain our HubSpot portal and its webhooks!",  # punctuation added
        "HubSpot portal ... webhooks",  # joined pieces
        "Maintain our Salesforce portal",  # invented
        "Hu",  # too short to prove anything
        "x" * 500,  # absurdly long
    ],
)
def test_a_quote_the_posting_does_not_contain_is_refused(quote: str) -> None:
    raw = answer(tools={"verdict": "strong", "matches": [match("T1", quote)]})
    out = published(raw)
    assert out.matches == ()
    assert out.aspect(Aspect.TOOLS).verdict is Verdict.UNRESOLVED
    assert out.report.rejected.get("match_without_verifiable_quote") == 1


def test_one_good_quote_among_bad_ones_is_enough_and_only_it_is_kept() -> None:
    raw = answer(
        tools={
            "verdict": "strong",
            "matches": [match("T2", "invented sentence", "its webhooks")],
        }
    )
    (found,) = published(raw).matches
    assert found.quotes == ("its webhooks",)


def test_a_title_quote_is_not_evidence() -> None:
    """The title is sent for orientation and is never quotable: the title
    buying fit is the one thing Search Fit never allows."""
    message = json.loads(user_message(INTENT, "Workflow Automation Lead", POSTING))
    assert message["posting"] == POSTING
    raw = answer(work={"verdict": "strong", "matches": [match("W1", "Workflow Automation Lead")]})
    assert published(raw).matches == ()


def test_extra_quotes_beyond_the_limit_are_ignored_and_counted() -> None:
    quotes = ["its webhooks", "Maintain our HubSpot", "HubSpot portal", "our HubSpot"]
    raw = answer(tools={"verdict": "strong", "matches": [match("T2", *quotes)]})
    out = published(raw)
    assert len(out.matches[0].quotes) == 3
    assert out.report.rejected["extra_quotes_ignored"] == 1


# =========================================================================
# ids
# =========================================================================


def test_an_invented_intent_id_is_refused() -> None:
    raw = answer(work={"verdict": "strong", "matches": [match("W9", "its webhooks")]})
    out = published(raw)
    assert out.matches == ()
    assert out.report.rejected["unknown_intent_id"] == 1


def test_an_id_answered_under_the_wrong_list_is_refused() -> None:
    raw = answer(work={"verdict": "strong", "matches": [match("T1", "Maintain our HubSpot")]})
    out = published(raw)
    assert out.matches == ()
    assert out.report.rejected["intent_id_in_wrong_list"] == 1


def test_a_match_on_a_list_nobody_configured_is_refused() -> None:
    raw = answer(other={"verdict": "strong", "matches": [match("O1", "free lunch")]})
    out = published(raw)
    assert out.aspect(Aspect.OTHER).verdict is Verdict.UNRESOLVED
    assert out.report.rejected["match_on_unconfigured_list"] == 1


def test_one_intent_item_is_published_once_keeping_the_stronger_reading() -> None:
    raw = answer(
        tools={
            "verdict": "strong",
            "matches": [
                match("T1", "our HubSpot portal", strength="partial"),
                match("T1", "Maintain our HubSpot portal"),
            ],
        }
    )
    out = published(raw)
    (found,) = out.matches
    assert found.strength is Strength.STRONG
    assert out.report.rejected["duplicate_intent_id"] == 1


# =========================================================================
# verdicts
# =========================================================================


def test_a_positive_verdict_with_nothing_checkable_becomes_unresolved() -> None:
    """Never 'no fit' and never fit: no semantic evidence."""
    raw = answer(work={"verdict": "strong", "matches": []})
    out = published(raw)
    assert out.aspect(Aspect.WORK).verdict is Verdict.UNRESOLVED
    assert out.report.rejected["positive_verdict_without_evidence"] == 1


def test_matches_under_a_none_verdict_are_judged_on_their_own_evidence() -> None:
    raw = answer(
        tools={"verdict": "none", "matches": [match("T2", "its webhooks", strength="partial")]}
    )
    out = published(raw)
    assert out.aspect(Aspect.TOOLS).verdict is Verdict.PARTIAL
    assert out.report.rejected["verdict_contradicts_matches"] == 1


def test_the_published_verdict_never_exceeds_the_published_matches() -> None:
    raw = answer(
        tools={"verdict": "strong", "matches": [match("T2", "its webhooks", strength="partial")]}
    )
    assert published(raw).aspect(Aspect.TOOLS).verdict is Verdict.PARTIAL


def test_a_plain_none_is_kept_as_none() -> None:
    raw = answer(work={"verdict": "none", "matches": []})
    assert published(raw).aspect(Aspect.WORK).verdict is Verdict.NONE


# =========================================================================
# shape
# =========================================================================


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "not json",
        "[1, 2]",
        '{"work": {"verdict": "strong", "matches": []}}',  # lists missing
        answer(work={"verdict": "certain", "matches": []}),  # unknown verdict
        answer(work={"verdict": "strong", "matches": [{"intent_id": "W1", "strength": "huge"}]}),
        json.dumps({**json.loads(answer()), "provider_confidence": 7}),
    ],
)
def test_a_malformed_answer_is_rejected_whole(raw: str) -> None:
    with pytest.raises(AnswerRejected):
        parse_answer(raw)


def test_a_code_fence_around_valid_json_is_tolerated() -> None:
    parsed = parse_answer("```json\n" + answer() + "\n```")
    assert parsed.work.verdict is Verdict.UNRESOLVED


def test_provider_confidence_is_metadata_and_never_reaches_findings() -> None:
    raw = json.dumps({**json.loads(answer()), "provider_confidence": 0.99})
    parsed = parse_answer(raw)
    assert parsed.provider_confidence == 0.99
    out = publish(parsed, INTENT, POSTING)
    assert not hasattr(out, "provider_confidence")
    assert out.matches == ()


# =========================================================================
# identity
# =========================================================================


def test_only_the_sent_text_is_quotable() -> None:
    long_posting = "a" * 20_000 + " unique tail sentence"
    assert "unique tail sentence" not in posting_text(long_posting)


def test_the_contract_identity_moves_when_the_instructions_move(monkeypatch) -> None:
    from career_agent.semantic import contract

    before = contract_identity()
    monkeypatch.setattr(contract, "SYSTEM_PROMPT", contract.SYSTEM_PROMPT + " ")
    assert contract.contract_identity() != before


def test_the_intent_digest_moves_when_an_item_moves() -> None:
    renamed = SearchIntent(
        items=(*INTENT.items[:-1], IntentItem("T2", Aspect.TOOLS, "webhooks", "Webhook APIs"))
    )
    assert renamed.digest != INTENT.digest


def test_the_provider_sees_intent_ids_and_text_but_never_signal_ids() -> None:
    sent = json.dumps(INTENT.for_provider())
    assert "W1" in sent and "Workflow automation" in sent
    assert "workflow_automation" not in sent


@pytest.mark.parametrize("quote", ["the", "and to", "de la"])
def test_function_words_alone_are_never_evidence(quote: str) -> None:
    posting = "Use the ledger and to de la things"
    assert locate(quote, posting) is None


def test_a_quote_must_sit_on_word_boundaries() -> None:
    assert locate("SQL", "Strong MySQLi skills") is None
    assert locate("SQL", "Strong SQL skills") == "SQL"


def test_a_work_quote_needs_words_enough_to_state_work() -> None:
    raw = answer(work={"verdict": "strong", "matches": [match("W2", "HubSpot")]})
    assert published(raw).matches == ()


def test_the_published_finding_knows_its_sentence() -> None:
    raw = answer(
        work={"verdict": "strong", "matches": [match("W2", "Maintain our HubSpot portal")]}
    )
    (found,) = published(raw).matches
    from career_agent.match.text import sentence_at

    at = POSTING.index("Maintain our HubSpot portal")
    assert found.sentence == sentence_at(POSTING, at, at + len("Maintain our HubSpot portal"))
    assert "webhooks" in found.sentence


def test_the_sentence_is_the_one_the_accepted_span_sits_in() -> None:
    posting = "Strong MySQLi skills.\nYou will write SQL every day."
    intent = SearchIntent(items=(IntentItem("T1", Aspect.TOOLS, "sql", "SQL"),))
    raw = answer(tools={"verdict": "strong", "matches": [match("T1", "SQL")]})
    (found,) = publish(parse_answer(raw), intent, posting).matches
    assert "every day" in found.sentence
