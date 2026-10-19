"""Jobs by Workable XML feed, vendor-confirmed public on 2026-09-10.

Download the whole hourly feed, parse on disk, then optionally filter locally.
This is Jobs by Workable inventory, not every customer's private board.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from career_agent.net.fetcher import FetchError, FetchErrorCategory
from career_agent.providers.base import RetrievalMode
from career_agent.providers.workable import FeedPage, WorkableError, WorkableProvider

XML_URL = "https://www.workable.com/boards/workable.xml"
BATCH_SIZE = 200
#: Workable ignores Range, so a cut download is asked for again from the start:
#: three attempts in all, the second after 30 s and the third after 2 min.
DOWNLOAD_ATTEMPTS = 3
DOWNLOAD_BACKOFF_SECONDS = (30, 120)


def _worth_retrying(error: FetchError) -> bool:
    """A dropped or slow connection, or the server failing; never a refusal."""
    if error.category in (
        FetchErrorCategory.CONNECTION,
        FetchErrorCategory.TIMEOUT,
        FetchErrorCategory.RETRY_EXHAUSTED,
    ):
        return True
    status = getattr(error, "status_code", None)
    return error.category is FetchErrorCategory.HTTP_STATUS and (status or 0) >= 500


def xml_records(path: Path) -> Iterator[dict[str, str]]:
    """Keep complete records only; malformed/truncated XML raises, never empty."""
    try:
        context = ET.iterparse(path, events=("start", "end"))
        _, root = next(context)
        if root.tag != "source":
            raise WorkableError("Workable XML root must be source")
        for event, element in context:
            if event == "end" and element.tag == "job":
                yield {child.tag: (child.text or "").strip() for child in element}
                root.clear()
    except (ET.ParseError, StopIteration) as exc:
        raise WorkableError("Workable XML is incomplete or malformed") from exc


def from_xml(fields: dict[str, str]) -> dict[str, Any]:
    """Adapt measured XML names; retain every source field for offline replay."""
    date = None
    with suppress(ValueError, TypeError, OverflowError):
        date = parsedate_to_datetime(fields.get("date", "")).isoformat()
    url = fields.get("url", "")
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in {"apply.workable.com", "jobs.workable.com"}:
        url = ""
    return {
        "id": fields.get("referencenumber"),
        "title": fields.get("title"),
        "url": url,
        "company": {"title": fields.get("company"), "website": fields.get("website")},
        "location": {
            "city": fields.get("city"),
            "subregion": fields.get("state"),
            "countryName": fields.get("country"),
        },
        "description": fields.get("description"),
        "employmentType": fields.get("jobtype"),
        # false does not distinguish hybrid from onsite.
        "workplace": "remote" if fields.get("remote") == "true" else None,
        "created": date,
        "department": fields.get("category"),
        "source_provider": "workable",
        "source_feed_url": XML_URL,
        "source_posting_url": url,
        "apply_url": url,
        "xml_fields": fields,
    }


def stage_records(
    path: Path, conn: sqlite3.Connection, check: Any = None
) -> tuple[int, int, Exception | None]:
    """Dedupe on disk while preserving each repeated reference's changed fields.

    A repeated reference can name another country, not just repeat a record.
    The first fields plus every delta reconstruct every complete input element.
    Keeping this staging index on disk bounds memory for the broad feed.
    """
    conn.execute(
        "CREATE TABLE records (seq INTEGER PRIMARY KEY, identity TEXT UNIQUE,"
        " fields TEXT, variants TEXT)"
    )
    count = duplicates = 0
    error: Exception | None = None
    try:
        for fields in xml_records(path):
            count += 1
            if check is not None and count % 500 == 0:
                check()
            identity = fields.get("referencenumber") or f"missing-reference:{count}"
            existing = conn.execute(
                "SELECT fields,variants FROM records WHERE identity=?", (identity,)
            ).fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO records(identity,fields,variants) VALUES (?,?,?)",
                    (identity, json.dumps(fields), "[]"),
                )
            else:
                first, variants = json.loads(existing[0]), json.loads(existing[1])
                variants.append({k: v for k, v in fields.items() if first.get(k) != v})
                conn.execute(
                    "UPDATE records SET variants=? WHERE identity=?",
                    (json.dumps(variants), identity),
                )
                duplicates += 1
    except WorkableError as exc:
        error = exc
    conn.commit()
    return count, duplicates, error


def staged_payload(fields: dict[str, str], variants: list[dict[str, str]]) -> dict[str, Any]:
    row = from_xml(fields)
    row["xml_variants"] = variants
    locations = []
    for patch in [{}, *variants]:
        f = fields | patch
        location = {
            "city": f.get("city"),
            "subregion": f.get("state"),
            "countryName": f.get("country"),
        }
        if location not in locations:
            locations.append(location)
    row["xml_locations"] = locations
    return row


class WorkableXmlProvider(WorkableProvider):
    """The preferred production path. The legacy API is never called here."""

    retrieval_mode = RetrievalMode.AGGREGATOR_FEED

    def __init__(
        self,
        fetcher: Any,
        max_pages: int | None = None,
        xml_path: Path | None = None,
        session: Any = None,
        check: Any = None,
        on_phase: Any = None,
    ):
        super().__init__(fetcher, max_pages=max_pages)
        self.xml_path = xml_path
        #: A `FeedSession` makes the read shared and resumable (see
        #: providers/workable_feed.py); without one the walk is the one-shot
        #: download the tests and the command line use.
        self.session = session
        self.check = check or (lambda: None)
        self.on_phase = on_phase or (lambda *_: None)
        self.not_modified = False
        self.resumed = False
        self.feed_last_modified: str | None = None
        self.page_last_seq = 0
        self.xml_jobs = 0
        self.xml_bytes = 0
        self.filtered_out = 0
        self.feed_duplicates = 0

    def walk(self, *, query=None, day_range=None, use_cache=True) -> Iterator[FeedPage]:
        del use_cache
        if day_range is not None and day_range < 1:
            raise WorkableError("day_range must be positive")
        if self.session is not None:
            yield from self._walk_shared(query=query, day_range=day_range)
            return
        with tempfile.TemporaryDirectory(prefix="career-workable-") as directory:
            path = self.xml_path or Path(directory) / "workable.xml"
            transfer_error: Exception | None = None
            if self.xml_path is None:
                try:
                    self._fetcher.download(XML_URL, path)
                except FetchError as exc:
                    transfer_error = exc
                    if not path.exists():
                        raise
            self.xml_bytes = path.stat().st_size
            staged = sqlite3.connect(Path(directory) / "records.sqlite")
            try:
                self.xml_jobs, self.feed_duplicates, parse_error = stage_records(path, staged)
                transfer_error = transfer_error or parse_error
                total = None if transfer_error else self.xml_jobs - self.feed_duplicates
                yield from self._staged_pages(staged, query=query, day_range=day_range, total=total)
            finally:
                staged.close()
            if transfer_error:
                raise WorkableError(
                    f"Partial XML: {self.xml_jobs} complete records retained; "
                    f"{self.xml_bytes} bytes; transfer or XML did not finish"
                ) from transfer_error

    def _walk_shared(self, *, query, day_range) -> Iterator[FeedPage]:
        session = self.session
        pending = session.pending()
        cursor = 0
        transfer_error: Exception | None = None
        if pending and pending.get("staged") and session.stage_path.exists():
            self.resumed = True
            self.feed_last_modified = pending.get("last_modified")
            self.xml_bytes = int(pending.get("bytes") or 0)
            cursor = int(pending.get("cursor") or 0)
        else:
            have_file = bool(
                pending
                and session.xml_path.exists()
                and session.xml_path.stat().st_size == pending.get("bytes")
            )
            if have_file:
                self.resumed = True
                self.feed_last_modified = pending.get("last_modified")
                self.xml_bytes = int(pending["bytes"])
            else:
                session.abandon()
                got, error = self._download(session)
                if got is None:
                    # A CUT TRANSFER IS NOT NOTHING. Every attempt was cut, so
                    # the longest one is read like a feed that did not parse
                    # to its end: complete records kept, never a finished
                    # read, closing nothing (2026-10-18 lost 847 MB of an
                    # ~850 MB feed to one dropped connection).
                    size = session.xml_path.stat().st_size if session.xml_path.exists() else 0
                    if not size:
                        assert error is not None  # every attempt failed, so one error was kept
                        raise error
                    transfer_error = error
                    self.xml_bytes = size
                    session.start_pending(last_modified=None, bytes=size)
                else:
                    self.feed_last_modified = got.last_modified
                    if got.status == 304:
                        self.not_modified = True
                        return
                    self.xml_bytes = got.size
                    session.start_pending(last_modified=got.last_modified, bytes=got.size)
            self.on_phase("staging", 0, None)
            session.stage_path.unlink(missing_ok=True)
            staged = sqlite3.connect(session.stage_path)
            try:
                count, duplicates, parse_error = stage_records(
                    session.xml_path, staged, check=self.check
                )
            finally:
                staged.close()
            # A faulty feed is not resumable: what was read (complete records
            # only) is handled below, the run fails, nothing is recorded as done.
            session.update_pending(
                staged=True,
                records=count,
                duplicates=duplicates,
                malformed=bool(parse_error or transfer_error),
            )
            session.xml_path.unlink(missing_ok=True)
        pending = session.pending() or {}
        self.xml_jobs = int(pending.get("records") or 0)
        self.feed_duplicates = int(pending.get("duplicates") or 0)
        malformed = bool(pending.get("malformed"))
        total = None if malformed else self.xml_jobs - self.feed_duplicates
        staged = sqlite3.connect(session.stage_path)
        try:
            yield from self._staged_pages(
                staged, query=query, day_range=day_range, total=total, after_seq=cursor
            )
        finally:
            staged.close()
        if malformed:
            session.abandon()
            raise WorkableError(
                f"Partial XML: {self.xml_jobs} complete records retained; "
                f"{self.xml_bytes} bytes; the transfer or the XML did not finish"
            )

    def _download(self, session) -> tuple[Any, FetchError | None]:
        """The whole feed, retried from the start a bounded number of times.

        Workable ignores Range, so a cut transfer cannot be continued, only
        asked for again. Returns (download, None), or (None, last error) with
        the LONGEST cut transfer left at `session.xml_path` (or nothing there
        when no attempt delivered a byte). A refusal or a client error is not
        retried; a Stop or the budget ends the wait between attempts.
        """
        best = session.partial_path
        best.unlink(missing_ok=True)
        error: FetchError | None = None
        for attempt in range(DOWNLOAD_ATTEMPTS):
            if attempt:
                self._wait(DOWNLOAD_BACKOFF_SECONDS[attempt - 1])
            self.on_phase("downloading", 0, None)
            try:
                got = self._fetcher.download_feed(
                    XML_URL,
                    session.xml_path,
                    if_modified_since=session.processed_last_modified(),
                    on_bytes=lambda done, total: self.on_phase("downloading", done, total),
                    keep_partial=True,
                )
            except FetchError as exc:
                error = exc
                size = session.xml_path.stat().st_size if session.xml_path.exists() else 0
                if size > (best.stat().st_size if best.exists() else 0):
                    os.replace(session.xml_path, best)
                session.xml_path.unlink(missing_ok=True)
                if not _worth_retrying(exc):
                    break
                continue
            best.unlink(missing_ok=True)
            return got, None
        if best.exists():
            os.replace(best, session.xml_path)
        return None, error

    def _wait(self, seconds: int) -> None:
        for _ in range(seconds):
            self.check()
            self._fetcher.pause(1)

    def page_done(self) -> None:
        """The records of the page just yielded are all handled: move the cursor."""
        if self.session is not None:
            self.session.update_pending(cursor=self.page_last_seq)

    def finish(self) -> None:
        """The feed was read to its end, or a 304 confirmed it unchanged."""
        if self.session is None:
            return
        if self.not_modified:
            self.session.confirm_unchanged()
        else:
            self.session.finish(self.feed_last_modified)

    def _staged_pages(self, staged, *, query, day_range, total, after_seq=0):
        cutoff = datetime.now(UTC) - timedelta(days=day_range) if day_range else None
        batch = []
        self.pages_read = 0
        self.filtered_out = 0
        self.stopped_early = self.repeated_itself = False
        for seq, encoded_fields, encoded_variants in staged.execute(
            "SELECT seq,fields,variants FROM records WHERE seq > ? ORDER BY seq", (after_seq,)
        ):
            self.page_last_seq = seq
            fields, variants = json.loads(encoded_fields), json.loads(encoded_variants)
            row = staged_payload(fields, variants)
            searchable = " ".join(
                [*fields.values(), *(v for patch in variants for v in patch.values())]
            )
            if query and query.casefold() not in searchable.casefold():
                self.filtered_out += 1
                continue
            if cutoff and row["created"] and datetime.fromisoformat(row["created"]) < cutoff:
                self.filtered_out += 1
                continue
            batch.append(row)
            if len(batch) == BATCH_SIZE:
                self.pages_read += 1
                yield FeedPage(tuple(batch), self.pages_read, total)
                batch = []
        if batch or not self.pages_read:
            self.pages_read += 1
            yield FeedPage(tuple(batch), self.pages_read, total)
