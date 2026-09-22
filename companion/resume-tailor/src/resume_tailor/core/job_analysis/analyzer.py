# Modified for the Career Agent public edition (2026-09-22). See NOTICE.
"""Job Description Analyzer.

Deterministic baseline: split the JD into lines/bullets, detect section
headings, classify lines by cue phrases (must / nice / responsibility),
detect technologies with the lexicon, infer seniority and years.

LLM refinement (when a provider is configured): the model returns the same
schema; its lists are merged with the deterministic ones, with two rules:

* lexicon technology hits found literally in the JD are always kept;
* a technology the model names that does not appear in the JD text is dropped
  (the analyzer must not hallucinate requirements either).

Finally every must/nice/responsibility/technology becomes a ``Requirement``
with an id, normalized lexicon terms and a weight.
"""

from __future__ import annotations

import re
from typing import Any

from resume_tailor.core.lexicon import (
    GENERIC_TERMS,
    LIST_CONTEXT_ALIASES,
    canonical,
    find_terms,
    known_term,
    term_kind,
)
from resume_tailor.core.models import JobAnalysis, Requirement, RequirementCategory
from resume_tailor.core.text import years_claims
from resume_tailor.providers.llm.base import LLMError, LLMProvider

_SECTION_CUES = {
    "ignore": [
        "benefits",
        "compensation",
        "what we offer",
        "perks",
        "about us",
        "about the company",
        "about our client",
        "about near",
        "work modality",
        "how to apply",
        "equal opportunity",
        "why join",
        "our mission",
        "salary",
        "the company",
        "who we are",
        # employment conditions, not candidate qualifications: pay, office attendance, location, schedule
        "pay and benefits",
        "pay & benefits",
        "benefits & perks",
        "salary range",
        "in-office",
        "office expectations",
        "office attendance",
        "work location",
        "location",
        "remote expectations",
        "hybrid expectations",
        "schedule",
    ],
    "skills": ["skills"],
    "responsibility": [
        "responsibilit",
        "what you'll do",
        "what you will do",
        "you will",
        "your role",
        "the role",
        "duties",
        "day to day",
        "day-to-day",
        "what you'll be doing",
    ],
    "must_have": [
        "requirements",
        "what you'll need",
        "what you need",
        "qualifications",
        "must have",
        "must-have",
        "minimum",
        "required",
        "basic qualifications",
        "you have",
        "skills and experience",
        "what you bring",
        "what you'll bring",
        "you bring",
        "your background",
        "your experience",
        "what you have",
        "minimum requirements",
        "minimum qualifications",
        "required qualifications",
    ],
    # ideal-candidate sections: bulleted lines and strong requirement cues are qualifications,
    # descriptive prose contributes cues and terms but never becomes a hard requirement
    "context": [
        "who you are",
        "about you",
        "your profile",
        "what we're looking for",
        "what we are looking for",
    ],
    "nice_to_have": [
        "nice to have",
        "nice-to-have",
        "nice-to-haves",
        "preferred",
        "bonus",
        "plus",
        "ideally",
        "good to have",
        "desirable",
        "extra credit",
        "it would be great",
        "preferred qualifications",
        "preferred requirements",
        "preferred experience",
        "bonus qualifications",
    ],
}
# key:value job-posting metadata: high-confidence labels only, never candidate requirements
_META_KEY = re.compile(
    r"^(job title|title|role|position|job type|employment type|location|work location|schedule)\s*:\s*\S",
    re.IGNORECASE,
)
_META_SCOPE = re.compile(
    r"^(job type|employment type|location|work location|schedule)\s*:", re.IGNORECASE
)
# a standalone line that reads as a qualification statement: inside an explicit qualification
# section it is an item even without a bullet marker
_QUAL_START = re.compile(
    r"^(experience|familiarity|proficien\w+|knowledge of|working knowledge|strong|excellent|solid|deep|expert|ability to|comfortable|exposure to|understanding of|track record|background in|degree|bachelor|master|fluen\w+|hands-on|demonstrated|proven|willingness|passion for|genuine)\b",
    re.IGNORECASE,
)
# only true optionality moves a rescued plain line to nice-to-have; a soft lead verb does not
_OPTIONAL_INLINE = re.compile(
    r"\b(a plus|is a plus|bonus|not required|nice to have|ideally|would be a plus|desirable|optional|preferred)\b",
    re.IGNORECASE,
)
_NICE_INLINE = re.compile(
    r"\b(nice to have|preferred|a plus|is a plus|bonus|ideally|would be a plus|desirable|not required|familiarity with)\b",
    re.IGNORECASE,
)
_MUST_INLINE = re.compile(
    r"\b(must|required|require|minimum|at least|proven|strong|demonstrated|hands-on|hands on|expert|extensive)\b",
    re.IGNORECASE,
)
_RESP_START = re.compile(
    r"^(become|handle|conduct|design|build|develop|own|manage|lead|partner|collaborate|drive|maintain|support|implement|create|define|deliver|analy[sz]e|monitor|document|automate|integrate|configure|administer|translate|work|ensure|identify|optimi[sz]e|troubleshoot|serve|act|coordinate|run|operate|execute|evaluate|improve|establish|champion|mentor|coach|report|present|scope|architect|migrate|enable|gather|elicit)\b",
    re.IGNORECASE,
)
_SENIORITY = [
    ("principal", "principal"),
    ("staff", "staff"),
    ("director", "director"),
    ("head of", "head"),
    ("vp", "vp"),
    ("lead", "lead"),
    ("senior", "senior"),
    ("sr.", "senior"),
    ("sr ", "senior"),
    ("junior", "junior"),
    ("jr.", "junior"),
    ("entry", "entry"),
    ("internship", "intern"),
    ("intern", "intern"),
    ("associate", "associate"),
    ("mid-level", "mid"),
]
_MGMT = re.compile(
    r"\b(manage a team|lead a team|direct reports|people management|hire and|hiring and managing|managing a team|team of \d+|mentor)\b",
    re.IGNORECASE,
)
_CUSTOMER = re.compile(
    r"\b(customer-facing|client-facing|customers|clients|stakeholders|executives|c-suite|presentations?|demos?|pre-sales|post-sales|account)\b",
    re.IGNORECASE,
)
_SCOPE = re.compile(
    r"(everything is no-code|no-code only|no code only|zero code|writes zero code|no code required|non-coding role|not a customer-facing|not a people[- ]management|\bon-site\b|\bonsite\b|\bin-office\b|\bhybrid\b|relocat\w*|latin america-based|based in latin america|us time zones?|u\.s\. (business|working) hours|time zone overlap|overlap with u\.s\.|fully remote|remote-first|asynchronous environment)",
    re.IGNORECASE,
)
_DESCRIPTIVE = re.compile(
    r"^(the ideal candidate|you should be|you are |we are looking for|we're looking for|if you are|if you're|join our|our client|note:)",
    re.IGNORECASE,
)
_HARD = re.compile(
    r"\b(bachelor'?s?|master'?s?|degree|authori[sz]ed to work|work authori[sz]ation|visa|citizen|clearance|on-site|onsite|hybrid|relocat|located in|based in|time ?zone|\d+\+?\s*years|certified|certification|fluent|bilingual|travel)\b",
    re.IGNORECASE,
)

