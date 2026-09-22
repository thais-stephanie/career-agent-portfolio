"""Compensation that was collected all along, finally reaching the score.

All three providers archive a salary -- 2,865 Ashby payloads, 413 Greenhouse
ones and 167 Lever ones in the real corpus -- and nothing ever opened the
payload to look, so `has_salary` matched nothing across 18,549 postings while
roughly 3,400 of them named a number. Greenhouse was the last to be read and
the one that was read wrongly twice over: first as having no pay data at all,
then, when it turned out to have some, as a vendor whose amounts are numbers.
They are STRINGS, and every adapter's private parser rejected `str`, so the
correct-looking reader would have returned nothing on all 413. These tests
cover the seam, and the properties it must not break on the way:

1. **Vendor knowledge stays inside `providers/`.** The reader is reached
   through the registry, exactly as `field_map_for` is, and reaching it must
   not construct an HTTP client -- scoring opens no socket.
2. **Absence stays absence.** A missing maximum is None, a display string is
   never parsed into a number, a period nobody stated is not filled in, and a
   posting with no salary scores unknown rather than being rejected.
3. **A conversion is either shown or refused.** Twelve months to a year is
   arithmetic and is applied with the working written down; an exchange rate is
   an observation and is applied only when it carries the date it was observed.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.domain.enums import CollectionStatus
from career_agent.match.engine import JobFacts, match_job
from career_agent.pipeline.rescore import rescore
from career_agent.providers import ashby, greenhouse, lever
from career_agent.providers.base import (
    COMPENSATION_PERIODS,
    CompensationHint,
    parse_amount,
)
from career_agent.providers.registry import available_providers, compensation_reader_for
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.mvp_repo import SALARY_CONFIDENCE_ITEM, JobFilter, ScoredJobQuery
from career_agent.storage.records import (
    CompanyRecord,
    JobRecord,
    ProviderPayloadRecord,
    SourceBoardRecord,
)
from career_agent.storage.repositories import (
    CompanyRepo,
    JobRawRepo,
    JobRepo,
    ProviderPayloadRepo,
    SourceBoardRepo,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
#: The committed configuration, copied without the owner's private
#: overrides. A test that read `search.local.yaml` would pass or fail on
#: what is in one person's gitignored file. See `tests/support.py`.
CONFIG_DIR = committed_config_dir()

#: Enough of a posting that the matcher has something to read. The salary is
#: the variable under test; the body is held constant so any difference in the
#: score is the compensation and nothing else.
BODY = (
    "You will own our CRM architecture, build workflow automation across our "
    "business systems, and maintain REST API integrations and webhooks between "
    "them. We use n8n for orchestration. We hire globally. "
) * 6


# =========================================================================
# ASHBY: structured components, summary strings, and nothing at all
# =========================================================================


def _tier(*components: dict[str, Any]) -> dict[str, Any]:
    return {"components": list(components), "id": "tier-1", "title": None}


def _monthly(minimum: float, maximum: float) -> dict[str, Any]:
    """An Ashby payload stating a monthly band -- the period the configured
    target is expressed in, so it is the one that gets compared."""
    return {
        "compensation": {
            "compensationTiers": [_tier(_salary(minimum, maximum, interval="1 MONTH"))]
        }
    }


def _salary(
    minimum: float | None,
    maximum: float | None,
    currency: str = "USD",
    interval: str = "1 YEAR",
) -> dict[str, Any]:
    return {
        "compensationType": "Salary",
        "currencyCode": currency,
        "interval": interval,
        "minValue": minimum,
        "maxValue": maximum,
    }


def test_ashby_reads_the_structured_components() -> None:
    """The real shape, copied from a payload in the corpus."""
    payload = {
        "compensation": {
            # punctuation-check: allow: employer-written fixture
            "compensationTierSummary": "$205K – $300K • Offers Equity",
            "scrapeableCompensationSalarySummary": "$205K - $300K",
            "compensationTiers": [
                _tier(
                    {
                        "compensationType": "EquityCashValue",
                        "currencyCode": "USD",
                        "interval": "1 YEAR",
                        "minValue": None,
                        "maxValue": None,
                    },
                    _salary(205000, 300000),
                )
            ],
        }
    }

    hint = ashby.read_compensation(payload)

    assert hint is not None
    assert (hint.min_value, hint.max_value) == (205000.0, 300000.0)
    assert hint.currency == "USD"
    assert hint.period == "YEAR"
    # punctuation-check: allow: employer-written fixture
    assert hint.raw_text == "$205K – $300K • Offers Equity"
    assert hint.source_field == "compensation.compensationTiers"


@pytest.mark.parametrize(
    ("interval", "expected"),
    [("1 YEAR", "YEAR"), ("1 MONTH", "MONTH"), ("1 HOUR", "HOUR"), ("1 WEEK", None)],
)
def test_ashby_maps_only_the_intervals_the_matcher_can_compare(
    interval: str, expected: str | None
) -> None:
    """An interval outside the vocabulary is None, never the nearest guess.

    A weekly rate silently read as monthly would be compared against a monthly
    target and answer a question nobody asked.
    """
    payload = {"compensation": {"compensationTiers": [_tier(_salary(100, 200, interval=interval))]}}

    hint = ashby.read_compensation(payload)

    assert hint is not None
    assert hint.period == expected
    assert expected is None or expected in COMPENSATION_PERIODS


def test_ashby_never_parses_a_display_string_into_a_number() -> None:
    """Summary only: the text travels, the numbers do not get invented.

    "$205K" is a rounding Ashby rendered for a human. Reading 205000 back out
    of it would assert a precision the employer never stated, and would be
    wrong outright the first time a board writes "from $205K".
    """
    payload = {
        "compensation": {
            # punctuation-check: allow: employer-written fixture
            "compensationTierSummary": "$205K – $300K • Offers Equity",
            "scrapeableCompensationSalarySummary": "$205K - $300K",
            "compensationTiers": [],
        }
    }

    hint = ashby.read_compensation(payload)

    assert hint is not None
    # punctuation-check: allow: employer-written fixture
    assert hint.raw_text == "$205K – $300K • Offers Equity"
    assert hint.min_value is None and hint.max_value is None
    assert hint.has_amounts is False
    assert hint.currency is None and hint.period is None


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"compensation": None},
        {"compensation": {}},
        {"compensation": {"compensationTiers": []}},
        {"compensation": {"compensationTiers": [{"components": []}]}},
        {"compensation": "not an object"},
        "not a payload at all",
    ],
    ids=["absent", "null", "empty", "no-tiers", "no-components", "wrong-type", "not-a-dict"],
)
def test_ashby_says_nothing_when_the_payload_says_nothing(payload: Any) -> None:
    assert ashby.read_compensation(payload) is None


def test_ashby_keeps_a_stated_minimum_with_no_maximum_open() -> None:
    """Absent stays absent. Copying the min into the max would close a band the
    employer left open, and turn a floor into a ceiling."""
    payload = {"compensation": {"compensationTiers": [_tier(_salary(205000, None))]}}

    hint = ashby.read_compensation(payload)

    assert hint is not None
    assert hint.min_value == 205000.0
    assert hint.max_value is None


def test_ashby_widens_across_tiers_that_agree_on_currency_and_interval() -> None:
    """108 of the 167 multi-component payloads are one band written in pieces."""
    payload = {
        "compensation": {
            "compensationTiers": [
                _tier(_salary(120000, 150000)),
                _tier(_salary(140000, 190000)),
            ]
        }
    }

    hint = ashby.read_compensation(payload)

    assert hint is not None
    assert (hint.min_value, hint.max_value) == (120000.0, 190000.0)
    assert hint.currency == "USD"


def test_ashby_does_not_widen_across_different_currencies() -> None:
    """The documented multi-tier rule, and the case that forces it.

    59 of the 167 multi-component payloads state DIFFERENT currencies -- one
    offers GBP 90-122K, EUR 135-183K and PLN 280-378K, which are three regional
    offers rather than one range. Widening across them would produce "90,000 to
    378,000" in no currency anyone named. The first salary component fixes the
    currency and the interval; the rest stay in the payload.
    """
    payload = {
        "compensation": {
            "compensationTiers": [
                _tier(_salary(90000, 122000, currency="GBP")),
                _tier(_salary(135000, 183000, currency="EUR")),
                _tier(_salary(280000, 378000, currency="PLN")),
            ]
        }
    }

    hint = ashby.read_compensation(payload)

    assert hint is not None
    assert hint.currency == "GBP"
    assert (hint.min_value, hint.max_value) == (90000.0, 122000.0)


def test_ashby_ignores_component_types_that_are_not_salary() -> None:
    """Equity and bonus are real compensation and deliberately not added in:
    a bonus range folded into a salary range is a number no posting states."""
    payload = {
        "compensation": {
            "compensationTiers": [
                _tier(
                    {
                        "compensationType": "Bonus",
                        "currencyCode": "USD",
                        "interval": "1 YEAR",
                        "minValue": 10000,
                        "maxValue": 40000,
                    },
                    _salary(150000, 180000),
                )
            ]
        }
    }

    hint = ashby.read_compensation(payload)

    assert hint is not None
    assert (hint.min_value, hint.max_value) == (150000.0, 180000.0)


# =========================================================================
# LEVER: one flat object
# =========================================================================


def test_lever_maps_the_salary_range() -> None:
    payload = {
        "salaryRange": {
            "currency": "USD",
            "interval": "per-year-salary",
            "min": 167000,
            "max": 230000,
        },
        "salaryDescriptionPlain": "Compensation is market-driven and data-informed.",
    }

    hint = lever.read_compensation(payload)

    assert hint is not None
    assert (hint.min_value, hint.max_value) == (167000.0, 230000.0)
    assert hint.currency == "USD"
    assert hint.period == "YEAR"
    assert hint.raw_text == "Compensation is market-driven and data-informed."
    assert hint.source_field == "salaryRange"


@pytest.mark.parametrize(
    ("interval", "expected"),
    [("per-year-salary", "YEAR"), ("per-hour-wage", "HOUR"), ("bi-week-salary", None)],
)
def test_lever_leaves_a_fortnightly_rate_uncomparable(interval: str, expected: str | None) -> None:
    """4 payloads in the corpus are `bi-week-salary`. There is no honest
    conversion into the YEAR/MONTH/HOUR vocabulary, so the period is None and
    the posting scores `salary_unknown` -- with the range still readable."""
    payload = {"salaryRange": {"currency": "EUR", "interval": interval, "min": 1, "max": 2}}

    hint = lever.read_compensation(payload)

    assert hint is not None
    assert hint.period == expected


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"salaryRange": None},
        {"salaryRange": "60000"},
        {"salaryDescriptionPlain": "We pay well."},
        "not a payload at all",
    ],
    ids=["absent", "null", "wrong-type", "prose-only", "not-a-dict"],
)
def test_lever_says_nothing_without_a_structured_range(payload: Any) -> None:
    """Prose about pay is a description-channel claim, not a field to read a
    number out of. Exactly 1 posting in the corpus is prose-only."""
    assert lever.read_compensation(payload) is None


# =========================================================================
# THE REGISTRY SEAM
# =========================================================================


def test_asking_about_a_provider_with_no_adapter_returns_none_and_does_not_raise() -> None:
    """`manual_import` is why this returns None instead of raising the way
    `field_map_for` does: a pasted posting has no adapter and never will, and it
    is an ordinary row in every corpus pass.

    Greenhouse used to be the other example here, and it was the wrong one. Its
    absence from `_COMPENSATION_READERS` asserted that no archived Greenhouse
    payload carries pay, and 633 of them do. Absence is how that dict speaks, so
    an absence that is not true is worse than a missing feature.
    """
    assert "greenhouse" in available_providers()
    assert compensation_reader_for("greenhouse") is not None
    assert compensation_reader_for("manual_import") is None
    assert compensation_reader_for("") is None


def test_the_reader_is_reachable_without_a_fetcher(monkeypatch: pytest.MonkeyPatch) -> None:
    """Offline scoring must never build an HTTP client.

    `httpx.Client` is broken for the duration, so a lookup that went through
    `get_provider` would fail here rather than three months from now on someone
    else's metered connection. This is the same guarantee `field_map_for`
    carries, and the reason the reader is a registry entry and not a method on
    the provider protocol.
    """
    import httpx

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("reading compensation must not construct an HTTP client")

    monkeypatch.setattr(httpx, "Client", refuse)

    reader = compensation_reader_for("ashby")

    assert reader is not None
    hint = reader({"compensation": {"compensationTiers": [_tier(_salary(1000, 2000))]}})
    assert hint is not None and hint.min_value == 1000.0


def test_every_registered_reader_answers_none_for_an_empty_payload() -> None:
    """A reader may never treat "nothing here" as an exception."""
    for name in available_providers():
        reader = compensation_reader_for(name)
        if reader is not None:
            assert reader({}) is None


# =========================================================================
# ROUND TRIP: payload -> JobFacts -> score -> query
# =========================================================================


def _hint_facts(hint: CompensationHint | None) -> JobFacts:
    return JobFacts(
        title="Business Systems Engineer",
        description=BODY,
        salary_min=hint.min_value if hint else None,
        salary_max=hint.max_value if hint else None,
        salary_currency=hint.currency if hint else None,
        salary_period=hint.period if hint else None,
        salary_alternates=hint.alternate_bands if hint else (),
    )


def _config() -> Any:
    config, _ = load_search_config(CONFIG_DIR)
    return config


def _awarded(result: Any, item_id: str) -> bool:
    return any(item.item_id == item_id and item.awarded for item in result.confidence_items)


def _compensation_signal(result: Any) -> str:
    component = next(c for c in result.components if c.component_id == "compensation_contract")
    return next(c.signal_id for c in component.contributions if c.signal_id.startswith("salary_"))


def _with_compensation(config: Any, **updates: Any) -> Any:
    """The shipped compensation preference, with fields overridden in memory.

    The shipped config is a placeholder target of 0 with no conversion rates --
    the real numbers live in the gitignored local file -- so most of what this
    module tests is unreachable through the file as committed. Nothing here
    writes one.
    """
    compensation = config.preferences.compensation.model_copy(update=updates)
    preferences = config.preferences.model_copy(update={"compensation": compensation})
    return config.model_copy(update={"preferences": preferences})


def _with_target(config: Any, amount: float) -> Any:
    return _with_compensation(config, target_monthly_amount=amount)


def _compensation_note(result: Any) -> str:
    component = next(c for c in result.components if c.component_id == "compensation_contract")
    return component.note or ""


def test_a_stated_salary_reaches_the_score_as_the_configuration_says() -> None:
    """The configured target is a monthly USD figure.

    A monthly USD band is compared directly. An ANNUAL USD band is now also
    compared, restated per month, because twelve months to a year is the
    definition of the two words rather than an observation about the world --
    it invents nothing, and refusing it left 2,633 of the 3,032 postings that
    state a salary permanently uncomparable. A band in a currency with no
    configured rate is still `salary_unknown`, because that one WOULD be an
    invention.
    """
    config = _with_target(_config(), 10000)

    meets = ashby.read_compensation(_monthly(11000, 14000))
    below = ashby.read_compensation(_monthly(4000, 6000))
    annual_below = lever.read_compensation(
        {"salaryRange": {"currency": "USD", "interval": "per-year-salary", "min": 1, "max": 2}}
    )
    annual_meets = lever.read_compensation(
        {
            "salaryRange": {
                "currency": "USD",
                "interval": "per-year-salary",
                "min": 180000,
                "max": 240000,
            }
        }
    )
    foreign = lever.read_compensation(
        {"salaryRange": {"currency": "JPY", "interval": "per-month-salary", "min": 1, "max": 2}}
    )

    def signal(hint: CompensationHint | None) -> str:
        return _compensation_signal(match_job(config, _hint_facts(hint), computed_at="Z"))

    assert signal(meets) == "salary_meets_target"
    assert signal(below) == "salary_below_target"
    # 2 per year is 0.17 per month, which is below the target rather than
    # unknown: the comparison was possible and the answer was no.
    assert signal(annual_below) == "salary_below_target"
    # 240,000 per year is 20,000 per month, comfortably above.
    assert signal(annual_meets) == "salary_meets_target"
    # No rate is configured for JPY, so this one is genuinely uncomparable.
    assert signal(foreign) == "salary_unknown"


def test_a_posting_with_no_salary_is_unknown_and_not_rejected() -> None:
    """Compensation is a preference, never a filter. The posting still scores,
    still passes screening, and simply carries the unknown."""
    config = _config()
    silent = match_job(config, _hint_facts(None), computed_at="Z")
    stated = match_job(
        config,
        _hint_facts(ashby.read_compensation(_monthly(9000, 12000))),
        computed_at="Z",
    )

    assert _compensation_signal(silent) == "salary_unknown"
    assert silent.match_score > 0, "a missing salary must not sink the posting"
    assert not _awarded(silent, SALARY_CONFIDENCE_ITEM)
    assert _awarded(stated, SALARY_CONFIDENCE_ITEM)
    assert stated.posting_facts["salary"] == {
        "min": 9000.0,
        "max": 12000.0,
        "currency": "USD",
        "period": "MONTH",
    }


# =========================================================================
# THE WHOLE SEAM, THROUGH THE DATABASE
# =========================================================================


def _seed(
    conn: sqlite3.Connection, *, external: str, provider: str, payload: dict[str, Any]
) -> str:
    with transaction(conn):
        company_id = CompanyRepo(conn).upsert(CompanyRecord(slug="acme", name="Acme"))
        board_id = SourceBoardRepo(conn).upsert(
            SourceBoardRecord(
                company_id=company_id,
                provider=provider,
                board_identifier=f"acme-{provider}",
                board_url=f"https://example.invalid/{provider}",
                active=False,
            )
        )
        digest = JobRawRepo(conn).put(BODY)
        job_id = JobRepo(conn).upsert_seen(
            JobRecord(
                company_id=company_id,
                source_board_id=board_id,
                provider=provider,
                external_id=external,
                url=f"https://example.invalid/{external}",
                title="Business Systems Engineer",
                content_hash=digest,
            ),
            status=CollectionStatus.NORMALISED,
        )
        if payload:
            ProviderPayloadRepo(conn).put(
                ProviderPayloadRecord(job_id=job_id, provider=provider, payload=payload)
            )
    return job_id


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(tmp_path / "compensation.db")
    migrate(conn)
    return conn


def test_has_salary_finds_the_posting_that_states_one_and_only_that_one(
    db: sqlite3.Connection,
) -> None:
    """The regression this whole change exists for.

    Before it, `has_salary=true` returned nothing across 18,549 real postings
    while roughly 3,000 of them stated a number: the payload was archived, and
    nobody opened it. Two defects, both here: the payload was never read, and
    the filter looked for a confidence item id the scorer does not emit.
    """
    config = _config()
    target = config.preferences.compensation.target_monthly_amount
    paid = _seed(
        db,
        external="paid",
        provider="ashby",
        payload={
            "compensation": {
                # punctuation-check: allow: employer-written fixture
                "compensationTierSummary": "$9K – $12K / month",
                "compensationTiers": [
                    _tier(_salary(max(target, 9000), max(target, 12000), interval="1 MONTH"))
                ],
            }
        },
    )
    silent = _seed(db, external="silent", provider="ashby", payload={"compensation": {}})

    stats = rescore(db, config)
    assert stats.jobs_scored == 2 and stats.errors == 0, stats.error_samples

    query = ScoredJobQuery(db)
    with_salary = {
        job.job_id
        for job in query.page(config.config_id, config.config_version, JobFilter(has_salary=True))
    }
    without = {
        job.job_id
        for job in query.page(config.config_id, config.config_version, JobFilter(has_salary=False))
    }

    assert with_salary == {paid}
    assert without == {silent}
    db.close()


def test_a_lever_range_survives_the_round_trip_to_the_card(db: sqlite3.Connection) -> None:
    """What the card shows is what the score was computed from -- and both come
    from the provider's own reader, not from a second reading of the payload."""
    config = _config()
    job_id = _seed(
        db,
        external="lever-1",
        provider="lever",
        payload={
            "salaryRange": {
                "currency": "USD",
                "interval": "per-year-salary",
                "min": 167000,
                "max": 230000,
            },
            "salaryDescriptionPlain": "Market-driven and data-informed.",
        },
    )

    rescore(db, config)

    from career_agent.storage.mvp_repo import MatchRepo

    stored = MatchRepo(db).get(job_id, config.config_id, config.config_version)
    assert stored is not None
    assert stored.posting_facts["salary"] == {
        "min": 167000.0,
        "max": 230000.0,
        "currency": "USD",
        "period": "YEAR",
    }
    db.close()


