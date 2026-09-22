"""Greenhouse job board adapter.

Greenhouse publishes every company's board as public JSON with no key and no
account: the same information any visitor can read, in a machine-readable form.

    GET /v1/boards/{token}/jobs?content=true

`content=true` returns every posting *with its full description* in a single
request. That is one HTTP call per company rather than one per posting -- much
politer, and cacheable as a unit.

This module translates Greenhouse's shape into the provider-neutral objects in
`base.py`. It contains no judgement of any kind. When Greenhouse says
`location.name = "Remote - United States"`, this adapter records that string and
stops. Whether it means the candidate can or cannot work there is decided much
later, by M3 resolution, after weighing it against what the description itself
says -- and the two frequently disagree.
"""

import re
from collections.abc import Iterator
from typing import Any

from career_agent.domain.enums import MetadataDimension
from career_agent.domain.normalize import html_to_text
from career_agent.domain.timestamps import to_rfc3339_utc
from career_agent.net.fetcher import FetchError, FetchErrorCategory, HttpFetcher
from career_agent.providers.base import (
    BoardRef,
    CompensationBand,
    CompensationHint,
    FieldMapping,
    JobProvider,
    PostingStub,
    ProviderCapabilities,
    ProviderFieldMap,
    ProviderKind,
    RawPosting,
    parse_amount,
    stated_amounts,
)

#: The custom-field `value_type` that holds a pay band. Greenhouse's board
#: custom fields are otherwise `single_select`, `short_text`, `number` and nine
#: more; this is the only one that is a range of money.
CURRENCY_RANGE_TYPE = "currency_range"

#: Custom fields that state an INTERNAL BUDGET rather than a published offer.
#: Both are `currency_range` and neither is what the employer told candidates.
#: Read `read_compensation` for the measurement behind excluding them.
BUDGET_FIELD_NAMES: frozenset[str] = frozenset(
    {"Baseline Budgeted Salary", "Other Large Market Budgeted Salary"}
)

API_BASE = "https://boards-api.greenhouse.io/v1/boards"
PUBLIC_BOARD_BASE = "https://boards.greenhouse.io"

GREENHOUSE_CAPABILITIES = ProviderCapabilities(
    # One request returns every description.
    full_description_in_list=True,
    # `?content=true` returns the employer's whole posting body in the
    # list response. Nothing is truncated and no second request exists to
    # be missing.
    obtains_full_description=True,
    # MEASURED, not assumed. The M1A brief guessed that `first_published` was
    # absent on many boards and declared this False. Auditing 414 real postings
    # across 11 boards found it present on 414/414, and genuinely different from
    # `updated_at` on 344 of them (83%) -- so it is a real publication date, not
    # a modification time in disguise. Declaring True means a missing date reads
    # as genuinely unknown rather than as a provider limitation.
    exposes_posted_date=True,
    # Also measured: `departments` is present on 412/414 postings in the
    # content=true list response. The brief assumed this needed the per-job
    # detail endpoint; it does not.
    exposes_department=True,
    # MEASURED, and it was False on a wrong conclusion rather than a wrong
    # observation. The list call does not send `?pay_transparency=true`, which
    # is true and was taken to mean no archived payload could carry pay. It
    # does: pay ranges also arrive as ordinary board custom fields under
    # `metadata`, with no flag, and 633 of the 10,156 archived payloads carry
    # one. See `read_compensation` and ADR-0007.
    exposes_compensation=True,
    # location.name is free text ("Remote - United States", "London or Remote"),
    # not a structured field. Interpreting it is M2/M3 work.
    exposes_location_structured=False,
    exposes_remote_flag=False,
    exposes_employment_type=False,
)


GREENHOUSE_FIELD_MAP = ProviderFieldMap(
    (
        # One entry, and that is the honest answer for a provider whose
        # compensation, remote-flag, employment-type and structured-location
        # capabilities are all False. Greenhouse fits because its map is
        # SMALLER, not because anything special-cases it: the three dimensions
        # it never mentions are simply absent from `dimensions()`, which reads
        # as "this vendor cannot express work model" rather than as "no
        # Greenhouse posting is remote".
        #
        # Mapped even though `exposes_location_structured` is False, because
        # those answer different questions. The capability says the value is
        # free text ("Remote - United States", "London or Remote") rather than a
        # structured code. The mapping says that free text is still the
        # provider's location assertion. Mapping it does not upgrade it.
        FieldMapping("location.name", MetadataDimension.HIRING_LOCATION_HINT),
    )
)

