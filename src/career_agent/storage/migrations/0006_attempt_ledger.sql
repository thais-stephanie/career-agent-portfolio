-- M2: an attempt is a physical event, not a semantic identity
--
-- THE DEFECT THIS REPAIRS
-- ----------------------
-- `llm_call` carried `UNIQUE (cache_key, attempt)` and its writer used
-- `ON CONFLICT DO NOTHING`. Together those say: a semantic request may be
-- attempted N times, ever, across the life of the database.
--
-- That is false, and it cost a whole benchmark run. The Cerebras screen was
-- refused with HTTP 402 on 9 postings x 2 attempts. After billing was
-- activated, the SAME screen ran again: 18 real HTTP requests, 137,878 input
-- and 62,794 output tokens, $0.0954 of promotional credit. Every one of those
-- 18 rows collided with a 402 row on (cache_key, attempt) and was silently
-- discarded. What the model actually said was thrown away, and the run's cost
-- vanished from the ledger with it.
--
-- `records.py` calls `raw_output` "whatever came back, byte for byte ... the
-- most useful row in the table when a prompt regresses". It cannot be that and
-- also be conditional on no earlier attempt having failed under the same key.
--
-- THE SEPARATION
-- --------------
--   cache_key       WHICH QUESTION was asked. Semantic. Unchanged, still
--                   indexed, still what a cache lookup reads.
--   execution_id    WHICH RUN asked it. One value per `run_plan` invocation.
--   job_id          WHICH POSTING it was asked for.
--   attempt         the retry ordinal WITHIN that execution.
--
-- All four together are one physical event: within one run, for one posting,
-- for one question, at one retry ordinal, exactly one thing happened. Two
-- executions may legitimately hold the same cache_key at the same ordinal --
-- that is the case that was being deleted -- and so may two postings inside one
-- execution, because two jobs can share an observation block and be served the
-- same answer. Neither is a duplicate event.
--
-- Historical rows carry a NULL execution_id, and SQLite treats NULLs as
-- distinct in a UNIQUE index, so nothing already stored can collide.
--
-- No run material enters `cache_key`. A cache lookup must still find an answer
-- given only the question, and a key that varied per execution would never hit.
--
-- WHY A REBUILD
-- -------------
-- SQLite cannot drop a UNIQUE constraint in place. The table is small (a few
-- hundred rows) and the copy is exact: every historical row keeps its id, its
-- bytes and its provenance. `execution_id` is NULL for them, which is honest --
-- those rows predate the concept and inventing an execution for them would
-- manufacture provenance.
--
-- `response_envelope` arrives in the same rebuild rather than as a seventh
-- migration: it is the other half of the same lesson. Storing only the
-- adapter-selected `raw_output` was not enough to diagnose the Cerebras run,
-- which returned empty content while billing for 62,794 output tokens. The
-- envelope is the vendor's own response structure, captured BEFORE content
-- extraction and parsing, with credentials never present in it.

CREATE TABLE llm_call_new (
    id             TEXT PRIMARY KEY,
    fingerprint_id TEXT REFERENCES fingerprint(id) ON DELETE SET NULL,
    job_id         TEXT REFERENCES job(id),
    cache_key      TEXT NOT NULL,
    -- NULL only for rows written before executions were identified.
    execution_id   TEXT,
    family         TEXT NOT NULL,
    purpose        TEXT NOT NULL,
    provider       TEXT NOT NULL,
    model          TEXT NOT NULL,
    reasoning_config TEXT,
    prompt_version TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    structured_output TEXT NOT NULL DEFAULT 'UNRECORDED'
        CHECK (structured_output IN ('STRICT_SCHEMA', 'PLAIN_JSON', 'UNRECORDED')),
    input_hash     TEXT NOT NULL,
    raw_output     TEXT NOT NULL,
    -- The vendor's response as it arrived, minus nothing we were given and
    -- plus nothing we invented. Credential-free by construction: it is built
    -- from the response body, never from the request or its headers.
    response_envelope TEXT,
    parsed_ok      INTEGER NOT NULL CHECK (parsed_ok IN (0, 1)),
    validated_ok   INTEGER NOT NULL DEFAULT 0 CHECK (validated_ok IN (0, 1)),
    error          TEXT,
    attempt        INTEGER NOT NULL DEFAULT 1,
    input_tokens   INTEGER,
    output_tokens  INTEGER,
    cost_usd       REAL,
    runner         TEXT NOT NULL DEFAULT 'UNRECORDED'
        CHECK (runner IN ('PRODUCTION_API', 'REPLAY', 'COWORK_ASSISTED', 'FAKE', 'UNRECORDED')),
    created_at     TEXT NOT NULL,
    UNIQUE (cache_key, execution_id, job_id, attempt)
);

INSERT INTO llm_call_new (
    id, fingerprint_id, job_id, cache_key, execution_id, family, purpose, provider, model,
    reasoning_config, prompt_version, schema_version, structured_output, input_hash,
    raw_output, response_envelope, parsed_ok, validated_ok, error, attempt,
    input_tokens, output_tokens, cost_usd, runner, created_at
)
SELECT
    id, fingerprint_id, job_id, cache_key, NULL, family, purpose, provider, model,
    reasoning_config, prompt_version, schema_version, structured_output, input_hash,
    raw_output, NULL, parsed_ok, validated_ok, error, attempt,
    input_tokens, output_tokens, cost_usd, runner, created_at
FROM llm_call;

DROP TABLE llm_call;
ALTER TABLE llm_call_new RENAME TO llm_call;

CREATE INDEX idx_llm_call_cache  ON llm_call(cache_key);
CREATE INDEX idx_llm_call_job    ON llm_call(job_id);
CREATE INDEX idx_llm_call_model  ON llm_call(model, family);
CREATE INDEX idx_llm_call_runner ON llm_call(runner);
CREATE INDEX idx_llm_call_exec   ON llm_call(execution_id);
