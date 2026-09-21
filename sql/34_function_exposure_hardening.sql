-- 34_function_exposure_hardening.sql
-- (supersedes the withdrawn "22_function_exposure_hardening.sql": that number was already taken
--  by 22_platform_connections.sql. Confirm 33 is still the highest existing number before placing.)
--
-- Closes Supabase advisor lints 0011 (mutable search_path, 8 fns) and 0028 (anon can execute
-- SECURITY DEFINER fns: 19 -> 2 intentional), and fixes the root cause: default privileges
-- auto-granted EXECUTE on every new function in `public` to anon / authenticated.
--
-- SCOPE
--   A. Default privileges: no more auto-granted EXECUTE on future functions
--   B. Trigger functions: not RPC-callable
--   C. anon loses every data/diagnostic RPC (keeps auth_can, verify_public_certificate)
--   D. search_path pinned on the 8 flagged functions
--
-- NOT IN SCOPE (later migrations): revoking `authenticated` on security_posture / rls_report /
--   verify_audit_chain / audit_daily_root (pending repo grep); audit_insert + tasks_read policies;
--   retiring auth_role()/has_role(); moving pg_net out of public (production-only extension).
--
-- DELIBERATELY UNTOUCHED: auth_can() stays open to anon (~102 policies call it, some TO public
--   incl. storage.objects; revoking would turn "0 rows" into "permission denied").
--
-- LOCAL DRIFT POLICY: local snm_test_db is known to differ from production (production has
--   chain_audit_row() and audit_daily_root(); local has chain_audit_hash() and no audit_daily_root).
--   Every function below is looked up with to_regprocedure(); an absent one is SKIPPED with a
--   NOTICE '34-skip (absent): ...' instead of aborting. In production none may be skipped:
--   the post-apply verification queries prove that.
--
-- CONVENTION FROM HERE ON: every new function in `public` needs an explicit
--   GRANT EXECUTE ... TO authenticated, snm_app;  functions used inside RLS policies MUST be
--   granted to authenticated. Nothing is auto-granted any more.
--
-- Idempotent. Follow the BEGIN/COMMIT convention of the other sql/*.sql files.

-- A. Default privileges (functions created by the migration role) ---------------------------
ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM anon, authenticated;

DO $$
DECLARE
    -- B. trigger functions (EXECUTE is checked at CREATE TRIGGER, not when a trigger fires).
    --    chain_audit_hash() is the local name of production's chain_audit_row().
    trigger_fns text[] := ARRAY[
        'public.handle_new_user()', 'public.log_change()', 'public.on_qc_fail()',
        'public.on_test_fail()', 'public.chain_audit_row()', 'public.chain_audit_hash()',
        'public.check_despatch_qc_hold()', 'public.check_user_role_conflicts()',
        'public.freeze_approved_costing()', 'public.process_job_material_issue()',
        'public.touch_updated_at()', 'public.validate_yarn_lot_release()'];

    -- C. data / diagnostic RPCs: anon loses them; signed-in and server roles keep them explicitly
    rpc_fns text[] := ARRAY[
        'public.audit_daily_root(date)', 'public.auth_role()',
        'public.get_user_role_conflicts(uuid)', 'public.has_role(text)',
        'public.job_on_hold(uuid)', 'public.job_traceability(uuid)', 'public.my_roles()',
        'public.my_tasks()', 'public.rls_report()', 'public.security_posture()',
        'public.spec_check_plan(uuid)', 'public.verify_audit_chain()',
        'public.verify_spec_pdf_integrity(uuid,text,text,text)'];

    -- stay open to everyone, granted explicitly so nothing depends on PUBLIC
    open_fns text[] := ARRAY['public.auth_can(text,text)', 'public.verify_public_certificate(text,text)'];

    -- D. search_path pinned (lint 0011). qc_verdict / lab_test_verdict back generated `verdict` columns.
    path_fns text[] := ARRAY[
        'public.qc_verdict(public.limit_kind,numeric,numeric,numeric,numeric)',
        'public.lab_test_verdict(public.limit_kind,numeric,numeric,numeric,boolean,numeric[])',
        'public.touch_updated_at()', 'public.check_despatch_qc_hold()',
        'public.freeze_approved_costing()', 'public.process_job_material_issue()',
        'public.validate_yarn_lot_release()', 'public.check_user_role_conflicts()'];

    f text;
    r text;
BEGIN
    FOREACH f IN ARRAY trigger_fns LOOP
        IF to_regprocedure(f) IS NULL THEN RAISE NOTICE '34-skip (absent): %', f; CONTINUE; END IF;
        EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC, anon, authenticated', f);
    END LOOP;

    FOREACH f IN ARRAY rpc_fns LOOP
        IF to_regprocedure(f) IS NULL THEN RAISE NOTICE '34-skip (absent): %', f; CONTINUE; END IF;
        EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC, anon', f);
        FOREACH r IN ARRAY ARRAY['authenticated', 'snm_app', 'service_role'] LOOP
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
                EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO %I', f, r);
            END IF;
        END LOOP;
    END LOOP;

    FOREACH f IN ARRAY open_fns LOOP
        IF to_regprocedure(f) IS NULL THEN RAISE NOTICE '34-skip (absent): %', f; CONTINUE; END IF;
        FOREACH r IN ARRAY ARRAY['anon', 'authenticated', 'snm_app', 'service_role'] LOOP
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
                EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO %I', f, r);
            END IF;
        END LOOP;
    END LOOP;

    FOREACH f IN ARRAY path_fns LOOP
        IF to_regprocedure(f) IS NULL THEN RAISE NOTICE '34-skip (absent): %', f; CONTINUE; END IF;
        EXECUTE format('ALTER FUNCTION %s SET search_path = pg_catalog, public', f);
    END LOOP;
END
$$;

-- ROLLBACK (manual, only if a regression is proven; never automatic):
--   ALTER DEFAULT PRIVILEGES GRANT EXECUTE ON FUNCTIONS TO PUBLIC;
--   ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO anon, authenticated;
--   GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO anon, authenticated;  -- restores pre-34 exposure
--   ALTER FUNCTION <each of the 8> RESET search_path;
