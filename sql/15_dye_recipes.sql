-- ============================================================================
-- 15_dye_recipes.sql — Dye Recipes Audit, Approval & Controlled Shade Result
-- ============================================================================
-- NOTE ON LIVE DATABASE (SUPABASE) USE:
-- 1. Table dye_recipes currently has 0 rows in local test database.
-- 2. If existing rows exist when run on Supabase, created_by is backfilled from
--    the first available profile before setting NOT NULL.
-- 3. RLS policies on dye_recipes remain on the legacy auth_role() check
--    in 01_schema.sql deliberately — scheduled for RLS Batch 2.
-- ============================================================================

-- 1. Add created_by with safe backfill for NOT NULL
ALTER TABLE dye_recipes
  ADD COLUMN IF NOT EXISTS created_by uuid REFERENCES profiles(id);

-- Backfill legacy rows if any exist
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM dye_recipes WHERE created_by IS NULL) THEN
    UPDATE dye_recipes 
    SET created_by = (SELECT id FROM profiles ORDER BY created_at ASC LIMIT 1)
    WHERE created_by IS NULL;
  END IF;
END $$;

ALTER TABLE dye_recipes
  ALTER COLUMN created_by SET NOT NULL;

-- 2. Add approved_at timestamp
ALTER TABLE dye_recipes
  ADD COLUMN IF NOT EXISTS approved_at timestamptz;

-- 3. Add status with default 'Draft'
ALTER TABLE dye_recipes
  ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'Draft';

-- 4. Non-Negotiable Rule 6: Segregation of Duties Self-Approval Constraint
ALTER TABLE dye_recipes
  DROP CONSTRAINT IF EXISTS dye_recipes_no_self_approval;

ALTER TABLE dye_recipes
  ADD CONSTRAINT dye_recipes_no_self_approval
  CHECK (approved_by IS NULL OR approved_by <> created_by);

-- 5. Status State Machine Constraint
ALTER TABLE dye_recipes
  DROP CONSTRAINT IF EXISTS dye_recipes_status_check;

ALTER TABLE dye_recipes
  ADD CONSTRAINT dye_recipes_status_check
  CHECK (status IN ('Draft', 'Lab Dip', 'Approved', 'Rejected', 'Cancelled'));

-- 6. Controlled Shade Result Constraint
ALTER TABLE dye_recipes
  DROP CONSTRAINT IF EXISTS dye_recipes_shade_result_check;

ALTER TABLE dye_recipes
  ADD CONSTRAINT dye_recipes_shade_result_check
  CHECK (shade_result IS NULL OR shade_result IN ('Matched to Master', 'Close - Acceptable', 'Off-shade', 'Pending Review'));

-- 7. Performance Indexes
CREATE INDEX IF NOT EXISTS idx_dye_recipes_recipe_no ON dye_recipes(recipe_no);
CREATE INDEX IF NOT EXISTS idx_dye_recipes_job_id ON dye_recipes(job_id);
CREATE INDEX IF NOT EXISTS idx_dye_recipes_status ON dye_recipes(status);
CREATE INDEX IF NOT EXISTS idx_dye_recipes_target_shade ON dye_recipes(target_shade);