def test_a_provider_without_a_reader_scores_without_error(db: sqlite3.Connection) -> None:
    """`manual_import` has no adapter and no payload, and must not be a special
    case anywhere: it scores, it is unknown, and it is not rejected."""
    config = _config()
    job_id = _seed(db, external="pasted", provider="manual_import", payload={})

    stats = rescore(db, config)

    assert stats.errors == 0, stats.error_samples
    from career_agent.storage.mvp_repo import MatchRepo

    stored = MatchRepo(db).get(job_id, config.config_id, config.config_version)
    assert stored is not None
    assert stored.posting_facts["salary"] is None
    assert stored.match_score > 0
    db.close()


def test_the_page_reads_payloads_in_one_query_not_one_per_job(
    db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cost guard. A per-job round trip is what turns a 5 ms/job matcher
    into an evening, and it is the kind of regression that passes every
    correctness test in this file."""
    config = _config()
    for index in range(12):
        _seed(
            db,
            external=f"j-{index}",
            provider="ashby",
            payload={"compensation": {"compensationTiers": [_tier(_salary(1000, 2000))]}},
        )

    calls: list[int] = []
    original = ProviderPayloadRepo.latest_for_jobs

    def counting(self: ProviderPayloadRepo, job_ids: Any) -> Any:
        calls.append(len(job_ids))
        return original(self, job_ids)

    monkeypatch.setattr(ProviderPayloadRepo, "latest_for_jobs", counting)

    rescore(db, config)

    assert calls == [12], f"expected one batched read for the page, got {calls}"
    db.close()


def test_latest_for_jobs_agrees_with_latest_for_job(db: sqlite3.Connection) -> None:
    """Two methods, one ordering rule. If they ever disagreed about which
    payload is current, the score and the evidence would describe different
    captures of the same posting."""
    job_id = _seed(
        db,
        external="versioned",
        provider="ashby",
        payload={"compensation": {"compensationTiers": [_tier(_salary(1000, 2000))]}},
    )
    repo = ProviderPayloadRepo(db)
    with transaction(db):
        repo.put(
            ProviderPayloadRecord(
                job_id=job_id,
                provider="ashby",
                payload={"compensation": {"compensationTiers": [_tier(_salary(3000, 4000))]}},
            )
        )

    assert repo.latest_for_jobs([job_id])[job_id] == repo.latest_for_job(job_id)
    assert repo.latest_for_jobs([]) == {}
    assert repo.latest_for_jobs(["no-such-job"]) == {}
    db.close()


# =========================================================================
# GREENHOUSE: a published range, an internal budget, and no interval at all
# =========================================================================
#
# This whole section is a correction. The commit that added compensation
# reading recorded that all 10,156 archived Greenhouse payloads carry no pay
# data, and registered no reader "on purpose". The premise it rested on is
# true -- the collector never sends `?pay_transparency=true` -- and the
# conclusion drawn from it is false: boards publish their range as an ordinary
# board custom field, which arrives under `metadata` with no flag asked for.
# 633 payloads carry one and 413 state a non-zero band.


def _currency_range(
    name: str,
    minimum: str | None,
    maximum: str | None,
    unit: str | None = "USD",
) -> dict[str, Any]:
    """One Greenhouse custom field, in the shape the corpus actually holds --
    including the amounts being STRINGS, which is the whole trap."""
    return {
        "id": 29446322,
        "name": name,
        "value": {"min_value": minimum, "max_value": maximum, "unit": unit},
        "value_type": "currency_range",
    }


def _gh(*fields: dict[str, Any]) -> dict[str, Any]:
    return {"id": 1, "title": "Business Systems Engineer", "metadata": list(fields)}


def test_greenhouse_reads_the_published_range_that_was_there_all_along() -> None:
    """Copied from job `01M0XZK7FCZR93MC1YXQNR0MZM`, which states USD
    320,000-400,000 and scored `salary_unknown` for as long as the reader was
    missing."""
    hint = greenhouse.read_compensation(
        _gh(_currency_range("Pay Transparency Range", "320000.0", "400000.0"))
    )

    assert hint is not None
    assert (hint.min_value, hint.max_value) == (320000.0, 400000.0)
    assert hint.currency == "USD"
    assert hint.source_field == "metadata.Pay Transparency Range"


def test_greenhouse_never_presents_an_internal_budget_as_a_published_offer() -> None:
    """`Baseline Budgeted Salary` is what the employer budgeted, not what it
    told candidates. Two different claims, and a card that showed one as the
    other would be putting a number in the employer's mouth.

    Measured before excluding them: 89 payloads carry a non-zero budget figure
    and every one of them also carries a published range, so reading budgets
    would add nothing but the risk.
    """
    hint = greenhouse.read_compensation(
        _gh(
            _currency_range("Baseline Budgeted Salary", "100000.0", "120000.0"),
            _currency_range("Other Large Market Budgeted Salary", "110000.0", "130000.0"),
            _currency_range("Job Post Range (United States)", "150000.0", "180000.0"),
        )
    )

    assert hint is not None
    assert (hint.min_value, hint.max_value) == (150000.0, 180000.0)
    assert hint.source_field == "metadata.Job Post Range (United States)"
    assert hint.alternate_bands == (), "a budget figure is not an alternative band"


def test_greenhouse_with_only_a_budget_figure_states_no_salary() -> None:
    """No posting in the corpus is in this shape today. If one ever is, the
    honest answer is that the employer published nothing."""
    assert (
        greenhouse.read_compensation(
            _gh(_currency_range("Baseline Budgeted Salary", "100000.0", "120000.0"))
        )
        is None
    )


def test_greenhouse_states_no_period_and_none_is_invented_for_it() -> None:
    """`currency_range` carries `min_value`, `max_value` and `unit`, and no
    interval exists anywhere in the payload.

    "Job Post Range (United States): 320,000 - 400,000 USD" is annual by
    convention, and convention is not a statement. Filling in YEAR would
    manufacture the single fact that makes the number comparable to a target,
    so the posting states a salary and scores unknown against a period nobody
    named.
    """
    hint = greenhouse.read_compensation(
        _gh(_currency_range("Pay Transparency Range", "320000.0", "400000.0"))
    )

    assert hint is not None
    assert hint.period is None
    assert hint.has_amounts is True

    result = match_job(_with_target(_config(), 10000), _hint_facts(hint), computed_at="Z")
    assert _compensation_signal(result) == "salary_unknown"
    assert "an unnamed period" in _compensation_note(result)
    assert _awarded(result, SALARY_CONFIDENCE_ITEM), "the posting did state a band"


def test_greenhouse_carries_every_country_range_the_posting_states() -> None:
    """19 payloads state two countries' ranges, always in two currencies."""
    hint = greenhouse.read_compensation(
        _gh(
            _currency_range("Job Post Range (Canada)", "150000.0", "180000.0", unit="CAD"),
            _currency_range("Job Post Range (United States)", "170000.0", "200000.0"),
        )
    )

    assert hint is not None
    assert hint.currency == "CAD"
    assert [band.currency for band in hint.bands] == ["CAD", "USD"]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"metadata": None},
        {"metadata": []},
        {"metadata": "not a list"},
        {"metadata": [{"name": "Type", "value": "Full-time", "value_type": "short_text"}]},
        # 468 payloads define the field and leave it blank on this posting.
        {"metadata": [{"name": "Pay", "value": None, "value_type": "currency_range"}]},
        "not a payload at all",
    ],
    ids=["absent", "null", "empty", "wrong-type", "other-field", "blank-field", "not-a-dict"],
)
def test_greenhouse_says_nothing_when_the_payload_says_nothing(payload: Any) -> None:
    assert greenhouse.read_compensation(payload) is None


