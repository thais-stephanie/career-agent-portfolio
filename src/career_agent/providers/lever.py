"""Lever job board adapter.

Lever publishes every company's board as public JSON with no key and no account:

    GET https://api.lever.co/v0/postings/{site}?mode=json

One request returns the whole board -- measured on a 877-posting board, which
came back complete with no paging and no default cap. `limit` and `skip` exist
and behave, but paging a board that already arrives whole would only add failure
modes and a torn-read risk.

Like the Greenhouse adapter, this module translates shape and never meaning.
When Lever says `workplaceType: "remote"` or `country: "US"`, those are things
the employer asserted, recorded as-is. Whether the candidate can actually work
there is decided much later by M3 resolution, against the description text --
and the two disagree often enough that trusting the metadata would be a bug.
Toptal makes the point: a posting located "Anywhere" carries `country: "US"`,
and one located "Europe, South America" carries `country: "BR"`.

Two things here are less obvious than they look, and both are measured rather
than assumed. See `docs/architecture/milestone-1b-lever.md` for the numbers.

1. The description is spread across several fields (`_assemble_description`).
2. The org unit is two fields, and the coarse one is often missing
   (`_department`).
"""

import re
from collections.abc import Iterator
from html import escape
from typing import Any

from career_agent.domain.enums import MetadataDimension
from career_agent.domain.normalize import html_to_text
from career_agent.domain.timestamps import to_rfc3339_utc
from career_agent.net.fetcher import FetchError, FetchErrorCategory, HttpFetcher
from career_agent.providers.base import (
    BoardRef,
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

API_BASE = "https://api.lever.co/v0/postings"
PUBLIC_BOARD_BASE = "https://jobs.lever.co"

LEVER_CAPABILITIES = ProviderCapabilities(
    # One request returns every posting with all of its text -- after assembly.
    full_description_in_list=True,
    # After assembly: `description`, `lists` and `additional` are three
    # parts of one posting and the adapter joins them. What is stored is
    # everything Lever holds.
    obtains_full_description=True,
    # `createdAt` on 2174/2174 sampled postings. Epoch milliseconds, converted
    # to RFC 3339 so both providers land in `posted_at` in one format.
    exposes_posted_date=True,
    # `categories.department` on 85.6% and `categories.team` on 100%; combined
    # coverage 100%. Two boards in the sample (palantir, alloy) publish no
    # department at all, which is why the mapping uses both. See `_department`.
    exposes_department=True,
    # `salaryRange` appears on only 3.4% of postings pooled -- but on 100% of
    # one board and 63% of another. The flag answers "does the provider have a
    # place to put it?", not "how often is it filled in?". Declaring False would
    # relabel an employer's choice not to disclose as a tooling limitation, and
    # erase a real signal.
    exposes_compensation=True,
    # `country` is ISO-3166-1 alpha-2 and varies per posting (25 distinct values
    # on one board), so it is structured rather than free text. That is all this
    # asserts. It says nothing about whether the country is trustworthy.
    exposes_location_structured=True,
    # `workplaceType` on 2174/2174: remote 800, hybrid 327, onsite 1047.
    exposes_remote_flag=True,
    # `categories.commitment` on 98.7%, in twelve different spellings.
    exposes_employment_type=True,
)

# RESOLVED IN M1B.1. The four flags above that Greenhouse sets False are True
# here, and until M1B.1 `PostingStub` had nowhere to carry their values: they
# survived in the archived payload but were reachable only by knowing Lever's
# JSON paths, which is the leak this abstraction exists to prevent. The field
# map below is where those four dimensions now travel, provenance attached and
# uninterpreted. See docs/architecture/milestone-1b1-provider-metadata.md.

LEVER_FIELD_MAP = ProviderFieldMap(
    (
        # remote / hybrid / onsite, present on 2174/2174 sampled postings. What
        # the employer's ATS record asserts -- never eligibility. Toptal is the
        # standing reminder: a posting located "Anywhere" whose country is US.
        FieldMapping("workplaceType", MetadataDimension.WORK_MODEL_HINT),
        # Two paths, one dimension, on purpose. `country` is an ISO-3166-1
        # alpha-2 code and `categories.location` is free text, and they are two
        # separate assertions about the same question. Collapsing them into one
        # would be deciding which the employer meant. Both travel; M3 weighs
        # them, and can see when they disagree.
        FieldMapping("country", MetadataDimension.HIRING_LOCATION_HINT),
        FieldMapping("categories.location", MetadataDimension.HIRING_LOCATION_HINT),
        # Twelve distinct spellings across nine boards ("Full-Time",
        # "Full-time", "Regular - Full Time"...). All twelve travel unchanged;
        # picking winners among them is M2's job, with evidence attached.
        FieldMapping("categories.commitment", MetadataDimension.EMPLOYMENT_TYPE_HINT),
        # An object, serialised whole rather than flattened. Nothing here
        # decides it is annual, comparable, or good.
        FieldMapping("salaryRange", MetadataDimension.COMPENSATION_HINT),
    )
)

# Unmapped and archived only: `tags`, `categories.level` (never returned in
# 2174 sampled postings), `categories.allLocations` (multi-location is M3's
# problem, deferred), `categories.team` and `categories.department` (already
# carried as PostingStub.department), `applyUrl`, `salaryDescription` (free
# prose about pay -- description-channel material, not a metadata field).


class LeverProvider(JobProvider):
    name = "lever"
    kind = ProviderKind.ATS
    capabilities = LEVER_CAPABILITIES
    field_map = LEVER_FIELD_MAP

    def __init__(self, fetcher: HttpFetcher, api_base: str = API_BASE) -> None:
        self._fetcher = fetcher
        self._api_base = api_base.rstrip("/")

    # -- URLs --------------------------------------------------------------

    def board_url(self, board: BoardRef) -> str:
        return board.board_url or f"{PUBLIC_BOARD_BASE}/{board.board_identifier}"

    def jobs_url(self, board: BoardRef) -> str:
        return f"{self._api_base}/{board.board_identifier}?mode=json"

    # -- listing -----------------------------------------------------------

    def list_postings(self, board: BoardRef) -> Iterator[PostingStub]:
        """Every posting on the board.

        Raises FetchError on failure. A board that legitimately has no openings
        returns HTTP 200 with `[]` and yields nothing without raising -- that
        difference is what keeps the closing logic safe.

        The shape check below is not defensive padding. Lever answers an unknown
        board with HTTP 404 whose body is *valid JSON*: `{"ok": false, "error":
        "Document not found"}`. `HttpFetcher` already rejects the 404, but if a
        vendor ever served that object with a 200, a parser that only asked "did
        this parse?" would read it as an empty board and close every posting the
        company has. So anything that is not a list is an error, loudly.
        """
        url = self.jobs_url(board)
        payload = self._fetcher.get_json(url)

        if not isinstance(payload, list):
            raise FetchError(
                FetchErrorCategory.MALFORMED,
                url,
                f"expected a JSON array of postings, got {type(payload).__name__}",
            )

        for entry in payload:
            stub = self._to_stub(entry, board)
            if stub is None:
                # COUNTED, not silent. See `JobProvider.postings_skipped`.
                self.postings_skipped += 1
                continue
            yield stub

    def _to_stub(self, entry: Any, board: BoardRef) -> PostingStub | None:
        """Translate one Lever posting object.

        A single unusable entry is skipped rather than failing the whole board:
        losing one malformed posting is better than losing a company. The
        skipped count surfaces in the run statistics, so it is never silent.
        """
        if not isinstance(entry, dict):
            return None
        external_id = entry.get("id")
        title = entry.get("text")
        if not external_id or not title:
            return None

        categories = entry.get("categories")
        categories = categories if isinstance(categories, dict) else {}

        location = categories.get("location")
        location_raw = str(location).strip() if location else None

        return PostingStub(
            external_id=str(external_id),
            title=str(title),
            url=str(entry.get("hostedUrl") or f"{self.board_url(board)}/{external_id}"),
            location_raw=location_raw or None,
            department=_department(categories),
            posted_at=_created_at(entry),
            description_html=_assemble_description(entry),
            payload=entry,
        )

    # -- completing a posting ---------------------------------------------

    def fetch_posting(self, board: BoardRef, stub: PostingStub) -> RawPosting:
        """Complete a stub. No second request: the list response carried it all."""
        html = stub.description_html or ""
        return RawPosting(
            stub=stub,
            description_html=html,
            description_text=html_to_text(html),
            payload=stub.payload,
        )


def _assemble_description(entry: dict[str, Any]) -> str:
    """The whole posting, reassembled from the fields Lever splits it across.

    Greenhouse hands over one `content` field. Lever does not, and taking the
    obviously-named one loses most of the job: measured across nine real boards,
    `description` alone is 21.7% to 57.8% of the posting text. What sits outside
    it is `lists[]` -- "What You'll Do", "Requirements", "Nice to have" -- the
    responsibilities and the software stack. For a product built on *search for
    the work, not the title*, reading only `description` would mean reading the
    marketing paragraph and discarding the job.

    Order is `description`, then `lists` in the order Lever returned them, then
    `additional`. Never re-sorted, never filtered, nothing added.

    `opening` and `descriptionBody` are deliberately excluded. They are not
    extra content: `description == opening + descriptionBody`, confirmed by
    containment on 1417 postings across seven boards with no exceptions.
    Including them would duplicate text, and duplication is not cosmetic here --
    it would corrupt the content hash, inflate M2 token spend, and hand the
    model the same requirement twice to weigh.

    `lists[].content` arrives as bare `<li>` items with no enclosing `<ul>`, so
    the wrapper is restored and the section heading becomes a real heading.
    Without that, "Requirements" and "Nice to have" flatten into one
    undifferentiated list -- exactly the distinction M2 has to preserve.
    """
    parts: list[str] = []

    opening = entry.get("description")
    if isinstance(opening, str) and opening.strip():
        parts.append(opening)

    for section in entry.get("lists") or []:
        if not isinstance(section, dict):
            continue
        heading = str(section.get("text") or "").strip()
        content = str(section.get("content") or "").strip()
        if not heading and not content:
            continue
        if heading:
            # quote=False: this is element text, not an attribute value, so
            # escaping apostrophes would only turn "What You'll Do" into noise
            # that has to be decoded again two lines later.
            parts.append(f"<h3>{escape(heading, quote=False)}</h3>")
        if content:
            parts.append(f"<ul>{content}</ul>")

    additional = entry.get("additional")
    if isinstance(additional, str) and additional.strip():
        parts.append(additional)

    return "\n".join(parts)


def _department(categories: dict[str, Any]) -> str | None:
    """The posting's org unit, coarse level first.

    Lever splits this in two: `department` (coarse) and `team` (fine). Measured,
    `team` is present on 100% of postings and `department` on only 85.6% --
    and the gap is not random. Two boards in the sample (palantir, 308 postings;
    alloy, 5) publish no department at all. Mapping `department -> department`
    naively would blank the org signal on 14% of postings with no error
    anywhere.

    So both levels are joined with " / ", which is also how the Greenhouse
    adapter flattens its own department hierarchy -- one convention, two
    providers. Whichever level is missing is skipped:

        both        -> "Engineering / Platform"
        team only   -> "Platform"
        dept only   -> "Engineering"
        neither     -> None

    One extra rule: when the two levels are the same string -- 30 postings in
    the sample, e.g. "Professional Services / Professional Services" -- the
    repeat is dropped. That is de-duplication of an identical string, not a
    choice between different values, so it stays on the translation side of the
    line. Nothing is lost that the payload does not still hold.

    The full structure survives in the archived payload. Flattening loses the
    hierarchy, which is a real cost recorded as finding P2 in the M1B brief --
    but nothing downstream consumes department yet, so designing a structured
    type now would be designing against an imagined consumer.
    """
    names: list[str] = []
    for key in ("department", "team"):
        value = str(categories.get(key) or "").strip()
        if value and value not in names:
            names.append(value)
    return " / ".join(names) if names else None


#: Lever's `interval` strings, mapped to the shared period vocabulary. Counted
#: over the 167 archived payloads that carry a `salaryRange`:
#: "per-year-salary" 158, "per-hour-wage" 5, "bi-week-salary" 4.
#:
#: `bi-week-salary` is deliberately absent, and it is the reason this table
#: exists rather than a suffix-stripping rule. A fortnightly figure has no
#: entry in the matcher's YEAR/MONTH/HOUR vocabulary, and the nearest guess --
#: doubling it into a month -- would invent a number the employer never wrote
#: and hand it to a threshold comparison. Unmapped resolves to None, the
#: posting scores `salary_unknown`, and the range still travels for display.
COMPENSATION_INTERVALS: dict[str, str] = {
    "per-year-salary": "YEAR",
    "per-month-salary": "MONTH",
    "per-hour-wage": "HOUR",
}

#: Every payload path `read_compensation` reads, declared so the neutrality
#: guard can cover them. See `providers.registry.declared_payload_paths`.
COMPENSATION_PATHS: tuple[str, ...] = (
    "salaryRange",
    "salaryDescriptionPlain",
    "salaryDescription",
)


def read_compensation(payload: Any) -> CompensationHint | None:
    """What this stored Lever payload says about pay. No network, no guessing.

    `salaryRange` is one flat object -- `min`, `max`, `currency`, `interval` --
    with no tiers and no component types, so there is nothing to choose
    between: 167 payloads carry it and none has a missing bound.

    One posting states `{"min": 0, "max": 0}`, and `stated_amounts` reads that
    as the placeholder it is rather than as an offer of nothing. It used to
    award the `salary_known` confidence point and render a card that read
    "0 - 0".

    `salaryDescriptionPlain` becomes `raw_text` and is never parsed. It is
    prose the employer wrote about pay ("market-driven and data-informed..."),
    and prose stating a salary is a claim for the description channel to carry
    with evidence, not a field to read a number out of. A payload with that
    prose and no `salaryRange` therefore returns None: exactly 1 posting in the
    corpus, and the honest reading of it is that no structured pay was stated.
    """
    if not isinstance(payload, dict):
        return None
    salary_range = payload.get("salaryRange")
    if not isinstance(salary_range, dict):
        return None

    minimum, maximum = stated_amounts(
        parse_amount(salary_range.get("min")), parse_amount(salary_range.get("max"))
    )
    currency = _text(salary_range.get("currency"))
    interval = _text(salary_range.get("interval"))
    description = _text(payload.get("salaryDescriptionPlain")) or _text(
        payload.get("salaryDescription")
    )

    if minimum is None and maximum is None and description is None:
        return None

    return CompensationHint(
        source_field="salaryRange",
        min_value=minimum,
        max_value=maximum,
        currency=currency,
        period=COMPENSATION_INTERVALS.get(interval or ""),
        raw_text=description,
    )


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _created_at(entry: dict[str, Any]) -> str | None:
    """Lever's publication timestamp, under the shared `posted_at` contract.

    Lever returns epoch **milliseconds** as an int; Greenhouse returns a string
    in the board's local offset. `PostingStub.posted_at` is one string field, so
    without a convention `job.posted_at` would hold two incompatible encodings
    and every later freshness calculation would be a guess.

    The conversion itself lives in `domain/timestamps.py` rather than here, so
    both adapters cannot drift apart: `1519801128216` becomes
    `2018-02-28T06:58:48+00:00`, and milliseconds are dropped because Greenhouse
    has no sub-second resolution and a mixed-precision column invites
    comparisons that are only sometimes meaningful.

    Anything unusable returns None rather than a fabricated date: absence of a
    timestamp is a fact, and inventing one would make an unknown posting look
    fresh.
    """
    return to_rfc3339_utc(entry.get("createdAt"))


# -- recognising our own posting URLs -------------------------------------

#: `https://jobs.lever.co/<site>/<uuid>`, optionally with a trailing `/apply`
#: and any query string.
#:
#: Measured against the corpus this recognises all 2,586 archived Lever job
#: URLs, and the id it returns is byte-identical to the stored `external_id`.
_POSTING_URL = re.compile(
    r"^https?://jobs\.lever\.co/[^/]+/"
    r"(?P<id>[0-9a-fA-F-]{36})"
    r"(?:/apply|/thanks)?/?"
    # See the note in `ashby.py`: the comment above documented a query string
    # this pattern did not accept, and the gap let another adapter claim a
    # Lever URL unopposed.
    r"(?:[?#].*)?$",
    re.IGNORECASE,
)


def recognise_posting_url(url: str) -> str | None:
    """The Lever posting id in this URL, or None when it is not one of ours."""
    match = _POSTING_URL.match(url.strip())
    return match.group("id").lower() if match else None


#: `https://jobs.lever.co/<site>/<uuid>`, the site being the board.
_BOARD_URL = re.compile(
    r"^https?://jobs(?:\.eu)?\.lever\.co/(?P<board>[A-Za-z0-9_-]+)/"
    r"(?P<id>[0-9a-fA-F-]{36})(?:/apply|/thanks)?/?(?:[?#].*)?$",
    re.IGNORECASE,
)


def recognise_board_url(url: str) -> tuple[str, str | None] | None:
    """`(board_identifier, posting_id)` for a Lever posting URL, or None."""
    match = _BOARD_URL.match(url.strip())
    if match is None:
        return None
    return match.group("board").lower(), match.group("id").lower()
