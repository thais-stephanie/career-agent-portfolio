"""The review artifact: what a human needs to see, and nothing else.

Reviewing 24 postings in full would be a day's work and would mostly be
re-reading boilerplate. What a reviewer actually has to judge is much smaller:
for each case, the handful of assertions the case exists to test, and the exact
sentence each one rests on.

So the artifact shows the assertion beside its evidence, and marks the places
where the drafter was unsure. An open question is not a gap in the work -- it
is the drafter declining to guess on the reviewer's behalf, and it is the part
of the document worth reading first.

WHY THE DOCUMENT IS GROUPED BY THEME
------------------------------------
Ordered by case id, the artifact reads as 24 unrelated postings and a reviewer
has to hold the pattern in their head. Grouped by theme, the questions that
share an answer sit next to each other -- and a decision made once in a theme
is visibly a decision made for every case in it.

Grouping also makes coverage legible. A theme is declared here whether or not
the set has cases for it, so a theme with two cases reads as thin and a theme
with none reads as a gap. Both are findings a reviewer should be handed rather
than left to infer from an ordered list.

EVERY CASE APPEARS EXACTLY ONCE
-------------------------------
`assignment_problems` checks it. A case in two themes would be reviewed twice
and could be answered two ways; a case in none would silently never be reviewed
at all, which is the worse of the two.
"""

from __future__ import annotations

from dataclasses import dataclass

from career_agent.evaluation.golden import GoldenCase


@dataclass(frozen=True)
class Theme:
    """A group of cases that raise the same question, and why it is one question."""

    key: str
    title: str
    #: What a reviewer is being asked to decide across this whole group. Read
    #: before the cases, not after.
    decision: str
    case_ids: tuple[str, ...]
    #: Set when the set does not cover this theme well. Shown to the reviewer
    #: instead of the cases, because "we did not test this" is information.
    gap: str = ""


#: The themes, in the order a reviewer should read them. Geography comes first
#: because it carries the product's central claim and the largest share of the
#: set; refusal comes last because it is about the extractor declining to answer
#: rather than about any dimension.
THEMES: tuple[Theme, ...] = (
    Theme(
        key="geography",
        title="Hiring geography, remote work, and worksite",
        decision=(
            "Two invariants meet here. **REMOTE != WORLDWIDE**: the word remote describes an "
            "arrangement, never a hiring policy, and reading WORLDWIDE off it is the most "
            "expensive single error this system can make. **OFFICE LOCATION != HIRING SCOPE**: a "
            "named office says where the desk is, not whose applications the employer accepts. "
            "Ten of the 24 cases sit here, and the reviewer's decisions on them set the rule for "
            "the whole corpus."
        ),
        case_ids=(
            "gc-01",
            "gc-02",
            "gc-03",
            "gc-04",
            "gc-05",
            "gc-06",
            "gc-13",
            "gc-16",
            "gc-19",
            "gc-22",
        ),
    ),
    Theme(
        key="languages",
        title="Language requirements",
        decision=(
            "Where the line falls between a language that blocks a job and a language that "
            "decorates it. A HARD_REQUIREMENT the candidate does not hold eliminates the role; a "
            "NICE_TO_HAVE must not. The two cases are the clean ends of the scale, which means "
            "the ambiguous middle is untested -- see the gap note under each."
        ),
        case_ids=("gc-09", "gc-10"),
    ),
    Theme(
        key="software",
        title="Software centrality",
        decision=(
            "Whether a named tool is the job or is furniture. CORE and REQUIRED can block on a "
            "refused tool; PREFERRED and MENTIONED must not, and tools offered as alternatives to "
            "each other ('X, Y or equivalent') must not block on any one of them. Centrality is "
            "semantic, not a word count."
        ),
        case_ids=("gc-11", "gc-17", "gc-18"),
    ),
    Theme(
        key="responsibilities",
        title="Responsibilities",
        decision=(
            "Whether the extracted responsibility mix describes the work as an experienced reader "
            "would describe it, independently of the title. The two cases are deliberately "
            "opposites: **gc-25** is a role whose title hides work worth finding, **gc-26** a role "
            "whose title promises work that is not there. Both are only decidable from the body, "
            "which is the product's founding claim stated as a pair of postings.\n\n"
            "This theme was empty until the pre-spend review, and closing it before the benchmark "
            "rather than before M4 was the right call: model selection happens now, and a "
            "benchmark that never measured responsibility extraction could have picked the "
            "cheapest model that reads eligibility well and reads work badly."
        ),
        case_ids=("gc-25", "gc-26"),
    ),
    Theme(
        key="coding_management",
        title="Coding intensity and people management",
        decision=(
            "Two dimensions that decide whether a role is the kind of work the candidate wants, "
            "and that postings state indirectly. Management is usually visible in the "
            "responsibilities rather than the title; coding intensity is usually not stated at all."
        ),
        case_ids=("gc-14", "gc-15"),
    ),
    Theme(
        key="employment",
        title="Employment conditions, work authorisation and relocation",
        decision=(
            "Three questions a posting can answer separately and often answers partly: may the "
            "employer employ this person at all, will it arrange the paperwork, and must the "
            "person move. Assistance offered is not relocation required. Silence about "
            "sponsorship is not a refusal of it."
        ),
        case_ids=("gc-07", "gc-08", "gc-12", "gc-23"),
    ),
    Theme(
        key="compensation_travel",
        title="Compensation and travel",
        decision=(
            "Both are read arithmetically once extracted, so extraction has to be exact. Travel "
            "carries a second question the postings answer badly: who pays. A remote role with "
            "company-paid travel may be excellent; the same role with unstated coverage is an "
            "unpriced cost."
        ),
        case_ids=("gc-20", "gc-21"),
    ),
    Theme(
        key="provenance",
        title="Provider metadata versus description text",
        decision=(
            "When a provider field and the description disagree, neither wins in M2 -- both are "
            "recorded, in separate tables, with their provenance attached. **gc-27** is a real "
            "posting where they disagree outright: the ATS location field says `Remote`, the "
            "description says employees local to NYC are in the office three days a week.\n\n"
            "The separation is already structural -- the description call is never handed the "
            "provider payload -- so what this case adds is a posting where borrowing would be "
            "*tempting*: `REMOTE` is the answer a candidate wants and the field supplies, and it "
            "appears nowhere in the text. That makes it a leak detector rather than a preference."
        ),
        case_ids=("gc-27",),
    ),
    Theme(
        key="refusal",
        title="Refusing to answer",
        decision=(
            "A posting that describes no work should produce a recorded failure, not a fingerprint "
            "full of NOT_STATED. An empty analysis and a refused one look alike downstream and "
            "mean opposite things."
        ),
        case_ids=("gc-24",),
    ),
)


