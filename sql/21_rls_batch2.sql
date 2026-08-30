-- ============================================================================
-- 21_rls_batch2.sql — RLS Batch 2: Full Schema RBAC Conversion
-- ============================================================================
-- Converts all 13 remaining tables from legacy single-enum auth_role() checks
-- to strict RBAC auth_can(module, action) policies.
--
-- Tables converted:
--   1. profiles
--   2. customers
--   3. constructions
--   4. downtime
--   5. despatch
--   6. capa
--   7. dye_recipes
--   8. costing
--   9. audit_log
--  10. campaigns
--  11. masters
--  12. param_library
--  13. mil_w_4088_types
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. profiles
-- ---------------------------------------------------------------------------
DROP POLICY IF EXISTS profiles_self ON profiles;
DROP POLICY IF EXISTS profiles_owner_write ON profiles;
DROP POLICY IF EXISTS profiles_select ON profiles;
DROP POLICY IF EXISTS profiles_insert ON profiles;
DROP POLICY IF EXISTS profiles_update ON profiles;

CREATE POLICY profiles_select ON profiles FOR SELECT TO authenticated
  USING ((id = auth.uid()) OR auth_can('people', 'read'));

CREATE POLICY profiles_update ON profiles FOR UPDATE TO authenticated
  USING ((id = auth.uid()) OR auth_can('people', 'update'));

CREATE POLICY profiles_insert ON profiles FOR INSERT TO authenticated
  WITH CHECK ((id = auth.uid()) OR auth_can('people', 'create') OR auth_can('people', 'approve'));


-- ---------------------------------------------------------------------------
-- 2. customers
-- ---------------------------------------------------------------------------
DROP POLICY IF EXISTS customers_read ON customers;
DROP POLICY IF EXISTS customers_write ON customers;
DROP POLICY IF EXISTS customers_select ON customers;
DROP POLICY IF EXISTS customers_insert ON customers;
DROP POLICY IF EXISTS customers_update ON customers;

CREATE POLICY customers_select ON customers FOR SELECT TO authenticated
  USING (auth_can('customers', 'read'));

CREATE POLICY customers_insert ON customers FOR INSERT TO authenticated
  WITH CHECK (auth_can('customers', 'create'));

CREATE POLICY customers_update ON customers FOR UPDATE TO authenticated
  USING (auth_can('customers', 'update') OR auth_can('customers', 'approve'));


-- ---------------------------------------------------------------------------
-- 3. constructions
-- ---------------------------------------------------------------------------
DROP POLICY IF EXISTS constructions_read ON constructions;
DROP POLICY IF EXISTS constructions_write ON constructions;
DROP POLICY IF EXISTS constructions_select ON constructions;
DROP POLICY IF EXISTS constructions_insert ON constructions;
DROP POLICY IF EXISTS constructions_update ON constructions;

CREATE POLICY constructions_select ON constructions FOR SELECT TO authenticated
  USING (auth_can('constructions', 'read'));

CREATE POLICY constructions_insert ON constructions FOR INSERT TO authenticated
  WITH CHECK (auth_can('constructions', 'create'));

CREATE POLICY constructions_update ON constructions FOR UPDATE TO authenticated
  USING (auth_can('constructions', 'update') OR auth_can('constructions', 'approve'));


-- ---------------------------------------------------------------------------
-- 4. downtime
-- ---------------------------------------------------------------------------
DROP POLICY IF EXISTS downtime_read ON downtime;
DROP POLICY IF EXISTS downtime_write ON downtime;
DROP POLICY IF EXISTS downtime_update ON downtime;
DROP POLICY IF EXISTS downtime_select ON downtime;
DROP POLICY IF EXISTS downtime_insert ON downtime;

CREATE POLICY downtime_select ON downtime FOR SELECT TO authenticated
  USING (auth_can('downtime', 'read'));

CREATE POLICY downtime_insert ON downtime FOR INSERT TO authenticated
  WITH CHECK (auth_can('downtime', 'create'));