# `offices[]` stays unmapped, as M1A deferred it: archived, never interpreted.
# `departments[]` is unmapped too -- it is already carried as
# `PostingStub.department`, and org structure is not an eligibility dimension.
#
# `metadata[]` stays unmapped as a DIMENSION and is now read for NUMBERS by
# `read_compensation` below. Those are different questions: a field map says
# which path carries a canonical dimension, and pay is not a dimension, it is
# an amount. This is the same split Ashby and Lever already have.


class GreenhouseProvider(JobProvider):
    name = "greenhouse"
    kind = ProviderKind.ATS
    capabilities = GREENHOUSE_CAPABILITIES
    field_map = GREENHOUSE_FIELD_MAP

    def __init__(self, fetcher: HttpFetcher, api_base: str = API_BASE) -> None:
        self._fetcher = fetcher
        self._api_base = api_base.rstrip("/")

    # -- URLs --------------------------------------------------------------

    def board_url(self, board: BoardRef) -> str:
        return board.board_url or f"{PUBLIC_BOARD_BASE}/{board.board_identifier}"

    def jobs_url(self, board: BoardRef) -> str:
        return f"{self._api_base}/{board.board_identifier}/jobs?content=true"

    # -- listing -----------------------------------------------------------

    def list_postings(self, board: BoardRef) -> Iterator[PostingStub]:
        """Every posting on the board.

        Raises FetchError on failure. A board that legitimately has no openings
        yields nothing and does NOT raise -- that difference is what keeps the
        closing logic safe.
        """
        url = self.jobs_url(board)
        payload = self._fetcher.get_json(url)

        if not isinstance(payload, dict) or "jobs" not in payload:
            raise FetchError(
                FetchErrorCategory.MALFORMED,
                url,
                "response did not contain a 'jobs' key",
            )
        jobs = payload["jobs"]
        if not isinstance(jobs, list):
            raise FetchError(FetchErrorCategory.MALFORMED, url, "'jobs' was not a list")

        for entry in jobs:
            stub = self._to_stub(entry, board)
            if stub is None:
                # COUNTED, not silent. See `JobProvider.postings_skipped`.
                self.postings_skipped += 1
                continue
            yield stub

    def _to_stub(self, entry: Any, board: BoardRef) -> PostingStub | None:
        """Translate one Greenhouse job object.

        A single unusable entry is skipped rather than failing the whole board:
        losing one malformed posting is better than losing a company. The
        skipped count surfaces in the run statistics, so it is never silent.
        """
        if not isinstance(entry, dict):
            return None
        external_id = entry.get("id")
        title = entry.get("title")
        if external_id is None or not title:
            return None

        location = entry.get("location")
        location_raw = location.get("name") if isinstance(location, dict) else None

        return PostingStub(
            external_id=str(external_id),
            title=str(title),
            url=str(entry.get("absolute_url") or f"{self.board_url(board)}/jobs/{external_id}"),
            location_raw=location_raw,
            department=_department(entry),
            # Deliberately NOT entry["updated_at"]: a modification time is not a
            # publication date, and pretending otherwise would silently make
            # every edited posting look brand new.
            posted_at=_first_published(entry),
            description_html=entry.get("content") or "",
            payload=entry,
        )

    # -- completing a posting ---------------------------------------------

    def fetch_posting(self, board: BoardRef, stub: PostingStub) -> RawPosting:
        """Complete a stub. No second request: content=true already supplied it."""
        html = stub.description_html or ""
        return RawPosting(
            stub=stub,
            description_html=html,
            description_text=html_to_text(html),
            payload=stub.payload,
        )


#: Every payload path `read_compensation` reads, declared so the neutrality
#: guard can cover what it can. See `providers.registry.declared_payload_paths`
#: and the note there about `metadata`.
COMPENSATION_PATHS: tuple[str, ...] = ("metadata", CURRENCY_RANGE_TYPE)


