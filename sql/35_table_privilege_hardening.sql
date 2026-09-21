-- 35_table_privilege_hardening.sql   (REVISED: DELETE is now policy-driven, not a hard-coded list)
--
-- WHY: on production, anon and authenticated hold arwdDxtm (incl. DELETE, TRUNCATE, REFERENCES,
-- TRIGGER, and MAINTAIN on PG17) on every public table, and snm_app INHERITS authenticated's
-- privileges (member of authenticated, inherit = true). Row-level security is therefore the only
-- gate, and TRUNCATE is not subject to RLS at all: code running as snm_app / authenticated could
-- TRUNCATE audit_log despite "no DELETE/UPDATE policy" and the hash chain.
--
-- SCOPE
--   A. Default privileges: new tables/sequences no longer auto-grant the dangerous privileges
--   B. anon: loses ALL privileges on every table/view/sequence in public
--   C. authenticated (and so snm_app): loses TRUNCATE, REFERENCES, TRIGGER (+ MAINTAIN on PG17+)
--   D. authenticated (and so snm_app): DELETE is held EXACTLY by tables that have a DELETE (or ALL)
--      policy, and revoked from every other table. Evaluated per table at apply time, so it is correct
--      whatever migrations have already run (local has sql/31 trade_documents; production did not
--      at the time of writing).
--
-- BEHAVIOUR CHANGES TO EXPECT
--   * anon touching a table now gets SQLSTATE 42501 (permission denied) instead of "0 rows".
--   * authenticated DELETE on a table without a DELETE policy: 42501 instead of "0 rows deleted".
--   * verify_public_certificate() is SECURITY DEFINER and keeps working for anon.
--
-- NOT IN SCOPE (later): making snm_app NOINHERIT (needs app testing), audit_insert / tasks_read
--   policy tightening, storage-schema grants, function privileges (see 34).
--
-- CONVENTION FROM HERE ON
--   * A new table gets SELECT/INSERT/UPDATE for authenticated automatically.
--   * A migration that adds a DELETE policy MUST also  GRANT DELETE ON public.<table> TO authenticated;
--     (a policy without the privilege is dead; the privilege without a policy is refused by 35 on re-run).
--   * Never grant anything on public tables to anon.
--
-- LOCAL DRIFT POLICY: objects absent locally are skipped by construction (D iterates pg_class).
-- Idempotent. Follow the BEGIN/COMMIT convention of the other sql/*.sql files.

-- A. Default privileges (objects created by the migration role) ------------------------------
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM anon;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    REVOKE DELETE, TRUNCATE, REFERENCES, TRIGGER ON TABLES FROM authenticated;

-- B. anon: nothing -----------------------------------------------------------------------------
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon;

-- C. authenticated: no TRUNCATE / REFERENCES / TRIGGER (snm_app inherits) ----------------------
REVOKE TRUNCATE, REFERENCES, TRIGGER ON ALL TABLES IN SCHEMA public FROM authenticated;

DO $$
DECLARE
    r record;
BEGIN
    -- MAINTAIN exists only on PG17+ (production); the local test DB may be older.
    IF current_setting('server_version_num')::int >= 170000 THEN
        EXECUTE 'REVOKE MAINTAIN ON ALL TABLES IN SCHEMA public FROM authenticated';
    END IF;

    -- D. DELETE follows the policies.
    FOR r IN
        SELECT c.oid::regclass AS tbl,
               EXISTS (SELECT 1 FROM pg_policies p
                       WHERE p.schemaname = 'public' AND p.tablename = c.relname
                         AND p.cmd IN ('DELETE', 'ALL')) AS has_delete_policy
        FROM pg_class c
        WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p')
        ORDER BY c.relname
    LOOP
        IF r.has_delete_policy THEN
            EXECUTE format('GRANT DELETE ON %s TO authenticated', r.tbl);
            RAISE NOTICE '35: DELETE kept/granted (has DELETE policy): %', r.tbl;
        ELSE
            EXECUTE format('REVOKE DELETE ON %s FROM authenticated', r.tbl);
        END IF;
    END LOOP;
END
$$;

-- ROLLBACK (manual, only if a regression is proven; never automatic):
--   GRANT ALL ON ALL TABLES IN SCHEMA public TO anon, authenticated;      -- restores pre-35 exposure
--   GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO anon;
--   ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO anon, authenticated;
--   ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO anon;