SYSTEM_PROMPT = """You are a job-description analyst for a resume-tailoring tool.
Read the job description and return STRICT JSON with this shape:
{
  "role_title": "", "company": "", "seniority": "",
  "domain": [], "must_have": [], "nice_to_have": [], "responsibilities": [],
  "technologies": [], "business_capabilities": [], "keywords": [],
  "customer_facing_expectations": [], "management_expectations": [],
  "potential_hard_filters": [], "years_required": null
}
Rules:
- Preserve the JD's own wording for requirements (ATS matching depends on it); trim to one requirement per item.
- must_have = explicitly required; nice_to_have = preferred/bonus/plus. Never move a "preferred" item into must_have.
- technologies = named tools/platforms/languages ONLY if they literally appear in the JD.
- business_capabilities = process/business outcomes the role must deliver (e.g. "quote-to-cash", "contract-to-billing reconciliation").
- keywords = 10-25 ATS-relevant terms/phrases from the JD.
- potential_hard_filters = degree, years, location, authorization, certifications, language.
- years_required = integer minimum years if stated, else null.
Do not invent anything that is not in the text."""


def _clean_line(s: str) -> str:
    s = re.sub(r"^[\s\-\*•●▪–—>·o#]+", "", s)  # bullets and Markdown heading markers are decoration
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _is_heading(line: str) -> bool:
    if len(line) > 70:
        return False
    if line.endswith(":"):
        return True
    words = line.split()
    if not (1 <= len(words) <= 6) or line.endswith("."):
        return False
    if line.istitle() or line.isupper():
        return True
    # a short lowercase line is a heading only when it *is* a cue ("nice to have"), not when a
    # sentence merely contains one ("Back-end/admin depth is a plus")
    low = line.lower().strip(" :")
    return any(
        low == c or (len(c) >= 5 and low.startswith(c))
        for cues in _SECTION_CUES.values()
        for c in cues
    )


def _section_for(line: str) -> str | None:
    low = line.lower()
    best: tuple[int, str] | None = None
    for sec, cues in _SECTION_CUES.items():
        for c in cues:
            if c in low and (best is None or len(c) > best[0]):
                best = (len(c), sec)
    return best[1] if best else None


