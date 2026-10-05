"""Deterministic Tailor V2: the Master plus one job ad, into a grounded version.

No model, no provider, no network. The stages, each a plain function whose
output is stored on the run (`tailoring_run`):

1. snapshot the ad (immutable) and capture the Master's exact revision;
2. analyse the ad into quoted requirements (`resume_doc.jd`);
3. retrieve support per requirement from confirmed evidence and the Master:
   SHOWN_IN_MASTER, HAVE_EVIDENCE_NOT_SHOWN or NO_EVIDENCE;
4. decide a strategy (what to add, show, order, hide), citing ids only;
5. draft: copy the Master revision and apply the strategy, every added line a
   confirmed statement verbatim (or with a first-person pronoun dropped);
6. review the draft independently (`review`): grounding, numbers, named
   tools, titles, employers, dates, chronology, length, duplication;
7. validate (requirement ids, quotes, evidence still confirmed now);
8. create the TAILORED document, the run and its changes in one transaction.

WHAT IT MAY DO: select, order, hide, show and lightly rephrase confirmed
material. WHAT IT NEVER DOES: write a number, tool, title, employer, date,
certification or responsibility that no confirmed source holds. The Master is
never written; a gap stays a gap; eligibility (where the job hires, visas,
schedules) is reported and never "covered" by a resume.

The headline and summary are the Master's: no safe deterministic rewrite of
them exists without composing prose, and composed prose is how filler and
invented tenure get in.
"""

from __future__ import annotations

import dataclasses
import re
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any

from career_agent.clock import new_id
from career_agent.resume_doc import jd
from career_agent.resume_doc.evidence import unconfirmed_lines
from career_agent.resume_doc.master import _career
from career_agent.resume_doc.models import (
    EVIDENCED_ORIGINS,
    Origin,
    Override,
    ResumeDocument,
    upgrade_resume_document,
)
from career_agent.resume_doc.store import NotFound, ResumeStore, ResumeStoreError, StoredDocument
from career_agent.storage.career_repo import HIGHLIGHT_CATEGORIES, SKILL_CATEGORIES
from career_agent.storage.db import transaction

MODE = "DETERMINISTIC"
SHOWN, HAVE, NONE = "SHOWN_IN_MASTER", "HAVE_EVIDENCE_NOT_SHOWN", "NO_EVIDENCE"
#: Bounds that keep a version readable; a version is a selection, not a dump.
MAX_ADDS, MAX_ADDS_PER_ROLE, MAX_SHOWN_PER_ROLE = 8, 3, 6
LONG_LINE_WORDS = 40
#: A strength at or above this answers an ask fully.
COVERED_AT = 0.75
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_PRONOUN = re.compile(r"^(?:I|Eu|Yo)\s+(?=\w)")


class TailorFailed(ResumeStoreError):
    """The draft failed a blocking check; nothing was saved. `findings` say why."""

    def __init__(self, findings: list[dict[str, Any]]) -> None:
        super().__init__("the tailored draft failed its checks")
        self.findings = findings


# ------------------------------------------------------------------ sources


@dataclass
class Source:
    """One piece of support: a confirmed claim, or a line of a resume."""

    id: str
    text: str
    claim_key: str | None = None
    category: str | None = None
    experience_id: str | None = None
    tools: tuple[str, ...] = ()
    #: In the resume being compared (the Master, or the version itself).
    in_resume: bool = False
    shown: bool = False
    #: The resume line's id, and the confirmed claims it cites.
    line_id: str | None = None
    evidence_ids: tuple[str, ...] = ()

    @property
    def names(self) -> set[str]:
        """Names this source states: its tools, and names read strictly."""
        return jd.named_terms(self.text, strict=True) | {jd.folded(t) for t in self.tools}

    @property
    def tokens(self) -> set[str]:
        out = jd.tokens(self.text)
        for tool in self.tools:
            out |= jd.tokens(tool)
        return out


def _lines(doc: ResumeDocument) -> list[tuple[str | None, Any, bool]]:
    """(entry id, line, shown) for every bullet-like line of a document. Shown
    means on the page: the line, its entry and its section are not hidden (the
    headline, the summary and a custom section included, as the renderer
    hides them)."""
    hidden = set(doc.layout.hidden_sections)
    out: list[tuple[str | None, Any, bool]] = []
    for section in ("experience", "projects", "education"):
        for entry in getattr(doc, section):
            for b in entry.bullets:
                out.append((entry.id, b, not (b.hidden or entry.hidden or section in hidden)))
    for custom in doc.custom_sections:
        off = custom.hidden or f"custom:{custom.id}" in hidden
        for b in custom.items:
            out.append((custom.id, b, not (b.hidden or off)))
    for name in ("headline", "summary"):
        block = getattr(doc, name)
        if block is not None:
            out.append((None, block, name not in hidden))
    return out


