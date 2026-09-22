-- 0023_intake_package.sql
--
-- The Candidate Intake Package: proposed evidence that arrived as a FILE.
--
-- WHY THIS IS NOT `cv_import`
-- ---------------------------
-- Migration 0019 stages a CV that THIS PROGRAM read. `cv/extract.py` turned a
-- document into characters and `cv/propose.py` said what those characters
-- appear to claim, and both are deterministic code in this repository whose
-- behaviour is covered by tests.
--
-- An intake package is different in exactly one way that changes everything
-- downstream: **somebody else's model may have written it.** The intended flow
-- is that a candidate either uploads their documents here, or copies an
-- official prompt into an AI they already use, attaches their CV and their
-- LinkedIn export, and brings back `career-agent-import.json`.
--
-- That file is a STRANGER'S READING of the candidate's own documents. It may
-- normalise a date the document did not state. It may merge two employers. It
-- may invent a metric that reads plausibly. None of that is detectable by
-- looking at the JSON, so none of it may be trusted by being well-formed.
--
-- The answer is the same one the rest of this product gives: the machine
-- proposes and the person confirms. Every row here starts UNREVIEWED, and
-- nothing in this schema can produce a `verified_claim`. Only a review can,
-- through the three existing places that set `verified=True`.
--
-- WHY THE PACKAGE IS HASHED AND THE DOCUMENTS ARE NOT
-- ---------------------------------------------------
-- `package_sha256` hashes the CANONICAL JSON of the package, so re-importing
-- the identical file is recognised rather than re-asked -- the same property
-- `cv_import.text_sha256` gives a re-read CV. The candidate's actual CV and
-- LinkedIn PDF are never hashed here and never stored here: this table holds
-- what an importer was handed, not the documents behind it.
--
-- WHY `declared_sources` IS A COLUMN RATHER THAN AN INFERENCE
-- -----------------------------------------------------------
-- A package must say which documents it was built from, and a claim must say
-- which of them it came from. Two documents about one career disagree -- a CV
-- says a role ended in March, a LinkedIn export says April -- and the only
-- honest handling of that is to keep both and ask. A package that declared no
-- sources would make every conflict unattributable, so the parser refuses one.
--
-- **Neither document is authoritative over the other.** There is no column
-- here for precedence and no code that ranks them, because deciding that a CV
-- outranks a LinkedIn profile would be this program resolving a conflict about
-- somebody's own career on their behalf.
--
-- WHY REVIEW STATE IS A COLUMN AND NOT A DELETION
-- -----------------------------------------------
-- A REJECTED row stays, for migration 0019's reason exactly: "answered no" is
-- the only thing that stops a line reappearing tomorrow. It is a fact about
-- one read of one package, it never becomes a claim, and discarding the
-- package discards it.
--
-- ONE HONEST NOTE ABOUT THIS FILE'S HISTORY
-- ------------------------------------------
-- The `DEFAULT` clauses on the two `_json` columns were added a few hours
-- after this migration first ran, when `tests/unit/test_sql_portability.py`
-- refused it: a NULL in a TEXT column breaks a later JSONB conversion, so the
-- rule is NOT NULL *and* a default.
--
-- A database migrated in that window therefore has these columns without the
-- default clause. **No rebuild is warranted and none is provided.** Nothing
-- reads the default -- every insert in `intake/store.py` supplies both columns
-- -- and rebuilding two tables to change a clause that never fires would trade
-- a cosmetic divergence for the one migration shape this project has always
-- refused: a destructive one. Recorded here rather than quietly corrected.

CREATE TABLE IF NOT EXISTS intake_package (
    id                TEXT PRIMARY KEY,
    -- The contract version the FILE declared. Refused if unsupported, never
    -- coerced: a package written against a schema this build does not know is
    -- a package whose fields may mean something else.
    schema_version    TEXT NOT NULL,
    -- Canonical JSON of the package as parsed. Re-importing the same bytes is
    -- recognised rather than re-proposed.
    package_sha256    TEXT NOT NULL,
    -- What produced it, as the file declared: this program's own extractor, or
    -- the name a person typed for the assistant they used. Never inferred.
    generator         TEXT,
    -- The documents the package says it was built from, as canonical JSON.
    -- Required. See the header.
    declared_sources  TEXT NOT NULL DEFAULT '[]',
    -- How many claims arrived, so a partly-reviewed package can report
    -- progress without counting rows every time.
    claim_count       INTEGER NOT NULL DEFAULT 0,
    -- OPEN while a review is in progress, DISCARDED when the candidate throws
    -- the package away. Never DELETEd: a discarded package's rejections are
    -- what stop its lines being offered again by a re-import.
    status            TEXT NOT NULL DEFAULT 'OPEN',
    filename          TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_intake_package_hash ON intake_package (package_sha256);

CREATE TABLE IF NOT EXISTS intake_claim (
    id                TEXT PRIMARY KEY,
    package_id        TEXT NOT NULL REFERENCES intake_package (id) ON DELETE CASCADE,
    -- Stable across re-imports of the same content, so a claim already
    -- answered is recognised instead of offered again. Derived from the claim's
    -- own content by the parser, never supplied by the package: an id chosen by
    -- whoever generated the file could collide with an unrelated claim, or be
    -- reused to overwrite one.
    claim_key         TEXT NOT NULL,
    claim_type        TEXT NOT NULL,
    -- The claim as the package stated it, canonical JSON. Kept verbatim so a
    -- reviewer can always see what arrived rather than what we made of it.
    payload_json      TEXT NOT NULL DEFAULT '{}',
    -- Which declared source this claim came from. Required by the parser.
    source_ref        TEXT NOT NULL,
    -- The candidate's own words, when they corrected the claim rather than
    -- accepting it. NULL until then. The original stays in payload_json.
    corrected_text    TEXT,
    -- UNREVIEWED | CONFIRMED | CORRECTED_BY_USER | CONFLICT | UNRESOLVED
    -- | REJECTED. Every imported row starts UNREVIEWED, and a test asserts it.
    review_state      TEXT NOT NULL DEFAULT 'UNREVIEWED',
    -- Claims the parser found to disagree share a group. Assigned by
    -- detection, never resolved by it.
    conflict_group    TEXT,
    -- The `verified_claim.claim_key` this became, once a review confirmed it.
    -- NULL for everything else, which is most rows for most of their life.
    resolved_claim_key TEXT,
    reviewed_at       TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    UNIQUE (package_id, claim_key)
);

CREATE INDEX IF NOT EXISTS idx_intake_claim_package ON intake_claim (package_id, review_state);
CREATE INDEX IF NOT EXISTS idx_intake_claim_conflict ON intake_claim (package_id, conflict_group);
