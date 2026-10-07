"""Who did what, how often and with whom: the words a rewrite may not move.

A HEURISTIC LEXICON, not a grammar and not a set of equivalences. It names a
few kinds of responsibility a resume line claims, the words that say a person
took PART rather than did the whole, the words that bound a claim (a pilot,
part of the work, a frequency, an audience) and the words that state a
result. `tailor.grounding` uses it to refuse a rewrite that changes any of
these against the evidence it cites; `jd.named_terms` uses the verbs to know
that "Built Zapier ..." opens a sentence with a verb, not a name.

The verb kinds group past-tense forms (EN) and first-person preterites (PT,
folded) by what they say the person did. Two verbs of one kind are not the
same claim ("configured" is narrower than "built"); a verb of ANOTHER kind is
always a different claim ("reviewed" is not "designed", "used" is not
"built"). Words are folded: lower case, no accents.
"""

from __future__ import annotations

_VERBS = {
    "use": "used utilized utilised leveraged usei utilizei",
    "support": (
        "supported assisted helped contributed participated collaborated aided "
        "apoiei auxiliei ajudei contribui participei colaborei"
    ),
    "assess": (
        "reviewed tested analyzed analysed audited evaluated assessed validated checked "
        "monitored revisei testei analisei auditei avaliei validei verifiquei monitorei"
    ),
    "build": (
        "built created developed implemented wrote configured automated integrated migrated "
        "coded programmed deployed connected produced rebuilt construi criei desenvolvi "
        "implementei escrevi configurei automatizei integrei migrei programei implantei "
        "conectei produzi"
    ),
    "design": (
        "designed architected redesigned planned modeled modelled conceived specified "
        "projetei desenhei planejei modelei arquitetei redesenhei"
    ),
    "operate": "maintained operated administered mantive operei administrei",
    "organize": (
        "organized organised structured standardized standardised mapped cleaned documented "
        "prepared reconciled tracked updated consolidated organizei estruturei padronizei "
        "mapeei limpei documentei preparei conciliei acompanhei atualizei consolidei"
    ),
}
#: verb -> the kind of responsibility it claims.
ROLE: dict[str, str] = {w: kind for kind, words in _VERBS.items() for w in words.split()}

#: Words that say the person took part: a rewrite that drops all of them
#: turns a contribution into the whole. "co" is the prefix of "co-authored".
PARTICIPATION = frozenset(
    """
    helped help helping assisted assist assisting contributed contribute contributing
    participated participating collaborated collaborating supported supporting aided co
    ajudei ajudou auxiliei apoiei contribui participei colaborei
    """.split()  # noqa: SIM905
)
#: Two-word forms of the same: "worked with", "part of", "fiz parte".
PARTICIPATION_PAIRS = frozenset(
    [
        ("worked", "with"),
        ("part", "of"),
        ("together", "with"),
        ("fiz", "parte"),
        ("junto", "com"),
        ("em", "conjunto"),
    ]
)
#: "built BY the RevOps team": the work of someone else.
BY = frozenset(["by", "por", "pela", "pelo", "pelas", "pelos"])
#: An agent that is the person themself is no one else's work.
SELF = frozenset(["me", "myself", "mim", "eu"])
#: Irregular participles that can take an agent ("written by").
PARTICIPLES = frozenset("built written made done run led taught sent drawn held".split())  # noqa: SIM905

#: How often, folded to one value per period so a translation still matches.
FREQUENCY = {
    **dict.fromkeys(["daily", "diario", "diaria", "diarios", "diarias", "diariamente"], "day"),
    **dict.fromkeys(["weekly", "semanal", "semanais", "semanalmente"], "week"),
    **dict.fromkeys(["biweekly", "fortnightly", "quinzenal", "quinzenais"], "fortnight"),
    **dict.fromkeys(["monthly", "mensal", "mensais", "mensalmente"], "month"),
    **dict.fromkeys(["quarterly", "trimestral", "trimestrais", "trimestralmente"], "quarter"),
    **dict.fromkeys(["yearly", "annual", "annually", "anual", "anuais", "anualmente"], "year"),
    **dict.fromkeys(["hourly"], "hour"),
}
#: Whom or how far: an audience or an extent the evidence did not state.
EXTENT = frozenset(
    """
    internal external regional national international local entire whole every all
    interno interna internos internas externo externa externos externas regionais nacional
    nacionais internacional internacionais locais inteiro inteira todo toda todos todas
    """.split()  # noqa: SIM905
)
#: What bounds a claim: a rewrite may not drop it ("a pilot of", "partially").
LIMITS = {
    **dict.fromkeys(
        ["partially", "partly", "partial", "part", "parcialmente", "parcial", "parciais"], "partial"
    ),  # fmt: skip
    **dict.fromkeys(["pilot", "pilots", "piloto", "pilotos"], "pilot"),
    **dict.fromkeys(["prototype", "prototypes", "prototipo", "prototipos"], "prototype"),
    **dict.fromkeys(["draft", "drafts", "rascunho", "rascunhos"], "draft"),
    **dict.fromkeys(["initial", "preliminary", "inicial", "preliminar"], "initial"),
    **dict.fromkeys(["some", "alguns", "algumas"], "some"),
}
#: Words that state a result. Read by prefix or whole word.
OUTCOME_PREFIXES = (
    "improv", "reduc", "reduz", "increas", "aument", "decreas", "diminu", "lower", "boost",
    "halv", "shorten", "melhor", "cresc", "economiz", "drop", "grow",
)  # fmt: skip
OUTCOME_WORDS = frozenset(
    """
    cut cuts saved save saves saving savings fell fall falls rose grew less fewer faster
    menos caiu cairam
    """.split()  # noqa: SIM905
)


def outcome(word: str) -> bool:
    return word in OUTCOME_WORDS or word.startswith(OUTCOME_PREFIXES)


def participle(word: str) -> bool:
    """A word that can take an agent: "designed BY", "criados PELA"."""
    return (
        word in ROLE
        or word in PARTICIPLES
        or word.endswith("ed")
        or word.endswith(("ado", "ada", "ados", "adas", "ido", "ida", "idos", "idas"))
    )
