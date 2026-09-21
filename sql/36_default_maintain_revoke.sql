-- 36_default_maintain_revoke.sql
--
-- Follow-up to 35. 35 revoked MAINTAIN (PG17+: VACUUM, ANALYZE, REINDEX, CLUSTER, LOCK TABLE,
-- REFRESH MATERIALIZED VIEW) from authenticated on EXISTING tables, but the DEFAULT privileges for
-- FUTURE tables still granted it (production default ACL showed authenticated=arwm).
-- Found during post-apply verification of 35 on production (Postgres 17.6); local test DB is
-- PG16, which has no MAINTAIN privilege, so it could not surface this.
--
-- Guarded by server version so the same file runs on the PG16 local harness (no-op there).
-- Idempotent.
--
-- ROLLBACK (manual): ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT MAINTAIN ON TABLES TO authenticated;

DO $$
BEGIN
    IF current_setting('server_version_num')::int >= 170000 THEN
        EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE MAINTAIN ON TABLES FROM authenticated';
    END IF;
END
$$;