def read_compensation(payload: Any) -> CompensationHint | None:
    """What this stored Greenhouse payload says about pay. No network, no guessing.

    **This reader exists because the previous conclusion was wrong.** The
    collector does not send `?pay_transparency=true` -- that part is correct and
    unchanged, `jobs_url` still asks only for `content=true`. What did not
    follow is that no archived payload carries pay. Boards publish their range
    as an ordinary custom field, which arrives under `metadata` with no flag
    asked for: **633 of the 10,156 archived payloads carry a `currency_range`
    value, and 413 of those state a non-zero range.** Job
    `01M0XZK7FCZR93MC1YXQNR0MZM` states USD 320,000-400,000 and was scoring
    `salary_unknown` the whole time.

    **The amounts are STRINGS** -- `{"min_value": "320000.0"}` -- which is why
    `parse_amount` is shared rather than copied. Every adapter's private number
    parser rejected `str`, so a reader written by copying one would have
    returned None on all 413 postings and looked implemented.

    **A published range and a budget figure are different claims.** The same
    list carries `Baseline Budgeted Salary` and `Other Large Market Budgeted
    Salary` -- what the employer budgeted internally, not what it published as
    the offer -- and presenting one as the other would put a number on a card
    that the posting never made to a candidate. They are not read.
    `source_field` records which field the numbers came from, so a score can be
    audited back to the employer's own label. Measured before deciding: 89
    payloads carry a non-zero budget figure and **every one of them also
    carries a published range**, so excluding budget fields costs zero
    coverage. If that ever stops being true the count is the thing to re-run.

    **The period is None, always, and that is the honest answer.** Greenhouse's
    `currency_range` states `min_value`, `max_value` and `unit`, and no
    interval anywhere in the payload. "Job Post Range (United States): 320,000
    - 400,000 USD" is annual by convention, and convention is not a statement.
    Filling in YEAR would manufacture the one fact that makes the number
    comparable to a target. So these postings state a salary -- `has_salary`
    matches them, the card shows the band -- and score `salary_unknown` against
    a period they never named. `?pay_transparency=true` returns Greenhouse's
    own structured `pay_input_ranges`, which is where an interval would come
    from; collecting it is the fix, and it would presumably widen coverage well
    beyond 633.

    A payload stating two countries' ranges -- 19 do, always in two different
    currencies -- carries both, lead first, so a candidate targeting either one
    is compared against the band in their own currency.
    """
    if not isinstance(payload, dict):
        return None
    entries = payload.get("metadata")
    if not isinstance(entries, list):
        return None

    bands: list[tuple[str, CompensationBand]] = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("value_type") != CURRENCY_RANGE_TYPE:
            continue
        name = str(entry.get("name") or "").strip()
        if name in BUDGET_FIELD_NAMES:
            continue
        value = entry.get("value")
        if not isinstance(value, dict):
            # `value: null` on a field the board defined but this posting left
            # blank. An empty custom field is not a range.
            continue
        minimum, maximum = stated_amounts(
            parse_amount(value.get("min_value")), parse_amount(value.get("max_value"))
        )
        if minimum is None and maximum is None:
            continue
        bands.append(
            (
                name,
                CompensationBand(
                    min_value=minimum,
                    max_value=maximum,
                    # 61 of the 633 state no unit. A number with no currency is
                    # not comparable to anything, and None says so.
                    currency=_text(value.get("unit")),
                    period=None,
                ),
            )
        )

    if not bands:
        return None

    (lead_name, lead), *alternates = bands
    return CompensationHint(
        source_field=f"metadata.{lead_name}" if lead_name else "metadata",
        min_value=lead.min_value,
        max_value=lead.max_value,
        currency=lead.currency,
        period=lead.period,
        alternate_bands=tuple(band for _, band in alternates),
    )


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _first_published(entry: dict[str, Any]) -> str | None:
    """Greenhouse's publication timestamp, normalised to UTC.

    Deliberately NOT `updated_at`: a modification time is not a publication
    date, and using it would make every edited posting look brand new. Measured
    across 414 real postings, the two differ on 83% of them.

    Greenhouse states this in the board's local offset --
    `2026-02-28T09:04:24-05:00`. Under the M1B.1 contract `posted_at` is UTC, so
    it becomes `2026-02-28T14:04:24+00:00` and compares directly against Lever's
    converted epoch. The provider's own encoding is untouched in the archived
    payload.
    """
    return to_rfc3339_utc(entry.get("first_published"))


