"""A filesystem seam that lets an assisting agent stand in for a model.

M2 has to be exercised on real postings before anyone is authorised to spend
money on a benchmark. This is how: export the exact production requests to
disk, let the Cowork environment answer them, import the answers, and run them
through the same validation, verification and assembly the production path uses.
Zero incremental paid calls.

WHY THIS IS NOT A SECOND EXTRACTION ARCHITECTURE
------------------------------------------------
Because there is nothing here to be a second architecture *with*. The export is
`LLMRequest` written as JSON. The import is `LLMResponse` read back, handed to
the ordinary `ReplayClient`. `pipeline/extract.py` cannot tell the difference
and is never asked to: there is no `if runner is COWORK` anywhere in this
repository, and a test asserts it.

The one thing this module deliberately does *not* do is repair. If the answer
is not valid JSON, or does not match the transport schema, it is imported
exactly as written and the pipeline records the failure. An importer that
tidied up its input would be measuring the tidying, and the whole reason to run
this before paying for a benchmark is to find out what the real prompt and the
real schema actually produce.

WHAT COMES OUT IS DEVELOPMENT DATA
----------------------------------
Every call is recorded with `runner = COWORK_ASSISTED` and the model identity
`cowork-development`, which is not the name of any production model and never
will be. Those fingerprints may drive downstream development; they may not be
used to claim accuracy for a model that never ran.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from career_agent.clock import now_utc
from career_agent.llm.client import (
    LLMResponse,
    ModelConfig,
    ReplayClient,
    Runner,
    StructuredOutput,
)
from career_agent.llm.requests import (
    BuiltRequest,
    build_description_request,
    build_provider_request,
)
from career_agent.pipeline.extract import ExtractionOutcome, JobSource, extract_job

#: Bumped when the on-disk batch layout changes. The importer refuses a batch
#: it does not recognise rather than guessing at an older shape.
BATCH_FORMAT_VERSION = 1

#: The vendor and model identity every Cowork-assisted call is recorded under.
#:
#: Not a production model name, and never to be replaced by one. It is part of
#: the cache key, so a Cowork answer can never be served to a benchmark arm and
#: a benchmark answer can never be served here -- the separation is structural
#: rather than a convention someone has to remember.
COWORK_VENDOR = "cowork"
COWORK_MODEL_IDENTIFIER = "cowork-development"

#: Exactly the keys an exported request file carries. Nothing else may appear.
#:
#: This is the privacy boundary, written as data so a test can assert it. The
#: export contains a job posting, an ATS record and our own prompts. It carries
#: no CV, no career intent, no residence, no compensation requirement and no
#: candidate identity -- and `JobSource` has no field that could supply one.
EXPORTED_KEYS = frozenset(
    {
        "batch_id",
        "request_id",
        "job_id",
        "family",
        "provider",
        "prompt_version",
        "transport_schema_version",
        "cache_key",
        "content_hash",
        "payload_hash",
        "truncated",
        "schema_name",
        "schema",
        "system",
        "user",
    }
)


class CoworkBatchError(ValueError):
    """The batch on disk is not something this importer can honestly run."""


def cowork_config() -> ModelConfig:
    """The model identity for a Cowork-assisted run.

    `structured_output=False` because no vendor is enforcing the schema here.
    Our own validators do the work -- which is what they were always going to
    do anyway, since provider-side schema enforcement was never permitted to
    replace them.
    """
    return ModelConfig(
        vendor=COWORK_VENDOR,
        identifier=COWORK_MODEL_IDENTIFIER,
        structured_output=StructuredOutput.PLAIN_JSON,
    )


# =========================================================================
# EXPORT
# =========================================================================


@dataclass(frozen=True)
class BatchSummary:
    """What an export produced, in the terms the cost report needs."""

    batch_id: str
    directory: Path
    jobs: int
    description_requests: int
    provider_requests: int

    @property
    def total_requests(self) -> int:
        return self.description_requests + self.provider_requests

    @property
    def jobs_needing_no_provider_call(self) -> int:
        """Jobs whose ATS record a lookup table resolved on its own.

        Reported rather than inferred, because "the provider family made no
        call" is a measurement the topology decision rests on and should be
        visible on every batch rather than recomputed from a corpus query.
        """
        return self.jobs - self.provider_requests


def _as_export(built: BuiltRequest, source: JobSource, batch_id: str) -> dict[str, Any]:
    """One request, as the file on disk.

    `system` and `user` are the production prompt and the production input,
    copied verbatim. Not a paraphrase, not a summary, not a version rewritten
    to read more naturally: the entire value of this exercise is that what the
    assisting agent answers is what a paid model would have been asked.
    """
    is_description = built.request.family.value == "description"
    return {
        "batch_id": batch_id,
        "request_id": built.cache_key.key,
        "job_id": source.job_id,
        "family": built.request.family.value,
        "provider": source.provider,
        "prompt_version": built.prompt_version,
        "transport_schema_version": built.transport_version,
        "cache_key": built.cache_key.key,
        "content_hash": source.content_hash if is_description else None,
        "payload_hash": source.payload_hash if not is_description else None,
        "truncated": built.truncated,
        "schema_name": built.request.schema_name,
        "schema": built.request.schema,
        "system": built.request.system,
        "user": built.request.user,
    }


def export_batch(
    sources: Sequence[JobSource],
    directory: Path,
    batch_id: str,
    config: ModelConfig | None = None,
) -> BatchSummary:
    """Write one request file per call the production pipeline would make.

    Including the *absence* of calls: a job whose ATS record needs no
    interpretation exports one file, not two, exactly as it would cost one call
    and not two. A harness that exported a provider request for every job would
    be measuring a pipeline we do not run.
    """
    config = config or cowork_config()
    root = directory / batch_id
    requests_dir = root / "requests"
    results_dir = root / "results"
    requests_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)

    entries: list[dict[str, Any]] = []
    description_count = 0
    provider_count = 0

    for index, source in enumerate(sources, start=1):
        built_requests = [
            build_description_request(source.content_hash, source.description_text, config)
        ]
        provider_request = build_provider_request(
            source.provider, source.observations, source.field_map_digest, config
        )
        if provider_request is not None:
            built_requests.append(provider_request)

        for built in built_requests:
            family = built.request.family.value
            exported = _as_export(built, source, batch_id)
            name = f"{index:04d}-{family}-{source.job_id}.json"
            (requests_dir / name).write_text(
                json.dumps(exported, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            entries.append(
                {
                    "file": f"requests/{name}",
                    "cache_key": built.cache_key.key,
                    "job_id": source.job_id,
                    "family": family,
                }
            )
            if family == "description":
                description_count += 1
            else:
                provider_count += 1

    manifest = {
        "batch_format_version": BATCH_FORMAT_VERSION,
        "batch_id": batch_id,
        "created_at": now_utc(),
        "runner": Runner.COWORK_ASSISTED.value,
        "vendor": config.vendor,
        "model": config.identifier,
        "jobs": [s.job_id for s in sources],
        "requests": entries,
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (root / "README.md").write_text(_readme(batch_id, len(entries)), encoding="utf-8")

    return BatchSummary(
        batch_id=batch_id,
        directory=root,
        jobs=len(sources),
        description_requests=description_count,
        provider_requests=provider_count,
    )


def _readme(batch_id: str, request_count: int) -> str:
    return f"""# Cowork extraction batch `{batch_id}`

