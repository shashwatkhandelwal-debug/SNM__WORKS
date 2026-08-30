-- ============================================================================
-- 12_rls_batch1_active_modules.sql — RLS Batch 1: Active Modules (QC, Jobs, SKUs)
-- ============================================================================
--
-- Replaces all legacy single-enum auth_role() policies with strict RBAC
-- auth_can(module, action) checks for the three active modules:
--   1. qc_checks
--   2. jobs
--   3. skus
--
-- No legacy role fallbacks ('owner', 'supervisor', 'qc', 'operator', 'store').
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. qc_checks
-- ---------------------------------------------------------------------------
DROP POLICY IF EXISTS qc_read ON qc_checks;
DROP POLICY IF EXISTS qc_insert ON qc_checks;
DROP POLICY IF EXISTS qc_update ON qc_checks;

CREATE POLICY qc_read ON qc_checks FOR SELECT
  USING (auth_can('qc', 'read'));

CREATE POLICY qc_insert ON qc_checks FOR INSERT
  WITH CHECK (auth_can('qc', 'create'));

CREATE POLICY qc_update ON qc_checks FOR UPDATE
  USING (auth_can('qc', 'update') OR auth_can('qc', 'approve'));


-- ---------------------------------------------------------------------------
-- 2. jobs
-- ---------------------------------------------------------------------------
DROP POLICY IF EXISTS jobs_read ON jobs;
DROP POLICY IF EXISTS jobs_insert ON jobs;
DROP POLICY IF EXISTS jobs_update ON jobs;

CREATE POLICY jobs_read ON jobs FOR SELECT
  USING (auth_can('jobs', 'read'));

CREATE POLICY jobs_insert ON jobs FOR INSERT
  WITH CHECK (auth_can('jobs', 'create'));

CREATE POLICY jobs_update ON jobs FOR UPDATE
  USING (auth_can('jobs', 'update') OR auth_can('jobs', 'approve'));


-- ---------------------------------------------------------------------------
-- 3. skus
-- ---------------------------------------------------------------------------
DROP POLICY IF EXISTS skus_read ON skus;
DROP POLICY IF EXISTS skus_write ON skus;
DROP POLICY IF EXISTS skus_insert ON skus;
DROP POLICY IF EXISTS skus_update ON skus;

CREATE POLICY skus_read ON skus FOR SELECT
  USING (auth_can('skus', 'read'));

CREATE POLICY skus_insert ON skus FOR INSERT
  WITH CHECK (auth_can('skus', 'create'));

CREATE POLICY skus_update ON skus FOR UPDATE
  USING (auth_can('skus', 'update'));
