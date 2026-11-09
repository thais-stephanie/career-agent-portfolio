"""A Professional Summary composed from confirmed evidence (resume-tailoring
spec v1, S3: 3 to 5 sentences, about 70 to 110 words).

No model, no invented prose. A summary is put together from facts the profile
already holds, each sentence traceable:

1. the role: the current (or most recent) shown role's title, and the shown
   roles' employers ("{title} with experience at {A}, {B} and {C}.");
2. the skills: up to three skills a job's confirmed evidence holds, the ones
   the job asks for first ("Skilled in {a}, {b} and {c}.");
3. then confirmed statements of those roles, VERBATIM (a leading "I" goes):
   the ones answering the job's asks first, then the ones with a number,
   then the rest by role.

The only words of its own are the connecting words of the two frames
(`FRAMES`). Years are never computed, nothing is padded to reach 70 words:
fewer than three sentences is "not enough evidence" (None) and the person is
told, never given filler. Numbers can only come from a confirmed statement's
own words. The Master is never written here; the Tailor uses this for a
version whose Master has no summary, and offers it as a suggestion when the
Master's summary exists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from career_agent.resume_doc import jd
from career_agent.resume_doc.models import ResumeDocument
from career_agent.storage.career_repo import HIGHLIGHT_CATEGORIES

MIN_WORDS, MAX_WORDS, MIN_SENTENCES, MAX_SENTENCES = 70, 110, 3, 5
#: Analyze calls a longer summary a paragraph (`analyze.LONG_SUMMARY_CHARS`).
MAX_CHARS = 700
MAX_SKILLS, MAX_EMPLOYERS = 3, 3
FRAMES = {
    "en": ("{title} with experience at {where}.", "Skilled in {skills}.", "and"),
    "pt": ("{title} com experiência em {where}.", "Competências em {skills}.", "e"),
}
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


def _language(doc: ResumeDocument) -> str:
    return "pt" if doc.language[:2] == "pt" else "en"


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
    frames = " ".join(" ".join(f).replace("{", " ").replace("}", " ") for f in FRAMES.values())
    roles = " ".join(f"{e.display_title} {e.employer}" for e in _roles(doc))
    return f"{frames} {roles}"


def compose(
    doc: ResumeDocument,
    claims: dict[str, Any],
    supports: list[Any] = (),  # type: ignore[assignment]
) -> Composed | None:
    """The summary `doc` can have from confirmed evidence, or None when there
    is not enough for three sentences. `claims` is the profile's confirmed
    claims by key (`tailor.sources`); `supports` the job's asks, if any."""
    from career_agent.resume_doc.master import JOB, skill_origin

    roles = _roles(doc)
    if not roles:
        return None
    now = next((e for e in roles if e.current or e.end is None), roles[0])
    if not now.display_title.strip() or not now.employer.strip():
        return None
    title_frame, skills_frame, conjunction = FRAMES[_language(doc)]
    employers: list[str] = []
    for e in roles:
        if e.employer.strip() and e.employer not in employers:
            employers.append(e.employer)
    sentences: list[dict[str, Any]] = [{
        "text": title_frame.format(title=now.display_title.strip(),
                                   where=_listed(employers[:MAX_EMPLOYERS], conjunction)),
        "kind": "ROLE", "evidence_ids": [],
    }]  # fmt: skip
    asked = {t for s in supports for t in (*s.requirement.named, *s.requirement.concepts)}
    skills: list[tuple[str, list[str]]] = []
    if "skills" not in doc.layout.hidden_sections:
        for group in doc.skills:
            for item in group.items if not group.hidden else []:
                ids = [k for k in item.evidence_ids if k in claims]
                if ids and ids == list(item.evidence_ids) and skill_origin(ids, claims) == JOB:
                    skills.append((item.label, ids))
    skills.sort(key=lambda s: jd.folded(s[0]) not in asked)  # stable: asked first
    if skills:
        chosen = skills[:MAX_SKILLS]
        sentences.append({
            "text": skills_frame.format(skills=_listed([s[0] for s in chosen], conjunction)),
            "kind": "SKILLS", "evidence_ids": [k for _, ids in chosen for k in ids],
        })  # fmt: skip
    placed = {e.experience_id: i for i, e in enumerate(roles) if e.experience_id}
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
    statements = [
        c for c in claims.values()
        if c.category in HIGHLIGHT_CATEGORIES and c.experience_id in placed and c.text.strip()
    ]  # fmt: skip
    from career_agent.resume_doc.tailor import _WORD, _participates

    def rank(c: Any) -> tuple[bool, float, bool, int]:
        # Shared work ("worked with IT, who installed ...") is true, and last:
        # a summary leads with what the person did.
        shared = _participates(_WORD.findall(jd.folded(c.text)))
        return (
            shared,
            -relevance.get(c.claim_key, 0.0),
            not _NUMBER.search(c.text),
            placed[c.experience_id],
        )

    statements.sort(key=rank)
    words = sum(len(s["text"].split()) for s in sentences)
    chars = sum(len(s["text"]) + 1 for s in sentences)
    seen = {jd.folded(s["text"]) for s in sentences}
    for c in statements:
        text = _sentence(c.text)
        if len(sentences) >= MAX_SENTENCES or jd.folded(text) in seen:
            continue
        n = len(text.split())
        if words + n > MAX_WORDS or chars + len(text) + 1 > MAX_CHARS:
            continue
        seen.add(jd.folded(text))
        sentences.append({"text": text, "kind": "STATEMENT", "evidence_ids": [c.claim_key]})
        words, chars = words + n, chars + len(text) + 1
    if len(sentences) < MIN_SENTENCES or not any(s["kind"] == "STATEMENT" for s in sentences):
        return None
    ids = list(dict.fromkeys(k for s in sentences for k in s["evidence_ids"]))
    return Composed(" ".join(s["text"] for s in sentences), ids, sentences)