def assignment_problems(cases: list[GoldenCase]) -> list[str]:
    """Everything wrong with the theme assignment. Empty means every case has a home."""
    problems: list[str] = []

    assigned = [cid for theme in THEMES for cid in theme.case_ids]
    seen: set[str] = set()
    for cid in assigned:
        if cid in seen:
            problems.append(f"{cid} is assigned to more than one theme")
        seen.add(cid)

    present = {c.case_id for c in cases}
    for cid in sorted(present - seen):
        problems.append(f"{cid} is in no theme and would never be reviewed")
    for cid in sorted(seen - present):
        problems.append(f"{cid} is assigned to a theme and does not exist")

    return problems


def heading_above(quote: str, raw_text: str) -> str:
    """The nearest heading-looking line above a quote, or "".

    Added because the artifact hid the fact a decision turned on. gc-09's
    Salesforce bullet was shown as the single word "Salesforce", and the
    reviewer was asked whether it sat in a requirements section -- a question
    the artifact had removed the evidence for. The bullet sits under
    "Nice to Have", which makes it neither of the two answers on offer.

    Heuristic and deliberately so: a short line, no trailing full stop, not a
    bullet. It is shown as context beside the quote, never used to decide
    anything, so a wrong guess costs a reviewer one glance.
    """
    index = raw_text.find(quote)
    if index < 0:
        return ""
    for line in reversed(raw_text[:index].splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("-", "*", "•")) or len(stripped) > 60:
            continue
        if stripped.endswith((".", ",", ";", ":")):
            return stripped.rstrip(":")
        return stripped
    return ""


def _value(raw: object) -> str:
    if raw is None:
        return ": "
    if isinstance(raw, bool):
        return "true" if raw else "false"
    return str(raw)


def _excerpts(case: GoldenCase) -> list[str]:
    """The posting's own sentences, deduplicated, in the order they were cited."""
    seen: set[str] = set()
    out: list[str] = []
    for e in case.expectations:
        if e.evidence and e.evidence not in seen:
            seen.add(e.evidence)
            out.append(e.evidence)
    return out