# =========================================================================
# ONE NUMBER PARSER, SHARED -- because the copies had already gone wrong
# =========================================================================


def test_every_provider_reads_an_amount_that_arrives_as_a_string() -> None:
    """The finding this section exists for, and it is not hypothetical.

    Greenhouse states `{"min_value": "320000.0"}`. Both existing `_number`
    helpers rejected `str` outright, so a Greenhouse reader written by copying
    either of them would have returned None on all 413 postings that state a
    range -- silently, with every test still green. The same is one
    serialisation change away for the other two vendors, which is why the
    parser is shared and why this test asks all three.
    """
    assert ashby.read_compensation(
        {
            "compensation": {
                "compensationTiers": [
                    _tier(
                        {
                            "compensationType": "Salary",
                            "currencyCode": "USD",
                            "interval": "1 YEAR",
                            "minValue": "205000",
                            "maxValue": "300000.0",
                        }
                    )
                ]
            }
        }
    ) == CompensationHint(
        source_field="compensation.compensationTiers",
        min_value=205000.0,
        max_value=300000.0,
        currency="USD",
        period="YEAR",
    )

    lever_hint = lever.read_compensation(
        {
            "salaryRange": {
                "currency": "USD",
                "interval": "per-year-salary",
                "min": "167000",
                "max": "230000.0",
            }
        }
    )
    assert lever_hint is not None
    assert (lever_hint.min_value, lever_hint.max_value) == (167000.0, 230000.0)

    greenhouse_hint = greenhouse.read_compensation(
        _gh(_currency_range("Pay Transparency Range", "320000.0", "400000.0"))
    )
    assert greenhouse_hint is not None
    assert (greenhouse_hint.min_value, greenhouse_hint.max_value) == (320000.0, 400000.0)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (205000, 205000.0),
        (205000.5, 205000.5),
        ("205000", 205000.0),
        ("  320000.0  ", 320000.0),
        ("-5", -5.0),
        (True, None),
        (False, None),
        (None, None),
        ("", None),
        ("205K", None),
        ("$205000", None),
        ("320,000", None),
        ("2e5", None),
        ("inf", None),
        ("nan", None),
        (float("inf"), None),
        ({"min": 1}, None),
    ],
)
def test_parse_amount_reads_a_plain_number_and_refuses_everything_else(
    value: Any, expected: float | None
) -> None:
    """`True` must never become a salary of 1.0, and "205K" must never become
    205000 -- one is a type accident, the other is an interpretation of a
    display string. Both return None, which scores unknown."""
    assert parse_amount(value) == expected


