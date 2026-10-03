"""Signs that a job ad may be a scam, read from the ad's own words.

PRESENTATION ONLY. Nothing here reaches a score, a gate or a stored row: the
signals are recomputed from the description every time a card is drawn, and
`career_agent.match` never imports this module.

Each signal is an ASK the ad makes of the candidate, so the screen can name
what it saw. "This ad may be a scam" is the strongest thing said; never "is".
A topic is not an ask, and neither is a duty: "no application fee", "we
reimburse your course fee", "pay vendors on time", "provide bank details for
payroll", "customer support via WhatsApp" and "contact HR on WhatsApp or by
email" are ordinary ads and fire nothing.

Deliberately absent: "pay much higher than similar jobs". That needs a
comparable-salary basis this product does not hold, and a comparison made up
here would be a claim about the market nobody measured.
"""

from __future__ import annotations

import re

_I = re.IGNORECASE

#: The candidate is told to do something: an imperative opening the sentence
#: or a list item, or "you / applicants must", "é necessário", "é obrigatório".
_ASKED = (
    r"(?:^\s*(?:[-*•·]\s*)?(?:please\s+|kindly\s+|por favor,?\s+)?"
    r"|\b(?:you|applicants|candidates|selected candidates)(?:'ll| will)?\s+(?:must|need to|"
    r"have to|are required to|will be asked to|will need to)\s+"
    r"|\b(?:é necessário|é preciso|é obrigatório|você (?:precisa|deve|terá que))\s+)"
)

#: What is being paid FOR. A fee, never a generic "payment" or "valor": a
#: finance job pays vendors and handles deposits as its daily work.
_FEE = r"\b(?:\w+\s+)?(?:fee|taxa|security (?:deposit|payment)|cau[cç][aã]o)\b"

_SIGNALS: dict[str, tuple[re.Pattern[str], ...]] = {
    # Paying to get the job.
    "fee": (
        re.compile(_ASKED + r"(?:pay|deposit|transfer|send)\b[^.\n]{0,50}" + _FEE, _I),
        re.compile(
            _ASKED + r"(?:pague|pagar|deposite|depositar|transfira|transferir|o pagamento de)\b"
            r"[^.\n]{0,50}" + _FEE,
            _I,
        ),
        re.compile(r"\bser[aá] cobrad[ao] (?:uma )?taxa\b", _I),
        # "pague" is an ask wherever it sits ("Para garantir sua vaga, pague a taxa").
        re.compile(r"\bpague\b[^.\n]{0,50}" + _FEE, _I),
    ),
    # The candidate's own bank details, asked for up front.
    "bank": (
        re.compile(
            _ASKED + r"(?:send|provide|share|give)\b[^.\n]{0,25}\byour\s+(?:bank|banking|card)\b",
            _I,
        ),
        re.compile(
            _ASKED + r"(?:envie|enviar|informe|informar|mande|mandar)\b[^.\n]{0,25}"
            r"\bseus dados banc[aá]rios\b",
            _I,
        ),
    ),
    # WhatsApp or Telegram as the ONLY way to apply or make contact.
    "whatsapp": (
        re.compile(
            r"\b(?:only|exclusively|apenas|somente|exclusivamente)\s+(?:(?:via|on|through|by|"
            r"pelo|no|por)\s+)?(?:whats ?app|telegram)\b",
            _I,
        ),
        re.compile(r"\b(?:whats ?app|telegram)\s+(?:only|apenas|somente)\b", _I),
    ),
    # Buying equipment from the employer.
    "equipment": (
        re.compile(
            _ASKED + r"(?:buy|purchase)\b[^.\n]{0,40}\b(?:equipment|kit|laptop|software)\b"
            r"[^.\n]{0,30}\b(?:from us|from our|through us)\b",
            _I,
        ),
        re.compile(
            _ASKED + r"(?:compre|comprar)\b[^.\n]{0,40}\b(?:equipamento|kit)\b[^.\n]{0,30}"
            r"\b(?:conosco|da empresa)\b",
            _I,
        ),
    ),
}

#: Context that makes a bank-details ask ordinary: being paid after an offer.
_PAYROLL = re.compile(
    r"\b(?:payroll|salary|paycheck|wages|after (?:the|your) (?:offer|hire|hiring)|"
    r"folha de pagamento|sal[aá]rio|ap[oó]s a contrata[cç][aã]o)\b",
    _I,
)

#: A WhatsApp-only line counts only when it is about applying or contacting.
_APPLYING = re.compile(
    r"\b(?:apply|applications?|contact|reach|text|message|cv|resume|candidat\w*|contato|"
    r"curr[ií]culo|fale|chame|envie)\b",
    _I,
)


def caution_signals(text: str | None) -> list[str]:
    """The signal keys this ad's own text shows, in a fixed order."""
    if not text:
        return []
    sentences = [part.strip() for part in re.split(r"(?<=[.!?;\n])\s+", text) if part.strip()]
    found: list[str] = []
    for key, patterns in _SIGNALS.items():
        for sentence in sentences:
            if key == "bank" and _PAYROLL.search(sentence):
                continue
            if key == "whatsapp" and not _APPLYING.search(sentence):
                continue
            if any(pattern.search(sentence) for pattern in patterns):
                found.append(key)
                break
    return found
