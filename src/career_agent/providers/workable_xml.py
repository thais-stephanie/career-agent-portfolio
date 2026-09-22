"""Jobs by Workable XML feed, vendor-confirmed public on 2026-09-10.

Download the whole hourly feed, parse on disk, then optionally filter locally.
This is Jobs by Workable inventory, not every customer's private board.
"""

from __future__ import annotations

import json
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

from career_agent.net.fetcher import FetchError
from career_agent.providers.base import RetrievalMode
from career_agent.providers.workable import FeedPage, WorkableError, WorkableProvider

XML_URL = "https://www.workable.com/boards/workable.xml"
BATCH_SIZE = 200


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


def stage_records(path: Path, conn: sqlite3.Connection) -> tuple[int, int, Exception | None]:
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

    def __init__(self, fetcher: Any, max_pages: int | None = None, xml_path: Path | None = None):
        super().__init__(fetcher, max_pages=max_pages)
        self.xml_path = xml_path
        self.xml_jobs = 0
        self.xml_bytes = 0
        self.filtered_out = 0
        self.feed_duplicates = 0

    def walk(self, *, query=None, day_range=None, use_cache=True) -> Iterator[FeedPage]:
        del use_cache
        if day_range is not None and day_range < 1:
            raise WorkableError("day_range must be positive")
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

    def _staged_pages(self, staged, *, query, day_range, total):
        cutoff = datetime.now(UTC) - timedelta(days=day_range) if day_range else None
        batch = []
        self.pages_read = 0
        self.filtered_out = 0
        self.stopped_early = self.repeated_itself = False
        for encoded_fields, encoded_variants in staged.execute(
            "SELECT fields,variants FROM records ORDER BY seq"
        ):
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