{request_count} requests. Each file in `requests/` is one production LLM call,
exported verbatim.

## What to do

For each `requests/<name>.json`:

1. Read `system` as the system prompt and `user` as the user message. Do not
   summarise them, do not reorder them, and do not add instructions of your own.
2. Produce a JSON object matching `schema`.
3. Write `results/<name>.json` containing exactly:

```json
{{ "cache_key": "<the cache_key from the request file>",
   "raw_output": "<your JSON answer, as a STRING>" }}
```

## Why `raw_output` is a string and not an object

Because the importer must run the real parser over the real bytes. Handing the
pipeline an already-parsed object would test the exporter instead of the
extraction, and the failures worth finding before we pay for a benchmark --
trailing prose, a truncated response, a not-quite-schema-shaped object -- are
exactly the ones that disappear when something helpfully re-serialises them.

Nothing here repairs output. An answer that does not parse is stored as it was
written and recorded as a failed attempt, which is the honest result.

## What this is not

These results are recorded as `runner = COWORK_ASSISTED`, model
`cowork-development`. They are development data. They do not establish accuracy
for any production model, and they are not a benchmark.
"""


# =========================================================================
# IMPORT
# =========================================================================


@dataclass(frozen=True)
class ImportedBatch:
    """Recorded answers, ready to drive the ordinary extraction path."""

    batch_id: str
    manifest: dict[str, Any]
    client: ReplayClient
    #: Requests the batch asked for and nobody answered. Not an error: a
    #: partially answered batch is a normal state while work is in progress,
    #: and the jobs whose families are all present still run.
    unanswered: tuple[str, ...]

    @property
    def job_ids(self) -> list[str]:
        return [str(j) for j in self.manifest.get("jobs", [])]

    def answered_jobs(self) -> list[str]:
        """Jobs whose every exported request has an answer.

        A job answered on one family and not the other is skipped rather than
        half-extracted: assembling a document from one family and an empty
        stand-in for the other would silently produce a fingerprint whose
        provider channel says nothing, which is a claim, not a gap.
        """
        missing = set(self.unanswered)
        by_job: dict[str, list[str]] = {}
        for entry in self.manifest.get("requests", []):
            by_job.setdefault(str(entry["job_id"]), []).append(str(entry["cache_key"]))
        return [job for job, keys in by_job.items() if not (set(keys) & missing)]


def load_batch(directory: Path) -> ImportedBatch:
    """Read a batch and its answers into a replay client.

    Refuses a result whose `cache_key` is not in the manifest. A stray answer
    is not a harmless extra file -- it means the answer was produced against a
    request this batch did not export, and running it would attribute an
    extraction to a prompt that never asked for it.
    """
    manifest_path = directory / "manifest.json"
    if not manifest_path.exists():
        raise CoworkBatchError(f"{directory} holds no manifest.json; it is not a batch directory")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    version = manifest.get("batch_format_version")
    if version != BATCH_FORMAT_VERSION:
        raise CoworkBatchError(
            f"batch format {version!r} is not {BATCH_FORMAT_VERSION}; re-export the batch "
            "rather than importing a layout this code does not understand"
        )

    expected = {str(entry["cache_key"]) for entry in manifest.get("requests", [])}
    responses: dict[str, LLMResponse] = {}

    for path in sorted((directory / "results").glob("*.json")):
        answer = json.loads(path.read_text(encoding="utf-8"))
        key = str(answer.get("cache_key", ""))
        if key not in expected:
            raise CoworkBatchError(
                f"{path.name} answers cache_key {key!r}, which this batch never exported"
            )
        raw = answer.get("raw_output")
        if not isinstance(raw, str):
            raise CoworkBatchError(
                f"{path.name} carries raw_output as {type(raw).__name__}, not a string. "
                "The importer runs the real parser over the real bytes; re-serialising an "
                "object here would hide exactly the failures this exercise exists to find."
            )
        responses[key] = LLMResponse(raw_text=raw, model=str(manifest.get("model", "")))

    return ImportedBatch(
        batch_id=str(manifest.get("batch_id", "")),
        manifest=manifest,
        client=ReplayClient(
            responses=responses,
            vendor=str(manifest.get("vendor", COWORK_VENDOR)),
            runner=Runner.COWORK_ASSISTED,
        ),
        unanswered=tuple(sorted(expected - set(responses))),
    )


def run_batch(
    batch: ImportedBatch,
    sources: Sequence[JobSource],
    config: ModelConfig | None = None,
) -> list[ExtractionOutcome]:
    """Run the answered jobs through the ordinary extraction path.

    Nothing in this function knows it is running Cowork output. It calls
    `extract_job` with a client and a config, which is what a production run
    does with a vendor adapter and a production model.
    """
    config = config or cowork_config()
    return [extract_job(batch.client, config, source) for source in sources]
