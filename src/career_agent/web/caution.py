"""Signs that a job ad may be a scam, read from the ad's own words.

PRESENTATION ONLY. Nothing here reaches a score, a gate or a stored row: the
signals are recomputed from the description every time a card is drawn, and
`career_agent.match` never imports this module.

Each signal is an ASK the ad makes of the candidate, so the screen can name
what it saw. "This ad may be a scam" is the strongest thing said; never "is".
A topic is not an ask: "no application fee", "we reimburse your course fee",
"provide bank details for payroll after the offer" and "contact HR on WhatsApp
or by email" are ordinary ads and fire nothing.

Deliberately absent: "pay much higher than similar jobs". That needs a
comparable-salary basis this product does not hold, and a comparison made up
here would be a claim about the market nobody measured.
"""

from __future__ import annotations

import re

_I = re.IGNORECASE

#: The candidate is told to do something: an imperative at the start of the
#: sentence, or "you must / you need to / é necessário".
_ASKED = (
    r"(?:^\s*(?:please\s+|por favor,?\s+)?|\byou(?:'ll| will)?\s+(?:must|need to|have to|"
    r"are required to|will be asked to)\s+|\b(?:é necessário|é preciso|você (?:precisa|deve|"
    r"terá que))\s+)"
)

_SIGNALS: dict[str, tuple[re.Pattern[str], ...]] = {
    # Paying to get the job.
    "fee": (
        re.compile(
            _ASKED + r"(?:pay|deposit|transfer|send)\b[^.\n]{0,50}\b(?:fee|deposit|payment)\b", _I
        ),
        re.compile(
            _ASKED + r"(?:pague|pagar|deposite|depositar|transfira|transferir)\b[^.\n]{0,50}"
            r"\b(?:taxa|valor|dep[oó]sito)\b",
            _I,
        ),
    ),
    # Bank details asked for up front.
    "bank": (
        re.compile(
            _ASKED + r"(?:send|provide|share|give)\b[^.\n]{0,40}\b(?:bank (?:account )?details|"
            r"bank account number|card number|banking information)\b",
            _I,
        ),
        re.compile(
            _ASKED + r"(?:envie|enviar|informe|informar|mande|mandar)\b[^.\n]{0,40}"
            r"\bdados banc[aá]rios\b",
            _I,
        ),
    ),
    # WhatsApp or Telegram as the ONLY way in.
    "whatsapp": (
        re.compile(
            r"\b(?:only|exclusively|apenas|somente|s[oó])\b[^.\n]{0,25}\b(?:whats ?app|telegram)\b",
            _I,
        ),
        re.compile(r"\b(?:whats ?app|telegram)\b[^.\n]{0,12}\b(?:only|apenas|somente)\b", _I),
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

#: Context that makes a bank-details ask ordinary: payroll after an offer.
_PAYROLL = re.compile(
    r"\b(?:payroll|salary|after (?:the|your) (?:offer|hire|hiring)|folha de pagamento|"
    r"dep[oó]sito do sal[aá]rio|ap[oó]s a contrata[cç][aã]o)\b",
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
            if any(pattern.search(sentence) for pattern in patterns):
                found.append(key)
                break
    return found