def sources(conn: sqlite3.Connection, doc: ResumeDocument) -> list[Source]:
    """Confirmed claims of THIS profile, and every line and skill of `doc`.

    A resume line stands for confirmed experience only when it is made from
    evidence (its origin says so) AND every claim it cites is confirmed now;
    otherwise it is the person's own text, matched by its words and never
    called confirmed. A line reworded from evidence (by AI, or edited by the
    person) answers an ask only by what its evidence says: wording is not
    support."""
    hidden = set(doc.layout.hidden_sections)
    overview, rows = _career(conn)
    placed = {str(e["id"]) for e in overview["experiences"]}
    confirmed = {r["claim_key"] for r in rows if r["state"] == "CONFIRMED"}
    texts = {r["claim_key"]: str(r.get("text") or "") for r in rows if r["state"] == "CONFIRMED"}
    tools = {r["claim_key"]: [jd.folded(t) for t in r.get("tools") or []] for r in rows}
    out: list[Source] = []
    # A statement shows only as a line; a skill item citing it shows the skill,
    # not what the person did with it.
    shown_keys: set[str] = set()
    shown_skills: set[str] = set()
    reworded: list[tuple[Source, str]] = []
    for _, line, shown in _lines(doc):
        backed = line.origin in EVIDENCED_ORIGINS and set(line.evidence_ids) <= confirmed
        out.append(
            Source(
                id=line.id, text=line.text, in_resume=True, shown=shown, line_id=line.id,
                evidence_ids=tuple(line.evidence_ids) if backed else (),
            )
        )  # fmt: skip
        stale = (
            backed
            and line.origin is Origin.EVIDENCE_VERBATIM
            and any(
                jd.folded(texts.get(k, "")).strip(" .") != jd.folded(line.text).strip(" .")
                for k in line.evidence_ids
            )
        )
        if backed and (
            line.origin is Origin.AI_REWRITE or line.override is not Override.NONE or stale
        ):
            reworded.append((out[-1], "" if stale else line.original_text or ""))
        if backed and shown:
            shown_keys |= set(line.evidence_ids)
    for group in doc.skills:
        for item in group.items:
            shown = not group.hidden and "skills" not in hidden
            label = jd.folded(item.label).strip()
            backed = (
                item.origin in EVIDENCED_ORIGINS
                and bool(item.evidence_ids)
                and set(item.evidence_ids) <= confirmed
                and any(label in tools.get(k, []) or label in jd.folded(texts[k])
                        for k in item.evidence_ids)
            )  # fmt: skip
            out.append(
                Source(
                    id=item.id, text=item.label, in_resume=True, shown=shown, line_id=item.id,
                    evidence_ids=tuple(item.evidence_ids) if backed else (), category="SKILL",
                )
            )  # fmt: skip
            if shown and backed:
                shown_skills |= set(item.evidence_ids)
    for cert in doc.certifications:
        out.append(
            Source(id=cert.id, text=cert.name, in_resume=True,
                   shown=not cert.hidden and "certifications" not in hidden,
                   line_id=cert.id, category="CERTIFICATION")
        )  # fmt: skip
    for row in rows:
        if row["state"] != "CONFIRMED" or not str(row.get("text") or "").strip():
            continue
        if row.get("experience_id") is not None and row["experience_id"] not in placed:
            continue  # an experience the person archived: theirs to leave out
        key, category = row["claim_key"], row.get("category")
        shown = key in shown_keys or (category in SKILL_CATEGORIES and key in shown_skills)
        out.append(
            Source(
                id=key, text=row["text"], claim_key=key, category=category,
                experience_id=row.get("experience_id"), tools=tuple(row.get("tools") or ()),
                shown=shown, evidence_ids=(key,),
            )
        )  # fmt: skip
    said = {s.claim_key: s.text for s in out if s.claim_key}
    for source, original in reworded:
        source.text = " ".join([original, *(said.get(k, "") for k in source.evidence_ids)])
    return out


# ---------------------------------------------------------------- retrieval


def strength(req: jd.Requirement, source: Source) -> float:
    """How well `source` answers `req`: 0 when it does not.

    A named thing the ad asks for (a tool, a product) must be named by the
    source itself; "CRM" never answers "Salesforce". Otherwise the shared
    words must reach two (or all of the ask, when it has fewer)."""
    if not req.concepts and not req.named:
        return 0.0
    names = source.names
    # A name answers a name: the same one, or inside a longer one the source
    # states ("Salesforce" in "Salesforce Apex"). Ordinary words never do.
    named_hit = {n for n in req.named if any(n == m or f" {n} " in f" {m} " for m in names)}
    if req.named and not named_hit:
        return 0.0
    overlap = len(set(req.concepts) & source.tokens)
    if not named_hit and overlap < min(2, len(req.concepts)):
        return 0.0
    named_part = len(named_hit) / len(req.named) if req.named else 1.0
    word_part = overlap / max(1, len(req.concepts))
    return round(0.5 * named_part + 0.5 * word_part, 3)


@dataclass
class Support:
    requirement: jd.Requirement
    state: str
    #: Matching sources, strongest first.
    shown: list[tuple[Source, float]] = field(default_factory=list)
    unshown: list[tuple[Source, float]] = field(default_factory=list)

    @property
    def coverage(self) -> str:
        """COVERED, PARTLY, SAID or NOT_FOUND for the person; ELIGIBILITY is not ours."""
        if self.requirement.eligibility:
            return "ELIGIBILITY"
        best = max([v for s, v in self.shown if s.claim_key or s.evidence_ids] or [0.0])
        if not best:
            # Only lines the person typed say it: in the resume, not evidenced.
            return "SAID" if self.shown else "NOT_FOUND"
        # Years are never compared: the work may be shown, the tenure is not judged.
        if self.requirement.kind == "EXPERIENCE":
            return "PARTLY"
        return "COVERED" if best >= COVERED_AT else "PARTLY"


def retrieve(analysis: jd.Analysis, pool: list[Source]) -> list[Support]:
    """Each requirement's support in `pool` (shown in the resume, or not)."""
    out = []
    for req in analysis.requirements:
        if req.eligibility:
            out.append(Support(req, NONE))
            continue
        hits = sorted(((s, strength(req, s)) for s in pool), key=lambda pair: -pair[1])
        hits = [(s, v) for s, v in hits if v > 0]
        shown = [(s, v) for s, v in hits if s.shown]
        # Not shown, and still support: confirmed evidence, or a hidden line made
        # from it. A hidden line the person typed is no confirmed experience.
        unshown = [(s, v) for s, v in hits if not s.shown and (s.claim_key or s.evidence_ids)]
        # Shown only when what shows answers it well, or as well as anything
        # unshown would: a bare tool name in Skills does not hide a confirmed
        # line that says what was done with it.
        # Typed text on the page only SAYS it (coverage SAID): it never hides
        # confirmed experience that answers better.
        backed = [v for s, v in shown if s.claim_key or s.evidence_ids]
        best_shown = backed[0] if backed else 0.0
        best_unshown = unshown[0][1] if unshown else 0.0
        if backed and (best_shown >= COVERED_AT or best_shown >= best_unshown):
            state = SHOWN
        else:
            state = HAVE if unshown else NONE
        out.append(Support(req, state, shown, unshown))
    return out


# ----------------------------------------------------------------- strategy


def rewrite(text: str) -> str:
    """The only rewrite: a leading first-person pronoun goes ("I built" -> "Built")."""
    cut = _PRONOUN.sub("", text.strip(), count=1)
    return cut[:1].upper() + cut[1:] if cut != text.strip() else text.strip()


