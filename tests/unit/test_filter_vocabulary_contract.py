"""The filter panel and `/api/jobs` are two spellings of one vocabulary.

WHAT IT COST
------------
The "Nothing standing in the way" preset asked for two eligibility values:

    patch: { eligibility: ['LIKELY_ELIGIBLE', 'VERIFIED_ELIGIBLE'] }

`LIKELY_ELIGIBLE` is a member of `EligibilityStatus`. It has a label in
`i18n.js` and a colour in `format.js`. The deterministic matcher has never
produced it -- `match/gates.py` says so in as many words -- so `/api/jobs`
accepts three values and not four.

Clicking that preset therefore did not narrow the list. It returned

    HTTP 400: eligibility must be one of VERIFIED_ELIGIBLE,
              VERIFIED_NOT_ELIGIBLE, UNRESOLVED -- got 'LIKELY_ELIGIBLE'

and the Jobs view showed "The list of jobs could not be loaded." Found in the
owner's own Morning Acceptance run, on the real corpus, not by any test.

WHY NOTHING CAUGHT IT
---------------------
`web/mock/api-fixture.json` still speaks the four-value vocabulary: six of its
postings carry `LIKELY_ELIGIBLE` and its facet block offers it as a bucket. The
frontend was consistent with the fixture it was developed against, and the
fixture was the one reader that never validated anything. The browser suite
runs against the demo corpus, where nothing produces the value either, and no
test clicked this particular preset.

A `_VOCABULARIES` entry is a CLOSED vocabulary and unknown values are a 400 on
purpose -- "no results" and "that is not a value this system has" are different
statements. This file is what stops the two ends of that promise drifting.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from career_agent.web.api import _VOCABULARIES

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "src" / "career_agent" / "web" / "static"
FILTERS_JS = STATIC / "js" / "filters.js"
MOCK_FIXTURE = ROOT / "src" / "career_agent" / "web" / "mock" / "api-fixture.json"

#: `patch: { key: [...] }` and `patch: { key: 12 }` from a preset definition.
PATCH = re.compile(r"patch:\s*\{(?P<body>[^}]*)\}", re.DOTALL)
ARRAY_FIELD = re.compile(r"(?P<key>\w+)\s*:\s*\[(?P<values>[^\]]*)\]", re.DOTALL)
STRING = re.compile(r"'([^']*)'|\"([^\"]*)\"")


def preset_patches() -> list[tuple[str, tuple[str, ...]]]:
    """Every closed-vocabulary value a filter preset can put into a request."""
    source = FILTERS_JS.read_text(encoding="utf-8")
    found: list[tuple[str, tuple[str, ...]]] = []
    for patch in PATCH.finditer(source):
        for field in ARRAY_FIELD.finditer(patch.group("body")):
            key = field.group("key")
            if key not in _VOCABULARIES:
                continue
            values = tuple(a or b for a, b in STRING.findall(field.group("values")))
            found.append((key, values))
    return found


def test_the_presets_were_actually_found() -> None:
    """A regex that matched nothing would make every assertion below vacuous."""
    patches = preset_patches()

    assert patches, "no preset patch was parsed out of filters.js"
    assert any(key == "eligibility" for key, _ in patches)
    assert any(key == "status" for key, _ in patches)


@pytest.mark.parametrize("key,values", preset_patches(), ids=lambda v: str(v))
def test_a_preset_never_asks_for_a_value_the_api_refuses(key: str, values) -> None:
    """The defect itself, in one line.

    Not "the preset returns results" -- it may legitimately return none. The
    assertion is that the REQUEST is one the server will answer at all.
    """
    accepted = set(_VOCABULARIES[key])
    unknown = [value for value in values if value not in accepted]

    assert not unknown, (
        f"the {key} filter offers {unknown}, which /api/jobs answers with a 400. "
        f"accepted: {sorted(accepted)}"
    )


def test_the_mock_fixture_speaks_the_same_vocabulary() -> None:
    """The fixture is a reader too, and it is the one that never complains.

    It is what `dev.html` serves and what the frontend is developed against.
    A fixture holding a value the real API refuses does not fail -- it teaches
    the wrong contract, which is how the eligibility preset came to exist.
    """
    payload = json.loads(MOCK_FIXTURE.read_text(encoding="utf-8"))
    accepted = set(_VOCABULARIES["eligibility"])

    on_items = {
        item["eligibility_status"]
        for item in payload.get("items", [])
        if item.get("eligibility_status")
    }
    # The facet block spells it `eligibility`; a posting spells it
    # `eligibility_status`. Both are read, because the drift can start in
    # either and a fixture is believed by whichever one the panel happens to
    # look at.
    facet = payload.get("facets", {}).get("eligibility", [])
    in_facets = {
        bucket["key"] for bucket in facet if isinstance(bucket, dict) and bucket.get("key")
    }

    assert not (on_items - accepted), (
        f"the mock fixture gives postings an eligibility the API refuses: "
        f"{sorted(on_items - accepted)}"
    )
    assert not (in_facets - accepted), (
        f"the mock fixture offers an eligibility facet the API refuses: "
        f"{sorted(in_facets - accepted)}"
    )
