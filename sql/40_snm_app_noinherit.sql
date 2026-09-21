-- 40_snm_app_noinherit.sql        *** HELD: apply only after local tests + repo greps G4 are clean ***
--
-- snm_app is a member of authenticated with INHERIT = true, so a connection that has NOT done
-- SET LOCAL ROLE authenticated still carries authenticated's privileges. The documented model is the opposite:
-- snm_app is a restricted login role that ACQUIRES authenticated per request (SET LOCAL ROLE + claims).
--
-- Change: keep the membership and the right to SET ROLE, drop the automatic inheritance.
--   GRANT authenticated TO snm_app WITH INHERIT FALSE, SET TRUE;      (PG16+)
--
-- Dry-run on production (rolled back, 2026-09-21): inherit t -> f, set stays t.
-- Expected effective change for a plain snm_app session (no SET ROLE):
--   * DELETE only where snm_app holds it directly: sku_specifications (1 table); no longer platform_connections, user_roles
--   * no INSERT on audit_log (snm_app holds SELECT only there, by design)
--   * EXECUTE unchanged (snm_app holds all 28 public functions directly)
--   * every request that does SET LOCAL ROLE authenticated behaves exactly as before
--
-- RISK: any code path that uses snm_app WITHOUT SET LOCAL ROLE and relies on inherited privileges (background
-- jobs, staging scripts, admin tools) will now get 42501. Repo grep G4 + the full suite must show none.
--
-- Guarded: PG16+ syntax; roles must exist. Idempotent.
-- ROLLBACK (manual): GRANT authenticated TO snm_app WITH INHERIT TRUE, SET TRUE;

DO $$
BEGIN
    IF current_setting('server_version_num')::int >= 160000
       AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'snm_app')
       AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        EXECUTE 'GRANT authenticated TO snm_app WITH INHERIT FALSE, SET TRUE';
    END IF;
END
$$;
