"""Signs that a job ad may be a scam, read from the ad's own words.

PRESENTATION ONLY. Nothing here reaches a score, a gate or a stored row: the
signals are recomputed from the description every time a card is drawn, and
`career_agent.match` never imports this module.

Each signal is a phrase the ad itself contains, so the screen can name what it
saw. "This ad may be a scam" is the strongest thing said; never "is a scam".

Deliberately absent: "pay much higher than similar jobs". That needs a
comparable-salary basis this product does not hold, and a comparison made up
here would be a claim about the market nobody measured.
"""

from __future__ import annotations

import re

#: Signal key -> patterns (English and Portuguese). Anchored on the ASK, not on
#: the topic: "no fees" and "we never ask for payment" must not fire.
_SIGNALS: dict[str, tuple[re.Pattern[str], ...]] = {
    "fee": (
        re.compile(
            r"\b(pay|send|deposit|transfer)\b[^.\n]{0,40}\b(fee|deposit|registration|"
            r"training cost|starter kit)\b",
            re.I,
        ),
        re.compile(r"\b(training|registration|application|onboarding) fee\b", re.I),
        re.compile(r"\btaxa de (inscri[cç][aã]o|cadastro|treinamento|matr[ií]cula)\b", re.I),
        re.compile(r"\bpagar (uma )?taxa\b", re.I),
    ),
    "bank": (
        re.compile(
            r"\b(send|provide|share)\b[^.\n]{0,30}\b(bank (account )?details|bank account number|"
            r"card number|banking information)\b",
            re.I,
        ),
        re.compile(r"\b(envie|informe|mande)\b[^.\n]{0,30}\bdados banc[aá]rios\b", re.I),
    ),
    "whatsapp": (
        re.compile(
            r"\b(contact|message|text|apply|send)\b[^.\n]{0,30}\b(on|via|through|by) "
            r"(whats ?app|telegram)\b",
            re.I,
        ),
        re.compile(
            r"\b(chame|fale|envie|candidate-se)\b[^.\n]{0,30}\b(no|pelo|via) "
            r"(whats ?app|telegram)\b",
            re.I,
        ),
    ),
    "equipment": (
        re.compile(
            r"\b(buy|purchase)\b[^.\n]{0,40}\b(equipment|kit|laptop|software)\b[^.\n]{0,30}"
            r"\b(from us|from our|through us)\b",
            re.I,
        ),
        re.compile(
            r"\bcomprar\b[^.\n]{0,40}\b(equipamento|kit)\b[^.\n]{0,30}\b(conosco|da empresa)\b",
            re.I,
        ),
    ),
}

#: A sentence that DENIES the ask ("we will never ask you for a fee").
_DENIAL = re.compile(r"\b(never|não|nunca|no one will|won't|will not|do not|don't)\b", re.I)


def caution_signals(text: str | None) -> list[str]:
    """The signal keys this ad's own text shows, in a fixed order."""
    if not text:
        return []
    found: list[str] = []
    sentences = re.split(r"(?<=[.!?\n])\s+", text)
    for key, patterns in _SIGNALS.items():
        for sentence in sentences:
            if _DENIAL.search(sentence):
                continue
            if any(pattern.search(sentence) for pattern in patterns):
                found.append(key)
                break
    return found


if __name__ == "__main__":
    assert caution_signals("You must pay a training fee of USD 49 before you start.") == ["fee"]
    assert caution_signals("We never ask for a training fee.") == []
    assert caution_signals("Please send your bank account details first.") == ["bank"]
    assert caution_signals("Contact us only on WhatsApp: +1 555 0100.") == ["whatsapp"]
    assert caution_signals("Buy the starter laptop from us before day one.") == ["equipment"]
    assert caution_signals("Envie seus dados bancários para iniciar.") == ["bank"]
    assert caution_signals("Senior engineer, Python, remote.") == []
    print("ok")