def _case_section(case: GoldenCase) -> list[str]:
    meta = case.meta
    lines: list[str] = [
        f"### {case.case_id}: {case.exercises}",
        "",
        f"**{meta.get('company', '?')}** · {meta.get('title', '?')} · "
        f"`{meta.get('provider', '?')}` · {meta.get('description_chars', '?')} chars · "
        f"[posting]({meta.get('url', '')})",
        "",
        "**Why this case exists**",
        "",
        f"{case.notes}",
        "",
    ]

    excerpts = _excerpts(case)
    if excerpts:
        lines += ["**Source excerpts**", ""]
        for quote in excerpts:
            heading = heading_above(quote, case.raw_text)
            if heading:
                lines.append(f"> *under the heading* **{heading}**  ")
            lines.append(f"> {quote}")
            lines.append(">")
        lines[-1] = ""

    if case.expect_extraction_failure:
        lines += [
            "**Draft expectation**",
            "",
            "> **Extraction FAILS.** No fingerprint is stored. A posting that describes no work "
            "has nothing to analyse, and an empty fingerprint would claim otherwise.",
            "",
        ]

    scored = [e for e in case.expectations if not e.review_question]
    if scored:
        lines += [
            "**Draft expectation**",
            "",
            "| Dimension | Expected | Evidence from the posting | Why | Label |",
            "|---|---|---|---|---|",
        ]
        for e in scored:
            expected = e.status or ""
            if e.value is not None:
                expected += f" · `{_value(e.value)}`"
            if e.forbidden_values:
                expected += f" · **never** {', '.join(e.forbidden_values)}"
            quote = f"“{e.evidence}”" if e.evidence else "*(silence, nothing to quote)*"
            mark = "✅ reviewed" if e.reviewed else "⏳ provisional"
            cells = [f"`{e.dimension}`", expected, quote, e.why or ": ", mark]
            safe = [c.replace("|", "\\|").replace("\n", " ") for c in cells]
            lines.append("| " + " | ".join(safe) + " |")
        lines.append("")

    questions = [e for e in case.expectations if e.review_question]
    if questions:
        lines += ["**Open questions**", ""]
        for e in questions:
            lines += [
                f"> ❓ **`{e.dimension}`**  ",
                f"> {e.review_question}",
                "",
            ]
            if e.why:
                lines += [f"> *Drafter's note:* {e.why}", ""]

    reviewed = sum(1 for e in case.expectations if e.reviewed)
    total = len(case.expectations)
    lines += [
        f"**Label status**: {reviewed} of {total} assertions reviewed. "
        + (
            "This case is settled."
            if reviewed == total and total
            else "The rest were drafted by the development agent and are not ground truth."
        ),
        "",
        "---",
        "",
    ]
    return lines


def review_markdown(cases: list[GoldenCase]) -> str:
    """One document, grouped by theme, ready to be read top to bottom."""
    by_id = {c.case_id: c for c in cases}
    total = sum(len(c.expectations) for c in cases)
    questions = sum(1 for c in cases for e in c.expectations if e.review_question)
    provisional = sum(1 for c in cases if not c.reviewed)

    lines: list[str] = [
        "# Golden set: review artifact",
        "",
        f"**{len(cases)} cases · {total} assertions · {questions} open questions · "
        f"{provisional} awaiting review**",
        "",
        "Every expectation below was drafted by the development agent, not by a human "
        "annotator. Until each case is reviewed and its `label_status` changed to "
        "`REVIEWED`, the scorer refuses to run: scoring against unreviewed labels would "
        "measure agreement with the drafter rather than accuracy.",
        "",
        "Every quote shown was verified to appear in that posting's `raw.txt` by the same "
        "verifier the extractor's output is held to. `raw.txt` itself is checked against the "
        "`content_hash` recorded when the posting was collected, so what is quoted here is what "
        "the employer published.",
        "",
        "**Read the open questions first.** They are the places the drafter declined to guess on "
        "the reviewer's behalf, and each one, once answered, becomes a rule applied to thousands "
        "of postings.",
        "",
    ]

    problems = assignment_problems(cases)
    if problems:
        lines += ["> ⚠️ **Theme assignment is broken:**", ""]
        lines += [f"> - {p}" for p in problems]
        lines.append("")

    # -- contents ----------------------------------------------------------
    lines += ["## Contents", ""]
    for theme in THEMES:
        count = len(theme.case_ids)
        plural = "case" if count == 1 else "cases"
        marker = "**no cases: see the gap note**" if theme.gap else f"{count} {plural}"
        lines.append(f"- [{theme.title}](#{theme.key}): {marker}")
    lines += ["", "---", ""]

    # -- themes ------------------------------------------------------------
    for theme in THEMES:
        lines += [
            f'<a id="{theme.key}"></a>',
            "",
            f"## {theme.title}",
            "",
            f"**What the reviewer is deciding.** {theme.decision}",
            "",
        ]

        if theme.gap:
            lines += [f"> ⚠️ {theme.gap}", "", "---", ""]
            continue

        ids = ", ".join(f"`{cid}`" for cid in theme.case_ids)
        lines += [f"*Cases: {ids}*", "", "---", ""]

        for cid in theme.case_ids:
            case = by_id.get(cid)
            if case is not None:
                lines += _case_section(case)

    return "\n".join(lines)
