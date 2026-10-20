"""The list export: a CSV a spreadsheet opens correctly.

Without a Deep Reading filter it holds the GOOD and STRONG postings of the
list; with one, exactly the filtered list (`api.export_good_strong`).

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

#: The deep reading's recommendation codes, as the screen words them.
_DEEP_LEVEL = {
    "STRONG": "Apply",
    "PREPARE": "Apply, preparing",
    "CHECK_FIRST": "Check first",
    "NOT_COMPARED": "Aligned, history not compared",
    "TO_CONFIRM": "To confirm",
    "LOW": "Low priority",
    "OTHER_WORK": "Different main activity",
}
_ALIGNMENT = {
    "CENTRAL": "main activity is work you asked for",
    "SECONDARY": "work you asked for is a secondary duty",
    "NEIGHBOUR": "close to your work, but a different job",
    "UNRELATED": "main activity is other work",
    "UNCLEAR": "unclear what the job is",
}
_HISTORY = {
    "SHOWN": "history well shown",
    "PARTLY": "history partly shown",
    "LITTLE": "history little shown",
    "NOT_SENT": "history not compared",
    "NONE_STATED": "no technical requirement to compare",
}
#: `deep_run.freshness` states, under the names the export promises.
_DEEP_STATE = {"current": "current", "evidence": "evidence_changed", "stale": "stale"}


def _deep(j: dict[str, Any], key: str) -> Any:
    return (j.get("deep_export") or {}).get(key)


def _deep_level(j: dict[str, Any]) -> str | None:
    """The CURRENT level only: a level that no longer holds is never shown as one."""
    level = _deep(j, "level_then")
    if level is None:
        return None
    if _deep(j, "stale"):
        return "Not current"
    return _DEEP_LEVEL.get(level, level)


def _deep_reason(j: dict[str, Any]) -> str | None:
    """The reading's own structured grounds, in words: alignment, history, the
    counted requirements and the first central gap."""
    assessment = _deep(j, "assessment")
    if not assessment:
        return None
    counts = assessment.get("counts") or {}
    parts = [
        _ALIGNMENT.get(assessment.get("alignment"), str(assessment.get("alignment"))),
        _HISTORY.get(assessment.get("history"), str(assessment.get("history"))),
    ]
    if counts.get("mandatory"):
        parts.append(
            f"{counts['mandatory']} required: {counts.get('shown', 0)} shown,"
            f" {counts.get('partial', 0) + counts.get('declared', 0) + counts.get('review', 0)}"
            f" partly, {counts.get('not_shown', 0)} not shown"
        )
    reqs = assessment.get("requirements") or []
    gaps = [reqs[i]["quote"] for i in assessment.get("central_gaps") or [] if i < len(reqs)]
    if gaps:
        parts.append(f"central gap: {gaps[0]}")
    return "; ".join(parts)


def _salary(j: dict[str, Any]) -> str | None:
    pay = j.get("salary")
    if not pay:
        return None
    if isinstance(pay, str):
        return pay
    low, high = pay.get("min"), pay.get("max")
    if low is None and high is None:
        return pay.get("raw")
    band = f"{low:,.0f}" if high in (None, low) else f"{low or 0:,.0f}-{high:,.0f}"
    period = f"/{str(pay['period']).lower()}" if pay.get("period") else ""
    return f"{pay.get('currency') or ''} {band}{period}".strip()


COLUMNS = (
    ("Search Fit band", lambda j: j.get("fit_band")),
    ("Search Fit", lambda j: j.get("match_score")),
    ("Deep Reading level", _deep_level),
    (
        "Deep Reading level when read",
        lambda j: _DEEP_LEVEL.get(_deep(j, "level_then") or "") if _deep(j, "stale") else None,
    ),
    ("Deep Reading state", lambda j: _DEEP_STATE.get(_deep(j, "state") or "")),
    ("Deep Reading work relation", lambda j: _deep(j, "work_relation")),
    ("Deep Reading grounds", _deep_reason),
    ("Title", lambda j: j.get("title")),
    ("Company", lambda j: j.get("company_name")),
    ("Salary", _salary),
    ("Location", lambda j: j.get("location_raw")),
    ("Work model", lambda j: j.get("work_model")),
    ("Source", lambda j: j.get("provider")),
    ("Posted", lambda j: (j.get("posted_at") or "")[:10] or None),
    ("Freshness", lambda j: (j.get("freshness") or "").title() or None),
    ("Status", lambda j: _STATUS.get(str(j.get("application_status") or ""))),
    ("URL", lambda j: j.get("url")),
    ("Job id", lambda j: j.get("job_id")),
    # The WHOLE advert as stored, line breaks and all: never the excerpt a
    # card shows. The csv module quotes it; a spreadsheet keeps the breaks.
    ("Job description", lambda j: j.get("description_full")),
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
