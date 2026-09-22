-- M2: how far an attempt got before it stopped
--
-- Four rounds of evidence loss all had one shape: an attempt was observed, held
-- in memory, and thrown away by whatever ended the run before the end-of-posting
-- write. The lifecycle fix persists each attempt the moment it is observed. This
-- column is what makes those rows answerable afterwards.
--
-- The three states a report actually needs to tell apart:
--
--   NOT_SENT           our own process failed before a request existed. No
--                      quota consumed, no vendor involved, and always our
--                      defect: it will fail the same way on the next posting.
--   SENT_NO_RESPONSE   a request reached the transport and no usable response
--                      came back. Quota IS consumed. The vendor may or may not
--                      have run the model, and `error` says which where it can.
--   RESPONSE_RECEIVED  a response arrived. `response_envelope` holds its shape,
--                      whatever happened to it afterwards.
--
-- These were previously derivable only by reading error prose -- "was never
-- sent" versus "could not be reached" -- which is exactly the fragility that
-- made a payment error look like a rate limit. A fact this load-bearing gets a
-- column.
--
-- It also settles an accounting question that cost a whole report: the GLM
-- screen recorded 18 attempts and consumed 0 of a 50-request daily allowance.
-- Free-tier usage counts SENT rows, not attempt rows.
--
-- UNRECORDED for historical rows, as with `runner` and `structured_output`.
-- Those attempts predate the distinction and inventing one would manufacture
-- provenance -- though for the record, the 18 OpenRouter rows were NOT_SENT and
-- the 18 Cerebras 402 rows were SENT_NO_RESPONSE.

-- CORRECTED 2026-09-03, AFTER THE OPENROUTER CANARY
-- -------------------------------------------------
-- The reading of SENT_NO_RESPONSE above says "no usable RESPONSE", and it was
-- applied as "no usable ANSWER". Those are different layers, and collapsing
-- them recorded a 429 from provider Decart -- status line, provider error code,
-- Retry-After: 5 -- as an attempt nobody had heard back from.
--
-- The state means, and has always been meant to mean, that no HTTP RESPONSE
-- existed: a reset, a timeout, a socket that closed with nothing on it. ANY
-- HTTP response, 2xx or 4xx or 5xx, is RESPONSE_RECEIVED.
--
-- So the parenthetical below is wrong on its second half and is left standing
-- rather than quietly edited: the 18 Cerebras 402 rows were RESPONSE_RECEIVED.
-- Cerebras answered every one of them. `scripts/repair_transport.py` corrects
-- stored rows whose own `error` proves a status, and nothing else.

ALTER TABLE llm_call ADD COLUMN transport TEXT NOT NULL DEFAULT 'UNRECORDED'
    CHECK (transport IN ('NOT_SENT', 'SENT_NO_RESPONSE', 'RESPONSE_RECEIVED', 'UNRECORDED'));

CREATE INDEX idx_llm_call_transport ON llm_call(transport);
