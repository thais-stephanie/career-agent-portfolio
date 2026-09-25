"""A certification line, read as the fields it states. Never guessed.

THE DEFECT (2026-09-25, the owner's own CV)
-------------------------------------------
Certifications arrived as one line each, "Title | Issuer | Sep 2026 | Sep 2027
| credential-id", and Career Profile showed that raw pipe-delimited string.
The claim text and its source line are provenance and stay exactly as they
are; this module only READS them, for display.

THE SHAPES IT READS, and nothing else:

    Title
    Title | Issuer
    Title | Issuer | Issued
    Title | Issuer | Issued | Expires-or-"No expiration"
    Title | Issuer | Issued | Expires-or-"No expiration" | Credential ID
    Title | Issuer | Issued | Credential ID

A column may be empty: "-", an en or em dash, or "N/A" fills its place and
states nothing. After the expiry column, one unbroken token is the ID.

A date may be a month and year ("Sep 2026", "September 2026", "09/2026",
"2026-09", Portuguese month names too) or a year alone. A field may say what
it is ("Issued Sep 2026", "Expires Sep 2027", "Credential ID ABC-123").

**Two dates are issued and expiry ONLY in that position, in that order, and
only when the second is not earlier than the first.** Anything else -- a third
date, a second free-text field, dates out of order, an ID before the dates --
returns None: the line is shown as written rather than as fields a reader
invented.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from career_agent.cv.structure import fold

_MONTHS = {
    "jan": 1, "january": 1, "janeiro": 1,
    "feb": 2, "february": 2, "fev": 2, "fevereiro": 2,
    "mar": 3, "march": 3, "marco": 3,
    "apr": 4, "april": 4, "abr": 4, "abril": 4,
    "may": 5, "mai": 5, "maio": 5,
    "jun": 6, "june": 6, "junho": 6,
    "jul": 7, "july": 7, "julho": 7,
    "aug": 8, "august": 8, "ago": 8, "agosto": 8,
    "sep": 9, "sept": 9, "september": 9, "set": 9, "setembro": 9,
    "oct": 10, "october": 10, "out": 10, "outubro": 10,
    "nov": 11, "november": 11, "novembro": 11,
    "dec": 12, "december": 12, "dez": 12, "dezembro": 12,
}  # fmt: skip

#: Words that say a credential does not lapse. Only ever read as the expiry.
_NO_EXPIRY = frozenset(
    {
        "no expiration",
        "no expiration date",
        "does not expire",
        "never expires",
        "present",
        "sem expiracao",
        "sem validade",
        "nao expira",
        "atual",
    }
)
_ISSUED_LABEL = re.compile(r"^(issued|issue date|emitido|emissao|obtained|awarded)\s*:?\s*", re.I)
_EXPIRES_LABEL = re.compile(
    r"^(expires|expiry|expiration|valid until|valido ate|validade|expira)\s*:?\s*", re.I
)
_ID_LABEL = re.compile(
    # The label must END: a colon, "#" or a space. "ID-4432" is one token, not
    # the label "ID" with "-4432" after it.
    r"^(credential id|credential|license number|licence number|id|credencial|codigo)"
    r"(?:\s*[:#]\s*|\s+)",
    re.I,
)


@dataclass(frozen=True)
class Certificate:
    title: str
    issuer: str | None = None
    issued: str | None = None  # "YYYY-MM" or "YYYY", as stated
    expires: str | None = None
    no_expiry: bool = False
    credential_id: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _date(field: str) -> str | None:
    """A month and year, or a year, and nothing else in the field."""
    value = fold(field).strip().rstrip(".")
    if m := re.fullmatch(r"(\d{4})", value):
        return m.group(1) if 1900 <= int(m.group(1)) <= 2100 else None
    if m := re.fullmatch(r"(\d{1,2})\s*[/.-]\s*(\d{4})", value):
        month, year = int(m.group(1)), m.group(2)
        return f"{year}-{month:02d}" if 1 <= month <= 12 else None
    if m := re.fullmatch(r"(\d{4})\s*-\s*(\d{1,2})", value):
        year, month = m.group(1), int(m.group(2))
        return f"{year}-{month:02d}" if 1 <= month <= 12 else None
    if m := re.fullmatch(r"([a-z]+)\.?\s*(?:de\s+)?(\d{4})", value):
        named = _MONTHS.get(m.group(1))
        return f"{m.group(2)}-{named:02d}" if named else None
    return None


def _is_no_expiry(field: str) -> bool:
    return re.sub(r"\s+", " ", fold(field).strip().rstrip(".")) in _NO_EXPIRY


def _empty(field: str) -> bool:
    """An empty table cell: dashes, or "N/A". It fills its column and states
    NOTHING -- in the expiry column it means "not stated", never "does not
    expire"."""
    value = fold(field).strip()
    dashes = {"-", chr(0x2013), chr(0x2014)}  # hyphen, en dash, em dash
    return bool(value) and (set(value) <= dashes or value in {"n/a", "na", "none"})


