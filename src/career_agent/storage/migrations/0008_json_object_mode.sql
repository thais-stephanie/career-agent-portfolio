-- M2: the third enforcement mode, in the vocabulary the database checks
--
-- WHAT BROKE, AND HOW
-- -------------------
-- `StructuredOutput.JSON_OBJECT` was added to the Python enum, sent on the
-- wire, carried into `static_digest`, tested across four adapters and the CLI
--, and refused by a CHECK constraint written when there were two modes.
--
-- The failure landed in the worst possible place. The request was SENT, the
-- vendor ANSWERED, and the insert that would have made the answer durable was
-- rejected:
--
--     IntegrityError: CHECK constraint failed:
--         structured_output IN ('STRICT_SCHEMA', 'PLAIN_JSON', 'UNRECORDED')
--
-- So a real HTTP call was spent and its evidence discarded: the fifth
-- variation on the one failure this project keeps paying for, and the first to
-- come through the schema rather than through control flow. The save-first
-- lifecycle did its job perfectly: it wrote at the moment of observation. The
-- write was refused by a vocabulary nobody had updated.
--
-- AN ENUM AND A CHECK CONSTRAINT ARE TWO SPELLINGS OF ONE VOCABULARY
-- ------------------------------------------------------------------
-- Nothing tied them together. `runner`, `structured_output` and `transport`
-- each duplicate a Python enum in SQL, and each is a place where the two can
-- drift apart silently until a live call pays for it. A test now inserts every
-- member of every one of those enums, so a future member cannot reach a vendor
-- before it can reach the table.
--
-- SQLite cannot alter a CHECK constraint, so this rebuilds the table. No
-- PRAGMA: the repository forbids one inside a migration, the whole file runs
-- in a transaction, and `llm_call.fingerprint_id` is ON DELETE SET NULL --
-- nothing here can orphan a row. Every
-- column, default, constraint, index and row is preserved; only the permitted
-- set of `structured_output` grows by one member. UNRECORDED stays for the
-- historical rows that predate the column.

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
        CHECK (structured_output IN ('STRICT_SCHEMA', 'JSON_OBJECT', 'PLAIN_JSON', 'UNRECORDED')),
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
