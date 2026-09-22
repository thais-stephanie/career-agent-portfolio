-- M1D: canonical company identity, board discovery provenance, and the
-- mass-disappearance hold.
--
-- Additive only. Every statement is ALTER TABLE ... ADD COLUMN or CREATE INDEX,
-- both of which port to Postgres unchanged (hazard A3). No table is rewritten,
-- no column is dropped, and every existing row stays valid with NULLs.
--
-- Why company identity earns a migration: M1A-M1C identified companies by a
-- hand-written slug, which is fine for eleven engineering seed boards and not
-- fine for a registry of hundreds sourced from the open market. Two entries for
-- one company would double-collect it and quietly corrupt every per-company
-- count; a verified domain is the anchor that prevents it.
--
-- What deliberately did NOT get a column:
--   registry_status  - candidates live in config/company_candidates.yaml. The
--                      database holds the ACTIVE registry, so a status column
--                      with one possible value would be decoration.
--   previous_open_count - derivable by counting open jobs on the board. A
--                      denormalised copy is one more thing that can go stale
--                      and disagree with the rows it summarises.

-- ---------------------------------------------------------------------
-- COMPANY IDENTITY
-- ---------------------------------------------------------------------

-- Normalised: lowercase, no scheme, no www., no path. "Acme.com/careers" and
-- "https://www.acme.com" are the same company.
ALTER TABLE company ADD COLUMN canonical_domain TEXT;

-- How this company entered the registry: candidate-named, ATS discovery,
-- curated sourcing, corpus feedback. Answers "why are we monitoring this?"
ALTER TABLE company ADD COLUMN discovery_source TEXT;

-- Why it was prioritised, as a signal name rather than as candidate data.
-- "target_company_market" belongs here; the candidate's profile does not.
ALTER TABLE company ADD COLUMN priority_reason TEXT;

-- Unique where present, absent where unknown. The M1A-M1C engineering seed
-- companies have no verified domain and must not be made to invent one, so a
-- partial index gives uniqueness without forcing the value.
CREATE UNIQUE INDEX idx_company_domain
    ON company(canonical_domain) WHERE canonical_domain IS NOT NULL;

-- ---------------------------------------------------------------------
-- BOARD DISCOVERY PROVENANCE
-- ---------------------------------------------------------------------

-- board_url, domain_slug, name_slug, careers_page, seed. Evidence for how this
-- identifier was arrived at, which matters because a guessed slug that happens
-- to answer is not the same as one a careers page pointed at.
ALTER TABLE source_board ADD COLUMN discovery_method TEXT;

-- When a live probe last confirmed this board exists and answers.
ALTER TABLE source_board ADD COLUMN verified_at TEXT;

-- ---------------------------------------------------------------------
-- MASS-DISAPPEARANCE HOLD
-- ---------------------------------------------------------------------
-- Set when a SUCCESSFUL pass returned so few postings that closing the rest
-- would more likely mean a truncated response than an emptied board. While
-- held, observed postings are still updated and nothing is closed.

ALTER TABLE source_board ADD COLUMN suspicious_since TEXT;

-- How many postings the suspicious pass actually returned. A later pass that
-- returns no more than this confirms the disappearance; one that returns
-- materially more clears the hold.
ALTER TABLE source_board ADD COLUMN suspicious_observed INTEGER;

CREATE INDEX idx_board_suspicious
    ON source_board(suspicious_since) WHERE suspicious_since IS NOT NULL;