# =========================================================================
# WHICH BAND, AND WHETHER IT IS A BAND AT ALL
# =========================================================================


def test_the_scorer_picks_the_band_in_the_configured_currency() -> None:
    """Payload order is not relevance.

    Ashby posting `01M0XZKEGAPG0EY02HK36QBTW8` states EUR 110,500-181,500 first
    and USD 170,350-275,550 second. The reader used to keep only the first, so
    a USD-targeting candidate was told the posting stated nothing comparable
    while it stated a band in their own currency. The reader cannot choose --
    it does not know the preference -- so it carries both and the scorer picks.
    """
    payload = {
        "compensation": {
            "compensationTiers": [
                _tier(_salary(110500, 181500, currency="EUR")),
                _tier(_salary(170350, 275550, currency="USD")),
            ]
        }
    }

    hint = ashby.read_compensation(payload)

    assert hint is not None
    assert hint.currency == "EUR", "the lead is still the first band the payload stated"
    assert [band.currency for band in hint.bands] == ["EUR", "USD"]

    # 275,550 USD per year is 22,962.50 per month, above a 10,000 target. Under
    # the old reader this posting was `salary_unknown`.
    usd = _with_target(_config(), 10000)
    assert _compensation_signal(match_job(usd, _hint_facts(hint), computed_at="Z")) == (
        "salary_meets_target"
    )

    # A candidate targeting neither currency still gets the lead band, and the
    # honest "no rate configured" answer about it.
    other = _with_compensation(_config(), currency="JPY", target_monthly_amount=1)
    unknown = match_job(other, _hint_facts(hint), computed_at="Z")
    assert _compensation_signal(unknown) == "salary_unknown"
    assert "stated in EUR" in _compensation_note(unknown)