_ROLE_WORDS = re.compile(
    r"\b(engineer|manager|analyst|architect|specialist|developer|administrator|consultant|lead|director|scientist|coordinator|associate|officer)\b",
    re.IGNORECASE,
)
_MODALITY = re.compile(
    r"\b(part[- ]time|full[- ]time|remote|hybrid|on[- ]?site|contract(or)?|freelance|temporary|permanent|[A-Z]{2,4}T hours|[A-Z]{2,4} hours|business hours)\b",
    re.IGNORECASE,
)


def _is_title_line(line: str, title: str, section: str | None, bulleted: bool) -> bool:
    """A repeated or decorated job title ("Senior X Engineer (Full-Time | Remote - EST Hours)")
    is a heading, not a requirement. Real requirements keep their section context: a bulleted
    line inside Requirements is never treated as a title, however short."""
    if bulleted or not line:
        return False
    core = re.sub(r"\s*[\(\[|].*$", "", line).strip(" -:")
    if title and (core.lower() == title.lower() or line.lower() == title.lower()):
        return True
    if (
        section is None
        and len(line) <= 120
        and _ROLE_WORDS.search(core)
        and _MODALITY.search(line)
        and not _MUST_INLINE.search(line)
    ):
        # a role noun plus a modality tag before any section: "Business Systems Engineer (Remote, EST)"
        return not re.search(
            r"\b(years?|experience|ability|knowledge|proficien\w+|availability|fluent|degree)\b",
            line,
            re.IGNORECASE,
        )
    return False


_VERB_WORDS = r"own|support|manage|build|maintain|troubleshoot|ensure|design|develop|lead|partner|collaborate|drive|implement|create|define|deliver|analy[sz]e|monitor|document|automate|integrate|configure|administer|translate|work|identify|optimi[sz]e|investigate|reconcile|help|improve|operate|evaluate|run|coordinate|report|scope|migrate|enable|gather|handle|oversee|keep|resolve|track|set up|onboard"
_VERB = re.compile(rf"^(?:{_VERB_WORDS})\b", re.IGNORECASE)
_NARRATIVE_LEAD = re.compile(
    r"^(?:you(?:(?:'|’)(?:ll|d)|\s+(?:will|would))(?: be)?(?: also)?\s+|this (?:person|role) (?:will|would)\s+|the (?:role|position) (?:is|will be)\s+|(?:and\s+)?helping\s+(?:to\s+)?|(?:and\s+)?help\s+(?:to\s+)?)",
    re.IGNORECASE,
)
_WORKING_WITH = re.compile(
    r"^(?:work(?:ing)? with|using|leveraging|working across)\s*", re.IGNORECASE
)
# "platforms such as A/B, C, and D" up to the point where the sentence moves on to what the person does
_PLATFORM_LIST = re.compile(
    rf"\b(platforms?|tools?|systems?|technologies|frameworks?|stack)\s+(?:such as|like|including|e\.g\.,?)\s+((?:(?!,\s+(?:and\s+)?(?:{_VERB_WORDS}|\w+ing)\b)[^.;])+)",
    re.IGNORECASE,
)
_PLATFORM_PAREN = re.compile(
    r"\b(?:platforms?|tools?|systems?|technologies|frameworks?|software|stack|suites?)\s*\(([^)]+)\)",
    re.IGNORECASE,
)
_TOOLS_TO = re.compile(
    rf"^(?:work(?:ing)? with|using|leveraging)\s+(.+?)\s+to\s+((?:{_VERB_WORDS})\b.*)$",
    re.IGNORECASE,
)
_FAMILY_LIST = re.compile(
    r"\b(?:intersection of|across|spanning|covering|centered on|focused on)\s+([^.;]+)",
    re.IGNORECASE,
)
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
_MARKETING_PROSE = re.compile(
    r"^(we\b|we're|our (mission|team|company|clients?)|join (us|our)|about (us|the)|founded|headquartered|benefits|apply)",
    re.IGNORECASE,
)


def _split_platforms(listing: str) -> list[str]:
    """'Treasure AI/CDP, HubSpot/Marketo, and Adobe Analytics/CJA' -> each named platform, in the
    JD's own spelling; 'X/Y' lists two platforms, it never says they are the same thing."""
    out: list[str] = []
    for chunk in re.split(r",|\band\b|\bor\b|/", listing):
        c = chunk.strip(" .,;:()")
        c = re.sub(r"^(such as|like|including|e\.g\.)\s+", "", c, flags=re.IGNORECASE)
        if (
            c
            and len(c) <= 40
            and len(c.split()) <= 4
            and re.search(r"[A-Z]", c)
            and c.lower() not in {x.lower() for x in out}
        ):
            out.append(c)
    return out