CREATE POLICY downtime_update ON downtime FOR UPDATE TO authenticated
  USING (auth_can('downtime', 'update'));


-- ---------------------------------------------------------------------------
-- 5. despatch
-- ---------------------------------------------------------------------------
DROP POLICY IF EXISTS despatch_read ON despatch;
DROP POLICY IF EXISTS despatch_write ON despatch;
DROP POLICY IF EXISTS despatch_select ON despatch;
DROP POLICY IF EXISTS despatch_insert ON despatch;
DROP POLICY IF EXISTS despatch_update ON despatch;

CREATE POLICY despatch_select ON despatch FOR SELECT TO authenticated
  USING (auth_can('despatch', 'read'));

CREATE POLICY despatch_insert ON despatch FOR INSERT TO authenticated
  WITH CHECK (auth_can('despatch', 'create'));

CREATE POLICY despatch_update ON despatch FOR UPDATE TO authenticated
  USING (auth_can('despatch', 'update') OR auth_can('despatch', 'approve'));


-- ---------------------------------------------------------------------------
-- 6. capa
-- ---------------------------------------------------------------------------
DROP POLICY IF EXISTS capa_read ON capa;
DROP POLICY IF EXISTS capa_write ON capa;
DROP POLICY IF EXISTS capa_select ON capa;
DROP POLICY IF EXISTS capa_insert ON capa;
DROP POLICY IF EXISTS capa_update ON capa;

CREATE POLICY capa_select ON capa FOR SELECT TO authenticated
  USING (auth_can('capa', 'read'));

CREATE POLICY capa_insert ON capa FOR INSERT TO authenticated
  WITH CHECK (auth_can('capa', 'create'));

CREATE POLICY capa_update ON capa FOR UPDATE TO authenticated
  USING (auth_can('capa', 'update') OR auth_can('capa', 'approve'));


-- ---------------------------------------------------------------------------
-- 7. dye_recipes
-- ---------------------------------------------------------------------------
DROP POLICY IF EXISTS recipes_read ON dye_recipes;
DROP POLICY IF EXISTS recipes_write ON dye_recipes;
DROP POLICY IF EXISTS recipes_select ON dye_recipes;
DROP POLICY IF EXISTS recipes_insert ON dye_recipes;
DROP POLICY IF EXISTS recipes_update ON dye_recipes;

CREATE POLICY recipes_select ON dye_recipes FOR SELECT TO authenticated
  USING (auth_can('recipes', 'read'));

CREATE POLICY recipes_insert ON dye_recipes FOR INSERT TO authenticated
  WITH CHECK (auth_can('recipes', 'create'));

CREATE POLICY recipes_update ON dye_recipes FOR UPDATE TO authenticated
  USING (auth_can('recipes', 'update') OR auth_can('recipes', 'approve'));


-- ---------------------------------------------------------------------------
-- 8. costing
-- ---------------------------------------------------------------------------
DROP POLICY IF EXISTS costing_owner_only ON costing;
DROP POLICY IF EXISTS costing_select ON costing;
DROP POLICY IF EXISTS costing_insert ON costing;
DROP POLICY IF EXISTS costing_update ON costing;

CREATE POLICY costing_select ON costing FOR SELECT TO authenticated
  USING (auth_can('costing', 'read'));

CREATE POLICY costing_insert ON costing FOR INSERT TO authenticated
  WITH CHECK (auth_can('costing', 'create'));

CREATE POLICY costing_update ON costing FOR UPDATE TO authenticated
  USING (auth_can('costing', 'update') OR auth_can('costing', 'approve'));


-- ---------------------------------------------------------------------------
-- 9. audit_log
-- ---------------------------------------------------------------------------
DROP POLICY IF EXISTS audit_read ON audit_log;
DROP POLICY IF EXISTS audit_insert ON audit_log;
DROP POLICY IF EXISTS audit_select ON audit_log;

CREATE POLICY audit_select ON audit_log FOR SELECT TO authenticated
  USING (auth_can('audit', 'read'));

