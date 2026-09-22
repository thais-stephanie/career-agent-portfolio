"""The golden set: real postings, drafted expectations, and what may be done with them.

A golden case is four files:

    raw.txt        the normalised description text, exactly as collected
    payload.json   the archived provider payload, exactly as collected
    meta.yaml      which posting this is, and what the case exercises
    expected.yaml  what we believe a correct extraction says

THE LABELS ARE NOT GROUND TRUTH YET
-----------------------------------
Every `expected.yaml` carries `label_status: PROVISIONAL` and
`label_source: DEVELOPMENT_AGENT_DRAFT`. A development agent may assemble cases
and draft expectations; it is not a human annotator, and calling its own drafts
ground truth would make every later accuracy number a measurement of agreement
with itself.

`score` refuses to run on a provisional case for exactly that reason. The refusal
is the point: a benchmark that quietly scored against unreviewed labels would
produce a number nobody could defend, and nothing about the output would say so.

THE LABELS THEMSELVES ARE VERIFIED
----------------------------------
`validate_case` checks every cited quote against `raw.txt` using the production
verifier. A label whose evidence was paraphrased would fail the case for a
reason that has nothing to do with the extractor -- so the labels are held to
the same standard as the model's output, before anything is scored.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from career_agent.domain.enums import (
    ExtractionStatus,
    HiringScopeKind,
    RequirementStrength,
    SoftwareCentrality,
)
from career_agent.domain.fingerprint import (
    HiringScope,
    TimezoneRequirement,
    WorksiteRequirement,
)
from career_agent.domain.verify import verify_quote
from career_agent.llm.transport import ALL_DIMENSIONS

#: The committed review artifact. It lives beside the cases rather than under
#: `out/` because it is the thing handed to a reviewer, and a reviewer who has
#: to run a command to see it is a reviewer who reviews later. A test keeps it
#: byte-identical to freshly generated output, so it can never drift silently
#: away from the labels it claims to show.
REVIEW_ARTIFACT = Path("evaluation/golden-review.md")

#: The only label states that exist.
PROVISIONAL = "PROVISIONAL"
REVIEWED = "REVIEWED"

#: The only two things that may claim to have produced a label.
DEVELOPMENT_AGENT_DRAFT = "DEVELOPMENT_AGENT_DRAFT"
HUMAN_REVIEW_SOURCES = frozenset({"HUMAN_REVIEW"})

#: Dimensions that are lists rather than single-valued fields. An expectation
#: may name one of these, and it is checked differently.
LIST_DIMENSIONS = frozenset({"software", "languages", "responsibilities", "evidence"})


class GoldenError(ValueError):
    """A case is malformed, or is being used in a way its label status forbids."""


@dataclass(frozen=True)
class Expectation:
    """One assertion about one dimension, with the sentence that supports it."""

    dimension: str
    status: str | None = None
    value: Any = None
    evidence: str | None = None
    why: str = ""
    #: Values this dimension must never take on this posting. The hiring-scope
    #: cases use it to say "and never WORLDWIDE", which is the assertion that
    #: matters most and the one a status/value pair cannot express.
    forbidden_values: tuple[str, ...] = ()
    #: True when the drafter was genuinely unsure. The reviewer is being asked a
    #: question rather than shown a conclusion, and a case full of these is more
    #: honest than one that guesses confidently.
    review_question: str = ""
    #: Review state of THIS assertion, not of the case around it. Review arrives
    #: one decision at a time -- a reviewer settles the hiring scope on a
    #: posting and leaves its software centrality open -- and a case-level flag
    #: forced a choice between scoring nothing and scoring the unreviewed part.
    label_status: str = PROVISIONAL
    #: Who decided. A development agent may draft; only a human may review.
    label_source: str = DEVELOPMENT_AGENT_DRAFT

    @property
    def reviewed(self) -> bool:
        return self.label_status == REVIEWED

    @property
    def scoreable(self) -> bool:
        """Reviewed, and asserting something rather than asking something."""
        return self.reviewed and not self.review_question


@dataclass(frozen=True)
class GoldenCase:
    case_id: str
    directory: Path
    meta: dict[str, Any]
    raw_text: str
    payload: dict[str, Any]
    exercises: str
    notes: str
    expectations: tuple[Expectation, ...] = ()
    #: Some postings should not produce a fingerprint at all. A test requisition
    #: that says "This is a Test Job" describes no work, and the honest outcome
    #: is a recorded failure rather than an empty analysis.
    expect_extraction_failure: bool = False

    @property
    def reviewed(self) -> bool:
        """Every assertion in this case has been through a human.

        Derived rather than declared. A case-level flag and per-assertion flags
        that disagree is a bug waiting to be believed, so there is one source of
        truth and it is the assertions.
        """
        return bool(self.expectations) and all(e.reviewed for e in self.expectations)

    @property
    def partially_reviewed(self) -> bool:
        return any(e.reviewed for e in self.expectations) and not self.reviewed

    @property
    def label_status(self) -> str:
        return REVIEWED if self.reviewed else PROVISIONAL

    @property
    def scoreable(self) -> tuple[Expectation, ...]:
        return tuple(e for e in self.expectations if e.scoreable)


def load_case(directory: Path) -> GoldenCase:
    """Read one case from disk. Raises rather than guessing at a missing file."""
    for name in ("raw.txt", "payload.json", "meta.yaml", "expected.yaml"):
        if not (directory / name).exists():
            raise GoldenError(f"{directory.name} is missing {name}")

    meta = yaml.safe_load((directory / "meta.yaml").read_text(encoding="utf-8")) or {}
    expected = yaml.safe_load((directory / "expected.yaml").read_text(encoding="utf-8")) or {}

    return GoldenCase(
        case_id=str(expected.get("case_id", directory.name)),
        directory=directory,
        meta=meta,
        raw_text=(directory / "raw.txt").read_text(encoding="utf-8"),
        payload=json.loads((directory / "payload.json").read_text(encoding="utf-8")),
        exercises=str(meta.get("exercises", "")),
        notes=str(expected.get("notes", "")),
        expect_extraction_failure=bool(expected.get("expect_extraction_failure", False)),
        expectations=tuple(
            Expectation(
                dimension=str(row["dimension"]),
                status=row.get("status"),
                value=row.get("value"),
                evidence=row.get("evidence"),
                why=str(row.get("why", "")),
                forbidden_values=tuple(row.get("forbidden_values", ()) or ()),
                review_question=str(row.get("review_question", "")),
                label_status=str(
                    row.get("label_status", expected.get("label_status", PROVISIONAL))
                ),
                label_source=str(
                    row.get("label_source", expected.get("label_source", DEVELOPMENT_AGENT_DRAFT))
                ),
            )
            for row in expected.get("expectations", []) or []
        ),
    )


def load_cases(root: Path) -> list[GoldenCase]:
    return [load_case(d) for d in sorted(root.iterdir()) if d.is_dir()]


def golden_job_ids(root: Path) -> list[str]:
    """Which corpus postings the golden set is drawn from, in case order.

    The set *is* the manifest: each `meta.yaml` records the `job_id` the case
    was built from, and the count follows the directory listing. Anything that
    needs to run the golden postings -- a benchmark, a preflight -- resolves
    them through here rather than carrying a list of identifiers that a
    twenty-eighth case would silently invalidate.

    Refuses a case with no `job_id` rather than skipping it. A benchmark that
    quietly ran twenty-six of twenty-seven postings would report a number for a
    set that does not exist.
    """
    resolved: list[str] = []
    for case in load_cases(root):
        job_id = str(case.meta.get("job_id", "")).strip()
        if not job_id:
            raise GoldenError(
                f"{case.case_id} records no job_id in meta.yaml, so it cannot be resolved "
                "against the corpus"
            )
        if job_id not in resolved:
            resolved.append(job_id)
    return resolved


# =========================================================================
# VALIDATING THE LABELS THEMSELVES
# =========================================================================


def validate_case(case: GoldenCase) -> list[str]:
    """Everything wrong with this case's own labels. Empty means well-formed.

    Checks the label, not the extractor. A dimension name that does not exist,
    a status that is not one of the four, a quote that is not in the posting --
    each of those would fail a case for a reason that says nothing about how
    well extraction works, and each is silent unless something looks for it.
    """
    problems: list[str] = []

    for expectation in case.expectations:
        if expectation.label_status not in {PROVISIONAL, REVIEWED}:
            problems.append(
                f"{expectation.dimension}: unknown label_status {expectation.label_status!r}"
            )
        if (
            expectation.label_status == PROVISIONAL
            and expectation.label_source != DEVELOPMENT_AGENT_DRAFT
        ):
            problems.append(
                f"{expectation.dimension}: a provisional label must record that a development "
                "agent drafted it"
            )
        if (
            expectation.label_status == REVIEWED
            and expectation.label_source not in HUMAN_REVIEW_SOURCES
        ):
            problems.append(
                f"{expectation.dimension}: {expectation.label_source!r} may not mark an assertion "
                "REVIEWED. Only a human may, and the whole point of the flag is that an agent "
                "cannot promote its own draft."
            )
        if expectation.label_status == REVIEWED and expectation.review_question:
            problems.append(
                f"{expectation.dimension}: an assertion cannot be both reviewed and an open "
                "question. If the reviewer answered it, the question belongs in the notes."
            )

    known = set(ALL_DIMENSIONS) | LIST_DIMENSIONS
    statuses = {s.value for s in ExtractionStatus}

    for expectation in case.expectations:
        if expectation.dimension not in known:
            problems.append(f"{expectation.dimension!r} is not a dimension this system extracts")

        if expectation.status is not None and expectation.status not in statuses:
            problems.append(
                f"{expectation.dimension}: {expectation.status!r} is not one of the four statuses"
            )

        if expectation.status == ExtractionStatus.NOT_STATED.value and expectation.evidence:
            problems.append(
                f"{expectation.dimension}: a NOT_STATED expectation cites evidence. Silence has "
                "nothing to point at, and a citation here would be arguing the opposite case."
            )

        if expectation.evidence:
            result = verify_quote(expectation.evidence, case.raw_text, expectation.dimension)
            if not result.verified:
                problems.append(
                    f"{expectation.dimension}: the cited sentence is not in raw.txt "
                    f"({expectation.evidence[:60]!r}). The label paraphrased the posting."
                )

    if not case.expectations and not case.expect_extraction_failure:
        problems.append("a case that asserts nothing cannot fail, and measures nothing")

    return problems


# =========================================================================
# SCORING -- and refusing to
# =========================================================================


@dataclass
class CaseResult:
    """How one extraction compared with one case's expectations."""

    case_id: str
    matched: list[str] = field(default_factory=list)
    mismatched: list[str] = field(default_factory=list)
    #: Expectations the drafter flagged as uncertain. Reported separately so a
    #: pass rate is never inflated by agreement on a coin flip.
    questions: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.matched) + len(self.mismatched)

    @property
    def rate(self) -> float:
        return 1.0 if not self.total else len(self.matched) / self.total


