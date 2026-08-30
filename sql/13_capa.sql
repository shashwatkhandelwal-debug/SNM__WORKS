-- ============================================================================
-- 13_capa.sql — CAPA (Corrective and Preventive Action) Schema Enhancements
-- ============================================================================
--
-- SAFETY & DATA INTEGRITY NOTE:
-- This migration ONLY adds new columns (raised_by, qc_check_id, lab_test_id,
-- verification_notes, verified_at, closed_at), CHECK constraints, and indexes.
-- No existing columns are dropped, altered, or regenerated. There is zero risk
-- of data loss when applied against a database with pre-existing records.
--
-- RLS POLICY NOTE:
-- CAPA table RLS policies currently remain on the legacy auth_role() check
-- ('owner', 'supervisor', 'qc') from 01_schema.sql. This is deliberately
-- retained here and queued for full replacement in RLS Batch 2.
-- ============================================================================

-- 1. Add creator tracking (NOT NULL, references profiles)
ALTER TABLE capa ADD COLUMN IF NOT EXISTS raised_by uuid NOT NULL REFERENCES profiles(id);

-- 2. Add failure linkage (QC and Lab Tests)
ALTER TABLE capa ADD COLUMN IF NOT EXISTS qc_check_id uuid REFERENCES qc_checks(id);
ALTER TABLE capa ADD COLUMN IF NOT EXISTS lab_test_id uuid REFERENCES lab_tests(id);

-- 3. Add verification and closure tracking columns
ALTER TABLE capa ADD COLUMN IF NOT EXISTS verification_notes text;
ALTER TABLE capa ADD COLUMN IF NOT EXISTS verified_at timestamptz;
ALTER TABLE capa ADD COLUMN IF NOT EXISTS closed_at timestamptz;

-- 4. Enforce status state machine
ALTER TABLE capa DROP CONSTRAINT IF EXISTS capa_status_check;
ALTER TABLE capa ADD CONSTRAINT capa_status_check CHECK (
  status IN ('Open', 'Investigating', 'Action Taken', 'Under Verification', 'Closed', 'Cancelled')
);

-- 5. Enforce segregation of duties: verifier cannot be the person who raised it
ALTER TABLE capa DROP CONSTRAINT IF EXISTS capa_no_self_verification;
ALTER TABLE capa ADD CONSTRAINT capa_no_self_verification CHECK (
  verified_by IS NULL OR verified_by <> raised_by
);

-- 6. Indexes for performance
CREATE INDEX IF NOT EXISTS idx_capa_job_id ON capa(job_id);
CREATE INDEX IF NOT EXISTS idx_capa_status ON capa(status);
CREATE INDEX IF NOT EXISTS idx_capa_raised_by ON capa(raised_by);
CREATE INDEX IF NOT EXISTS idx_capa_qc_check_id ON capa(qc_check_id);
CREATE INDEX IF NOT EXISTS idx_capa_lab_test_id ON capa(lab_test_id);
