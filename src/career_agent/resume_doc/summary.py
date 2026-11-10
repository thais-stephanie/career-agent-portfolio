"""A Professional Summary composed from confirmed evidence (Resume Tailor
v1.1: 2 to 4 sentences, usually 35 to 80 words; shorter and specific beats
long and repetitive, so the range is an editorial aim, never a quota).

No model, no invented prose. A summary is put together from facts the profile
already holds, each sentence traceable:

1. the role: the current (or most recent) shown role's title, and the shown
   roles' employers ("{title} with experience at {A}, {B} and {C}.");
2. the skills: up to three skills a job's confirmed evidence holds, the ones
   the job asks for first ("Skilled in {a}, {b} and {c}.");
3. one or two confirmed statements of those roles, VERBATIM (a leading "I"
   goes): the ones answering the job's asks first, then the ones with a
   number, then the rest by role. At most one of them may repeat a bullet the
   resume shows; the other must say something the page does not already, and
   none repeats the page's only bullet.

Projects (academic or personal experiences, `master.PROJECT_KINDS`) lead
when the version shows no role, or one role and a project line answers the
job more strongly than any line of it (the rule `tailor.sections_ahead`
moves sections by). Then their statements compete for the statement slots
and their skills get a sentence of their own, each sentence saying where it
comes from ("Academic project: ...", "Skills from academic projects: ...").
A project is never a role: it adds no title and no employer.

The only words of its own are the connecting words of the frames
(`FRAMES`, `PROJECT_FRAMES`). Years are never computed and nothing is padded:
no statement is "not enough evidence" (None) and the person is told, never
given filler. A person with no job yet gets a summary from their projects or
none, never an invented role; nor does a role, or a project, with only a
line the page already shows. The frames speak the language of the person's statements.
Numbers can only come from a confirmed
statement's own words. The Master is never written here; the Tailor uses
this for a version whose Master has no summary, and offers it as a
suggestion when the Master's summary exists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from career_agent.resume_doc import jd
from career_agent.resume_doc.models import ResumeDocument
from career_agent.storage.career_repo import HIGHLIGHT_CATEGORIES

MAX_WORDS, MIN_SENTENCES, MAX_SENTENCES = 80, 2, 4
#: Statements a summary carries, and how many may repeat a shown bullet.
MAX_STATEMENTS, MAX_REPEATED = 2, 1
#: Analyze calls a longer summary a paragraph (`analyze.LONG_SUMMARY_CHARS`).
MAX_CHARS = 700
MAX_SKILLS, MAX_EMPLOYERS = 3, 3
FRAMES = {
    "en": ("{title} with experience at {where}.", "Skilled in {skills}.", "and"),
    "pt": ("{title} com experiência em {where}.", "Competências em {skills}.", "e"),
    "es": ("{title} con experiencia en {where}.", "Habilidades en {skills}.", "y"),
}
#: (academic statement, independent statement, project skills, the kinds'
#: plural words): the origin is always said, a project never reads as a job.
PROJECT_FRAMES = {
    "en": ("Academic project: {said}", "Independent project: {said}",
           "Skills from {kinds} projects: {skills}.", ("academic", "independent")),
    "pt": ("Projeto acadêmico: {said}", "Projeto independente: {said}",
           "Competências de projetos {kinds}: {skills}.", ("acadêmicos", "independentes")),
    "es": ("Proyecto académico: {said}", "Proyecto independiente: {said}",
           "Habilidades de proyectos {kinds}: {skills}.", ("académicos", "independientes")),
}  # fmt: skip
_KINDS = ("ACADEMIC", "PERSONAL")
_NUMBER = re.compile(r"\d")
_PRONOUN = re.compile(r"^(?:I|Eu|Yo)\s+(?=\w)")


@dataclass
class Composed:
    text: str
    evidence_ids: list[str]
    #: Each sentence with what it rests on: {text, kind, evidence_ids}.
    sentences: list[dict[str, Any]] = field(default_factory=list)

    @property
    def words(self) -> int:
        return len(self.text.split())


def _language(doc: ResumeDocument, statements: list[Any]) -> str:
    """The document's language when the person chose one; on the default
    (English, what a Master starts with) the language of the person's own
    statements, the words the summary carries verbatim, so a summary of
    Portuguese statements does not get English frames."""
    from career_agent.resume_doc.parse import language_of

    lang = doc.language[:2]
    said = " ".join(c.text for c in statements)
    if lang == "en" and said.strip():
        lang = language_of(said)
    return lang if lang in FRAMES else "en"


def _listed(items: list[str], conjunction: str) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} {conjunction} {items[-1]}"


def _sentence(text: str) -> str:
    said = " ".join(text.split())
    cut = _PRONOUN.sub("", said, count=1)
    said = cut[:1].upper() + cut[1:] if cut != said else said
    return said if said.endswith((".", "!", "?")) else f"{said}."


def _roles(doc: ResumeDocument) -> list[Any]:
    return [
        e for e in doc.experience if not e.hidden and "experience" not in doc.layout.hidden_sections
    ]


def grounds(doc: ResumeDocument) -> str:
    """What a composed summary may say besides its claims' words: the frames'
    own words and the shown roles' titles and employers."""
    words = [*FRAMES.values(), *(f[:3] + f[3] for f in PROJECT_FRAMES.values())]
    frames = " ".join(" ".join(f).replace("{", " ").replace("}", " ") for f in words)
    roles = " ".join(f"{e.display_title} {e.employer}" for e in _roles(doc))
    return f"{frames} {roles}"


