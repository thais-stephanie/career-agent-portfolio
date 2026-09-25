"""Role-core alignment: a READ-ONLY prototype, measured before anything uses it.

Search Fit reads the whole posting, which is why it finds "Revenue Operations"
work under a title nobody would have searched for. Its known failure is the
opposite case: a posting whose text shares the person's vocabulary while the
job itself is a different role (a "Sales Engineer" advert full of CRM words
for somebody who wants to run CRM operations). This module asks one narrower
question of the TITLE alone:

    is this the same role as one the person named, a neighbour of it, or
    something else?

    ALIGNED     the title's role core contains one of the person's role cores
                ("Senior ICU Nurse" for "ICU Nurse");
    ADJACENT    same role noun or same field, not the same role
                ("Charge Nurse" for "ICU Nurse"; "Customer Success
                Operations" for "Customer Success Manager");
    OUTSIDE     no shared role noun and no shared field;
    UNRESOLVED  nothing to compare: no role named (work phrases describe work,
                and a title that does not repeat them says nothing), or a title
                that names no role ("General application").

NOTHING READS THIS FOR A SCORE. It awards no title-fit points, changes no
eligibility and hides nothing. evaluation/role_alignment/README.md records the
benchmark and the decision; tests/unit/test_role_core.py keeps both honest.

Occupation-agnostic by construction: the only vocabulary is a short list of
words that are never a role on their own ("remote", "senior", "team"), and
the person's own words.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from career_agent.discovery.aliases import ABBREVIATIONS, MAX_TITLE, _strip_qualifiers
from career_agent.discovery.anchors import clean


class Alignment(StrEnum):
    ALIGNED = "ALIGNED"
    ADJACENT = "ADJACENT"
    OUTSIDE = "OUTSIDE"
    UNRESOLVED = "UNRESOLVED"


_WORD = re.compile(r"[^\W_]+(?:['.][^\W_]+)*", re.UNICODE)
#: Words that carry no role on their own. Structural, not occupational.
_NOISE_WORDS = """
    a an and the of for to in at on with by from or ou de da do das dos e em para
    remote remoto remota hybrid hibrido híbrido onsite full part time fulltime parttime
    contract temporary temp freelance internship intern estagio estágio
    senior sr junior jr mid level entry lead principal staff associate pleno sênior júnior
    i ii iii iv v vi
    team global worldwide latam brazil brasil emea apac americas us uk eu
    new hiring urgent immediate position role vacancy vaga job opportunity
"""
_NOISE = frozenset(_NOISE_WORDS.split())
#: Titles that name no role.
_NO_ROLE = frozenset(
    {
        "general application",
        "open application",
        "talent pool",
        "talent community",
        "spontaneous application",
        "banco de talentos",
        "candidatura espontânea",
        "various roles",
        "multiple positions",
    }
)


def _tokens(text: str) -> list[str]:
    return [w for w in (m.group(0).casefold() for m in _WORD.finditer(text)) if w]


def role_core(title: str) -> tuple[str, ...]:
    """The words of a title that say which role it is, in order.

    Qualifiers, levels, places and work arrangements removed; "Manager,
    Customer Success" read as "Customer Success Manager"; abbreviations
    expanded, so "SDR" and "Sales Development Representative" meet.
    """
    text = _strip_qualifiers(clean(title or "")[:MAX_TITLE])
    # "Manager, Customer Success" and "Head of Sales": the role noun first.
    if "," in text:
        head, _, rest = text.partition(",")
        if len(head.split()) <= 2 and rest.strip():
            text = f"{rest.strip()} {head.strip()}"
    words: list[str] = []
    for word in _tokens(text):
        expansion = ABBREVIATIONS.get(word)
        words.extend(_tokens(expansion) if expansion else [word])
    return tuple(w for w in words if w not in _NOISE and not w.isdigit())


@dataclass(frozen=True)
class IntentCores:
    """The person's role cores: named roles, their aliases, work phrases."""

    roles: tuple[tuple[str, ...], ...]
    work: tuple[tuple[str, ...], ...]

    @classmethod
    def of(cls, roles: Iterable[str], work: Iterable[str] = ()) -> IntentCores:
        return cls(
            roles=tuple(c for c in (role_core(r) for r in roles) if c),
            work=tuple(c for c in (role_core(w) for w in work) if c),
        )

    @property
    def empty(self) -> bool:
        return not self.roles and not self.work


def _contains(title: tuple[str, ...], core: tuple[str, ...]) -> bool:
    """Every word of `core`, in order, as a contiguous run of `title`."""
    n = len(core)
    return any(title[i : i + n] == core for i in range(len(title) - n + 1))


def classify(title: str, intent: IntentCores) -> Alignment:
    folded = " ".join(_tokens(title or ""))
    core = role_core(title)
    if intent.empty or not core or folded in _NO_ROLE:
        return Alignment.UNRESOLVED
    if any(_contains(core, c) for c in intent.roles):
        return Alignment.ALIGNED
    words = set(core)
    head = core[-1]
    for c in intent.roles:
        # Same role noun ("Charge Nurse" / "ICU Nurse"), or the same field
        # words ("Customer Success Operations" / "Customer Success Manager").
        if head == c[-1] or (len(c) > 1 and set(c[:-1]) and set(c[:-1]) <= words):
            return Alignment.ADJACENT
    for c in intent.work:
        # Work phrases describe work, not titles: a title containing a whole
        # work phrase is a neighbour of the roles named, or, when none is
        # named, the closest thing to alignment a title can show.
        if _contains(core, c):
            return Alignment.ALIGNED if not intent.roles else Alignment.ADJACENT
    if not intent.roles:
        # Measured on a real corpus: against work phrases alone, 94% of the
        # postings Search Fit rated STRONG have a title sharing none of them.
        # A title saying nothing about work is not evidence of a different
        # role, so without a named role the answer is "cannot tell".
        return Alignment.UNRESOLVED
    return Alignment.OUTSIDE