#: How each list dimension's entries are rendered for comparison. The rendering
#: matches the grammar the labels are written in, so a mismatch reads as two
#: strings a person can diff rather than two object repr()s.
_LIST_RENDERERS: dict[str, Any] = {
    "software": lambda o: f"{o.raw_mention}:{o.centrality.value}",
    "languages": lambda o: f"{o.language_code}:{o.requirement_level.value}",
    "responsibilities": lambda o: f"{o.category.value}:{o.prominence.value}",
}


def _render_software(entries: Any) -> set[str]:
    """Software, with alternative groups rendered by their MEMBERS.

    `ALTERNATIVE` centrality means "these tools are substitutes for each other",
    and that claim is entirely in the grouping. Scoring centrality alone would
    call `Oracle:ALTERNATIVE` and `Workday:ALTERNATIVE` correct even when the
    extractor put them in *different* groups -- which is precisely the case
    where it failed to understand they were offered as alternatives, and the
    downstream consequence is a refused tool blocking a job the employer said
    could use something else.

    The group is rendered as its sorted membership rather than as the model's
    own id, because the id is arbitrary per call and comparing it would measure
    a random string:

        Oracle:ALTERNATIVE#ORACLE|PEOPLESOFT|SAP|WORKDAY

    A label writes the same form. Non-ALTERNATIVE entries are unchanged.
    """
    groups: dict[str, list[str]] = {}
    for entry in entries:
        if entry.centrality is SoftwareCentrality.ALTERNATIVE and entry.alternative_group:
            groups.setdefault(entry.alternative_group, []).append(entry.raw_mention.upper())

    rendered: set[str] = set()
    for entry in entries:
        base = f"{entry.raw_mention}:{entry.centrality.value}".upper()
        group = groups.get(entry.alternative_group or "")
        if entry.centrality is SoftwareCentrality.ALTERNATIVE and group:
            base += "#" + "|".join(sorted(group))
        rendered.add(base)
    return rendered


