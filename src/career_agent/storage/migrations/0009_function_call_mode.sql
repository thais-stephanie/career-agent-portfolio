-- M2 -- the fourth transport, in the vocabulary the database checks
--
-- WHY THIS EXISTS AT ALL
-- ----------------------
-- Migration 0008 was written because `JSON_OBJECT` reached a vendor before it
-- reached this CHECK constraint: the request was sent, the model answered, and
-- the insert that would have made the answer durable was refused. A real call
-- was spent and its evidence discarded.
--
-- 0008 also added a test that inserts every member of every enum the schema
-- duplicates. That test is what produced this file -- it failed the moment
-- `FUNCTION_CALL` was added to the Python enum, hours before any request could
-- be built, which is the entire point of writing it. The vocabulary is being
-- widened BEFORE the transport can reach a vendor, not after a spent call.
--
-- WHY FUNCTION_CALL IS A MODE AND NOT A FLAG
-- ------------------------------------------
-- The other three shape the model's final TEXT. This one moves the answer out
-- of the text: the family is declared as a callable tool and the arguments come
-- back in their own response part. That changes what a malformed answer means
-- -- prose is no longer a failed document, it is a failure to use the channel
-- -- so it belongs in the arm's identity, and a row must be able to say which
-- transport produced it.
--
-- SQLite cannot alter a CHECK constraint, so this rebuilds the table, exactly
-- as 0008 did. No PRAGMA: the repository forbids one inside a migration, the
-- whole file runs in a transaction, and `llm_call.fingerprint_id` is
-- ON DELETE SET NULL -- nothing here can orphan a row. Every column, default,
-- constraint, index and row is preserved; only the permitted set of
-- `structured_output` grows by one member. UNRECORDED stays for the historical
-- rows that predate the column.

CREATE TABLE llm_call_new (
    id             TEXT PRIMARY KEY,
    fingerprint_id TEXT REFERENCES fingerprint(id) ON DELETE SET NULL,
    job_id         TEXT REFERENCES job(id),
    cache_key      TEXT NOT NULL,
    execution_id   TEXT,
    family         TEXT NOT NULL,
    purpose        TEXT NOT NULL,
    provider       TEXT NOT NULL,
    model          TEXT NOT NULL,
    reasoning_config TEXT,
    prompt_version TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    structured_output TEXT NOT NULL DEFAULT 'UNRECORDED'
        CHECK (structured_output IN (
            'STRICT_SCHEMA', 'JSON_OBJECT', 'PLAIN_JSON', 'FUNCTION_CALL', 'UNRECORDED'
        )),
    input_hash     TEXT NOT NULL,
    raw_output     TEXT NOT NULL,
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
    transport      TEXT NOT NULL DEFAULT 'UNRECORDED'
        CHECK (transport IN ('NOT_SENT', 'SENT_NO_RESPONSE', 'RESPONSE_RECEIVED', 'UNRECORDED')),
    created_at     TEXT NOT NULL,
    UNIQUE (cache_key, execution_id, job_id, attempt)
);

INSERT INTO llm_call_new (
    id, fingerprint_id, job_id, cache_key, execution_id, family, purpose,
    provider, model, reasoning_config, prompt_version, schema_version,
    structured_output, input_hash, raw_output, response_envelope, parsed_ok,
    validated_ok, error, attempt, input_tokens, output_tokens, cost_usd,
    runner, transport, created_at
)
SELECT
    id, fingerprint_id, job_id, cache_key, execution_id, family, purpose,
    provider, model, reasoning_config, prompt_version, schema_version,
    structured_output, input_hash, raw_output, response_envelope, parsed_ok,
    validated_ok, error, attempt, input_tokens, output_tokens, cost_usd,
    runner, transport, created_at
FROM llm_call;

DROP TABLE llm_call;

ALTER TABLE llm_call_new RENAME TO llm_call;

CREATE INDEX idx_llm_call_cache     ON llm_call(cache_key);
CREATE INDEX idx_llm_call_job       ON llm_call(job_id);
CREATE INDEX idx_llm_call_model     ON llm_call(model, family);
CREATE INDEX idx_llm_call_runner    ON llm_call(runner);
CREATE INDEX idx_llm_call_exec      ON llm_call(execution_id);
CREATE INDEX idx_llm_call_transport ON llm_call(transport);
