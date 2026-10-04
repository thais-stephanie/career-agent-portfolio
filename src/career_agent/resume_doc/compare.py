"""What differs between two versions of a resume, said in the resume's terms.

Read-only: it is handed two documents and returns a list. Items are matched
by their stable ids (a version copied from another keeps them), so a line
that was reworded is CHANGED rather than one removed and one added. Nothing
here summarises, scores or judges a difference; it names it.

Each change is `{area, change, where, before, after}`:
* area: headline, summary, entry (an experience, project, education or
  custom section), title (an experience's display title), line, skill,
  section (shown or hidden), order (the order of the sections shown);
* change: ADDED, REMOVED, CHANGED, HIDDEN or SHOWN;
* where: the section ref, or the entry the line belongs to (its words in B,
  else in A).
"""

from __future__ import annotations

from typing import Any

from career_agent.resume_doc.models import ResumeDocument

Change = dict[str, Any]
_LISTS = ("experience", "projects", "education")


def _change(area: str, change: str, where: str, before: Any = None, after: Any = None) -> Change:
    return {"area": area, "change": change, "where": where, "before": before, "after": after}


def _name(entry: Any) -> str:
    return str(
        getattr(entry, "display_title", None)
        or getattr(entry, "name", None)
        or getattr(entry, "institution", None)
        or getattr(entry, "heading", "")
    )


def _lines(where: str, a: list[Any], b: list[Any]) -> list[Change]:
    out: list[Change] = []
    old = {line.id: line for line in a}
    new = {line.id: line for line in b}
    for line in b:
        before = old.get(line.id)
        if before is None:
            out.append(_change("line", "ADDED", where, None, line.text))
            continue
        if before.text != line.text:
            out.append(_change("line", "CHANGED", where, before.text, line.text))
        if before.hidden != line.hidden:
            out.append(
                _change("line", "HIDDEN" if line.hidden else "SHOWN", where, None, line.text)
            )
    out += [_change("line", "REMOVED", where, line.text) for line in a if line.id not in new]
    return out


def _entries(section: str, a: list[Any], b: list[Any]) -> list[Change]:
    out: list[Change] = []
    old = {e.id: e for e in a}
    new = {e.id: e for e in b}
    for entry in b:
        where = _name(entry)
        before = old.get(entry.id)
        if before is None:
            out.append(_change("entry", "ADDED", section, None, where))
            continue
        if section == "experience" and before.display_title != entry.display_title:
            out.append(
                _change("title", "CHANGED", where, before.display_title, entry.display_title)
            )
        if before.hidden != entry.hidden:
            out.append(
                _change("entry", "HIDDEN" if entry.hidden else "SHOWN", section, None, where)
            )
        items = "items" if section == "custom" else "bullets"
        out += _lines(where, getattr(before, items), getattr(entry, items))
    out += [_change("entry", "REMOVED", section, _name(e)) for e in a if e.id not in new]
    return out


def compare(a: ResumeDocument, b: ResumeDocument) -> list[Change]:
    """The differences from version A to version B."""
    out: list[Change] = []
    for block in ("headline", "summary"):
        old, new = getattr(a, block), getattr(b, block)
        before, after = (old.text if old else None), (new.text if new else None)
        if before != after:
            kind = "ADDED" if before is None else "REMOVED" if after is None else "CHANGED"
            out.append(_change(block, kind, block, before, after))
    for section in _LISTS:
        out += _entries(section, getattr(a, section), getattr(b, section))
    out += _entries("custom", a.custom_sections, b.custom_sections)
    old_skills = {i.label for g in a.skills for i in g.items}
    new_skills = {i.label for g in b.skills for i in g.items}
    out += [_change("skill", "ADDED", "skills", None, s) for s in sorted(new_skills - old_skills)]
    out += [_change("skill", "REMOVED", "skills", s) for s in sorted(old_skills - new_skills)]
    hidden_a, hidden_b = set(a.layout.hidden_sections), set(b.layout.hidden_sections)
    out += [_change("section", "HIDDEN", ref) for ref in sorted(hidden_b - hidden_a)]
    out += [_change("section", "SHOWN", ref) for ref in sorted(hidden_a - hidden_b)]
    shown_a = [r for r in a.layout.section_order if r not in hidden_a]
    shown_b = [r for r in b.layout.section_order if r not in hidden_b]
    common = set(shown_a) & set(shown_b)
    if [r for r in shown_a if r in common] != [r for r in shown_b if r in common]:
        out.append(_change("order", "CHANGED", "layout", shown_a, shown_b))
    return out