def _score_list(expectation: Expectation, fingerprint: Any, result: CaseResult) -> None:
    """Score a list dimension: software, languages, responsibilities.

    These are the dimensions the product's founding principle lives on -- *search
    for the work, not the title* is a claim about `responsibilities` -- and until
    this existed the scorer skipped them entirely, reporting "not present in the
    fingerprint" for every one. A benchmark that silently scored zero list
    dimensions would have chosen a model on geography alone.

    The comparison is **containment, not equality**. A label naming two
    responsibilities is asserting that those two are present at those
    prominences; it is not asserting that the extractor found nothing else, and
    a posting always describes more work than a case bothers to list. An
    extra entry is not an error. A missing or mis-prominenced one is.
    """
    entries = getattr(fingerprint, expectation.dimension, [])
    if expectation.dimension == "software":
        actual = {canonical(value) for value in _render_software(entries)}
    else:
        render = _LIST_RENDERERS[expectation.dimension]
        actual = {canonical(render(entry)) for entry in entries}

    wanted = [
        canonical(token) for token in str(expectation.value or "").split(",") if token.strip()
    ]
    missing = [token for token in wanted if token not in actual]

    forbidden = [
        token for token in (canonical(f) for f in expectation.forbidden_values) if token in actual
    ]

    if forbidden:
        result.mismatched.append(
            f"{expectation.dimension}: returned {', '.join(sorted(forbidden))}, which this case "
            "names as work the posting does not describe"
        )
    elif missing:
        result.mismatched.append(
            f"{expectation.dimension}: expected {', '.join(missing)}; "
            f"got {', '.join(sorted(actual)) or '(nothing)'}"
        )
    else:
        result.matched.append(expectation.dimension)