def strategy(doc: ResumeDocument, supports: list[Support]) -> dict[str, Any]:
    """What to add, show, order and hide, as ids. Decided before any line is written."""
    entries = {e.experience_id: e for e in doc.experience if e.experience_id and not e.hidden}
    present = {jd.folded(line.text) for _, line, _ in _lines(doc)}
    skill_labels = {jd.folded(i.label) for g in doc.skills for i in g.items}
    plan: dict[str, Any] = {"add": [], "show": [], "skills": [], "relevance": {}, "gaps": []}
    per_role: dict[str, int] = {}
    ranked = sorted(supports, key=lambda s: (-s.requirement.importance, s.requirement.start))
    for sup in ranked:
        rid = sup.requirement.id
        for source, value in sup.shown:
            if source.line_id:
                plan["relevance"][source.line_id] = plan["relevance"].get(source.line_id, 0) + (
                    sup.requirement.importance * value
                )
        if sup.state == NONE and not sup.requirement.eligibility:
            plan["gaps"].append(rid)
        if sup.state != HAVE or len(plan["add"]) + len(plan["show"]) >= MAX_ADDS:
            continue
        for source, _ in sup.unshown:
            if source.in_resume and source.line_id:  # a line the resume hides
                if source.line_id not in plan["show"]:
                    plan["show"].append(source.line_id)
                    plan["relevance"][source.line_id] = sup.requirement.importance
                break
            if source.claim_key is None:
                continue
            entry = entries.get(source.experience_id or "")
            text = rewrite(source.text)
            if (
                source.category in HIGHLIGHT_CATEGORIES
                and entry is not None
                and jd.folded(text) not in present
                and per_role.get(entry.id, 0) < MAX_ADDS_PER_ROLE
            ):
                present.add(jd.folded(text))
                per_role[entry.id] = per_role.get(entry.id, 0) + 1
                plan["add"].append(
                    {"entry_id": entry.id, "text": text, "evidence_id": source.claim_key,
                     "verbatim": text == source.text.strip(), "requirement_ids": [rid]}
                )  # fmt: skip
                break
            labels = [source.text] if source.category in SKILL_CATEGORIES else []
            labels += [t for t in source.tools if jd.folded(t) in set(sup.requirement.named)]
            label = next((x for x in labels if jd.folded(x) not in skill_labels), None)
            if label and len(label) <= 60:
                skill_labels.add(jd.folded(label))
                plan["skills"].append(
                    {"label": label, "evidence_id": source.claim_key, "requirement_ids": [rid]}
                )
                break
    return plan


# -------------------------------------------------------------------- draft


def draft(
    master: ResumeDocument, plan: dict[str, Any], *, target: dict[str, Any], provenance: dict
) -> tuple[ResumeDocument, list[dict[str, Any]]]:
    """The Master revision with the plan applied, and the changes it made."""
    data = master.model_dump(mode="json")
    changes: list[dict[str, Any]] = []
    relevance: dict[str, float] = plan["relevance"]
    by_entry = {e["id"]: e for e in data["experience"]}
    for add in plan["add"]:
        line = {
            "id": new_id(),
            "text": add["text"],
            "origin": "EVIDENCE_VERBATIM" if add["verbatim"] else "RULE_REWRITE",
            "evidence_ids": [add["evidence_id"]],
            "requirement_ids": add["requirement_ids"],
        }
        by_entry[add["entry_id"]]["bullets"].append(line)
        relevance[line["id"]] = 99.0
        changes.append(
            {"op": "ADD_BULLET", "line_id": line["id"], "entry_id": add["entry_id"],
             "after": add["text"], "requirement_ids": add["requirement_ids"],
             "evidence_ids": [add["evidence_id"]]}
        )  # fmt: skip
    for entry in data["experience"]:
        before = [b["id"] for b in entry["bullets"]]
        for b in entry["bullets"]:
            if b["id"] in plan["show"] and b.get("hidden"):
                b["hidden"] = False
                changes.append({"op": "SHOW_BULLET", "line_id": b["id"], "after": b["text"],
                                "evidence_ids": b.get("evidence_ids", [])})  # fmt: skip
        # Relevant lines first; the rest keep their order. Chronology of roles is untouched.
        entry["bullets"].sort(key=lambda b: -relevance.get(b["id"], 0.0))
        if [b["id"] for b in entry["bullets"]] != before:
            changes.append({"op": "REORDER_BULLETS", "entry_id": entry["id"]})
        visible = [b for b in entry["bullets"] if not b.get("hidden")]
        for b in visible[MAX_SHOWN_PER_ROLE:]:
            if relevance.get(b["id"], 0.0) == 0.0:
                b["hidden"] = True
                changes.append({"op": "HIDE_BULLET", "line_id": b["id"], "after": b["text"]})
    if plan["skills"]:
        if not data["skills"]:
            name = {"pt": "Competências", "es": "Habilidades"}.get(data["language"][:2], "Skills")
            data["skills"].append({"id": new_id(), "name": name, "hidden": False, "items": []})
        group = data["skills"][0]
        for skill in plan["skills"]:
            item = {"id": new_id(), "label": skill["label"], "origin": "EVIDENCE_VERBATIM",
                    "evidence_ids": [skill["evidence_id"]]}  # fmt: skip
            group["items"].insert(0, item)
            changes.append(
                {"op": "ADD_SKILL", "line_id": item["id"], "after": skill["label"],
                 "requirement_ids": skill["requirement_ids"],
                 "evidence_ids": [skill["evidence_id"]]}
            )  # fmt: skip
    data.update(
        id=new_id(),
        kind="TAILORED",
        title=" · ".join(filter(None, [target["title"], target.get("company")]))[:300],
        target=target,
        provenance=provenance,
    )
    return upgrade_resume_document(data), changes


# ------------------------------------------------------------------- review


def _numbers(text: str) -> set[str]:
    return {n.replace(",", ".") for n in _NUMBER.findall(text)}


