-- ============================================================================
-- SNM WORKS — LOCK ROW LEVEL SECURITY AT THE DATABASE
-- Run in the Supabase SQL Editor before connecting Django or FastAPI.
-- ============================================================================
--
-- THE PROBLEM THIS SOLVES
--
--   A web framework connects to Postgres as one database user, reused for
--   every request. If that user can bypass row level security, every policy
--   you wrote stops applying — and nothing looks broken. The application
--   works, the pages render, and the costing table is readable by the
--   store keeper.
--
--   Middleware that forwards the caller's identity is necessary but not
--   sufficient, because middleware can be written wrong. What follows makes
--   the database refuse regardless of what the application does.
--
--   Three locks:
--     1. FORCE row level security, so even a table owner is subject to policies
--     2. A dedicated application role with NOBYPASSRLS and no ownership
--     3. No DELETE granted at all — records are cancelled, never removed
-- ============================================================================


-- ---------------------------------------------------------------------------
-- 1. FORCE row level security on every table
-- ---------------------------------------------------------------------------
-- ENABLE alone does not bind the table's owner. FORCE does. Without this, a
-- connection that happens to own the tables sails straight past every policy.

do $$
declare t record;
begin
  for t in
    select c.relname
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public' and c.relkind = 'r'
  loop
    execute format('alter table public.%I enable row level security', t.relname);
    execute format('alter table public.%I force row level security', t.relname);
  end loop;
end $$;


-- ---------------------------------------------------------------------------
-- 2. The application role
-- ---------------------------------------------------------------------------
-- Change the password before running, and put it only in your .env file.
-- It must never reach the browser, a commit, or a chat message.

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'snm_app') then
    create role snm_app with login password 'CHANGE_THIS_TO_A_LONG_RANDOM_STRING';
  end if;
end $$;

-- explicit, even though it is the default — this is the line that matters
alter role snm_app nobypassrls nosuperuser nocreatedb nocreaterole noreplication;

-- let it become 'authenticated', which is the role your policies are written for
grant authenticated to snm_app;

grant usage on schema public to snm_app;

-- read, add and correct. No DELETE anywhere, deliberately.
grant select, insert, update on all tables in schema public to snm_app;
grant usage, select on all sequences in schema public to snm_app;
grant execute on all functions in schema public to snm_app;

-- and for tables added later
alter default privileges in schema public
  grant select, insert, update on tables to snm_app;
alter default privileges in schema public
  grant usage, select on sequences to snm_app;
alter default privileges in schema public
  grant execute on functions to snm_app;

-- the audit log is written by triggers only; the application never touches it
revoke insert, update on audit_log from snm_app;
grant select on audit_log to snm_app;


-- ---------------------------------------------------------------------------
-- 3. Prove it, from inside the database
-- ---------------------------------------------------------------------------
-- Run these now, and again after any schema change.

-- (a) every table must show both true
select
  c.relname as table_name,
  c.relrowsecurity as rls_enabled,
  c.relforcerowsecurity as rls_forced
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public' and c.relkind = 'r'
  and (c.relrowsecurity = false or c.relforcerowsecurity = false);
-- Expect: zero rows. Anything listed here is unprotected.

-- (b) the application role must not be able to step over policies
select rolname, rolsuper, rolbypassrls
from pg_roles
where rolname = 'snm_app';
-- Expect: rolsuper false, rolbypassrls false.

-- (c) no table may be left without a policy
select c.relname
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
left join pg_policies p on p.schemaname = 'public' and p.tablename = c.relname
where n.nspname = 'public' and c.relkind = 'r'
group by c.relname
having count(p.policyname) = 0;
-- Expect: zero rows. A table with RLS on and no policy denies everyone,
-- which is safe but usually means something was forgotten.


-- ---------------------------------------------------------------------------
-- 4. A standing check you can run any time
-- ---------------------------------------------------------------------------

create or replace function security_posture()
returns table (check_name text, result text, ok boolean)
language plpgsql stable security definer set search_path = public as $$
begin
  return query
  select 'Tables without forced RLS'::text,
         coalesce(string_agg(c.relname, ', '), 'none')::text,
         count(*) = 0
  from pg_class c join pg_namespace n on n.oid = c.relnamespace
  where n.nspname='public' and c.relkind='r'
    and (not c.relrowsecurity or not c.relforcerowsecurity);

  return query
  select 'Tables with no policy'::text,
         coalesce(string_agg(x.relname, ', '), 'none')::text,
         count(*) = 0
  from (
    select c.relname
    from pg_class c join pg_namespace n on n.oid=c.relnamespace
    left join pg_policies p on p.schemaname='public' and p.tablename=c.relname
    where n.nspname='public' and c.relkind='r'
    group by c.relname having count(p.policyname)=0
  ) x;

  return query
  select 'Application role can bypass RLS'::text,
         coalesce((select case when rolbypassrls then 'YES' else 'no' end
                   from pg_roles where rolname='snm_app'), 'role missing')::text,
         coalesce((select not rolbypassrls from pg_roles where rolname='snm_app'), false);

  return query
  select 'Delete policy on audit_log'::text,
         coalesce((select string_agg(policyname, ', ') from pg_policies
                   where tablename='audit_log' and cmd in ('DELETE','ALL')), 'none')::text,
         not exists (select 1 from pg_policies
                     where tablename='audit_log' and cmd in ('DELETE','ALL'));

  return query
  select 'Audit chain'::text,
         (select status from verify_audit_chain())::text,
         (select status from verify_audit_chain()) = 'OK';
end $$;

grant execute on function security_posture() to authenticated;

select * from security_posture();
-- Every row must show ok = true.


-- ============================================================================
-- WHAT THE APPLICATION MUST STILL DO
-- ============================================================================
--
--   Connection string uses snm_app, not postgres, and not the pooler's
--   superuser. Put it in .env only.
--
--   Every request, inside its transaction, before any query:
--
--       SET LOCAL ROLE authenticated;
--       SELECT set_config('request.jwt.claims', '<the caller''s claims>', true);
--
--   SET LOCAL and the third argument 'true' both mean transaction-scoped.
--   Plain SET would persist on a pooled connection and leak one user's
--   identity into the next request. Use LOCAL. Always.
--
--   In Django: ATOMIC_REQUESTS = True and CONN_MAX_AGE = 0.
--   In FastAPI: one transaction per request, and never share a session
--   across requests.
--
--   Then write the test that proves it, and watch it fail before the
--   middleware exists:
--
--       def test_supervisor_cannot_read_costing(supervisor_client):
--           assert supervisor_client.get("/api/costing/").json() == []
--
--   If that test has never failed, it is not testing anything.
-- ============================================================================
