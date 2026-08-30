-- ============================================================================
-- 14_constructions.sql — Constructions Module Schema Extensions
-- Adds approval audit fields and segregation of duties self-approval constraint.
--
-- Safety Confirmation:
--   - Only adds nullable columns and a check constraint.
--   - No columns are dropped or recreated.
--   - Zero risk of data loss on non-empty tables.
--
-- NOTE ON RLS:
--   - constructions table RLS policies remain on legacy auth_role() check
--     deliberately for now. This is part of RLS Batch 2 scope, not forgotten.
-- ============================================================================

-- 1. Add approval audit columns
ALTER TABLE constructions
  ADD COLUMN IF NOT EXISTS approved_by uuid REFERENCES profiles(id),
  ADD COLUMN IF NOT EXISTS approved_at timestamptz;

-- 2. Add segregation of duties constraint: Approver cannot be creator
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'constructions_no_self_approval'
  ) THEN
    ALTER TABLE constructions
      ADD CONSTRAINT constructions_no_self_approval
      CHECK (approved_by IS NULL OR approved_by <> created_by);
  END IF;
END $$;

-- 3. Add status check constraint if not already present
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'constructions_status_check'
  ) THEN
    ALTER TABLE constructions
      ADD CONSTRAINT constructions_status_check
      CHECK (status IN ('Draft', 'Under Review', 'Approved', 'Obsolete', 'Cancelled'));
  END IF;
END $$;

-- 4. Add performance index on spec_no and family
CREATE INDEX IF NOT EXISTS idx_constructions_spec_no ON constructions(spec_no);
CREATE INDEX IF NOT EXISTS idx_constructions_family ON constructions(family);
CREATE INDEX IF NOT EXISTS idx_constructions_status ON constructions(status);