#: Words of rank, leadership or scope, EN and PT (folded). A line may say one
#: only where a source says the same word before the same next word ("lead
#: routing" is no leadership claim; "owned the reporting" is not "owned 4
#: teams"), or where the role's own title says it.
_RANK = re.compile(
    r"^(?:lead(?:s|er|ers|ership|ing)?|led|manag\w*|head(?:s|ed|ing)?|direct\w*|supervis\w*"
    r"|oversee\w*|oversaw|spearhead\w*|mentor\w*|chief|senior|sr|principal|vp|executive|boss"
    r"|own|owns|owned|owning|owner\w*|ran|run|runs|running|charge|champion\w*|steer\w*"
    r"|chair\w*|lider\w*|gerent\w*|gerenc\w*|gest\w*|coorden\w*|chef\w*|diretor\w*|dirig\w*"
    r"|comand\w*|frente|encarregad\w*)$"
)
#: Words that claim a result, a scale or a quantity, EN and PT (folded): never
#: new in an AI rewrite. A number written as a word counts as a number.
_CLAIM = re.compile(
    r"^(?:award\w*|premi\w*|record\w*|global\w*|worldwide|mundial\w*|enterprise|corporativ\w*"
    r"|million\w*|milho\w*|billion\w*|bilho\w*|thousand\w*|milhar\w*|hundred\w*|centena\w*"
    r"|dozen\w*|dezena\w*|decade\w*|decada\w*|doubl\w*|dobr\w*|tripl\w*|quadrupl\w*"
    r"|multipl\w*|several|numerous|inumer\w*|certif\w*|best|melhor\w*|top|first|primeir\w*"
    r"|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty"
    r"|forty|fifty|dois|duas|tres|quatro|cinco|seis|sete|oito|nove|dez|vinte|trinta"
    r"|quarenta|cinquenta|cem|cento)$"
)
#: The only words an AI rewrite may bring that its sources do not hold (by
#: root): ways of saying what was done and how the words join, EN and PT. A
#: list we chose, because a list of forbidden words is never complete: a
#: result ("reducing churn"), a tool nobody catalogued ("snowflake"), a rank
#: ("in charge of") is simply not on it.
AI_WORDS = frozenset(
    """
    built build building created create designed design developed develop delivered deliver
    automated automate automating configured configure implemented implement maintained
    maintain organized organised organize wrote write written documented document analyzed
    analysed analyze prepared prepare supported support integrated integrate migrated migrate
    cleaned clean standardized standardised standardize structured structure mapped map
    tracked track reported set setup used use added add updated update reviewed review tested
    test handled handle existing internal external daily weekly monthly process processes
    workflow workflows data system systems tool tools reporting report reports team teams work
    sales rule rules pipeline record records request
    requests campaign campaigns dashboard dashboards documentation routing
    construi criei desenvolvi organizei estruturei automatizei configurei implementei mantive
    documentei analisei preparei apoiei integrei padronizei mapeei acompanhei atualizei revisei
    usando diarios diarias semanais mensais processo processos fluxo fluxos dados equipe
    sistema sistemas ferramenta ferramentas relatorio relatorios trabalho vendas regra regras
    pedido pedidos registro registros campanha campanhas painel paineis
    """.split()  # noqa: SIM905
)
#: Words that only join others, EN and PT: free, and never a claim.
AI_JOINERS = frozenset(
    """
    and or the a an to of in on for with by from at as via into onto per each its their
    across within through using including is are was were be been has have had it this
    that these those our we who which also
    o os as e ou de da do das dos em no na nos nas um uma uns umas ao aos por pela pelo pelas
    pelos com para entre que se cada meio
    """.split()  # noqa: SIM905
)
_WORD = re.compile(r"[a-z]+")
_PERCENT = re.compile(r"\s*(?:percent|per cent|por cento)\b")
_AMOUNT = re.compile(
    r"(?<![a-z0-9])(\d+(?:[.,]\d+)?)\s*(%|x(?![a-z])|k(?![a-z])|m(?![a-z])|bn(?![a-z]))?"
    r"(?![a-z0-9])|([a-z]+)"
)
#: New content words an AI rewrite may bring, beyond its sources' (by root).
AI_NEW_WORDS = 2


def ai_titles(doc: ResumeDocument, holder: str | None) -> str:
    """The titles an AI line may echo: its role's, or (a headline or a summary,
    which read as now) the current role's. A hidden or past title lends nothing."""
    shown = [e for e in doc.experience if not e.hidden and holder in (None, e.id)]
    if holder is None:
        shown = [e for e in shown if e.current or e.end is None] or shown[:1]
    return " ".join(f"{e.display_title} {e.source_title}" for e in shown)


#: number, unit, phrase counted (after), phrase measured (before), the
#: counted phrase's head word, and every word of the amount's own sentence.
Amount = tuple[str, str, tuple[str, ...], tuple[str, ...], str, frozenset[str]]
_ARTICLES = frozenset(["the", "a", "an", "o", "a", "os", "as", "um", "uma", "uns", "umas"])
_END = "qqend"
_FULL_STOP = "qqstop"
_SENTENCE = re.compile(r"[.!?]+(?=\s|$)")
#: Abbreviations end no sentence: "i.e.", "e.g.", "U.S.", "approx.", "vs.".
_ABBREVIATION = re.compile(r"\b(?:([a-z])\.|(approx|aprox|vs|etc|incl|ex|aprox)\.)")
_CLAUSE = re.compile(r"[.;:!?,()]+(?=\s|$)")
_PARTITIVE = frozenset({"of", "de", "da", "do", "das", "dos"})
#: What a duration or a share measures is the thing before it ("PROCESSING
#: TIME by 3 days"), so for these the words before it are compared too.
_MEASURES = frozenset(
    [
        "minute",
        "hour",
        "day",
        "week",
        "month",
        "year",
        "quarter",
        "minuto",
        "hora",
        "dia",
        "semana",
        "mes",
        "ano",
        "trimestre",
    ]
)
#: A number written as a word is a number: "four sales teams" is read as "4".
#: Not "one", "um" or "uma": those are also articles.
_NUMBER_WORDS = {
    w: str(n)
    for n, words in enumerate(
        [(), (), ("two", "dois", "duas"), ("three", "tres"), ("four", "quatro"),
         ("five", "cinco"), ("six", "seis"), ("seven", "sete"), ("eight", "oito"),
         ("nine", "nove"), ("ten", "dez"), ("eleven", "onze"), ("twelve", "doze")]
    )
    for w in words
}  # fmt: skip
_NUMBER_WORD = re.compile(rf"\b({'|'.join(_NUMBER_WORDS)})\b")


def _phrase(words: list[str], limit: int = 3) -> tuple[tuple[str, ...], str]:
    """Words up to the first joining word or another amount, at most `limit`:
    the local noun phrase ("regional teams", "manual data entry"), and its
    head: the word before a partitive ("TEAMS of engineers", "FUNCIONARIOS de
    hospitais"), else the last. A partitive goes on, past an article."""
    out: list[str] = []
    head = ""
    after_partitive = False
    for i, w in enumerate(words):
        if out and w in _PARTITIVE and i + 1 < len(words):
            head = head or out[-1]
            after_partitive = True
            continue
        if after_partitive and w in _ARTICLES:
            continue
        after_partitive = False
        if (
            not w
            or w in (_END, _FULL_STOP)
            or w in AI_JOINERS
            or w in jd._STOP
            or len(out) == limit
        ):
            break
        out.append(w.removesuffix("s"))
    return tuple(out), head or (out[-1] if out else "")


