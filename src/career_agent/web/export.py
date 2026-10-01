"""The GOOD + STRONG export: a CSV a spreadsheet opens correctly.

UTF-8 with a byte-order mark, because Excel on Windows reads a CSV without
one in the machine's legacy code page and turns "São Paulo" into mojibake.
Quoted as needed by the `csv` module, with CRLF line ends. A cell that a
spreadsheet would run as a FORMULA (starting with =, +, - or @) gets a
leading apostrophe: a posting title comes from an employer, and a title
like `=HYPERLINK(...)` must stay text.

Public posting facts and the person's own application status only. What is
private to the profile beyond that (CV, evidence, search phrases, notes,
which query found a posting) is not a column and never will be.
"""

from __future__ import annotations

import csv
import io
from typing import Any

#: Rows read per query while exporting (the list's own page ceiling).
PAGE_SIZE = 500

#: The two Search Fit bands the export holds, strongest first.
GOOD_PLUS = ("STRONG", "GOOD")

#: Words a person reads for a status (Career Agent's English labels).
_STATUS = {
    "DISCOVERED": "Found",
    "SHORTLISTED": "Interested",
    "APPLIED": "Applied",
    "INTERVIEW": "Interviewing",
    "OFFER": "Offer",
    "HIRED": "Hired",
    "REJECTED": "Rejected",
    "WITHDRAWN": "Withdrawn",
    "ARCHIVED": "Archived",
}

COLUMNS = (
    ("Search Fit band", lambda j: j.get("fit_band")),
    ("Search Fit", lambda j: j.get("match_score")),
    ("Title", lambda j: j.get("title")),
    ("Company", lambda j: j.get("company_name")),
    ("Location", lambda j: j.get("location_raw")),
    ("Work model", lambda j: j.get("work_model")),
    ("Source", lambda j: j.get("provider")),
    ("Posted", lambda j: (j.get("posted_at") or "")[:10] or None),
    ("Status", lambda j: _STATUS.get(str(j.get("application_status") or ""))),
    ("URL", lambda j: j.get("url")),
    ("Job id", lambda j: j.get("job_id")),
)


def _cell(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


def _csv(header: list[str], rows: Any) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(header)
    writer.writerows(rows)
    return ("\ufeff" + out.getvalue()).encode("utf-8")


def good_strong_csv(rows: list[dict[str, Any]]) -> bytes:
    return _csv([n for n, _ in COLUMNS], ([_cell(get(r)) for _, get in COLUMNS] for r in rows))


#: The Search Fit feedback export (migration 0046). Private to the profile:
#: the person's own judgements plus the public facts needed to read them. No
#: search phrases, CV, evidence, findings or provider output.
FEEDBACK_COLUMNS = (
    ("Job id", "job_id"),
    ("Title", "title"),
    ("Company", "company"),
    ("Source", "provider"),
    ("URL", "url"),
    ("Search Fit when judged", "match_score"),
    ("Band when judged", "fit_band"),
    ("Search Fit schema", "schema_version"),
    ("Settings version", "config_version"),
    ("Verdict", "verdict"),
    ("Reason", "reason"),
    ("Note", "note"),
    ("First answered", "created_at"),
    ("Last changed", "updated_at"),
)


def fit_feedback_csv(rows: list[dict[str, Any]]) -> bytes:
    return _csv(
        [n for n, _ in FEEDBACK_COLUMNS],
        ([_cell(r.get(k)) for _, k in FEEDBACK_COLUMNS] for r in rows),
    )