def compose(
    doc: ResumeDocument,
    claims: dict[str, Any],
    supports: list[Any] = (),  # type: ignore[assignment]
) -> Composed | None:
    """The summary `doc` can have from confirmed evidence, or None when there
    is not enough for it. `claims` is the profile's confirmed claims by key
    (`tailor.sources`); `supports` the job's asks, if any."""
    from career_agent.resume_doc.master import JOB, PROJECT, skill_origin

    roles = _roles(doc)
    off = "projects" in doc.layout.hidden_sections
    projects = [] if off else [p for p in doc.projects if not p.hidden]
    relevance: dict[str, float] = {}
    own = doc.summary.id if doc.summary else None
    for sup in supports:
        for source, value in [*sup.shown, *sup.unshown]:
            if own and source.line_id == own:
                continue  # the summary weighs nothing for itself: the same text every time
            for key in [source.claim_key, *source.evidence_ids]:
                if key:
                    weight = sup.requirement.importance * value
                    relevance[key] = max(relevance.get(key, 0.0), weight)
    placed = {e.experience_id: i for i, e in enumerate(roles) if e.experience_id}
    on_projects = {k for p in projects for b in p.bullets if not b.hidden for k in b.evidence_ids}
    work = [
        c for c in claims.values()
        if c.category in HIGHLIGHT_CATEGORIES and c.experience_id in placed and c.text.strip()
    ]  # fmt: skip
    built = [
        c for c in claims.values()
        if c.category in HIGHLIGHT_CATEGORIES and getattr(c, "project", None) in _KINDS
        and c.claim_key in on_projects and c.text.strip()
    ]  # fmt: skip

    def best(found: list[Any]) -> float:
        return max((relevance.get(c.claim_key, 0.0) for c in found), default=0.0)

    # Projects lead as `tailor.sections_ahead` moves them: no role, or one role
    # and a project that answers the job more strongly than any line of it.
    lead = bool(built) and (not roles or (len(roles) == 1 and best(built) > best(work)))
    statements = work + (built if lead else [])
    if not statements:
        return None
    lang = _language(doc, statements)
    title_frame, skills_frame, conjunction = FRAMES[lang]
    academic, independent, project_skills, plural = PROJECT_FRAMES[lang]
    sentences: list[dict[str, Any]] = []
    if roles:
        now = next((e for e in roles if e.current or e.end is None), roles[0])
        if not now.display_title.strip() or not now.employer.strip():
            return None
        employers: list[str] = []
        for e in roles:
            if e.employer.strip() and e.employer not in employers:
                employers.append(e.employer)
        sentences.append({
            "text": title_frame.format(title=now.display_title.strip(),
                                       where=_listed(employers[:MAX_EMPLOYERS], conjunction)),
            "kind": "ROLE", "evidence_ids": [],
        })  # fmt: skip
    asked = {t for s in supports for t in (*s.requirement.named, *s.requirement.concepts)}
    skills: list[tuple[str, list[str]]] = []
    built_skills: list[tuple[str, list[str]]] = []
    if "skills" not in doc.layout.hidden_sections:
        for group in doc.skills:
            for item in group.items if not group.hidden else []:
                ids = [k for k in item.evidence_ids if k in claims]
                if not ids or ids != list(item.evidence_ids):
                    continue
                origin = skill_origin(ids, claims)
                if origin == JOB:
                    skills.append((item.label, ids))
                elif (
                    lead
                    and origin == PROJECT
                    and all(getattr(claims[k], "project", None) in _KINDS for k in ids)
                ):
                    built_skills.append((item.label, ids))
    for found in (skills, built_skills):
        found.sort(key=lambda s: jd.folded(s[0]) not in asked)  # stable: asked first
    if skills:
        chosen = skills[:MAX_SKILLS]
        sentences.append({
            "text": skills_frame.format(skills=_listed([s[0] for s in chosen], conjunction)),
            "kind": "SKILLS", "evidence_ids": [k for _, ids in chosen for k in ids],
        })  # fmt: skip
    tail: list[dict[str, Any]] = []
    if built_skills:
        chosen = built_skills[:MAX_SKILLS]
        cited = [k for _, found in chosen for k in found]
        kinds = [plural[i] for i, kind in enumerate(_KINDS)
                 if any(claims[k].project == kind for k in cited)]  # fmt: skip
        tail.append({
            "text": project_skills.format(kinds=_listed(kinds, conjunction),
                                          skills=_listed([s[0] for s in chosen], conjunction)),
            "kind": "PROJECT_SKILLS", "evidence_ids": cited,
        })  # fmt: skip
    from career_agent.resume_doc.tailor import _WORD, _participates

    def rank(c: Any) -> tuple[bool, float, bool, bool, int]:
        # Shared work ("worked with IT, who installed ...") is true, and last:
        # a summary leads with what the person did. On a tie, a job's line.
        shared = _participates(_WORD.findall(jd.folded(c.text)))
        return (
            shared,
            -relevance.get(c.claim_key, 0.0),
            not _NUMBER.search(c.text),
            c.experience_id not in placed,
            placed.get(c.experience_id, len(placed)),
        )

    statements.sort(key=rank)
    shown_bullets = [
        jd.folded(_sentence(b.text))
        for entry in [*roles, *projects]
        for b in entry.bullets
        if not b.hidden and b.text.strip()
    ]
    only_bullet = len(set(shown_bullets)) == 1
    words = sum(len(s["text"].split()) for s in sentences + tail)
    chars = sum(len(s["text"]) + 1 for s in sentences + tail)
    seen = {jd.folded(s["text"]) for s in sentences + tail}
    said = repeated = 0
    for c in statements:
        text = _sentence(c.text)
        key = jd.folded(text)
        if said >= MAX_STATEMENTS or len(sentences) + len(tail) >= MAX_SENTENCES or key in seen:
            continue
        repeats = key in shown_bullets
        if repeats and (repeated >= MAX_REPEATED or only_bullet):
            continue  # the page already says it
        built_by = getattr(c, "project", None)
        if built_by in _KINDS:
            text = (academic if built_by == "ACADEMIC" else independent).format(said=text)
        n = len(text.split())
        if words + n > MAX_WORDS or chars + len(text) + 1 > MAX_CHARS:
            continue
        seen.add(key)
        sentence = {"text": text, "kind": "STATEMENT", "evidence_ids": [c.claim_key],
                    "repeats_bullet": repeats}  # fmt: skip
        if built_by in _KINDS:
            sentence["project"] = built_by
        sentences.append(sentence)
        words, chars = words + n, chars + len(text) + 1
        said, repeated = said + 1, repeated + repeats
    sentences += tail
    if len(sentences) < MIN_SENTENCES or not any(s["kind"] == "STATEMENT" for s in sentences):
        return None
    if all(s.get("repeats_bullet") for s in sentences if s["kind"] != "ROLE"):
        return None  # only the role and a line the page already shows: nothing to add
    ids = list(dict.fromkeys(k for s in sentences for k in s["evidence_ids"]))
    return Composed(" ".join(s["text"] for s in sentences), ids, sentences)