def _sentences(text: str) -> int:
    """How many sentences `text` has (abbreviations end none)."""
    folded = _ABBREVIATION.sub(lambda m: m.group(1) or m.group(2), jd.folded(text))
    return sum(1 for part in _SENTENCE.split(folded) if part.strip())


def _amounts(text: str, limit: int = 3) -> list[Amount]:
    """Each amount: its number, its unit, the phrase it counts (after it) and
    the phrase it measures (before it, past one joining word): "4 REGIONAL
    TEAMS", "PROCESSING TIME by 30%"; the head of what it counts; and the
    words of its own sentence."""
    # A sentence or clause ends a phrase: "4 regional teams. HubSpot" counts teams.
    folded = _ABBREVIATION.sub(lambda m: m.group(1) or m.group(2), jd.folded(text))
    folded = _SENTENCE.sub(f" {_FULL_STOP} ", _PERCENT.sub("%", folded))
    folded = _CLAUSE.sub(f" {_END} ", folded)
    folded = _NUMBER_WORD.sub(lambda m: _NUMBER_WORDS[m.group(1)], folded)
    items = _AMOUNT.findall(folded)
    words = [w for _, _, w in items]
    out = []
    for i, (number, unit, word) in enumerate(items):
        if word:
            continue
        following = words[i + 1 :]
        if following and following[0] in _PARTITIVE:  # "30% OF teams" counts teams
            following = following[1:]
        before = list(reversed(words[:i]))
        while (
            before
            and before[0]
            and before[0] not in (_END, _FULL_STOP)
            and (before[0] in AI_JOINERS or before[0] in jd._STOP)
        ):
            before = before[1:]
        start = max((j for j in range(i) if words[j] == _FULL_STOP), default=-1) + 1
        end = next((j for j in range(i + 1, len(words)) if words[j] == _FULL_STOP), len(words))
        sentence = frozenset(
            w.removesuffix("s") for w in words[start:end] if w and w not in (_END, _FULL_STOP)
        )
        counted, head = _phrase(following, limit)
        measured, _ = _phrase(before, limit)
        out.append((number.replace(",", "."), unit, counted, tuple(reversed(measured)), head,
                    sentence))  # fmt: skip
    return out


def _amount_held(amount: Amount, held: list[Amount], *, alone: bool = True) -> bool:
    """An amount is the source's when its number and unit are, and so is the
    phrase around it: the same thing counted (its head kept, no word the
    source's phrase lacks), for a share or a duration the same thing
    measured, and every word of its sentence from that number's source sentence.
    "4 regional teams" holds "4 teams", never "4 sales teams", "4 regional
    sales team" (for "team leads"), "4 countries" or "4 years"; "processing
    time by 30%" never holds "operating costs by 30%"; a number standing alone
    holds only a number that stood alone. Joining words and punctuation may
    change; a number does not move to another sentence's work."""
    number, unit, after, before, _, said = amount
    measured = bool(unit) or bool(after and after[-1] in _MEASURES)

    def same_before(b: tuple[str, ...]) -> bool:
        return bool(before and b and before[-1] == b[-1] and set(before) <= set(b))

    def plain(w: str) -> bool:
        return (
            w in AI_WORDS
            or f"{w}s" in AI_WORDS
            or w in AI_JOINERS
            or (w in jd._STOP or f"{w}s" in jd._STOP)
        )

    def from_sentence(source: frozenset[str], phrase: set[str]) -> bool:
        """Every word of the sentence holding the amount is the source
        sentence's, or only joins or says what was done, AND that sentence
        keeps a word of the source sentence's own work beyond what is
        counted, when the line has more than one sentence: a number never
        lends itself to another statement, across a comma or as a fragment
        of its own ("... system. For 4 teams.")."""
        anchors = {w for w in source - phrase if not plain(w)}
        return all(w in source or plain(w) for w in said) and (alone or bool(said & anchors))

    for n, u, a, b, head, sentence in held:
        if (n, u) != (number, unit) or not from_sentence(sentence, set(a) | set(b)):
            continue
        if not after and not before:
            if not a and not b:
                return True
            continue
        if after:
            if not a or not set(after) <= set(a) or head not in after:
                continue
            if measured and (before or b) and not same_before(b):
                continue
            return True
        if not a and same_before(b):
            return True
    return False


def _pairs(text: str) -> set[tuple[str, str]]:
    """Each word with the next one that says something ("owned the weekly"
    reads as "owned weekly")."""
    words = [w for w in _WORD.findall(jd.folded(text)) if w not in jd._STOP]
    return set(zip(words, [*words[1:], ""], strict=True))