def platform_signals(line: str) -> list[str]:
    """Named platforms a line lists as examples: "platforms such as A, B or similar" and
    "Platforms (A, B, C)". Each name is a separate signal in the JD's own spelling; nothing here
    says two of them are the same product."""
    out: list[str] = []
    for m in [*_PLATFORM_LIST.finditer(line), *_PLATFORM_PAREN.finditer(line)]:
        out.extend(
            x
            for x in _split_platforms(m.group(m.lastindex))
            if x.lower() not in {y.lower() for y in out}
        )
    return out


def platform_term(name: str) -> str:
    """Canonical lexicon term for a listed platform name, resolving list-only spellings (Make -> make.com);
    an unknown platform keeps the JD's spelling."""
    c = canonical(name)
    return LIST_CONTEXT_ALIASES.get(c, c) if known_term(name) or c in LIST_CONTEXT_ALIASES else name


def _atomic_clauses(body: str) -> list[str]:
    """Comma/'and'-joined verb clauses, one responsibility each. A noun-only fragment inherits the
    verb of the clause before it ('manage data flows, segmentation' -> 'manage segmentation'); an
    adjective list after 'are/is' is distributed ('ensure the platforms are reliable, secure, and
    well-governed' -> three clauses); 'own and support X' stays one clause."""
    parts = [
        re.sub(r"^(?:and\s+)?help(?:ing)?\s+(?:to\s+)?", "", x.strip(" ,;"), flags=re.IGNORECASE)
        for x in re.split(r",|;|\band\b", body)
        if x.strip(" ,;")
    ]
    parts = [
        x
        for x in parts
        if x
        and x.lower() not in {"platforms", "platform", "tools", "systems", "technologies", "stack"}
    ]
    clauses: list[str] = []
    verb = ""
    i = 0
    while i < len(parts):
        p = parts[i]
        m = re.match(r"^(.*\b(?:are|is|remain|stay)s?)\s+(.+)$", p)
        if (
            m
            and i + 1 < len(parts)
            and not _VERB.match(parts[i + 1])
            and len(parts[i + 1].split()) <= 3
        ):
            head, first = m.group(1), m.group(2)
            adjectives = [first]
            j = i + 1
            while j < len(parts) and not _VERB.match(parts[j]) and len(parts[j].split()) <= 3:
                adjectives.append(parts[j])
                j += 1
            clauses.extend(f"{head} {a}" for a in adjectives)
            i = j
            continue
        if _VERB.match(p):
            if len(p.split()) == 1 and i + 1 < len(
                parts
            ):  # "own and support X": two verbs, one object
                parts[i + 1] = f"{p} and {parts[i + 1]}"
                i += 1
                continue
            verb = _VERB.match(p).group(0).lower()
            clauses.append(p)
        elif verb:
            clauses.append(f"{verb} {p}")
        else:
            clauses.append(p)
        i += 1
    return clauses


