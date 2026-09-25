"""LinkedIn, through python-jobspy: OPTIONAL, EXPERIMENTAL, OFF BY DEFAULT.

WHY THIS EXISTS AT ALL
----------------------
`config/source_catalogue.yaml` records LinkedIn as FORBIDDEN: its robots.txt
prohibits automated access without express permission, and its job APIs are
for employers. That fact is NOT changed here, and nothing in this module makes
LinkedIn an ordinary source. The catalogue row still says forbidden, and
`can_refresh` refuses it unless the profile in use explicitly opted in.

What is added is a LOCAL EXPERIMENTAL OVERRIDE: the person running Career
Agent on their own machine may explicitly switch this adapter on for their own
profile, after being told plainly what LinkedIn says. Until they do, it never
runs. The owner of this repository asked for it; the opt-in is theirs, stored
in their profile, never in a shipped default.

WHAT IT DOES AND DOES NOT DO
----------------------------
* It asks LinkedIn's public guest job search a small number of short
  questions (one search phrase and one place each) and reads the result
  cards. It never logs in, never asks for or stores a LinkedIn password,
  cookie or session, and never uses the person's account.
* It reads a posting's own page only for postings the corpus does not hold
  yet, one at a time, slowly, and stops at the first sign of a block.
* It is QUERY-DRIVEN: what comes back is the answer to the questions asked,
  never LinkedIn's market.

WHY JOBSPY'S SILENCE IS NOT TRUSTED
-----------------------------------
python-jobspy turns a LinkedIn 429 ("too many requests") into a log line and
an empty or short result, and its posting-page reader swallows every error.
An empty answer is therefore NOT evidence that no job exists. This module
drives JobSpy's LinkedIn scraper with its OWN session: no automatic retries
(JobSpy's default retries a 429 three times and honours any Retry-After), and
every response's status recorded. A 429, 999 or 403 is REFUSED, and the
caller stops instead of concluding the market is empty.

Request counts are therefore real: a search reads up to three result pages
(JobSpy pages ten cards at a time and pauses 3 to 7 seconds between pages).

THE HEADERS ARE JOBSPY'S. Upstream sends browser-like request headers; they
are not changed or extended here, and nothing is done to disguise the traffic
further (no proxies, no rotation).

AVAILABILITY. python-jobspy pins numpy 1.26.3, which ships wheels only up to
Python 3.12, so the dependency is declared for Python 3.12 (the version the
launcher uses). Elsewhere `available()` is False and the source says so.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from career_agent.domain.timestamps import to_rfc3339_utc
from career_agent.net.fetcher import HttpFetcher
from career_agent.providers.base import (
    BoardRef,
    JobProvider,
    PostingStub,
    ProviderCapabilities,
    ProviderFieldMap,
    ProviderKind,
    RawPosting,
    RetrievalMode,
)

PROVIDER = "linkedin"
HOST = "www.linkedin.com"
VIEW_URL = "https://www.linkedin.com/jobs/view/{id}"

LINKEDIN_FIELD_MAP = ProviderFieldMap(mappings=())

#: Search outcomes. EMPTY is a real answer with nothing in it; it is kept
#: apart from RATE_LIMITED and FAILED so nobody reads a refusal as "no jobs".
#: RATE_LIMITED covers every refusal: a 429, LinkedIn's 999 bot answer, a 403.
OK = "OK"
EMPTY = "EMPTY"
RATE_LIMITED = "RATE_LIMITED"
FAILED = "FAILED"
PARSE_FAILED = "PARSE_FAILED"
UNAVAILABLE = "UNAVAILABLE"

REFUSAL_CODES = frozenset({403, 429, 999})
_STATUS = re.compile(r"status code (\d{3})")


def available() -> bool:
    """Whether python-jobspy is installed for this Python."""
    import importlib.util

    return importlib.util.find_spec("jobspy") is not None


_ID = re.compile(r"^(?:li-)?(\d{5,20})$")
#: Markdown emphasis, and the backslash Markdown puts before punctuation.
_MARKS = re.compile(r"\*\*|__|\\(?=[^\w\s])")


class LinkedInError(ValueError):
    """This adapter refused to do something, and says which."""


@dataclass
class SearchOutcome:
    status: str
    records: list[dict[str, Any]] = field(default_factory=list)
    #: What JobSpy logged at WARNING or above during the call, trimmed.
    detail: str = ""
    #: HTTP requests the call made (0 when unknown, as with a test fake).
    requests: int = 0


class _Listen(logging.Handler):
    """Collects what JobSpy says while one call runs."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage()[:300])