def _department(entry: dict[str, Any]) -> str | None:
    """The posting's department name(s), from the list response.

    Greenhouse returns a list of objects with hierarchy (`parent_id`,
    `child_ids`). M1A takes only the names and joins them: the hierarchy is
    preserved in the archived payload for whenever something actually needs it,
    and inventing a use for it now would be interpretation rather than
    translation.
    """
    departments = entry.get("departments")
    if not isinstance(departments, list):
        return None
    names = [
        str(d["name"]).strip()
        for d in departments
        if isinstance(d, dict) and d.get("name") and str(d["name"]).strip()
    ]
    return " / ".join(names) if names else None


# -- recognising our own posting URLs -------------------------------------

#: Greenhouse is the awkward one, and the reason this is a per-adapter
#: capability rather than one shared regex.
#:
#: An employer may host the board on their own domain
#: (`careers.datadoghq.com/detail/1497543/?gh_jid=1497543`), on
#: `boards.greenhouse.io/<board>/jobs/<id>`, or on
#: `job-boards.greenhouse.io/<board>/jobs/<id>`. The only thing common to all
#: three is the NUMERIC posting id, which appears either as the `gh_jid` query
#: parameter or as the last path segment after `/jobs/`.
#:
#: So this reads the query string, which `canonical_url` deliberately discards.
#: That is not a contradiction: `canonical_url` answers "are these two URLs the
#: same string", and this answers "which posting is this URL about". The second
#: question needs the parameter that carries the answer.
_PATH_ID = re.compile(
    r"^https?://[^/]+/(?:[^/]+/)?(?:jobs|detail)/(?P<id>\d{4,})/?$",
    re.IGNORECASE,
)


def recognise_posting_url(url: str) -> str | None:
    """The Greenhouse posting id in this URL, or None when there is none.

    Returns the id ONLY when the URL carries `gh_jid`, or when it sits on a
    Greenhouse-hosted board. A bare `example.com/jobs/12345` is not claimed:
    that path shape belongs to half the web, and claiming it would let this
    adapter assert ownership of another vendor's posting.

    `gh_jid` is still honoured on ANY host, and that is deliberate: employers
    embed Greenhouse boards in their own careers pages and the parameter is
    Greenhouse's own. What it is NOT is exclusive -- a Lever apply link can
    carry one too. The protection there is that the other adapters now accept
    a query string, so both claim the URL and the registry refuses the tie
    rather than believing whichever adapter answered.
    """
    from urllib.parse import parse_qs, urlsplit

    parts = urlsplit(url.strip())
    host = parts.netloc.lower().split(":")[0]

    # `endswith("greenhouse.io")` is a suffix test, not a domain test, so it
    # was true of `notgreenhouse.io` and `mygreenhouse.io` -- hostnames this
    # vendor does not own and anybody can register. The dot is the whole
    # difference between "a subdomain of" and "ends with the same letters".
    ours = host == "greenhouse.io" or host.endswith(".greenhouse.io")

    jid = parse_qs(parts.query).get("gh_jid")
    if jid and jid[0].isdigit():
        return jid[0]
    if ours:
        match = _PATH_ID.match(url.strip().split("?")[0])
        if match:
            return match.group("id")
    return None


#: A posting URL on a Greenhouse-HOSTED board, with the board token in the
#: path: `boards.greenhouse.io/<board>/jobs/<id>`,
#: `job-boards.greenhouse.io/<board>/jobs/<id>` and the EU host
#: `job-boards.eu.greenhouse.io/<board>/jobs/<id>`. An embedded board on an
#: employer's own domain carries `gh_jid` and NO board token, so that shape
#: names a posting and never a board.
_BOARD_URL = re.compile(
    r"^https?://(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/"
    r"(?P<board>[A-Za-z0-9_-]+)/jobs/(?P<id>\d{4,})/?(?:[?#].*)?$",
    re.IGNORECASE,
)


def recognise_board_url(url: str) -> tuple[str, str | None] | None:
    """`(board_identifier, posting_id)` for a Greenhouse-hosted posting URL.

    The board discovery half of ADR-0013: an aggregator's apply link that
    points at a hosted board names the board token in its path, and that is
    an identity somebody PUBLISHED rather than one derived from a domain. A
    `gh_jid` link on an employer's own site proves the family and not the
    board, and returns None: completing it would be a guess.
    """
    match = _BOARD_URL.match(url.strip())
    if match is None:
        return None
    return match.group("board").lower(), match.group("id")