def _credential(field: str) -> str | None:
    """A labelled ID, or one unbroken token that carries a digit."""
    labelled = _ID_LABEL.match(field)
    if labelled and field[labelled.end() :].strip():
        return field[labelled.end() :].strip()
    token = field.strip()
    if " " not in token and 4 <= len(token) <= 64 and re.search(r"\d", token) and not _date(token):
        return token
    return None


def read_certificate(text: str) -> Certificate | None:
    """The fields a certification line states, or None when it is ambiguous."""
    if "|" not in text:
        return _read_unpiped(text.strip())
    fields = [f.strip() for f in text.strip().strip("|").split("|")]
    fields = [f for f in fields if f]
    if not fields or len(fields) > 6:
        return None
    title, rest = fields[0], fields[1:]
    if _date(title) or _credential(title) == title:
        return None
    issuer: str | None = None
    issued: str | None = None
    expires: str | None = None
    no_expiry = False
    credential: str | None = None
    stage = 0  # 0 issuer, 1 issued, 2 expires, 3 credential, 4 nothing more
    for field in rest:
        expires_label = _EXPIRES_LABEL.match(field)
        issued_label = _ISSUED_LABEL.match(field)
        if expires_label:
            body = field[expires_label.end() :]
            if stage > 2:
                return None
            if _is_no_expiry(body):
                no_expiry = True
            elif (when := _date(body)) is not None:
                expires = when
            else:
                return None
            stage = 3
            continue
        if issued_label:
            when = _date(field[issued_label.end() :])
            if when is None or stage > 1:
                return None
            issued, stage = when, 2
            continue
        if _empty(field):
            if stage >= 4:
                return None
            stage += 1  # the column is there, and says nothing
            continue
        if stage == 3 and " " not in field.strip() and 4 <= len(field.strip()) <= 64:
            # The credential column, after the expiry column: one token,
            # with or without a digit, is the ID.
            credential, stage = field.strip(), 4
            continue
        when = _date(field)
        if when is not None:
            if stage <= 1:
                issued, stage = when, 2
            elif stage == 2:
                expires, stage = when, 3
            else:
                return None  # a third date: not a shape this reads
            continue
        if _is_no_expiry(field):
            if stage != 2:
                return None
            no_expiry, stage = True, 3
            continue
        if stage == 0 and _credential(field) is None:
            if not _looks_like_issuer(field):
                return None  # prose in the issuer's place: show the line as written
            issuer, stage = field, 1
            continue
        if stage >= 1 and (found := _credential(field)) is not None and stage <= 3:
            credential, stage = found, 4
            continue
        return None  # a second free-text field, or one out of place
    if issued and expires and _last_month(expires) < _first_month(issued):
        return None  # out of order: not provably issued-then-expiry
    return Certificate(
        title=title,
        issuer=issuer,
        issued=issued,
        expires=expires,
        no_expiry=no_expiry,
        credential_id=credential,
    )


def _first_month(value: str) -> str:
    return value if len(value) > 4 else f"{value}-01"


def _last_month(value: str) -> str:
    return value if len(value) > 4 else f"{value}-12"


#: Labels a note starts with. "Note: renewal pending" is a remark about a
#: certificate, not an issuer and a title.
_NOT_ISSUERS = frozenset(
    {"note", "notes", "status", "comment", "obs", "nota", "observacao", "pending", "renewal"}
)


def _looks_like_issuer(field: str) -> bool:
    """A name, not a sentence: short, no figures, and written like a name.

    "Salesforce", "HubSpot Academy", "Scrum Alliance" are issuers. "reduced
    cost by 30%" is a result somebody wrote beside a title, and structuring it
    as an issuer would put words in the certificate's mouth.
    """
    value = field.strip()
    if not value or len(value) > 60 or len(value.split()) > 6:
        return False
    if re.search(r"[\d%]", value):
        return False
    return value[:1].isupper() or value[:1] in "#&("


def _read_unpiped(text: str) -> Certificate | None:
    """The forms written without columns.

    Issuer: Title            a short issuer, no digits, before one colon
    Title (Sep 2026)         a date in trailing parentheses
    Title, 2021              a date after the last comma or spaced hyphen
    Title                    anything else: the title alone, as written
    """
    if not text:
        return None
    issuer: str | None = None
    labelled = re.match(r"^([^:\d]{2,40}):\s+(.+)$", text)
    if (
        labelled
        and _looks_like_issuer(labelled.group(1))
        and not re.search(r"certif", fold(labelled.group(1)))
        and fold(labelled.group(1)).strip() not in _NOT_ISSUERS
        and labelled.group(2)[:1].isupper()
    ):
        issuer, text = labelled.group(1).strip(), labelled.group(2).strip()
    issued: str | None = None
    dated = re.match(r"^(.+?)\s*\(([^()]+)\)$", text)
    if dated and _date(dated.group(2)):
        text, issued = dated.group(1).strip(), _date(dated.group(2))
    else:
        tail = re.match(r"^(.+?)(?:,|\s+-)\s*([^,]+)$", text)
        if tail and _date(tail.group(2)):
            text, issued = tail.group(1).strip(), _date(tail.group(2))
    if not text or _date(text):
        return None
    return Certificate(title=text, issuer=issuer, issued=issued)
