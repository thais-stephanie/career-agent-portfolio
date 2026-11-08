"""Page limit and length budget (resume-tailoring spec v1, sections 7 and B).

* **The limit is the person's, never a model's.** `page_limit(exception)` is 2,
  or 3 only when the person turned the page-limit exception on for that job.
  It takes a real bool and nothing else, so a value read from a model's answer
  (a number, a string) is refused rather than trusted.
* **Cuts hide, never delete.** `fit` works on a copy of a version and hides
  lines, entries and groups in the spec's cut order; everything hidden stays in
  the document (and in its JSON export), so the person can show it again.
* **Pages are measured, not promised.** `fit` asks `measure(doc)` for a page
  count after each round of cuts, at most `max_rounds` times. The default
  measure estimates from visible words; `pdf_pages` prints the real PDF.
* **Truth before length.** The header, the headline, the summary and the
  strongest shown line for each required ask are never cut, and no role loses
  its last line. When the version still does not fit, the result says so
  (`CONFLICT`) with what could not be cut, instead of cutting proof.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from career_agent.resume_doc import ats
from career_agent.resume_doc.models import ResumeDocument, upgrade_resume_document
from career_agent.resume_doc.render import render_html

DEFAULT_PAGES, EXCEPTION_PAGES = 2, 3
#: The spec's word budgets, per page limit.
WORD_BUDGET = {2: (850, 1000), 3: (1300, 1450)}
#: Visible words one page holds in the spec's layout (9.5 pt, single column):
#: the budget's own ratio, 1,000 words in 2 pages. A heuristic, never a
#: substitute for `pdf_pages` before a file is made.
WORDS_PER_PAGE = 500
MAX_CERTIFICATIONS = 5
OLD_ROLE_YEARS = 6
MAX_ROUNDS = 4


def page_limit(exception: bool) -> int:
    """2, or 3 when the person turned the exception on. Only a real bool counts."""
    if not isinstance(exception, bool):
        raise TypeError("the page-limit exception is the person's yes or no")
    return EXCEPTION_PAGES if exception else DEFAULT_PAGES


def visible_text(doc: ResumeDocument) -> list[str]:
    """What the printed resume says, paragraph by paragraph (the renderer decides)."""
    return [b.text for b in ats.blocks(render_html(doc, mode="print").html)]


def word_count(doc: ResumeDocument) -> int:
    return sum(len(text.split()) for text in visible_text(doc))


def estimate_pages(doc: ResumeDocument) -> int:
    return max(1, math.ceil(word_count(doc) / WORDS_PER_PAGE))


def pdf_pages(doc: ResumeDocument) -> int:
    """The real page count: the print HTML through the export's own browser."""
    from career_agent.resume_doc.export import print_pdf

    data, _ = print_pdf(render_html(doc, mode="print").html)
    return len(ats.read_pdf(data))


@dataclass
class Fit:
    status: str  # FITS, CUT, CONFLICT
    limit: int
    pages: int
    words: int
    budget: tuple[int, int]
    #: Each cut, in the order applied: {step, ref}.
    cuts: list[dict[str, str]] = field(default_factory=list)
    #: What was never cut and still did not fit (CONFLICT only).
    protected: list[str] = field(default_factory=list)
    rounds: int = 0
    notes: list[str] = field(default_factory=list)


def _requirement_lines(supports: list[Any]) -> tuple[set[str], set[str], set[str]]:
    """(lines answering any ask, lines answering a required ask, the strongest
    shown line per required ask), as line ids."""
    answering, required, strongest = set(), set(), set()
    for sup in supports:
        if sup.requirement.eligibility:
            continue
        ids = [s.line_id for s, _ in sup.shown if s.line_id and (s.claim_key or s.evidence_ids)]
        answering |= set(ids)
        if sup.requirement.hardness == "REQUIRED":
            required |= set(ids)
            if ids:
                strongest.add(ids[0])
    return answering, required, strongest