def _jobspy_loggers() -> list[logging.Logger]:
    names = {"JobSpy:LinkedIn", "JobSpy:Linkedin"}
    names.update(n for n in logging.Logger.manager.loggerDict if str(n).startswith("JobSpy"))
    return [logging.getLogger(n) for n in sorted(names)]


def classify(
    messages: list[str],
    count: int,
    error: Exception | None = None,
    statuses: list[int] | tuple[int, ...] = (),
) -> str:
    """One word for what a JobSpy call amounted to.

    Recorded HTTP statuses decide first; JobSpy's log line is the fallback
    (it is all a test fake or an older path provides). Only JobSpy's own
    phrasing counts as a refusal: "429 Response" or "too many requests", or a
    "status code" of 429, 999 or 403. A bare "429" or "blocked" inside some
    other text is not one.
    """
    if any(code in REFUSAL_CODES for code in statuses):
        return RATE_LIMITED
    text = " ".join(messages).casefold()
    logged = {int(code) for code in _STATUS.findall(text)}
    if "429 response" in text or "too many requests" in text or logged & REFUSAL_CODES:
        return RATE_LIMITED
    if error is not None:
        if isinstance(error, ImportError):
            return UNAVAILABLE
        name = type(error).__name__.casefold()
        return PARSE_FAILED if "linkedin" in name or "parse" in str(error).casefold() else FAILED
    if logged or any(code >= 400 for code in statuses) or "linkedin:" in text:
        return FAILED
    return OK if count else EMPTY


def external_id(record: Any) -> str | None:
    if not isinstance(record, dict):
        return None
    match = _ID.match(str(record.get("id") or "").strip())
    return f"li-{match.group(1)}" if match else None


def public_url(record: Any) -> str | None:
    """The LinkedIn posting page, rebuilt from its id: the only Apply target
    this adapter produces, and never a tracking-laden link."""
    ident = external_id(record)
    return VIEW_URL.format(id=ident.removeprefix("li-")) if ident else None


def direct_url(record: Any) -> str | None:
    """The employer's own apply link, when LinkedIn published one."""
    value = record.get("job_url_direct") if isinstance(record, dict) else None
    if not isinstance(value, str) or not value.startswith("https://"):
        return None
    host = (urlsplit(value).hostname or "").casefold()
    return None if not host or host.endswith("linkedin.com") else value


def _text(record: Any, key: str) -> str | None:
    value = record.get(key) if isinstance(record, dict) else None
    if value is None or (isinstance(value, float) and value != value):  # NaN from pandas
        return None
    text = str(value).strip()
    return text if text and text.casefold() not in {"nan", "none", "n/a"} else None


def posted_at(record: Any) -> str | None:
    raw = _text(record, "date_posted")
    if not raw:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        raw = f"{raw}T00:00:00+00:00"
    return to_rfc3339_utc(raw)


def company_of(record: Any) -> str | None:
    return _text(record, "company")


def to_stub(record: Any) -> PostingStub | None:
    ident = external_id(record)
    title = _text(record, "title")
    url = public_url(record)
    if not ident or not title or not url:
        return None
    return PostingStub(
        external_id=ident,
        title=title,
        url=url,
        location_raw=_text(record, "location"),
        department=None,
        posted_at=posted_at(record),
        description_html=_text(record, "description"),
        payload=_plain(record),
    )