CREATE POLICY audit_insert ON audit_log FOR INSERT TO authenticated
  WITH CHECK (auth.uid() IS NOT NULL);

-- Deliberately zero UPDATE and DELETE policies on audit_log to preserve tamper-proof audit trail.


-- ---------------------------------------------------------------------------
-- 10. campaigns
-- ---------------------------------------------------------------------------
ALTER TABLE campaigns ENABLE ROW LEVEL SECURITY;
ALTER TABLE campaigns FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS campaigns_select ON campaigns;
DROP POLICY IF EXISTS campaigns_insert ON campaigns;
DROP POLICY IF EXISTS campaigns_update ON campaigns;

CREATE POLICY campaigns_select ON campaigns FOR SELECT TO authenticated
  USING (auth_can('skus', 'read'));

CREATE POLICY campaigns_insert ON campaigns FOR INSERT TO authenticated
  WITH CHECK (auth_can('skus', 'create'));

CREATE POLICY campaigns_update ON campaigns FOR UPDATE TO authenticated
  USING (auth_can('skus', 'update'));


-- ---------------------------------------------------------------------------
-- 11. masters
-- ---------------------------------------------------------------------------
-- NOTE: The masters_select policy intentionally includes (auth.uid() IS NOT NULL)
-- because masters stores shared system reference data (units of measure, machine
-- numbers, defect codes, shifts) that must be readable across all modules by
-- all authenticated users on the shop floor and office.
DROP POLICY IF EXISTS masters_read ON masters;
DROP POLICY IF EXISTS masters_write ON masters;
DROP POLICY IF EXISTS masters_select ON masters;
DROP POLICY IF EXISTS masters_insert ON masters;
DROP POLICY IF EXISTS masters_update ON masters;

CREATE POLICY masters_select ON masters FOR SELECT TO authenticated
  USING ((auth.uid() IS NOT NULL) OR auth_can('systems', 'read'));

CREATE POLICY masters_insert ON masters FOR INSERT TO authenticated
  WITH CHECK (auth_can('systems', 'create'));

CREATE POLICY masters_update ON masters FOR UPDATE TO authenticated
  USING (auth_can('systems', 'update') OR auth_can('systems', 'approve'));


-- ---------------------------------------------------------------------------
-- 12. param_library
-- ---------------------------------------------------------------------------
DROP POLICY IF EXISTS params_read ON param_library;
DROP POLICY IF EXISTS params_write ON param_library;
DROP POLICY IF EXISTS params_select ON param_library;
DROP POLICY IF EXISTS params_insert ON param_library;
DROP POLICY IF EXISTS params_update ON param_library;

CREATE POLICY params_select ON param_library FOR SELECT TO authenticated
  USING (auth_can('specifications', 'read') OR auth_can('systems', 'read'));

CREATE POLICY params_insert ON param_library FOR INSERT TO authenticated
  WITH CHECK (auth_can('specifications', 'create') OR auth_can('systems', 'create'));

CREATE POLICY params_update ON param_library FOR UPDATE TO authenticated
  USING (auth_can('specifications', 'update') OR auth_can('systems', 'update'));


-- ---------------------------------------------------------------------------
-- 13. mil_w_4088_types
-- ---------------------------------------------------------------------------
DROP POLICY IF EXISTS mil_read ON mil_w_4088_types;
DROP POLICY IF EXISTS mil_select ON mil_w_4088_types;
DROP POLICY IF EXISTS mil_insert ON mil_w_4088_types;
DROP POLICY IF EXISTS mil_update ON mil_w_4088_types;

CREATE POLICY mil_select ON mil_w_4088_types FOR SELECT TO authenticated
  USING (auth_can('specifications', 'read'));

CREATE POLICY mil_insert ON mil_w_4088_types FOR INSERT TO authenticated
  WITH CHECK (auth_can('specifications', 'create'));

CREATE POLICY mil_update ON mil_w_4088_types FOR UPDATE TO authenticated
  USING (auth_can('specifications', 'update'));
