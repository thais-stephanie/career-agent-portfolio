"""Building an intake package from documents, on this machine.

WHY THIS EXISTS BESIDE THE EXTERNAL-AI PATH
--------------------------------------------
`docs/product/intake-prompt-v1.md` describes copying a prompt into an assistant
and bringing back `career-agent-import.json`. That path is real and some people
will want it, because they already have an assistant they trust.

**It is not the default, and it must not be.** Choosing it sends somebody's CV
and LinkedIn export to an AI provider. That is a decision only they can make,
and a product that made it for them by making it the easy path would have made
it for them.

So this is the other half: the same contract, produced locally, with the
documents never leaving the machine. `cv/extract.py` turns a file into
characters and `cv/propose.py` says what those characters appear to claim --
both already exist, both are deterministic, and neither confirms anything. This
module renders their output as a package so that a locally-read document and an
assistant-read one arrive through exactly one review, with one set of
refusals and one set of review states.

WHAT IT ADDS TO THE EXISTING EXTRACTOR
---------------------------------------
One thing: it reads PERIODS. `cv.propose` flags whether a line carries dates
and deliberately does not parse them, because a CV line is prose and a claim
built from prose has nowhere structured to put a date.

A package does have somewhere, and the contract requires both halves -- the
document's own wording AND the normalised form -- so a wrong reading is
visible beside the words it came from rather than standing in for them. That is
what makes the CV's "Jun 2024 - Mar 2025" and a LinkedIn export's "June 2024 -
April 2025" resolvable as a disagreement rather than as two unrelated lines.

**A date it cannot read is left out.** `normalized` is optional in the contract
precisely so that "the document said 2019" can be carried without this module
inventing a month for it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from career_agent.domain.enums import ClaimType
from career_agent.intake.models import (
    CURRENT_SCHEMA_VERSION,
    DatePoint,
    DeclaredSource,
    Evidence,
    Generator,
    IntakePackage,
    MetricMention,
    Period,
    ProposedClaim,
    SourceKind,
)

#: Month names this reader knows, English and Portuguese, in the forms a CV or
#: a LinkedIn export actually writes them. Named rather than derived from a
#: locale, because a locale is a property of the machine and these documents
#: are written in whatever language their author chose.
_MONTHS: dict[str, int] = {}
for index, names in enumerate(
    [
        ("jan", "january", "janeiro"),
        ("feb", "february", "fev", "fevereiro"),
        ("mar", "march", "marco", "março"),
        ("apr", "april", "abr", "abril"),
        ("may", "maio", "mai"),
        ("jun", "june", "junho"),
        ("jul", "july", "julho"),
        ("aug", "august", "ago", "agosto"),
        ("sep", "sept", "september", "set", "setembro"),
        ("oct", "october", "out", "outubro"),
        ("nov", "november", "novembro"),
        ("dec", "december", "dez", "dezembro"),
    ],
    start=1,
):
    for name in names:
        _MONTHS[name] = index

#: "Jun 2024", "June 2024", "junho de 2024". The year is required; a month name
#: on its own dates nothing.
_MONTH_YEAR = re.compile(
    r"\b(?P<month>[A-Za-zçÇ]{3,9})\.?\s*(?:de\s+)?(?P<year>(?:19|20)\d{2})\b",
    re.IGNORECASE,
)

#: A bare year, for "2019 - 2023".
_YEAR = re.compile(r"\b(?P<year>(?:19|20)\d{2})\b")

#: What a document writes between the two ends of a period. The long dashes are
#: DATA here -- they are what other people's documents contain, not text this
#: project authored.
#:
#: **The word separators require whitespace on both sides, and that is a bug
#: fix rather than tidiness.** The first version allowed zero spaces around
#: them, so the Portuguese separator `a` matched the letter inside `February`
#: and split the month away from its own year: the date read as "2024" with no
#: month, and a normalisation silently disappeared. Caught by a test written
#: against LinkedIn's own date line.
#: The dash class covers the hyphen AND the en and em dashes. Those characters
#: are DATA here: they are what other people's documents contain, and this
#: project's own text still may not use them.
#:
#: Leaving them out was not cosmetic. The owner's CV writes its date ranges
#: with an em dash, so every role parsed as a start with no end while the same
#: role from a LinkedIn export parsed with both -- and the two documents then
#: "disagreed" about 202 of 334 claims, none of it real. A conflict this
#: program manufactures is worse than one it misses: it asks somebody to
#: resolve a disagreement that does not exist.
_SEPARATOR = re.compile(
    r"\s+(?:to|at[eé]|a)\s+|\s*[-–—]\s*",  # punctuation-check: allow: other people's date ranges
    re.IGNORECASE,
)

#: Words meaning the role has not ended.
_CURRENT = re.compile(r"\b(current|present|now|atual|presente|hoje)\b", re.IGNORECASE)

#: A sentence carrying a figure. The same shape `cv.propose` flags, kept here
#: so a metric reaches the package as the WHOLE SENTENCE and never as a number.
_MEASURED = re.compile(r"\d+\s*(?:%|percent|k\b|x\b)|\$\s*\d|\b\d{2,}\b")


#: Bullet glyphs a PDF text layer leaves at the start of a line. Stripped so
#: a claim reads as a sentence rather than as a rendering artefact.
_BULLET_CHARS = ("•", "●", "▪", "�", "-", "*")

#: The shortest run of characters worth proposing as a claim.
_MIN_STATEMENT = 12


#: The en and em dash, built from code points rather than written.
#: They are DATA -- what other people's documents put between two dates --
#: and this project's own text may not contain them. `chr` keeps both true.
_LONG_DASHES = chr(0x2013) + chr(0x2014)


def _read_date(text: str) -> tuple[str, str | None] | None:
    """One date, as the document wrote it and as this reads it.

    Returns `(original, normalized_or_None)`. A month it does not recognise
    yields the original with no normalisation, which the contract allows on
    purpose: carrying "Q3 2024" unnormalised is honest, and inventing a month
    for it is not.
    """
    match = _MONTH_YEAR.search(text)
    if match is not None:
        month = _MONTHS.get(match.group("month").casefold().rstrip("."))
        original = match.group(0).strip()
        if month is None:
            return (original, None)
        return (original, f"{int(match.group('year')):04d}-{month:02d}")

    year = _YEAR.search(text)
    if year is not None:
        # A year with no month. Kept, never widened to January: "2019" and
        # "January 2019" are different statements.
        return (year.group(0), None)
    return None


def read_period(line: str) -> Period | None:
    """The span a line states, or None when it states none.

    Both ends are read from the SAME line, split on the separator the document
    used. A line with one date and a word meaning "still there" is a current
    role; a line with one date and nothing else has a start and no end, which
    is different from a current role and is stored as such.
    """
    if _CURRENT.search(line):
        head = _CURRENT.split(line)[0]
        start = _read_date(head)
        if start is None:
            return None
        return Period(start=DatePoint(original=start[0], normalized=start[1]), current=True)

    parts = [p for p in _SEPARATOR.split(line) if p.strip()]
    dates = [d for d in (_read_date(p) for p in parts) if d is not None]
    # De-duplicate a date read twice out of overlapping fragments.
    unique: list[tuple[str, str | None]] = []
    for date in dates:
        if date not in unique:
            unique.append(date)

    if not unique:
        return None
    start = unique[0]
    if len(unique) == 1:
        return Period(start=DatePoint(original=start[0], normalized=start[1]))
    end = unique[1]
    period = Period(
        start=DatePoint(original=start[0], normalized=start[1]),
        end=DatePoint(original=end[0], normalized=end[1]),
    )
    return period


def _employer_of(line: str) -> str | None:
    """The employer a CV's company line names, or None.

    Deliberately shallow: the first comma-separated fragment of a line that
    carries dates, which is the shape both a CV and a LinkedIn export use --
    "Northwind, Belo Horizonte, MG, Brazil (remote) Jun 2024". Anything cleverer would
    be guessing, and a guessed employer anchors a conflict group to the wrong
    role.
    """
    head = line.split(",")[0].strip()
    head = re.sub(r"\s*\(.*?\)\s*", " ", head).strip()
    # The long dashes are written as escapes rather than as characters: they
    # are DATA -- what other people's documents contain -- and writing them
    # literally would put them in this project's own source.
    head = _MONTH_YEAR.sub("", head).strip(" -|" + _LONG_DASHES)
    if not head or len(head) > 120:
        return None
    # A fragment that is mostly digits is a date line, not a company.
    if sum(ch.isdigit() for ch in head) > len(head) / 3:
        return None
    return head


def _linkedin_claims(text: str, ref: str, filename: str) -> list[ProposedClaim]:
    """The Experience section of an export, as claims.

    Every claim in one position carries that position's employer and period,
    which is what makes a LinkedIn role comparable with the same role on a CV.
    The title is one claim; each body line is another, exactly as the CV reader
    treats a role line and its bullets.
    """
    from career_agent.intake.linkedin import read_positions

    claims: list[ProposedClaim] = []
    for position in read_positions(text):
        period = read_period(position.period_line)
        statements = [position.title, *position.body]
        for statement in statements:
            cleaned = statement.strip().lstrip("".join(_BULLET_CHARS)).strip()
            if not (_MIN_STATEMENT <= len(cleaned) <= 2000):
                continue
            claims.append(
                ProposedClaim(
                    type=ClaimType.EMPLOYMENT,
                    text=cleaned,
                    source_ref=ref,
                    employer=position.employer,
                    role_title=position.title[:200],
                    period=period,
                    evidence=Evidence(
                        quote=statement.strip()[:1200],
                        locator=f"{filename}, Experience, {position.period_line}",
                    ),
                    metrics=(
                        [MetricMention(original=cleaned[:400])] if _MEASURED.search(cleaned) else []
                    ),
                )
            )
    return claims


@dataclass(frozen=True, slots=True)
class IntakeDocument:
    """One document to read, as BYTES rather than as a path.

    WHY BYTES AND NOT A PATH
    ------------------------
    The CLI hands this module files, and for a long time that was the only
    caller, so `build_package` took paths and called `read_bytes` itself. The
    browser cannot: an upload arrives as base64 inside a JSON body, and the one
    thing V1.3 settled about reading somebody's CV is that **it is never
    written to disk** -- `extract_bytes` reads a stream precisely so that
    nothing lands in the system temp directory.

    Spelling a path for the benefit of this function would have meant writing
    the upload out and deleting it afterwards, which is a privacy regression
    dressed as a convenience. So the parameter is the bytes, and the CLI reads
    its own files.

    `name` is the filename as the person's own machine spells it. It reaches
    `extract_bytes` (which picks a reader from the extension) and the claim
    locators, so a reviewer can see which document a line came from.

    `kind` is `RESUME`, `LINKEDIN` or `DOCUMENT`, and it is DECLARED by the
    caller rather than sniffed: a file called `profile.pdf` could be either,
    and guessing wrong mislabels the provenance of every claim in it.
    """

    name: str
    data: bytes
    ref: str
    kind: str


def build_package(
    documents: list[IntakeDocument],
    *,
    generator_name: str = "career-agent local extractor",
) -> IntakePackage:
    """One package from documents read on this machine.

    **Nothing here is confirmed.** The result is a package, which is a set of
    proposals, and it still has to be imported and reviewed one claim at a time.
    """
    from career_agent.cv.extract import extract_bytes
    from career_agent.cv.propose import read_cv

    sources: list[DeclaredSource] = []
    claims: list[ProposedClaim] = []

    for item in documents:
        ref, kind = item.ref, item.kind
        sources.append(DeclaredSource(ref=ref, kind=kind, title=item.name))
        document = extract_bytes(item.data, filename=item.name)

        # An export's EXPERIENCE section needs its own reader -- reading it
        # with the CV's produced 255 employment claims with no employer on any
        # of them. See `intake/linkedin.py`.
        #
        # **Only that section.** The CV reader handles Top Skills,
        # Certifications and Education in an export perfectly well, and an
        # earlier version of this loop skipped them along with Experience:
        # twelve certifications the export DID state stopped being proposed,
        # and the package then reported "nothing here about certifications",
        # which is a claim about the file that was true only because of this
        # code.
        is_export = kind == SourceKind.LINKEDIN
        if is_export:
            claims.extend(_linkedin_claims(document.text, ref, item.name))

        read = read_cv(document.text)

        # The employer for a run of employment proposals is the nearest line
        # above them that names one. A CV lists a role, then its bullets; the
        # bullets belong to the role above.
        employer: str | None = None
        period_context: Period | None = None
        for proposal in read.proposals:
            if is_export and proposal.claim_type is ClaimType.EMPLOYMENT:
                # Already read, and read better, above.
                continue
            if proposal.claim_type is ClaimType.EMPLOYMENT:
                named = _employer_of(proposal.evidence)
                if named and read_period(proposal.evidence) is not None:
                    employer = named
                    period_context = read_period(proposal.evidence)
            else:
                employer = None
                period_context = None

            claims.append(
                ProposedClaim(
                    type=proposal.claim_type,
                    text=proposal.text,
                    source_ref=ref,
                    employer=employer if proposal.claim_type is ClaimType.EMPLOYMENT else None,
                    period=(
                        read_period(proposal.evidence) or period_context
                        if proposal.claim_type is ClaimType.EMPLOYMENT
                        else None
                    ),
                    # The line the proposal came from. `cv.propose` already
                    # keeps it, and it is the whole reason a reviewer can see
                    # the context an item was taken from.
                    evidence=Evidence(
                        quote=proposal.evidence[:1200],
                        locator=f"{item.name}, {proposal.section}",
                    ),
                    metrics=(
                        [MetricMention(original=proposal.text[:400])]
                        if proposal.has_measurement and _MEASURED.search(proposal.text)
                        else []
                    ),
                )
            )

    return IntakePackage(
        schema_version=CURRENT_SCHEMA_VERSION,
        generator=Generator(kind="SELF", name=generator_name),
        sources=sources,
        claims=claims,
    )


def package_json(package: IntakePackage) -> str:
    """The package as a file somebody can read, keep or hand back to us."""
    import json

    return (
        json.dumps(
            package.model_dump(mode="json", exclude_none=True),
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )


def kinds() -> tuple[str, ...]:
    """The document kinds a caller may declare. Named, never sniffed."""
    return tuple(sorted(SourceKind.ALL))
