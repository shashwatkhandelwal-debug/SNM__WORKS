-- 39_drop_unused_pg_net.sql   (production-only effect; a no-op wherever pg_net is absent, e.g. local)
--
-- Advisor lint 0014: pg_net is installed in `public` (relocatable = false).
-- Verified before dropping (production, 2026-09-21):
--   * nothing of ours references net.*: no function in any schema other than extensions.grant_pg_net_access
--     (Supabase's own event-trigger helper) mentions net.http_*; no supabase_functions webhooks; no pg_cron
--   * net.http_request_queue: 0 rows, net._http_response: 1 row (discarded with the extension)
--   * anon and authenticated held USAGE on schema net and EXECUTE on net.http_get. Relocating the extension to
--     `extensions` was dry-run first, but the postgres role cannot revoke those grants (Supabase's event trigger
--     is the grantor), so relocation would have left outbound-HTTP-from-the-database reachable by anon /
--     authenticated code paths. Dropping removes the whole surface.
--
-- If Database Webhooks / pg_net are wanted later:  CREATE EXTENSION pg_net WITH SCHEMA extensions;
--   and then review the roles that Supabase's event trigger grants USAGE on schema net to.
--
-- Idempotent. ROLLBACK: CREATE EXTENSION pg_net WITH SCHEMA extensions;  (history in net.* is not recoverable)

DROP EXTENSION IF EXISTS pg_net;