def test_an_inverted_band_falls_back_to_one_an_employer_actually_wrote() -> None:
    """Two individually sane components can widen into nonsense.

    `[{min: 200000, max: None}, {min: None, max: 100000}]` gives min-of-mins
    200,000 and max-of-maxes 100,000, and the card would read "200,000 -
    100,000" while the scorer compared the 100,000. No payload in the corpus is
    in this shape today, which is exactly when it is cheap to refuse.
    """
    payload = {
        "compensation": {
            "compensationTiers": [
                _tier(_salary(200000, None)),
                _tier(_salary(None, 100000)),
            ]
        }
    }

    hint = ashby.read_compensation(payload)

    assert hint is not None
    assert hint.min_value is not None and hint.max_value is None
    assert hint.min_value == 200000.0, "the first component's own band"


def test_an_all_zero_band_is_not_a_stated_salary() -> None:
    """Lever posting `01M0XZMM88Y90B13ZBHP127VEA` states `{min: 0, max: 0}`.

    It was awarding the `salary_known` confidence point and rendering a card
    that read "0 - 0". A zero FLOOR beside a real ceiling survives, because
    `0 - 100000` is a range an employer could mean; only the all-zero case is
    unambiguously a placeholder for "no range".
    """
    placeholder = lever.read_compensation(
        {"salaryRange": {"currency": "USD", "interval": "per-year-salary", "min": 0, "max": 0}}
    )
    assert placeholder is None

    open_floor = lever.read_compensation(
        {"salaryRange": {"currency": "USD", "interval": "per-year-salary", "min": 0, "max": 100000}}
    )
    assert open_floor is not None
    assert (open_floor.min_value, open_floor.max_value) == (0.0, 100000.0)

    greenhouse_placeholder = greenhouse.read_compensation(
        _gh(_currency_range("Pay Transparency Range", "0.0", "0.0"))
    )
    assert greenhouse_placeholder is None, "220 Greenhouse payloads are in exactly this shape"

    result = match_job(_config(), _hint_facts(placeholder), computed_at="Z")
    assert not _awarded(result, SALARY_CONFIDENCE_ITEM)