def grounding(
    text: str,
    source: str,
    names: set[str] | frozenset[str] = frozenset(),
    *,
    ai: bool = False,
    titles: str = "",
    bait: set[str] | frozenset[str] = frozenset(),
    new_words: int = AI_NEW_WORDS,
) -> list[tuple[str, str]]:
    """What `text` says that `source` does not hold, as (check, detail) pairs.

    The one reader of grounding: the deterministic reviewer and the AI drafter
    both ask it. Numbers and named terms must be the source's exactly. For a
    rule-made line every word must be the source's too.

    An AI rewrite (`ai`) may reorder and condense. A word its sources lack
    must be a joining word or one of `AI_WORDS`, at most `new_words` of them. On top
    of that, read in any case: no tool, product or name the ad asked for
    (`bait`) or Career Agent knows; an amount only with its unit and what it
    counts; no word of result, scale or quantity; and no word of rank or
    leadership unless a source says it in the same place or `titles` holds it."""
    out: list[tuple[str, str]] = []
    extra_numbers = _numbers(text) - _numbers(source)
    if ai:
        held_amounts = _amounts(source, limit=4)
        extra_numbers |= {
            " ".join([a[0] + a[1], *a[2]])
            for a in _amounts(text)
            if not _amount_held(a, held_amounts, alone=_sentences(text) < 2)
        }
    if extra_numbers:
        out.append(("NUMBERS", f"numbers not in its evidence: {sorted(extra_numbers)}"))
    folded, held_text = jd.folded(text), jd.folded(source)
    if ai:
        folded, held_text = _PERCENT.sub("%", folded), _PERCENT.sub("%", held_text)
        # A capital at a sentence start is a name only when it is a known
        # tool: "Automated ..." names nothing.
        said = jd.named_terms(text, strict=True)
        known_tools = jd.KNOWN_TOOLS | jd.LOWERCASE_TOOLS
        said |= {w for w in jd.tokens(text) | set(_WORD.findall(folded)) if w in known_tools}
        said |= {b for b in bait if re.search(rf"(?<![a-z0-9]){re.escape(b)}(?![a-z0-9])", folded)}
    else:
        said = jd.named_terms(text, sentence_start=False)
    known = jd.named_terms(source) | jd.named_terms(titles) | set(names)
    held_text = f"{held_text} {jd.folded(titles)}"
    extra_names = {n for n in said - known if n not in held_text}
    if extra_names:
        out.append(("NAMED_TOOLS", f"named terms not in its evidence: {sorted(extra_names)}"))
    if not ai:
        if not jd.tokens(text) <= jd.tokens(source):
            out.append(("OVERSTATEMENT", "words not in its evidence"))
        return out
    words = _WORD.findall(folded)
    held = set(_WORD.findall(held_text))
    allowed = _pairs(source)
    titled = set(_WORD.findall(jd.folded(titles)))
    raised = {
        w for w, after in _pairs(text)
        if _RANK.match(w) and (w, after) not in allowed and w not in titled
    }  # fmt: skip
    if raised:
        out.append(("SENIORITY", f"rank or leadership not in its evidence: {sorted(raised)}"))
    claims = {w for w in words if _CLAIM.match(w) and w not in held}
    if claims:
        out.append(("CLAIMS", f"results or scale not in its evidence: {sorted(claims)}"))
    forms = held | titled
    new = {
        w for w in words
        if w not in AI_JOINERS and w not in forms
        and w.removesuffix("s") not in forms and f"{w}s" not in forms
    }  # fmt: skip
    foreign = new - AI_WORDS
    if foreign or len(new) > new_words:
        out.append(("OVERSTATEMENT", f"words not in its evidence: {sorted(foreign or new)}"))
    return out


def review(
    draft_doc: ResumeDocument, master: ResumeDocument, pool: list[Source], supports: list[Support]
) -> list[dict[str, Any]]:
    """Independent checks of the draft against the Master and the evidence.

    Every finding is `{check, outcome: PASS|WARN|FAIL, ref, detail}`. A FAIL
    means something no source holds reached the draft."""
    findings: list[dict[str, Any]] = []
    claims = {s.claim_key: s for s in pool if s.claim_key}
    before = {line.id: line.text for _, line, _ in _lines(master)}
    before |= {i.id: i.label for g in master.skills for i in g.items}

    def fail(check: str, ref: str, detail: str) -> None:
        findings.append({"check": check, "outcome": "FAIL", "ref": ref, "detail": detail})

    def warn(check: str, ref: str, detail: str) -> None:
        findings.append({"check": check, "outcome": "WARN", "ref": ref, "detail": detail})

    roles = {b.id: e.experience_id for e in draft_doc.experience for b in e.bullets}
    lines = [
        (line.id, line.text, list(line.evidence_ids), line.origin is Origin.AI_REWRITE, holder)
        for holder, line, _ in _lines(draft_doc)
    ]
    lines += [(i.id, i.label, list(i.evidence_ids), False, "skill")
              for g in draft_doc.skills for i in g.items]  # fmt: skip
    for line_id, text, cited, ai, holder in lines:
        if before.get(line_id) == text:
            continue  # the Master's own line, unchanged
        if not cited or any(k not in claims for k in cited):
            fail("GROUNDING", line_id, "a new line cites no confirmed evidence")
            continue
        held = {claims[k].experience_id for k in cited} - {None}
        if line_id in roles and held - {roles[line_id]}:
            fail("EMPLOYER", line_id, "a line cites another role's evidence")
        # A rewrite may keep what the line said before.
        source = " ".join(
            [
                before.get(line_id, ""),
                *(claims[k].text + " " + " ".join(claims[k].tools) for k in cited),
            ]
        )
        tools = {jd.folded(t) for k in cited for t in claims[k].tools}
        summary = draft_doc.summary is not None and line_id == draft_doc.summary.id
        for check, detail in grounding(
            text, source, tools, ai=ai, titles=ai_titles(master, holder),
            new_words=AI_NEW_WORDS * (2 if summary else 1),
        ):  # fmt: skip
            fail(check, line_id, detail)
    old = [(e.id, e.employer, e.source_title, e.display_title, e.start, e.end, e.current)
           for e in master.experience]  # fmt: skip
    new = [(e.id, e.employer, e.source_title, e.display_title, e.start, e.end, e.current)
           for e in draft_doc.experience]  # fmt: skip
    if [o[0] for o in old] != [n[0] for n in new]:
        fail("CHRONOLOGY", "experience", "roles were added, removed or reordered")
    for o, n in zip(old, new, strict=False):
        if o[1] != n[1]:
            fail("EMPLOYER", o[0], "an employer changed")
        if o[2:4] != n[2:4]:
            fail("SENIORITY", o[0], "a job title changed")
        if o[4:] != n[4:]:
            fail("DATES", o[0], "dates changed")
    if [c.name for c in master.certifications] != [c.name for c in draft_doc.certifications]:
        fail("CERTIFICATIONS", "certifications", "certifications changed")
    if master.education != draft_doc.education:
        fail("EDUCATION", "education", "education changed")
    for entry in draft_doc.experience:
        texts = [jd.folded(b.text) for b in entry.bullets if not b.hidden]
        if len(texts) != len(set(texts)):
            warn("DUPLICATION", entry.id, "the same line twice in one role")
        for b in entry.bullets:
            if not b.hidden and len(b.text.split()) > LONG_LINE_WORDS:
                warn("LENGTH", b.id, f"{len(b.text.split())} words")
    shown = sum(1 for _, _, visible in _lines(draft_doc) if visible)
    if shown > 30:
        warn("LENGTH", "document", f"{shown} lines shown")
    for sup in supports:
        if (
            sup.requirement.hardness == "REQUIRED"
            and sup.state == NONE
            and not sup.requirement.eligibility
        ):
            warn("COVERAGE", sup.requirement.id, "a required ask has no confirmed support")
    if not any(f["outcome"] == "FAIL" for f in findings):
        findings.append({"check": "GROUNDING", "outcome": "PASS", "ref": "document", "detail": ""})
    return findings


# --------------------------------------------------------------------- run


