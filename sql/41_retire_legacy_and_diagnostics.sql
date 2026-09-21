-- 41_retire_legacy_and_diagnostics.sql        *** LOCAL FIRST: not applied to the project until the local suite passes ***
--
-- A. Diagnostics with no signed-in caller. Verified: zero application/script callers (repo grep, round 5 B1).
--    security_posture(), verify_audit_chain(), audit_daily_root(date) are SECURITY DEFINER, have NO internal permission
--    check, and were executable by every signed-in account (including accounts with no role).
--    Revoked from PUBLIC/anon/authenticated. Unaffected: snm_app and service_role keep EXECUTE (admin tooling), the owner
--    (postgres / SQL editor) keeps it, and the internal callers security_posture -> verify_audit_chain and
--    verify_spec_pdf_integrity -> verify_audit_chain are SECURITY DEFINER, so they run as the owner.
--    verify_audit_chain is deliberately NOT gated with auth_can(): that would break verify_spec_pdf_integrity for
--    users who hold specifications.read but not audit.read.
--
-- B. Legacy role helpers. Verified on the project (2026-09-21) and locally: no policy, view, column default or trigger
--    depends on auth_role() / has_role(), and no function body other than rls_report() mentions them; rls_report() has no
--    caller and its only gate is the legacy `auth_role() = 'owner'` check, which cannot pass without a profiles.role match.
--    Dropped WITHOUT CASCADE so any unexpected dependency makes the migration fail instead of silently dropping objects.
--    (rls_report's job is served by SELECT ... FROM pg_policies / security_posture() run as the owner.)
--
-- Tolerant of local drift: absent functions are skipped with a NOTICE '41-skip (absent): ...'.
-- Idempotent.
--
-- ROLLBACK (manual; definitions captured from the project 2026-09-21):
--   GRANT EXECUTE ON FUNCTION public.security_posture(), public.verify_audit_chain(), public.audit_daily_root(date) TO authenticated;
--   CREATE OR REPLACE FUNCTION public.auth_role() RETURNS app_role LANGUAGE sql STABLE SECURITY DEFINER SET search_path TO 'public'
--     AS $$ select role from profiles where id = auth.uid() and active = true $$;
--   CREATE OR REPLACE FUNCTION public.has_role(p_role text) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path TO 'public'
--     AS $$ select exists (select 1 from user_roles ur join profiles p on p.id = ur.user_id
--                          where ur.user_id = auth.uid() and ur.role_code = p_role and ur.active and p.active) $$;
--   rls_report(): recreate from sql/03_security_report.sql (gate: auth_role() is distinct from 'owner').

DO $$
DECLARE
    f text;
BEGIN
    -- A
    FOREACH f IN ARRAY ARRAY['public.security_posture()', 'public.verify_audit_chain()', 'public.audit_daily_root(date)'] LOOP
        IF to_regprocedure(f) IS NULL THEN RAISE NOTICE '41-skip (absent): %', f; CONTINUE; END IF;
        EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC, anon, authenticated', f);
    END LOOP;

    -- B (order matters: rls_report first, it is the only body that mentions auth_role)
    FOREACH f IN ARRAY ARRAY['public.rls_report()', 'public.auth_role()', 'public.has_role(text)'] LOOP
        IF to_regprocedure(f) IS NULL THEN RAISE NOTICE '41-skip (absent): %', f; CONTINUE; END IF;
        EXECUTE format('DROP FUNCTION %s', f);
    END LOOP;
END
$$;