def narrative_analysis(
    lines: list[str], title: str, scope: list[str]
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Requirements from a short JD without headings. Returns (must_have, responsibilities,
    capabilities, named_platforms). Company prose ("We ...") and role-scope sentences are skipped;
    'platforms such as A/B, C' yields platform signals (never hard filters); 'intersection of A,
    B and C' yields capability families; verb sentences yield atomic responsibilities; only
    sentences with an explicit requirement cue become must-haves."""
    must: list[str] = []
    resp: list[str] = []
    caps: list[str] = []
    platforms: list[str] = []

    def add_platforms(listing: str) -> None:
        platforms.extend(
            x for x in _split_platforms(listing) if x.lower() not in {y.lower() for y in platforms}
        )

    for line in lines:
        if line == title or len(line) < 25:
            continue
        for sentence in _SENTENCE.split(line):
            s = sentence.strip()
            if not s or s in scope or _SCOPE.search(s) or _DESCRIPTIVE.match(s):
                continue
            for m in _PLATFORM_LIST.finditer(s):
                add_platforms(m.group(2))
            if _MARKETING_PROSE.match(s):
                continue  # company context: platforms above are kept as signals, the sentence is not a duty
            fam = _FAMILY_LIST.search(s)
            if fam and not _MUST_INLINE.search(s.replace("hands-on", "")):
                for c in [
                    x.strip(" .,;:")
                    for x in re.split(r",|\band\b", fam.group(1))
                    if x.strip(" .,;:")
                ]:
                    terms = find_terms(c)
                    if terms and any(term_kind(t) == "technology" for t in terms):
                        continue  # a platform family (Customer Data Platforms) is a technology signal, listed there
                    key = terms[0] if terms else c.lower()
                    if key not in {(find_terms(x) or [x.lower()])[0] for x in caps}:
                        caps.append(c)
                continue
            body = _NARRATIVE_LEAD.sub("", s.rstrip("."))
            tools_to = _TOOLS_TO.match(body)
            if (
                tools_to
            ):  # "work with Salesforce and Outreach to support GTM workflows, routing ..."
                add_platforms(tools_to.group(1))
                body = tools_to.group(2)
            body = _PLATFORM_LIST.sub(r"\1", body).strip(" ,;")
            body = _NARRATIVE_LEAD.sub("", _WORKING_WITH.sub("", body)).strip(" ,;")
            clauses = _atomic_clauses(body)
            if _MUST_INLINE.search(s) and not _NARRATIVE_LEAD.match(s):
                must.extend(clauses)
            else:
                resp.extend(c for c in clauses if _VERB.match(c) or find_terms(c))
    return must, resp, caps, platforms


_TITLE_NOUN = r"(?:Engineer|Manager|Analyst|Architect|Specialist|Developer|Administrator|Consultant|Scientist|Designer|Coordinator|Director)"
# strong self-identification only: capitalized role phrases in fixed frames, never arbitrary nouns
_TITLE_FALLBACKS = [
    re.compile(rf"\bAs an? ((?:[A-Z][\w&/-]*\s+){{0,4}}{_TITLE_NOUN})\b"),
    re.compile(rf"\bThe ((?:[A-Z][\w&/-]*\s+){{1,4}}{_TITLE_NOUN}) will\b"),
    re.compile(
        rf"\bWe(?:'re| are) (?:hiring|looking for|seeking) an? ((?:[A-Z][\w&/-]*\s+){{0,4}}{_TITLE_NOUN})\b"
    ),
    re.compile(rf"\b((?:[A-Z][\w&/-]*\s+){{1,4}}{_TITLE_NOUN}s)\s*(?:\([A-Za-z .]+\))?\s+are\b"),
]


# employment metadata a role heading may carry after the actual title: modality, location,
# schedule. Legitimate qualifiers ("Product Manager, Financial Systems", "- Integrations",
# "(Billing & Revenue Automation)") carry none of these cues and are never stripped.
_MODALITY_CUE = re.compile(
    r"\b(part[- ]?time|full[- ]?time|remote|hybrid|on[- ]?site|onsite|contract(?:or)?|freelance|temporary|permanent"
    r"|work from home|wfh|latam|emea|apac|americas|hours?|hrs?|\d{1,2}\s*[–-]\s*\d{1,2})\b",
    re.IGNORECASE,
)


def _strip_modality(line: str) -> tuple[str, str]:
    """(canonical title, stripped employment-modality suffix). Only trailing parentheticals and
    "|" / dash segments that clearly carry modality/location/schedule cues are removed."""
    suffix: list[str] = []
    core = line.strip()
    while True:
        m = re.search(r"\s*\(([^()]*)\)\s*$", core)
        if m and _MODALITY_CUE.search(m.group(1)):
            suffix.insert(0, m.group(1).strip())
            core = core[: m.start()].rstrip(" ,;|–—-")
            continue
        m = re.search(r"\s+[|–—]\s*([^|–—]+)$", core) or re.search(r"\s+-\s+([^-]+)$", core)
        if m and _MODALITY_CUE.search(m.group(1)):
            suffix.insert(0, m.group(1).strip())
            core = core[: m.start()].rstrip(" ,;|–—-")
            continue
        return core, " | ".join(suffix)


def _title_from(jd: str) -> str:
    for raw in jd.splitlines()[:8]:
        # an explicit heading near the top ("# Business Systems & AI Automation Engineer
        # (Part-Time / Full-Time | Remote – EST Hours)"): the modality suffix comes off first,
        # so the safety rules below judge the actual title, not the employment metadata
        line, _ = _strip_modality(_clean_line(raw))
        if not line or len(line) > 90 or line.endswith(".") or len(line.split()) > 10:
            continue  # a sentence is not a title, however early it appears
        if _section_for(line):
            continue  # a structural heading (About the Role, Requirements, Benefits) is never a title
        low = line.lower()
        if any(
            k in low
            for k in (
                "engineer",
                "manager",
                "analyst",
                "architect",
                "specialist",
                "developer",
                "administrator",
                "consultant",
                "lead",
                "director",
                "scientist",
                "operations",
            )
        ):
            return re.sub(r"^(job title|title|position|role)\s*:\s*", "", line, flags=re.IGNORECASE)
    # no title heading: accept a high-confidence self-identification ("As a <Role>, you will",
    # "<Role>s are a critical component"); anything weaker stays untitled
    for pat in _TITLE_FALLBACKS:
        m = next(
            (x for line in jd.splitlines() if (x := pat.search(line))), None
        )  # titles never span lines
        if m:
            title = re.sub(r"\s+", " ", m.group(1)).strip()
            title = title.removesuffix("s")  # "Integration Reliability Engineers" -> singular
            if 2 <= len(title.split()) <= 5 and len(title) <= 60:
                return title
    return ""


def deterministic_analysis(jd_text: str) -> JobAnalysis:
    raw_lines = [line for line in jd_text.splitlines() if _clean_line(line)]
    bulleted = {_clean_line(line) for line in raw_lines if re.match(r"^\s*[\-\*•●▪–—>·]", line)}
    lines = [_clean_line(line) for line in raw_lines]
    title = _title_from(jd_text)
    section = None
    must, nice, resp = [], [], []
    skills: list[str] = []
    hard: list[str] = []
    scope: list[str] = []
    # the employment-modality suffix stripped off the role heading stays visible as role scope
    for raw in jd_text.splitlines()[:8]:
        core, modality = _strip_modality(_clean_line(raw))
        if modality and core and title and core.lower() == title.lower():
            scope.append(f"Role modality: {modality}")
            break
    kept_lines: list[
        str
    ] = []  # lines that are not benefits/company blurbs: technology extraction uses only these
    for i, line in enumerate(lines):
        if _SCOPE.search(line) and len(line) < 400:
            scope.append(line)
        if re.match(r"^(compensation|salary|pay)\s*:", line, re.IGNORECASE):
            continue
        if _META_KEY.match(line):
            if _META_SCOPE.match(line):
                scope.append(
                    line
                )  # "Job Type: Full-time", "Location: Remote": conditions, not skills
            continue  # "Job Title: ..." already fed the title; no metadata line is a requirement
        if _is_title_line(line, title, section, line in bulleted) or (
            i < 3
            and len(line) < 90
            and not find_terms(line)
            and not _MUST_INLINE.search(line)
            and not (_is_heading(line) and _section_for(line))
        ):
            continue  # title / company / location header lines are not requirements (but a real section heading is never swallowed)
        if len(line) > 320 and not _MUST_INLINE.search(line) and section != "must_have":
            continue  # long intro prose; requirements come from the bulleted sections
        # a certification named on a title-case line inside Requirements ("Hubspot Service Hub Software
        # Certified") is a requirement, not a sub-heading
        named_cert = (
            section in ("must_have", "nice_to_have")
            and re.search(r"\b(certified|certification)\b", line, re.IGNORECASE)
            and find_terms(line)
        )
        if (
            line not in bulleted
            and _is_heading(line)
            and not named_cert
            and (section != "skills" or _section_for(line) or line.endswith(":"))
        ):
            # inside a Skills list, title-case items ("Project Management") are items, not headings
            sec = _section_for(line)
            if sec:
                section = sec
            continue
        if len(line) < 3:
            continue
        if (
            section is None
            and len(line) > 150
            and not _MUST_INLINE.search(line)
            and not _RESP_START.match(line)
        ):
            continue  # company blurb before any recognised section
        if _HARD.search(line) and len(line) < 220 and not _RESP_START.match(line):
            hard.append(line)
        if section == "ignore" or _DESCRIPTIVE.match(line):
            continue  # benefits, company blurbs and calls to action are not requirements
        kept_lines.append(line)
        plain_qual = (
            section in ("context", "must_have")
            and line not in bulleted
            and len(line) <= 160
            and len(_SENTENCE.split(line)) == 1
            and bool(_QUAL_START.match(line))
        )
        if (
            section == "context"
            and line not in bulleted
            and not _MUST_INLINE.search(line)
            and not plain_qual
        ):
            continue  # ideal-candidate prose: profile cues and terms, never a hard requirement
        if section == "skills":
            skills.append(line)
            continue
        if (
            _SCOPE.search(line)
            and len(line) < 160
            and not find_terms(
                line.replace("no-code", "").replace("no code", "").replace("zero code", "")
            )
        ):
            continue  # a pure scope statement ("This role writes zero code") is an observation, not a requirement
        target = "must_have" if section == "context" else section
        if (_OPTIONAL_INLINE if plain_qual else _NICE_INLINE).search(line):
            # a rescued plain line moves to nice-to-have only for true optionality ("a plus"),
            # never for a soft lead ("Familiarity with ...") under a required heading
            target = "nice_to_have"
        elif target is None:
            if _RESP_START.match(line):
                target = "responsibility"
            elif _MUST_INLINE.search(line) or find_terms(line):
                target = "must_have"
        if (
            target == "responsibility"
            and _RESP_START.match(line) is None
            and _MUST_INLINE.search(line)
        ):
            target = "must_have"
        if target == "must_have":
            must.append(line)
        elif target == "nice_to_have":
            nice.append(line)
        elif target == "responsibility":
            resp.append(line)

    # a JD with no recognised section: read the prose sentence by sentence instead of treating
    # a whole paragraph as one must-have
    narrative_platforms: list[str] = []
    narrative_caps: list[str] = []
    if (
        not any(_section_for(line) for line in lines if line not in bulleted and _is_heading(line))
        and not bulleted
    ):
        n_must, n_resp, n_caps, n_platforms = narrative_analysis(lines, title, scope)
        must, resp = n_must, n_resp
        narrative_caps, narrative_platforms = n_caps, n_platforms
        kept_lines = [
            line
            for line in lines
            if line != title and not _SCOPE.search(line) and not _DESCRIPTIVE.match(line)
        ]

    else:
        # the same platform-listing rule the narrative parser uses: "platforms such as A, B" and
        # "Platforms (A, B, C)" anywhere in the JD name individual platforms, known or not
        narrative_platforms = _dedupe([p for line in kept_lines for p in platform_signals(line)])
    found = [
        t
        for t in find_terms("\n".join([title, *kept_lines, *scope]))
        if t not in GENERIC_TERMS or t in ("crm", "erp", "hris", "ats")
    ]
    # a "Skills" list contributes ATS keywords; items naming a lexicon term are classified like any other
    # term (technology / capability / domain) and a listing item names its platforms; an item the
    # lexicon does not know stays a keyword, never a technology
    skill_terms = [t for line in skills for t in find_terms(line)]
    found = _dedupe([*found, *[t for t in skill_terms if t not in GENERIC_TERMS]])
    # taxonomy: platforms are technologies; practices are capabilities; domains are domains
    techs = [t for t in found if term_kind(t) == "technology"]
    capabilities = _dedupe(
        [
            *[t for t in found if term_kind(t) == "capability"],
            *[c for c in narrative_caps if not (find_terms(c) and find_terms(c)[0] in found)],
        ]
    )
    for p in narrative_platforms:  # named platforms: a lexicon product by its canonical name, an unknown one in the JD's own words
        t = platform_term(p)
        if t.lower() not in {x.lower() for x in techs} and (
            not known_term(t) or term_kind(t) == "technology"
        ):
            techs.append(t)
    low = jd_text.lower()
    hay = f"{_title_from(jd_text).lower()}\n{low[:400]}"
    seniority = next(
        (
            label
            for cue, label in _SENIORITY
            if re.search(rf"(?<![a-z0-9]){re.escape(cue)}(?![a-z0-9])", hay)
        ),
        "",
    )
    years = years_claims(jd_text)
    mgmt = sorted({m.group(0) for m in _MGMT.finditer(jd_text)})
    cust = sorted({m.group(0).lower() for m in _CUSTOMER.finditer(jd_text)})
    keywords = _dedupe([*techs, *capabilities, *skills])[:40]
    domains = [t for t in found if term_kind(t) == "domain"]
    return JobAnalysis(
        role_title=_title_from(jd_text),
        seniority=seniority,
        domain=domains,
        must_have=_dedupe(must),
        nice_to_have=_dedupe(nice),
        responsibilities=_dedupe(resp),
        technologies=techs,
        keywords=keywords,
        business_capabilities=capabilities,
        customer_facing_expectations=cust[:8],
        management_expectations=mgmt,
        potential_hard_filters=_dedupe(hard)[:10],
        years_required=min(years) if years else None,
        role_scope_observations=_dedupe(scope)[:8],
        analysis_source="deterministic",
    )


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for i in items:
        k = i.lower().strip(" .")
        if k and k not in seen:
            seen.add(k)
            out.append(i)
    return out


def _as_list(v: Any) -> list[str]:
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v, str) and v.strip():
        return [v.strip()]
    return []


def merge_llm(base: JobAnalysis, data: dict[str, Any], jd_text: str) -> JobAnalysis:
    low = jd_text.lower()
    llm_techs = [t for t in _as_list(data.get("technologies")) if t.lower() in low]
    techs = _dedupe(
        [
            *base.technologies,
            *[t for t in llm_techs if t.lower() not in {b.lower() for b in base.technologies}],
        ]
    )
    must = _as_list(data.get("must_have")) or base.must_have
    nice = _as_list(data.get("nice_to_have")) or base.nice_to_have
    # a "preferred"-flagged line can never sit in must_have
    must = [m for m in must if not _NICE_INLINE.search(m)]
    resp = _as_list(data.get("responsibilities")) or base.responsibilities
    years = data.get("years_required")
    try:
        years_i = int(years) if years is not None else base.years_required
    except (TypeError, ValueError):
        years_i = base.years_required
    return JobAnalysis(
        role_title=str(data.get("role_title") or base.role_title),
        company=str(data.get("company") or ""),
        seniority=str(data.get("seniority") or base.seniority),
        domain=_dedupe([*_as_list(data.get("domain")), *base.domain]),
        must_have=_dedupe(must),
        nice_to_have=_dedupe(nice),
        responsibilities=_dedupe(resp),
        technologies=techs,
        business_capabilities=_as_list(data.get("business_capabilities")),
        keywords=_dedupe([*_as_list(data.get("keywords")), *base.keywords]),
        customer_facing_expectations=_as_list(data.get("customer_facing_expectations"))
        or base.customer_facing_expectations,
        management_expectations=_dedupe(
            [*_as_list(data.get("management_expectations")), *base.management_expectations]
        ),
        potential_hard_filters=_dedupe(
            [*_as_list(data.get("potential_hard_filters")), *base.potential_hard_filters]
        ),
        years_required=years_i,
        role_scope_observations=base.role_scope_observations,
        analysis_source="llm+deterministic",
    )


WEIGHTS = {
    RequirementCategory.MUST_HAVE: 3.0,
    RequirementCategory.RESPONSIBILITY: 2.0,
    RequirementCategory.TECHNOLOGY: 2.0,
    RequirementCategory.CAPABILITY: 2.0,
    RequirementCategory.DOMAIN: 1.5,
    RequirementCategory.NICE_TO_HAVE: 1.0,
}


def build_requirements(ja: JobAnalysis, evidenced_terms: set[str] | None = None) -> JobAnalysis:
    """Turn the analysis lists into requirements. A JD technology becomes its own
    requirement when no other requirement line covers it, or when ``evidenced_terms``
    is given and the tool is not evidenced at all (so every tool gap is visible even
    inside an "X or Y" line that another tool satisfied)."""
    reqs: list[Requirement] = []
    covered_terms: set[str] = set()

    def add(cat: RequirementCategory, text: str) -> None:
        # a named platform the lexicon does not know ("HubSpot Intelligence", "Elvax Agents") is matched
        # as itself, not through the known product or word inside its name
        terms = (
            []
            if cat == RequirementCategory.TECHNOLOGY and not known_term(text)
            else find_terms(text)
        )
        covered_terms.update(terms)
        reqs.append(
            Requirement(
                id=f"{cat.value[:4]}_{len(reqs) + 1:03d}",
                text=text,
                category=cat,
                terms=terms,
                weight=WEIGHTS[cat],
            )
        )

    for t in ja.must_have:
        add(RequirementCategory.MUST_HAVE, t)
    for t in ja.nice_to_have:
        add(RequirementCategory.NICE_TO_HAVE, t)
    for t in ja.responsibilities:
        add(RequirementCategory.RESPONSIBILITY, t)
    for t in ja.business_capabilities:
        terms = find_terms(t)
        covered = bool(terms) and all(x in covered_terms for x in terms)
        unevidenced = (
            evidenced_terms is not None
            and bool(terms)
            and any(x not in evidenced_terms for x in terms)
        )
        if covered and not unevidenced:
            continue  # a requirement line already carries this capability and the bank evidences it
        add(RequirementCategory.CAPABILITY, t)
    for t in ja.technologies:
        if t in GENERIC_TERMS:
            continue
        if t not in covered_terms or (evidenced_terms is not None and t not in evidenced_terms):
            add(RequirementCategory.TECHNOLOGY, t)
    for t in ja.domain:
        if t.lower() not in covered_terms:
            add(RequirementCategory.DOMAIN, t)
    return ja.model_copy(update={"requirements": reqs})


class JobAnalysisService:
    """Career Agent integration point: ``analyze(jd_text) -> JobAnalysis``."""

    def __init__(self, llm: LLMProvider | None = None):
        self.llm = llm

    def analyze(
        self, jd_text: str, evidenced_terms: set[str] | None = None
    ) -> tuple[JobAnalysis, list[str]]:
        warnings: list[str] = []
        base = deterministic_analysis(jd_text)
        ja = base
        if self.llm is not None and self.llm.name != "none":
            try:
                resp = self.llm.complete_json(
                    SYSTEM_PROMPT, f"JOB DESCRIPTION:\n\n{jd_text[:12000]}", max_tokens=3000
                )
                if resp.data:
                    ja = merge_llm(base, resp.data, jd_text)
            except LLMError as e:
                warnings.append(f"job analysis: LLM unavailable, deterministic output used ({e})")
        return build_requirements(ja, evidenced_terms), warnings