def job_ad(conn: sqlite3.Connection, job_id: str) -> dict[str, Any] | None:
    """A Career Agent job as an ad to snapshot: its text exactly as stored."""
    row = conn.execute(
        "SELECT j.title, j.url, c.name AS company, r.description_text"
        " FROM job j JOIN company c ON c.id = j.company_id"
        " LEFT JOIN job_raw r ON r.content_hash = j.content_hash WHERE j.id = ?",
        (job_id,),
    ).fetchone()
    if row is None:
        return None
    title = str(row["title"] or "").strip() or "Job"
    return {
        "job_id": job_id,
        "title": title,
        "company": row["company"] or None,
        "url": row["url"] or None,
        "text": str(row["description_text"] or "").strip() or title,
    }


def tailor(
    conn: sqlite3.Connection,
    *,
    ad: dict[str, Any],
    pause: Any = None,
) -> tuple[StoredDocument, dict[str, Any]]:
    """Run every stage in ONE write transaction and store the result.

    `ad` is `{job_id?, title, company?, url?, text}`. `pause(stage)` is called
    between stages (tests use it to change the world mid-run). Raises
    `NotFound` without a Master, `TailorFailed` on a blocking finding and
    `EvidenceNotConfirmed` when evidence was retired before saving: in each
    case nothing is written."""
    store = ResumeStore(conn)
    timings: dict[str, float] = {}
    clock = time.perf_counter()

    def mark(stage: str) -> None:
        nonlocal clock
        now = time.perf_counter()
        timings[stage] = round((now - clock) * 1000, 1)
        clock = now
        if pause is not None:
            pause(stage)

    # Reading the ad holds no lock: it touches nothing stored.
    analysis = jd.analyse(ad["text"])
    with transaction(conn):
        master_row = store.current_master()
        if master_row is None:
            raise NotFound("there is no Master resume yet")
        revision = store.checkpoint_revision(master_row.id, "MANUAL_CHECKPOINT")
        master = revision.content
        snap = store.create_jd_snapshot(
            text=ad["text"], title=ad["title"], company=ad.get("company"),
            job_id=ad.get("job_id"), url=ad.get("url"), language=analysis.language,
        )  # fmt: skip
        mark("analysis")
        pool = sources(conn, master)
        supports = retrieve(analysis, pool)
        mark("retrieval")
        plan = strategy(master, supports)
        mark("strategy")
        run_id = new_id()
        target = {"jd_snapshot_id": snap.id, "job_id": snap.job_id, "title": snap.title,
                  "company": snap.company}  # fmt: skip
        provenance = {"created_from": "TAILOR", "master_document_id": master_row.id,
                      "master_revision_id": revision.id, "tailoring_run_id": run_id}  # fmt: skip
        doc, changes = draft(master, plan, target=target, provenance=provenance)
        mark("draft")
        findings = review(doc, master, pool, supports)
        mark("review")
        if any(f["outcome"] == "FAIL" for f in findings):
            raise TailorFailed([f for f in findings if f["outcome"] == "FAIL"])
        problems = validate(conn, doc, analysis, ad["text"], revision.id, master_row.id)
        mark("validation")
        if problems:
            raise TailorFailed(problems)
        stored = store.create_document(doc, reason="GENERATED", parent_document_id=master_row.id)
        store.create_tailoring_run(stored.id, mode=MODE, options={"ai": False}, run_id=run_id)
        report: dict[str, Any] = {
            "analysis": {"language": analysis.language,
                         "requirements": [_requirement(r) for r in analysis.requirements]},
            "retrieval": {"supports": [_support(s) for s in supports]},
            "strategy": {k: v for k, v in plan.items() if k != "relevance"},
            "review": {"findings": findings},
            "validation": {"problems": [], "master_revision_id": revision.id,
                           "timings_ms": {**timings, "total": round(sum(timings.values()), 1)}},
        }  # fmt: skip
        store.update_tailoring_run_stage(run_id, status="DONE", **report)
        for change in changes:
            made = store.record_tailoring_change(
                run_id, op=change, source="RULE",
                evidence_ids=change.get("evidence_ids"),
                requirement_ids=change.get("requirement_ids"),
                reason=change["op"],
            )  # fmt: skip
            store.decide_tailoring_change(made.id, "ACCEPTED")
    return stored, report


def validate(
    conn: sqlite3.Connection,
    doc: ResumeDocument,
    analysis: jd.Analysis,
    text: str,
    revision_id: str,
    master_id: str,
) -> list[dict[str, Any]]:
    """The last word before saving: ids, quotes and evidence, asked again now."""
    problems = []
    known = {r.id for r in analysis.requirements}
    for _, line, _ in _lines(doc):
        if set(line.requirement_ids) - known:
            problems.append(
                {"check": "REQUIREMENT", "ref": line.id, "detail": "unknown requirement"}
            )
    for r in analysis.requirements:
        if text[r.start : r.start + len(r.source_quote)] != r.source_quote:
            problems.append({"check": "QUOTE", "ref": r.id, "detail": "quote not in the ad"})
    for line_id in unconfirmed_lines(conn, doc):
        problems.append(
            {"check": "EVIDENCE", "ref": line_id, "detail": "evidence not confirmed now"}
        )
    if ResumeStore(conn).latest_revision_id(master_id) != revision_id:
        problems.append({"check": "MASTER", "ref": master_id, "detail": "the Master moved"})
    for p in problems:
        p["outcome"] = "FAIL"
    return problems


def _requirement(r: jd.Requirement) -> dict[str, Any]:
    return {**dataclasses.asdict(r), "quote": r.source_quote}


def _support(s: Support) -> dict[str, Any]:
    return {
        "requirement_id": s.requirement.id, "state": s.state, "coverage": s.coverage,
        "shown": [{"id": src.id, "strength": v} for src, v in s.shown[:3]],
        "unshown": [{"id": src.id, "strength": v} for src, v in s.unshown[:3]],
    }  # fmt: skip


# ----------------------------------------------------- explain, make it better


def _where(doc: ResumeDocument, entry_id: str | None) -> dict[str, str] | None:
    """A role, as the panel names it: its title and employer."""
    entry = next((e for e in doc.experience if e.id == entry_id), None)
    return {"title": entry.display_title, "employer": entry.employer} if entry else None


