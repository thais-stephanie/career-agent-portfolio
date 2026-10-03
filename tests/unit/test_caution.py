"""Scam signs are asks the ad itself makes, and they are only shown, never scored."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from career_agent.web.caution import caution_signals

MATCH = Path(__file__).resolve().parents[2] / "src" / "career_agent" / "match"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Before you start you must pay a training fee of USD 49.", ["fee"]),
        ("Pay the $50 registration fee and don't miss this opportunity.", ["fee"]),
        ("Antes de começar, é preciso pagar uma taxa de inscrição.", ["fee"]),
        ("Pague a taxa de inscrição de R$50, não perca!", ["fee"]),
        ("Please send your bank account details first.", ["bank"]),
        ("Send your bank account details to start; no experience needed.", ["bank"]),
        ("Envie seus dados bancários para iniciar.", ["bank"]),
        ("Contact us only on WhatsApp: +1 555 0100.", ["whatsapp"]),
        ("Candidaturas somente pelo WhatsApp.", ["whatsapp"]),
        ("Buy the starter laptop from us before day one.", ["equipment"]),
        # A list item, as html_to_text renders <li>, is an ask too.
        ("- Pay the training fee before day one", ["fee"]),
        ("- Send your bank account details to HR", ["bank"]),
        ("Kindly pay the registration fee of $35.", ["fee"]),
        ("Kindly send your bank account details.", ["bank"]),
        ("Applicants are required to pay a $50 processing fee.", ["fee"]),
        ("Selected candidates must deposit $100 as security payment.", ["fee"]),
        ("É obrigatório o pagamento de uma taxa de R$ 60.", ["fee"]),
        ("Será cobrada uma taxa de inscrição de R$ 50.", ["fee"]),
        ("Para garantir sua vaga, pague a taxa de R$ 80 via Pix.", ["fee"]),
    ],
)
def test_an_ask_the_ad_makes_is_named(text: str, expected: list[str]) -> None:
    assert caution_signals(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "There is no application fee.",
        "Sem taxa de inscrição.",
        "Isento de taxa de inscrição.",
        "Reembolso de taxa de matrícula em cursos de idiomas.",
        "Auxílio para pagar a taxa de matrícula da faculdade.",
        "We cover the onboarding fee for your visa.",
        "Benefits: we pay your gym membership and a home office deposit.",
        "We pay a sign-on bonus and relocation deposit.",
        "Contact HR via WhatsApp or email if you have questions.",
        "Our team will contact you on WhatsApp to schedule the interview.",
        "Envie seu currículo pelo WhatsApp.",
        "You must provide bank account details for payroll after the offer.",
        "Informe seus dados bancários para depósito do salário.",
        "We will never ask you to pay a fee.",
        # Duties of an ordinary finance or support job are not asks.
        "Pay vendors on time and track each payment in SAP.",
        "Transfer client funds and record each deposit.",
        "Pagar fornecedores e controlar o valor das notas fiscais.",
        "You must send weekly reports and payment reconciliations to the finance team.",
        "We are a small team so most chats happen on WhatsApp.",
        "Atender clientes apenas pelo WhatsApp e e-mail.",
        "Customer support via WhatsApp only, 24/7, you'll handle chats.",
        "We only use WhatsApp for interview reminders; apply on our site.",
        "Você precisa enviar seus dados bancários para receber o salário.",
        "Send your bank details to receive your first paycheck.",
        "Provide card verification support to customers; share banking information securely.",
        "Senior engineer, Python, remote. Salary USD 300,000.",
        "",
    ],
)
def test_an_ordinary_ad_fires_nothing(text: str) -> None:
    assert caution_signals(text) == []


def test_no_salary_comparison_is_claimed() -> None:
    # There is no comparable-salary basis, so no such signal exists.
    assert caution_signals("Pay: USD 9,000 a week for data entry.") == []


def test_scoring_never_reads_the_scam_signs() -> None:
    for path in MATCH.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "caution" not in node.module, path