def render_value(value: Any) -> str:
    """A domain value as the transport grammar would have written it.

    Comparison happens on this string rather than on the object, for a reason
    worth stating: the label is written in the grammar the model is asked to
    produce, and comparing in that space means a mismatch reads as the two
    strings that differ instead of as two repr()s a person has to diff by eye.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, HiringScope):
        prefix = (
            ""
            if value.requirement_level is RequirementStrength.REQUIRED
            else f"{value.requirement_level.value}:"
        )
        if value.kind is HiringScopeKind.REGION:
            body = ",".join(r.value for r in value.regions)
        elif value.kind is HiringScopeKind.COUNTRY_LIST:
            body = ",".join(value.countries)
        else:
            body = ""
        rendered = f"{prefix}{value.kind.value}" + (f":{body}" if body else "")
        return rendered + (f":!{','.join(value.exclusions)}" if value.exclusions else "")
    if isinstance(value, TimezoneRequirement):
        hours = f"+{value.overlap_hours}" if value.overlap_hours is not None else ""
        return f"{value.kind.value}:{value.anchor}{hours}"
    if isinstance(value, WorksiteRequirement):
        return f"{value.level.value}:{', '.join(value.locations)}"
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


#: A run of two or more single-letter abbreviations written with dots --
#: `D.C.`, `U.S.`, `U.K.` -- optionally separated by one space. Two or more,
#: never one: a lone `A.` at the end of a sentence is punctuation, not an
#: abbreviation, and collapsing it would change a value rather than tidy it.
_DOTTED_INITIALISM = re.compile(r"\b(?:[A-Z]\.[ ]?){2,}")


def _undot(match: re.Match[str]) -> str:
    """`D.C. ` -> `DC `. The trailing separator survives; only the dots go."""
    text = match.group(0)
    tail = " " if text.endswith(" ") else ""
    return text.replace(".", "").replace(" ", "") + tail


def canonical(raw: Any) -> str:
    """One rendered value, in the single spelling the scorer compares.

    THIS IS TYPOGRAPHY, NOT MEANING
    -------------------------------
    Every rule here is a deterministic rewrite that a human would call the
    *same string written differently*. There is no fuzzy matching, no edit
    distance, no alias table and no geography knowledge, and there must never
    be: `Washington, DC` and `Washington, VA` are two places, and a scorer that
    could be talked into merging them would be measuring its own tolerance
    instead of the extractor.

    Stage 0 Take 2 lost a correct answer to exactly this. The label said
    `REQUIRED:Washington, DC metropolitan area`; the extractor said
    `REQUIRED:Washington, D.C. Metropolitan Area`, copied from the posting's
    own sentence. Same claim, two dots apart, scored as a miss -- which
    understates the model and, worse, hides the real failures behind noise.

    The four rewrites, and nothing else:

    1. case folding -- `Metropolitan` and `metropolitan` are one word;
    2. dotted initialisms lose their dots -- `D.C.` is `DC`, `U.S.` is `US`;
    3. runs of whitespace collapse to one space;
    4. spacing around a separating comma is dropped entirely.
    """
    text = render_value(raw).upper()
    text = _DOTTED_INITIALISM.sub(_undot, text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*,\s*", ",", text)
    return text.strip()


def _equivalent(expected: Any, actual: Any) -> bool:
    """Whether two rendered values say the same thing.

    Blind to case, to spacing around separators and to the dots inside an
    abbreviation, because `REQUIRED:San Francisco, Dublin` and
    `REQUIRED:San Francisco,Dublin` are the same claim -- and so are
    `Washington, DC` and `Washington, D.C.` -- and a benchmark that scored them
    differently would be measuring typography.
    """
    return canonical(expected) == canonical(actual)


def score(case: GoldenCase, fingerprint: Any) -> CaseResult:
    """Compare an extraction with a case's REVIEWED assertions.

    Two guards, and they do different jobs.

    **Provisional assertions are never scored.** Labels a development agent
    drafted may drive development; turning them into an accuracy number before
    a human has seen them measures agreement with the drafter and calls it
    accuracy. Review arrives one decision at a time, so this skips the
    unreviewed assertions rather than refusing the whole case -- but a case
    with nothing reviewed still refuses outright, because a case that
    contributes no assertions and reports a rate of 1.0 would quietly inflate
    every average it appears in.

    **Values are compared, not only statuses.** An earlier version compared
    `status` alone, which meant an extractor answering `EXPLICIT WORLDWIDE`
    where the label said `EXPLICIT COUNTRY_LIST:US` scored a match. That is the
    exact error this product exists to prevent, and the scorer was blind to it.
    `forbidden_values` is checked too: it is the assertion that matters most on
    the geography cases and the one a status/value pair cannot express.
    """
    scoreable = case.scoreable
    if not scoreable:
        raise GoldenError(
            f"{case.case_id} has no reviewed assertions and cannot be scored. Its labels were "
            f"drafted by a development agent, and scoring against unreviewed labels measures "
            "agreement with the drafter rather than accuracy."
        )

    result = CaseResult(case_id=case.case_id)
    fields = fingerprint.scalar_fields()

    for expectation in case.expectations:
        if expectation.scoreable and expectation.dimension in LIST_DIMENSIONS:
            _score_list(expectation, fingerprint, result)
            continue
        if not expectation.scoreable:
            result.questions.append(
                f"{expectation.dimension}: "
                + (expectation.review_question or "awaiting review, not scored")
            )
            continue

        actual = fields.get(expectation.dimension)
        if actual is None:
            result.mismatched.append(f"{expectation.dimension}: not present in the fingerprint")
            continue

        if expectation.status and actual.status.value != expectation.status:
            result.mismatched.append(
                f"{expectation.dimension}: expected {expectation.status}, got {actual.status.value}"
            )
            continue

        rendered = render_value(actual.value)
        for forbidden in expectation.forbidden_values:
            if _equivalent(forbidden, actual.value):
                result.mismatched.append(
                    f"{expectation.dimension}: returned {rendered!r}, which this case names as "
                    "a value the posting does not support"
                )
                break
        else:
            if expectation.value is not None and not _equivalent(expectation.value, actual.value):
                result.mismatched.append(
                    f"{expectation.dimension}: expected {expectation.value!r}, got {rendered!r}"
                )
            else:
                result.matched.append(expectation.dimension)

    return result