# =========================================================================
# CONVERSION: shown, or refused
# =========================================================================


def test_an_annual_salary_is_compared_per_month_and_the_note_shows_it() -> None:
    """Twelve months to a year is the definition of the words, not an
    observation about the world, so no dated rate is needed and nothing is
    manufactured. Without it, 2,633 of the 3,032 postings that state a salary
    were permanently uncomparable to a monthly target.
    """
    config = _with_target(_config(), 10000)
    hint = lever.read_compensation(
        {
            "salaryRange": {
                "currency": "USD",
                "interval": "per-year-salary",
                "min": 96000,
                "max": 120000,
            }
        }
    )

    result = match_job(config, _hint_facts(hint), computed_at="Z")

    assert _compensation_signal(result) == "salary_meets_target"
    note = _compensation_note(result)
    assert "Stated per YEAR" in note and "10,000 per MONTH" in note


def test_an_hourly_rate_is_never_converted_into_a_month() -> None:
    """Hourly-to-monthly needs an assumed full-time week, and no posting states
    one. 52 Ashby payloads and 5 Lever ones are hourly; all stay unknown."""
    config = _with_target(_config(), 10000)
    hint = lever.read_compensation(
        {"salaryRange": {"currency": "USD", "interval": "per-hour-wage", "min": 90, "max": 120}}
    )

    result = match_job(config, _hint_facts(hint), computed_at="Z")

    assert _compensation_signal(result) == "salary_unknown"
    assert "stated per HOUR" in _compensation_note(result)


