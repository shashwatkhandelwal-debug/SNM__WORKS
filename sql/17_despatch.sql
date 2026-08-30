-- ============================================================================
-- 17_despatch.sql — Despatch Audit, Quality Release & Database QC Hold Gate
-- ============================================================================
-- NOTE ON LIVE DATABASE (SUPABASE) USE:
-- 1. Table despatch currently has 0 rows in local test database.
-- 2. If existing rows exist when run on Supabase, created_by and job_id are
--    backfilled before setting NOT NULL.
-- 3. RLS policies on despatch remain on legacy auth_role() check
--    in 01_schema.sql deliberately — scheduled for RLS Batch 2.
-- ============================================================================

-- 1. Backfill legacy rows if any exist before setting NOT NULL
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM despatch WHERE created_by IS NULL) THEN
    UPDATE despatch 
    SET created_by = (SELECT id FROM profiles ORDER BY created_at ASC LIMIT 1)
    WHERE created_by IS NULL;
  END IF;

  IF EXISTS (SELECT 1 FROM despatch WHERE job_id IS NULL) THEN
    UPDATE despatch 
    SET job_id = (SELECT id FROM jobs ORDER BY created_at ASC LIMIT 1)
    WHERE job_id IS NULL;
  END IF;
END $$;

-- 2. Hardened Identity & Job Linkage Constraints
ALTER TABLE despatch
  ALTER COLUMN created_by SET NOT NULL;

ALTER TABLE despatch
  ALTER COLUMN job_id SET NOT NULL;

-- 3. Quality Release Approval Fields
ALTER TABLE despatch
  ADD COLUMN IF NOT EXISTS approved_by uuid REFERENCES profiles(id);

ALTER TABLE despatch
  ADD COLUMN IF NOT EXISTS approved_at timestamptz;

-- 4. Non-Negotiable Rule 6: Segregation of Duties Self-Approval Constraint
ALTER TABLE despatch
  DROP CONSTRAINT IF EXISTS despatch_no_self_approval;

ALTER TABLE despatch
  ADD CONSTRAINT despatch_no_self_approval
  CHECK (approved_by IS NULL OR approved_by <> created_by);

-- 5. Status State Machine Constraint
ALTER TABLE despatch
  DROP CONSTRAINT IF EXISTS despatch_status_check;

ALTER TABLE despatch
  ADD CONSTRAINT despatch_status_check
  CHECK (status IN ('Packed', 'Ready for Dispatch', 'Dispatched', 'In Transit', 'Delivered', 'Cancelled'));

-- 6. Database Level QC Hold Gate (BEFORE INSERT OR UPDATE Trigger)
-- Ensures no dispatch can be created or approved for a job on QC hold without a documented override reason.
CREATE OR REPLACE FUNCTION check_despatch_qc_hold()
RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.job_id IS NOT NULL AND job_on_hold(NEW.job_id) THEN
    IF NEW.override_reason IS NULL OR length(trim(NEW.override_reason)) < 10 THEN
      RAISE EXCEPTION 'Cannot despatch job on QC hold without a documented override reason (minimum 10 characters).'
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_check_despatch_qc_hold ON despatch;
CREATE TRIGGER trg_check_despatch_qc_hold
  BEFORE INSERT OR UPDATE ON despatch
  FOR EACH ROW
  EXECUTE FUNCTION check_despatch_qc_hold();

-- 7. Performance Indexes
CREATE INDEX IF NOT EXISTS idx_despatch_despatch_no ON despatch(despatch_no);
CREATE INDEX IF NOT EXISTS idx_despatch_despatched_on ON despatch(despatched_on);
CREATE INDEX IF NOT EXISTS idx_despatch_job_id ON despatch(job_id);
CREATE INDEX IF NOT EXISTS idx_despatch_status ON despatch(status);
CREATE INDEX IF NOT EXISTS idx_despatch_created_by ON despatch(created_by);
CREATE INDEX IF NOT EXISTS idx_despatch_approved_by ON despatch(approved_by);