def _plain(record: dict[str, Any]) -> dict[str, Any]:
    """JSON-safe copy of a JobSpy row: NaN to None, dates to text. The logo
    and emails are not kept."""
    out: dict[str, Any] = {}
    for key, value in record.items():
        if key in {"company_logo", "emails"}:
            continue
        if value is None or (isinstance(value, float) and value != value):
            out[key] = None
        elif isinstance(value, str | int | float | bool):
            out[key] = value
        else:
            out[key] = str(value)
    return out


class _Recording:
    """A session wrapper that remembers every status and final URL."""

    def __init__(self, session: Any) -> None:
        self._session = session
        self.statuses: list[int] = []
        self.urls: list[str] = []

    def get(self, *args: Any, **kwargs: Any) -> Any:
        response = self._session.get(*args, **kwargs)
        self.statuses.append(int(getattr(response, "status_code", 0) or 0))
        self.urls.append(str(getattr(response, "url", "")))
        return response

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)


def _scraper() -> tuple[Any, _Recording]:
    """JobSpy's LinkedIn scraper on a session WITHOUT automatic retries."""
    from jobspy.linkedin import LinkedIn
    from jobspy.linkedin.constant import headers
    from jobspy.util import create_session

    scraper = LinkedIn()
    session = create_session(is_tls=False, has_retry=False, clear_cookies=True)
    session.headers.update(headers)
    recording = _Recording(session)
    scraper.session = recording
    return scraper, recording


def _post(post: Any) -> dict[str, Any]:
    location = getattr(post, "location", None)
    place = location.display_location() if location is not None else None
    posted = getattr(post, "date_posted", None)
    return {
        "id": post.id,
        "site": "linkedin",
        "job_url": post.job_url,
        "job_url_direct": post.job_url_direct,
        "title": post.title,
        "company": post.company_name,
        "location": place,
        "date_posted": posted.isoformat() if posted is not None else None,
        "is_remote": post.is_remote,
        "description": post.description,
        "company_url": post.company_url,
    }


def _default_scrape(**kwargs: Any) -> tuple[list[dict[str, Any]], list[int]]:
    from jobspy.model import DescriptionFormat, ScraperInput, Site

    scraper, recording = _scraper()
    response = scraper.scrape(
        ScraperInput(
            site_type=[Site.LINKEDIN],
            search_term=kwargs["search_term"],
            location=kwargs.get("location"),
            is_remote=bool(kwargs.get("is_remote")),
            results_wanted=int(kwargs["results_wanted"]),
            hours_old=kwargs.get("hours_old"),
            linkedin_fetch_description=False,
            description_format=DescriptionFormat.MARKDOWN,
        )
    )
    # A search redirected to the sign-in page answers 200 with no cards: that
    # is a refusal, not an empty market.
    if any("linkedin.com/signup" in u or "authwall" in u for u in recording.urls):
        return [_post(post) for post in response.jobs], [*recording.statuses, 999]
    return [_post(post) for post in response.jobs], recording.statuses


def _default_details(job_id: str) -> dict[str, Any]:
    from jobspy.model import DescriptionFormat, ScraperInput, Site

    scraper, recording = _scraper()
    scraper.scraper_input = ScraperInput(
        site_type=[Site.LINKEDIN], description_format=DescriptionFormat.MARKDOWN
    )
    found = dict(scraper._get_job_details(job_id) or {})
    found["_statuses"] = list(recording.statuses)
    # A redirect to the sign-in page is a refusal, not an empty posting.
    found["_signin"] = any("linkedin.com/signup" in u or "authwall" in u for u in recording.urls)
    return found


