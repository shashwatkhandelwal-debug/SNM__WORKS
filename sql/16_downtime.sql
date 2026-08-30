-- ============================================================================
-- 16_downtime.sql — Enforce Operator Identity Constraint on Downtime
-- ============================================================================
-- NOTE ON LIVE DATABASE (SUPABASE) USE:
-- 1. Table downtime currently has 0 rows in local test database.
-- 2. If existing rows exist when run on Supabase, operator_id is backfilled from
--    the first available profile before setting NOT NULL.
-- 3. RLS policies on downtime remain on the legacy auth_role() check
--    in 01_schema.sql deliberately — scheduled for RLS Batch 2.
-- ============================================================================

-- 1. Backfill legacy rows if any exist before setting NOT NULL
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM downtime WHERE operator_id IS NULL) THEN
    UPDATE downtime 
    SET operator_id = (SELECT id FROM profiles ORDER BY created_at ASC LIMIT 1)
    WHERE operator_id IS NULL;
  END IF;
END $$;

-- 2. Alter operator_id to NOT NULL
ALTER TABLE downtime
  ALTER COLUMN operator_id SET NOT NULL;

-- 3. Performance Indexes
CREATE INDEX IF NOT EXISTS idx_downtime_log_no ON downtime(log_no);
CREATE INDEX IF NOT EXISTS idx_downtime_logged_on ON downtime(logged_on);
CREATE INDEX IF NOT EXISTS idx_downtime_machine ON downtime(machine);
CREATE INDEX IF NOT EXISTS idx_downtime_job_id ON downtime(job_id);
CREATE INDEX IF NOT EXISTS idx_downtime_operator_id ON downtime(operator_id);
