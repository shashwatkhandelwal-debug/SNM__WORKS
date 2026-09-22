-- 30_owner_conflict_exemption.sql
--
-- DOCUMENTATION-ONLY. The owner-role exemption from segregation-of-duties role-conflict
-- checks is already applied by sql/42_production_parity_audit_and_functions.sql, which
-- replaces check_user_role_conflicts() with the project's version (owner-tier profiles
-- are exempt from blocking conflicts). This file exists solely so the local migration
-- history includes an entry matching the production migration name 30_owner_conflict_exemption.
-- It performs NO schema change and is safe to run any number of times, in any order,
-- before or after sql/42.

DO $$
BEGIN
    RAISE NOTICE '30: owner-role exemption already present via sql/42 (check_user_role_conflicts); no action taken here.';
END
$$;
