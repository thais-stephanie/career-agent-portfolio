"""Generate the committed JSON Schema for the Candidate Intake Package.

WHY IT IS GENERATED RATHER THAN WRITTEN
----------------------------------------
The schema is published so that an assistant, or a person, can produce a valid
`career-agent-import.json` without reading this repository. That makes it a
CONTRACT, and a contract maintained by hand beside the models it describes
drifts the first time somebody adds a field to one of them.

So `career_agent.intake.models` is the single source, this script renders it,
and `tests/unit/test_intake_schema.py` asserts the committed file is exactly
what this produces. Same discipline as `scripts/make_starter_config.py` and the
starter configuration.

Run it after any change to the models:

    uv run python scripts/make_intake_schema.py
"""

from __future__ import annotations

import json
from pathlib import Path

from career_agent.intake.models import (
    CURRENT_SCHEMA_VERSION,
    FORBIDDEN_KEYS,
    IntakePackage,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DESTINATION = REPO_ROOT / "schemas" / f"career-agent-import.v{CURRENT_SCHEMA_VERSION}.json"


def build() -> dict:
    """The schema, with the two things a generated one cannot say for itself."""
    schema = IntakePackage.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = (
        "https://github.com/career-agent/schemas/"
        f"career-agent-import.v{CURRENT_SCHEMA_VERSION}.json"
    )
    schema["title"] = f"Career Agent Candidate Intake Package v{CURRENT_SCHEMA_VERSION}"
    schema["description"] = (
        "Proposed evidence about one person's career. Every claim in this file is a "
        "PROPOSAL: importing it confirms nothing, and only the candidate can turn a "
        "proposal into a verified claim. A generator must not state that a claim is "
        "verified, must not answer a preference or eligibility question, and must keep "
        "both the original wording of a date and its normalised form."
    )
    # Generated schemas describe what is allowed. This one also has to say what
    # is REFUSED and why, because the refusals are the part a generator gets
    # wrong -- and a validator that only reports "additional property" leaves
    # the author guessing which of their extra fields was the problem.
    schema["x-refused-keys"] = {
        "keys": sorted(FORBIDDEN_KEYS),
        "why": (
            "Confirmation is an act by the candidate, so a package may not assert it. "
            "Preferences, eligibility, work authorisation and pay expectations are "
            "Candidate Profile answers given by the candidate directly: a CV can say "
            "where somebody has WORKED, never where they are permitted or willing to."
        ),
    }
    return schema


def main() -> None:
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(build(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    DESTINATION.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"wrote {DESTINATION.relative_to(REPO_ROOT)} ({len(rendered)} bytes)")


if __name__ == "__main__":
    main()
