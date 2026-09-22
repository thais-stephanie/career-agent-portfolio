-- M2 -- a schema-valid answer is not an accepted answer
--
-- WHAT THIS FIXES
-- ---------------
-- `validated_ok` means the transport document parsed and matched its schema.
-- The semantic cache read it as "this answer was accepted" and served it, and
-- for a long time the two were the same thing by accident.
--
-- They came apart on the first FUNCTION_CALL canary. The model returned a
-- perfectly shaped document: 33 observations, 33 unique dimensions, none
-- missing, none duplicated, every field the right type. All eighteen evidence
-- entries carried a real sentence from the posting -- in `source_value`, with
-- `quote` null -- and not one of the twelve EXPLICIT observations cited any of
-- them. Evidence verification: 0 of 18. `assemble` refused it. And
-- `validated_answer` returned it as a cache hit, because the only column it
-- could ask was `validated_ok`, which was correctly 1.
--
-- A frozen M2 requirement says failed outputs used as cache = exactly 0. There
-- was no column that could make that true.
--
-- WHY A NEW COLUMN AND NOT A CORRECTED `validated_ok`
-- ---------------------------------------------------
-- Because `validated_ok` is not wrong. The document DID validate. Overwriting
-- it would destroy a true fact to express a different one, and would rewrite
-- history in the one table whose purpose is recording what actually happened.
--
-- So admission gets its own column. "It parsed", "it matched the schema" and
-- "it would be accepted again" are three facts, and a row that collapses them
-- cannot say why an answer was refused. `raw_output`, `response_envelope`,
-- `parsed_ok`, `validated_ok` and `error` are all preserved exactly.
--
-- WHY THE DEFAULT IS UNVERIFIED AND NOT ACCEPTED
-- ----------------------------------------------
-- Every existing row arrives here unjudged, and an unjudged answer is exactly
-- the answer that must not be served. Defaulting the other way is how the
-- defect got in. `career-agent verify-cache-dispositions` reverifies them
-- offline, deterministically, against archived source material and with no
-- inference; rows whose material is gone stay UNVERIFIED and stay ineligible.
--
-- SQLite cannot add a CHECK to an existing column, so this rebuilds the table
-- as 0008 and 0009 did. No PRAGMA: the repository forbids one inside a
-- migration, the whole file runs in a transaction, and `llm_call.fingerprint_id`
-- is ON DELETE SET NULL -- nothing here can orphan a row. Every column, default,
-- constraint, index and row is preserved; one column is added.

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
    cache_disposition TEXT NOT NULL DEFAULT 'UNVERIFIED'
        CHECK (cache_disposition IN (
            'ACCEPTED', 'REJECTED_EVIDENCE', 'REJECTED_UNSUPPORTED',
            'REJECTED_OUTPUT', 'UNVERIFIED'
        )),
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
