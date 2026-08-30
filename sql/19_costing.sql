-- ============================================================================
-- 19_costing.sql — Schema Hardening, Approval Workflow & Cost Freeze Trigger
-- ============================================================================
-- 1. Alters costing table to enforce created_by NOT NULL and approval tracking.
-- 2. Adds Non-Negotiable Rule 6 self-approval constraint.
-- 3. Adds status check constraint ('Draft', 'Approved', 'Archived').
-- 4. Creates BEFORE UPDATE trigger trg_freeze_approved_costing to permanently
--    freeze cost-bearing fields once approved.
-- ============================================================================

ALTER TABLE costing
  ADD COLUMN IF NOT EXISTS created_by uuid REFERENCES profiles(id),
  ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now(),
  ADD COLUMN IF NOT EXISTS approved_by uuid REFERENCES profiles(id),
  ADD COLUMN IF NOT EXISTS approved_at timestamptz,
  ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'Draft';

DO $$
DECLARE
  v_default_user uuid;
BEGIN
  IF EXISTS (SELECT 1 FROM costing WHERE created_by IS NULL LIMIT 1) THEN
    SELECT id INTO v_default_user FROM profiles WHERE role = 'owner' LIMIT 1;
    IF v_default_user IS NULL THEN
      SELECT id INTO v_default_user FROM profiles LIMIT 1;
    END IF;
    IF v_default_user IS NOT NULL THEN
      UPDATE costing SET created_by = v_default_user WHERE created_by IS NULL;
    END IF;
  END IF;
END $$;

ALTER TABLE costing
  ALTER COLUMN created_by SET NOT NULL;

-- 2. Non-Negotiable Rule 6 Self-Approval constraint
ALTER TABLE costing
  DROP CONSTRAINT IF EXISTS costing_no_self_approval;

ALTER TABLE costing
  ADD CONSTRAINT costing_no_self_approval
  CHECK (approved_by IS NULL OR approved_by <> created_by);

-- 3. Status constraint (Draft, Approved, Archived)
ALTER TABLE costing
  DROP CONSTRAINT IF EXISTS costing_status_check;

ALTER TABLE costing
  ADD CONSTRAINT costing_status_check
  CHECK (status IN ('Draft', 'Approved', 'Archived'));

-- 4. BEFORE UPDATE Trigger: Freeze cost fields on approved/archived records
CREATE OR REPLACE FUNCTION freeze_approved_costing()
RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  -- If the cost sheet is already Approved or Archived, freeze all financial & input parameters
  IF OLD.status IN ('Approved', 'Archived') THEN
    IF (NEW.qty IS DISTINCT FROM OLD.qty) OR
       (NEW.unit IS DISTINCT FROM OLD.unit) OR
       (NEW.yarn_rate IS DISTINCT FROM OLD.yarn_rate) OR
       (NEW.yarn_consumption IS DISTINCT FROM OLD.yarn_consumption) OR
       (NEW.wastage_pct IS DISTINCT FROM OLD.wastage_pct) OR
       (NEW.dyeing IS DISTINCT FROM OLD.dyeing) OR
       (NEW.coating IS DISTINCT FROM OLD.coating) OR
       (NEW.labour IS DISTINCT FROM OLD.labour) OR
       (NEW.overhead IS DISTINCT FROM OLD.overhead) OR
       (NEW.packing IS DISTINCT FROM OLD.packing) OR
       (NEW.freight IS DISTINCT FROM OLD.freight) OR
       (NEW.margin_pct IS DISTINCT FROM OLD.margin_pct) THEN
      RAISE EXCEPTION 'Cannot modify cost parameters on an approved cost sheet. Cost figures are frozen once approved.'
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;

  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_freeze_approved_costing ON costing;
CREATE TRIGGER trg_freeze_approved_costing
  BEFORE UPDATE ON costing
  FOR EACH ROW
  EXECUTE FUNCTION freeze_approved_costing();

-- 5. Performance indexes
CREATE INDEX IF NOT EXISTS costing_status_idx ON costing(status);
CREATE INDEX IF NOT EXISTS costing_created_by_idx ON costing(created_by);
CREATE INDEX IF NOT EXISTS costing_approved_by_idx ON costing(approved_by);
