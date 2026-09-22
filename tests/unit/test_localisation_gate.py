"""Product-authored prose goes through the catalogue, and stays there.

`scripts/localisation_check.py` is the gate; this runs it in the suite, the way
`test_punctuation.py` runs the punctuation one. Both exist for the same reason:
a rule nothing enforces is a rule that decays between sessions, and this
particular one decayed into 99 English literals sitting on a Portuguese screen.

The check is on the CAUSE, not the symptom. "Is there English on this page" is
not answerable -- job titles, company names and evidence quotes are the
employer's words and must stay exactly as written. "Does a string this project
wrote reach the DOM without `t()`" is answerable, deterministically, and it is
what every mixed-language defect the owner reported turned out to be.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from localisation_check import findings  # noqa: E402


def test_no_product_authored_literal_bypasses_the_catalogue() -> None:
    found = findings()
    total = sum(len(hits) for hits in found.values())
    detail = "\n".join(
        f"  {name}:{number}  {value[:88]}"
        for name, hits in sorted(found.items())
        for number, value in hits
    )
    assert not total, (
        f"{total} product-authored literals bypass the catalogue:\n{detail}\n\n"
        "Route each through `t('some.key')` and add the key to BOTH catalogues "
        "in js/i18n.js."
    )


def test_the_two_catalogues_describe_the_same_interface() -> None:
    """A key in one and not the other is a sentence that silently falls back to
    English for a Portuguese reader -- which is the defect, one string at a
    time, rather than all at once."""
    from tests.unit.test_i18n_catalogue import EN, PT_BR  # type: ignore[attr-defined]

    missing = sorted(set(EN) - set(PT_BR))
    assert not missing, f"{len(missing)} keys have no Portuguese: {missing[:12]}"