class LinkedInJobSpyProvider(JobProvider):
    """Bounded guest searches. Never a board, never exhaustive."""

    name = PROVIDER
    kind = ProviderKind.AGGREGATOR
    retrieval_mode = RetrievalMode.QUERY_DRIVEN
    addresses_boards_by_company = False
    identifier_is_guessable = False
    field_map = LINKEDIN_FIELD_MAP
    capabilities = ProviderCapabilities(
        full_description_in_list=False,
        obtains_full_description=True,
        exposes_posted_date=True,
        exposes_department=False,
        #: FALSE. JobSpy parses a salary line with a guessed currency.
        exposes_compensation=False,
        exposes_location_structured=False,
        exposes_remote_flag=False,
        exposes_employment_type=False,
        #: FALSE. A card's place is where the job is listed, not who may apply.
        publishes_hiring_scope=False,
    )

    def __init__(
        self,
        fetcher: HttpFetcher | None = None,
        *,
        scrape: Callable[..., list[dict[str, Any]]] | None = None,
        details: Callable[[str], dict[str, Any]] | None = None,
    ) -> None:
        # JobSpy makes its own requests; the shared fetcher is accepted so the
        # registry can build every adapter the same way, and is not used.
        del fetcher
        self._scrape = scrape or _default_scrape
        self._details = details or _default_details

    def search(
        self,
        term: str,
        *,
        location: str | None,
        remote: bool,
        results_wanted: int,
        hours_old: int,
    ) -> SearchOutcome:
        listen = _Listen()
        loggers = _jobspy_loggers()
        for logger in loggers:
            logger.addHandler(listen)
        statuses: list[int] = []
        try:
            answer = self._scrape(
                search_term=term,
                location=location,
                is_remote=remote,
                results_wanted=results_wanted,
                hours_old=hours_old,
            )
            records, statuses = answer if isinstance(answer, tuple) else (answer, [])
            error: Exception | None = None
        except Exception as exc:  # noqa: BLE001 - JobSpy raises for one bad query
            records, error = [], exc
        finally:
            for logger in loggers:
                logger.removeHandler(listen)
        records = [r for r in records if isinstance(r, dict)]
        status = classify(listen.messages, len(records), error, statuses)
        detail = "; ".join(listen.messages)[:300] or (type(error).__name__ if error else "")
        return SearchOutcome(status, records, detail, requests=len(statuses))

    def enrich(self, record: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """(status, details) for one posting page. `{}` from JobSpy means the
        page could not be read (including a sign-in wall)."""
        ident = external_id(record)
        if not ident:
            return FAILED, {}
        listen = _Listen()
        loggers = _jobspy_loggers()
        for logger in loggers:
            logger.addHandler(listen)
        try:
            found = self._details(ident.removeprefix("li-"))
        except Exception:  # noqa: BLE001 - one page, reported
            found = {}
        finally:
            for logger in loggers:
                logger.removeHandler(listen)
        statuses = found.pop("_statuses", [])
        signin = found.pop("_signin", False)
        counted = {"_requests": len(statuses)}
        if signin or classify(listen.messages, 1, None, statuses) == RATE_LIMITED:
            return RATE_LIMITED, counted
        return (OK, {**found, **counted}) if found.get("description") else (FAILED, counted)

    # -- the JobProvider protocol -----------------------------------------

    def board_url(self, board: BoardRef) -> str:
        return f"https://{HOST}/jobs/"

    def validate_board(self, board: BoardRef) -> str | None:
        return None

    def list_postings(self, board: BoardRef) -> Iterator[PostingStub]:
        raise LinkedInError("LinkedIn is searched, not walked by board. The collector does.")

    def fetch_posting(self, board: BoardRef, stub: PostingStub) -> RawPosting:
        text = stub.description_html or ""
        return RawPosting(
            stub=stub,
            description_html=text,
            # JobSpy returns Markdown; the text keeps its line breaks and
            # loses only the emphasis marks.
            description_text=_MARKS.sub("", text).strip(),
            payload=dict(stub.payload),
        )
