"""Generate `schemas/resume-document.v1.json` from `career_agent.resume_doc.models`.

The Python models are the source of truth; the schema is the published
contract and `tests/unit/test_resume_doc.py` asserts the committed copy is
exactly what this writes. Run it after any change to the models:

    uv run python scripts/make_resume_schema.py
"""

from __future__ import annotations

import json
from pathlib import Path

from career_agent.resume_doc.models import SCHEMA_VERSION, ResumeDocument

REPO_ROOT = Path(__file__).resolve().parents[1]
DESTINATION = REPO_ROOT / "schemas" / f"resume-document.v{SCHEMA_VERSION.split('.')[0]}.json"


def build() -> dict:
    schema = ResumeDocument.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"https://github.com/career-agent/schemas/{DESTINATION.name}"
    schema["title"] = f"Career Agent ResumeDocument {SCHEMA_VERSION}"
    return schema


def render() -> str:
    return json.dumps(build(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    DESTINATION.write_text(render(), encoding="utf-8", newline="\n")
    print(f"wrote {DESTINATION.relative_to(REPO_ROOT)}")
