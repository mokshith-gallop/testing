-- Deliberately malformed: missing the closing paren, so HS2 rejects it mid-apply.
-- Used by source_setup_failure_negative.mvs.yaml to prove a source_setup failure SURFACES
-- (the run does not silently pass) and the build dataset is still torn down.
CREATE TABLE ss_bad (id BIGINT