def _plan(doc: ResumeDocument, supports: list[Any], year: int) -> list[tuple[str, str]]:
    """Every cut the spec allows, in its order: (step, ref). Refs are
    `certifications/<id>`, `projects/<id>`, `skills/<id>`, `bullet/<id>`."""
    answering, required, strongest = _requirement_lines(supports)
    strength: dict[str, float] = {}
    for sup in supports:
        for s, v in sup.shown:
            if s.line_id:
                strength[s.line_id] = max(strength.get(s.line_id, 0.0), v)
    cuts: list[tuple[str, str]] = []
    certs = [c for c in doc.certifications if not c.hidden]
    relevant = {
        s.line_id for sup in supports for s, _ in sup.shown if s.category == "CERTIFICATION"
    }
    certs.sort(key=lambda c: c.id not in relevant)  # stable: relevant first, then as listed
    cuts += [("CERTIFICATIONS_BEYOND_TOP_5", f"certifications/{c.id}")
             for c in certs[MAX_CERTIFICATIONS:]]  # fmt: skip
    for p in doc.projects:
        if not p.hidden and not {b.id for b in p.bullets} & required:
            cuts.append(("PROJECT_NOT_TIED_TO_MUST_HAVE", f"projects/{p.id}"))

    def weakest(bullets: list[Any]) -> list[Any]:
        return sorted(bullets, key=lambda b: (b.id in answering, strength.get(b.id, 0.0)))

    roles = [e for e in doc.experience if not e.hidden]
    old = [e for e in roles if not e.current and e.end and year - e.end.year > OLD_ROLE_YEARS]
    for e in reversed(old):  # the oldest role first
        cuts += [("BULLET_OF_OLD_ROLE", f"bullet/{b.id}")
                 for b in weakest([b for b in e.bullets if not b.hidden])]  # fmt: skip
    asked = {t for sup in supports for t in (*sup.requirement.concepts, *sup.requirement.named)}
    for g in doc.skills:
        said = " ".join(i.label for i in g.items).casefold()
        if not g.hidden and not any(t and t in said for t in asked):
            cuts.append(("SKILLS_NOT_IN_POSTING", f"skills/{g.id}"))
    rest = [b for e in reversed(roles) if e not in old for b in e.bullets if not b.hidden]
    cuts += [("WEAKEST_BULLET", f"bullet/{b.id}") for b in weakest(rest)]
    return [c for c in cuts if c[1].removeprefix("bullet/") not in strongest]


def _hide(data: dict[str, Any], ref: str) -> int | None:
    """Hide `ref` in a document dump and return about how many words it printed;
    None when nothing was hidden (unknown, or a role's last line)."""
    kind, _, ident = ref.partition("/")
    if kind == "bullet":
        for entry in data["experience"]:
            for b in entry["bullets"]:
                if b["id"] == ident:
                    if sum(not x.get("hidden") for x in entry["bullets"]) <= 1:
                        return None
                    b["hidden"] = True
                    return len(b["text"].split())
        return None
    for item in data[kind]:
        if item["id"] == ident:
            item["hidden"] = True
            return _words(item)
    return None


def _words(node: Any) -> int:
    """Words a dumped node prints, counted over the fields a page shows."""
    if isinstance(node, list):
        return sum(_words(v) for v in node)
    if not isinstance(node, dict):
        return 0
    said = sum(len(node[k].split()) for k in ats._SHOWN_KEYS if isinstance(node.get(k), str))
    return said + sum(_words(v) for v in node.values() if isinstance(v, (list, dict)))


def fit(
    doc: ResumeDocument,
    supports: list[Any],
    *,
    limit: int,
    year: int,
    measure: Callable[[ResumeDocument], int] = estimate_pages,
    max_rounds: int = MAX_ROUNDS,
) -> tuple[ResumeDocument, Fit]:
    """`doc` with the spec's cuts applied (hidden) until it measures `limit`
    pages or fewer. `year` is today's year, passed in so the result is the same
    for the same input. Each round hides enough for the word estimate to fit,
    then measures; a round that still measures over hides more."""
    if limit not in WORD_BUDGET:
        raise ValueError("the page limit is 2 or 3")
    budget = WORD_BUDGET[limit]
    plan = _plan(doc, supports, year)
    data = doc.model_dump(mode="json")
    current = doc
    pages = measure(current)
    result = Fit("FITS", limit, pages, word_count(current), budget, rounds=1)
    target_words, words = budget[1], result.words
    while pages > limit and result.rounds < max_rounds:
        hid = False
        # Counted down by what each cut printed; the page count is measured.
        while plan and words > target_words:
            step, ref = plan.pop(0)
            gone = _hide(data, ref)
            if gone is not None:
                result.cuts.append({"step": step, "ref": ref})
                words, hid = words - gone, True
        if not hid:
            if not plan:
                break
            target_words = int(target_words * 0.9)
            continue
        current = upgrade_resume_document(data)
        pages, words = measure(current), word_count(current)
        result.rounds += 1
        # Still over: the estimate was generous. Aim lower next round.
        target_words = int(target_words * limit / max(pages, limit + 1))
    result.pages, result.words = pages, word_count(current)
    if pages > limit:
        _, _, strongest = _requirement_lines(supports)
        result.status = "CONFLICT"
        result.protected = sorted(strongest)
        result.notes.append("what proves the required asks does not fit the page limit")
    elif result.cuts:
        result.status = "CUT"
    if (
        limit == EXCEPTION_PAGES
        and pages == EXCEPTION_PAGES
        and result.words < 2.5 * (WORDS_PER_PAGE)
    ):
        result.notes.append("the third page is less than half full: two pages may be enough")
    return current, result