def suggest(doc: ResumeDocument, supports: list[Support]) -> list[dict[str, Any]]:
    """What would make `doc` better against these supports, each with the exact
    change when there is one (a confirmed line to add or show, a confirmed
    skill to add) and none for a gap. Shared by the job panel and Analyze."""
    suggestions: list[dict[str, Any]] = []
    for sup in supports:
        req = sup.requirement
        if sup.state == HAVE:
            plan = strategy(doc, [sup])
            action: dict[str, Any] | None = None
            if plan["show"]:
                shown = next(line for _, line, _ in _lines(doc) if line.id == plan["show"][0])
                action = {"type": "show", "line_id": shown.id, "text": shown.text}
            elif plan["add"]:
                add = plan["add"][0]
                action = {"type": "add_bullet", "entry_id": add["entry_id"], "text": add["text"],
                          "origin": "EVIDENCE_VERBATIM" if add["verbatim"] else "RULE_REWRITE",
                          "evidence_ids": [add["evidence_id"]], "requirement_ids": [req.id],
                          "where": _where(doc, add["entry_id"])}  # fmt: skip
            elif plan["skills"]:
                skill = plan["skills"][0]
                action = {"type": "add_skill", "label": skill["label"],
                          "evidence_ids": [skill["evidence_id"]]}  # fmt: skip
            if action is not None:
                suggestions.append({"key": f"add:{req.id}", "kind": "UNSHOWN_EVIDENCE",
                                    "ask": req.source_quote, "action": action})  # fmt: skip
        elif sup.state == NONE and not req.eligibility:
            suggestions.append({"key": f"gap:{req.id}", "kind": "NO_EVIDENCE",
                                "ask": req.source_quote, "action": None})  # fmt: skip
    for _, line, shown in _lines(doc):
        words = len(line.text.split())
        if shown and words > LONG_LINE_WORDS:
            suggestions.append({"key": f"long:{line.id}", "kind": "LONG_LINE", "words": words,
                                "line_id": line.id, "text": line.text, "action": None})  # fmt: skip
    return suggestions


def explain(
    conn: sqlite3.Connection, stored: StoredDocument, snapshot_id: str | None = None
) -> dict[str, Any]:
    """A version against a job ad (its own, or `snapshot_id`): coverage, why it
    changed, gaps, and what would make it better now. Read only; recomputed on
    the version as it is, so a suggestion already applied is gone."""
    store = ResumeStore(conn)
    snapshot_id = snapshot_id or stored.jd_snapshot_id
    if snapshot_id is None:
        return {}
    snap = store.get_jd_snapshot(snapshot_id)
    doc = stored.working
    analysis = jd.analyse(snap.text)
    supports = retrieve(analysis, sources(conn, doc))
    quotes = {r.id: r.source_quote for r in analysis.requirements}

    changes = []
    run_id = doc.provenance.tailoring_run_id
    ai = reviewed = False
    if run_id and doc.provenance.created_from.value == "TAILOR":
        run = store.get_tailoring_run(run_id)
        ai = run.mode == "AI_ASSISTED"
        reviewed = run.stages["review"].get("ai", {}).get("status") == "DONE"
        rows = {e.id: e.id for e in doc.experience} | {
            b.id: e.id for e in doc.experience for b in e.bullets
        }
        for change in store.list_tailoring_changes(run_id):
            op = change.op
            if change.decision not in ("ACCEPTED", "EDITED"):
                continue  # a proposal the person rejected changed nothing
            text = op.get("edited") or op.get("after") or op.get("proposed_text")
            entry = op.get("entry_id") or rows.get(op.get("target_ref") or "")
            changes.append(
                {"op": op["op"], "text": text, "where": _where(doc, entry),
                 "asks": [quotes[r] for r in change.requirement_ids if r in quotes]}
            )  # fmt: skip
    dismissed = store.dismissed_findings(stored.id)
    suggestions = suggest(doc, supports)
    coverage = [
        {"ask": s.requirement.source_quote, "hardness": s.requirement.hardness,
         "kind": s.requirement.kind, "coverage": s.coverage}
        for s in sorted(supports, key=lambda s: (-s.requirement.importance, s.requirement.start))
    ]  # fmt: skip
    judged = [c for c in coverage if c["coverage"] != "ELIGIBILITY"]
    return {
        "job": {"title": snap.title, "company": snap.company, "job_id": snap.job_id},
        "tailored": doc.provenance.created_from.value == "TAILOR",
        "ai_assisted": ai,
        "ai_reviewed": reviewed,
        "coverage": coverage,
        "supported": sum(c["coverage"] in ("COVERED", "PARTLY") for c in judged),
        "total": len(judged),
        "changes": changes,
        "suggestions": [s for s in suggestions if s["key"] not in dismissed],
    }


def apply_suggestion(
    conn: sqlite3.Connection,
    document_id: str,
    key: str,
    *,
    expected_sha256: str,
    snapshot_id: str | None = None,
) -> StoredDocument:
    """Make one suggestion's exact change, from the confirmed text AS IT IS NOW.

    The browser names the suggestion; it never supplies the line. A suggestion
    that no longer stands (applied, dismissed, or its evidence changed) is
    refused rather than guessed at."""
    store = ResumeStore(conn)
    with transaction(conn):
        stored = store.get_document(document_id)
        said = explain(conn, stored, snapshot_id)
        found = next((s for s in said.get("suggestions", []) if s["key"] == key), None)
        action = found and found["action"]
        if not action:
            raise ResumeStoreError("this suggestion no longer applies")
        data = stored.working.model_dump(mode="json")
        if action["type"] == "add_bullet":
            entry = next(e for e in data["experience"] if e["id"] == action["entry_id"])
            entry["bullets"].insert(0, {
                "id": new_id(), "text": action["text"], "origin": action["origin"],
                "evidence_ids": action["evidence_ids"],
                "requirement_ids": action["requirement_ids"],
            })  # fmt: skip
        elif action["type"] == "show":
            for section in ("experience", "projects", "education"):
                for entry in data[section]:
                    for b in entry["bullets"]:
                        if b["id"] == action["line_id"]:
                            b["hidden"] = False
        else:
            if not data["skills"]:
                name = {"pt": "Competências", "es": "Habilidades"}.get(
                    data["language"][:2], "Skills"
                )
                data["skills"].append({"id": new_id(), "name": name, "hidden": False, "items": []})
            data["skills"][0]["items"].insert(0, {
                "id": new_id(), "label": action["label"], "origin": "EVIDENCE_VERBATIM",
                "evidence_ids": action["evidence_ids"],
            })  # fmt: skip
        store.save_working_copy(
            document_id, upgrade_resume_document(data), expected_sha256=expected_sha256
        )
    return store.get_document(document_id)
