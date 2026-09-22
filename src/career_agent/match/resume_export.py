"""The plan, as something a person can paste. Layout only, never words.

`resume.py` decides WHICH of her own sentences a posting argues for and in
what order. This turns that decision into lines she can take somewhere else,
and the split between the two modules is the safety property rather than a
tidiness one.

WHY TWO MODULES
---------------
`tests/integration/test_application_workspace.py` asserts, by walking the
syntax tree, that `resume.py` contains no f-string and no concatenation of a
string literal. That is what makes "it writes nothing" checkable rather than
promised. A renderer needs to join a bullet to a line and a blank line between
sections, so putting it in that module would have meant deleting the check
that protects the important half.

So the planner stays unable to compose a string at all, and everything that
composes lives here, where a narrower rule applies and is asserted the same
way: **a claim's text and a requirement's label are whole LINES.** They are
never interpolated, never joined to a neighbouring phrase, never truncated and
never re-cased. The only literals this module joins to anything are the
markers in `_MARKER`, which are punctuation.

WHAT THE EXPORT IS FOR
----------------------
Applying somewhere. The workflow this product supports ends at the employer's
own form, and it deliberately does not submit one -- so the last useful thing
it can do is hand her the sentences she already confirmed, ordered by what
this posting actually asked for, with the gaps named beside them.

WHAT IT IS NOT
--------------
It is not a resume, and the header says so in the file itself. It is not a
cover letter and there is no code path that would produce one. It contains no
sentence this program wrote about her: every line below the headings is either
something she confirmed, word for word, or a requirement label out of her own
configuration.

THE GAPS TRAVEL WITH IT. A document listing only the strengths is the
flattering view the whole preparation surface exists to refuse, and it is
worse on the way into an interview than anywhere else.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from career_agent.match.resume import ResumePlan


class LineKind(StrEnum):
    """What one line of the export is, so a renderer never has to guess."""

    #: A heading this product wrote. The only lines that are ours.
    HEADING = "HEADING"
    #: A sentence SHE confirmed, verbatim.
    CLAIM = "CLAIM"
    #: What that claim answers in this posting, from her own configuration.
    ANSWERS = "ANSWERS"
    #: A requirement label, from her own configuration.
    REQUIREMENT = "REQUIREMENT"
    #: The employer's own words: a job title, a company name.
    EMPLOYER = "EMPLOYER"
    #: A sentence this product wrote ABOUT THE FILE, never about her.
    NOTE = "NOTE"
    BLANK = "BLANK"


@dataclass(frozen=True, slots=True)
class Line:
    kind: LineKind
    text: str


#: What each kind is prefixed with. Punctuation, and the whole of what this
#: module is allowed to add to somebody's sentence.
_MARKER: dict[LineKind, str] = {
    LineKind.HEADING: "",
    LineKind.CLAIM: "- ",
    LineKind.ANSWERS: "    ",
    LineKind.REQUIREMENT: "- ",
    LineKind.EMPLOYER: "",
    LineKind.NOTE: "",
    LineKind.BLANK: "",
}

#: Every heading and note, as constants. They are in this table rather than
#: inline so that the structural test can tell "a string this module owns"
#: from "a string it was given", and so a reader can see the entire set of
#: words this file contributes in one place.
#:
#: They are ENGLISH here on purpose. This is a file she takes away, produced
#: by a CLI and by a route that returns text/plain; the interface's catalogue
#: renders the screen and does not reach a downloaded artefact. Translating
#: the export is worth doing and is a separate piece of work, because it
#: needs a decision about which language a file on disk is in when the person
#: reading it later may not be the person who exported it.
WORDS: dict[str, str] = {
    "title": "Preparing an application",
    "for": "For this posting",
    "lead": "Lead with these. They are your own sentences, in the order this posting argues for.",
    "answers": "answers:",
    "gaps": "Asked for, and nothing you have confirmed answers it",
    "unresolved": "Asked for, and this could not judge it either way",
    "spare": "Confirmed, and this posting did not ask for it",
    "note": (
        "Every line above marked with a dash is a sentence you confirmed yourself. "
        "Nothing in this file was written by Career Agent about you, and nothing "
        "here was rephrased, strengthened or summarised."
    ),
    "not_a_resume": (
        "This is not a resume and not a cover letter. It is a selection and an "
        "order, so that you write one."
    ),
    "nothing": "Nothing.",
}


def lines(plan: ResumePlan, *, title: str, company: str) -> tuple[Line, ...]:
    """The export as typed lines, before anything is joined.

    Returned as data so a caller can render it as text, as HTML, or as
    something else, without this module having to know. It also makes the
    important property directly assertable: a test can check that every
    `CLAIM` line equals a claim's text exactly, which no amount of reading a
    formatted string would prove.
    """
    out: list[Line] = [
        Line(LineKind.HEADING, WORDS["title"]),
        Line(LineKind.BLANK, ""),
        Line(LineKind.HEADING, WORDS["for"]),
        Line(LineKind.EMPLOYER, title),
        Line(LineKind.EMPLOYER, company),
        Line(LineKind.BLANK, ""),
        Line(LineKind.HEADING, WORDS["lead"]),
    ]
    if not plan.lead_with:
        out.append(Line(LineKind.NOTE, WORDS["nothing"]))
    for suggestion in plan.lead_with:
        out.append(Line(LineKind.CLAIM, suggestion.claim.text))
        for label in suggestion.answers:
            out.append(Line(LineKind.ANSWERS, label))

    out.extend(_section(WORDS["gaps"], plan.gaps))
    out.extend(_section(WORDS["unresolved"], plan.unresolved))
    out.extend(_section(WORDS["spare"], tuple(claim.text for claim in plan.not_relevant)))

    out.extend(
        [
            Line(LineKind.BLANK, ""),
            Line(LineKind.NOTE, WORDS["note"]),
            Line(LineKind.NOTE, WORDS["not_a_resume"]),
        ]
    )
    return tuple(out)


def _section(heading: str, items: tuple[str, ...]) -> list[Line]:
    """One heading and its items, or the heading and an honest "nothing".

    A section that disappears when empty would let a reader believe there were
    no gaps when the truth is that nobody looked. Every section is always
    present.
    """
    out = [Line(LineKind.BLANK, ""), Line(LineKind.HEADING, heading)]
    if not items:
        out.append(Line(LineKind.NOTE, WORDS["nothing"]))
        return out
    out.extend(Line(LineKind.REQUIREMENT, item) for item in items)
    return out


def as_text(plan: ResumePlan, *, title: str, company: str) -> str:
    """The lines, joined. The only place a marker meets somebody's sentence."""
    rendered = [
        _MARKER[line.kind] + line.text for line in lines(plan, title=title, company=company)
    ]
    return "\n".join(rendered).rstrip() + "\n"