def test_a_dated_conversion_rate_is_applied_and_the_arithmetic_is_readable() -> None:
    """The finding: a configured rate made a currency "comparable" and was then
    never applied.

    Target BRL 15,000 per month, rate 5.4 BRL per USD, posting offering USD
    3,500-4,000 per month. The old code took the "comparable" branch and
    compared 4,000 against 15,000, so the candidate saw a job rejected on a
    number nobody had converted. 4,000 x 5.4 is 21,600, comfortably above.
    """
    config = _with_compensation(
        _config(),
        currency="BRL",
        period="MONTH",
        target_monthly_amount=15000,
        conversion_rates={"USD": 5.4},
        conversion_rates_dated="2026-09-03",
    )
    hint = ashby.read_compensation(_monthly(3500, 4000))

    result = match_job(config, _hint_facts(hint), computed_at="Z")

    assert _compensation_signal(result) == "salary_meets_target"
    note = _compensation_note(result)
    assert "5.4 BRL per USD" in note
    assert "2026-09-03" in note
    assert "BRL 21,600" in note


def test_an_undated_conversion_rate_is_refused_rather_than_used() -> None:
    """An undated rate is a manufactured fact wearing a number's clothes. The
    posting scores unknown and the note says which half is missing."""
    config = _with_compensation(
        _config(),
        currency="BRL",
        period="MONTH",
        target_monthly_amount=15000,
        conversion_rates={"USD": 5.4},
        conversion_rates_dated=None,
    )
    hint = ashby.read_compensation(_monthly(3500, 4000))

    result = match_job(config, _hint_facts(hint), computed_at="Z")

    assert _compensation_signal(result) == "salary_unknown"
    assert "conversion_rates_dated" in _compensation_note(result)


def test_a_dated_rate_still_reports_a_salary_that_falls_short() -> None:
    """The conversion is not a way of passing. It is a way of comparing."""
    config = _with_compensation(
        _config(),
        currency="BRL",
        period="MONTH",
        target_monthly_amount=30000,
        conversion_rates={"USD": 5.4},
        conversion_rates_dated="2026-09-03",
    )
    hint = ashby.read_compensation(_monthly(3500, 4000))

    result = match_job(config, _hint_facts(hint), computed_at="Z")

    assert _compensation_signal(result) == "salary_below_target"
    assert "BRL 21,600" in _compensation_note(result)
