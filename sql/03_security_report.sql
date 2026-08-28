-- ============================================================================
-- SNM WORKS — SECURITY REPORT
-- Run in the Supabase SQL Editor, after 01 and 02.
-- ============================================================================
--
-- Adds a function that reports, from inside the database, which tables have
-- row level security switched on and which commands each one permits.
--
-- This is better than probing from the app. A blocked DELETE does not raise
-- an error in Postgres — it silently affects zero rows — so an app-side test
-- can easily conclude the opposite of the truth. Asking the catalogue directly
-- cannot be fooled.
-- ============================================================================

create or replace function rls_report()
returns table (
  tbl           text,
  rls_on        boolean,
  select_rules  int,
  insert_rules  int,
  update_rules  int,
  delete_rules  int
)
language plpgsql
stable
security definer
set search_path = public as $$
begin
  -- policy layout is owner-only information
  if auth_role() is distinct from 'owner' then
    raise exception 'Only the owner may read the security report';
  end if;

  return query
  select
    c.relname::text,
    c.relrowsecurity,
    count(p.policyname) filter (where p.cmd in ('SELECT','ALL'))::int,
    count(p.policyname) filter (where p.cmd in ('INSERT','ALL'))::int,
    count(p.policyname) filter (where p.cmd in ('UPDATE','ALL'))::int,
    count(p.policyname) filter (where p.cmd in ('DELETE','ALL'))::int
  from pg_class c
  join pg_namespace n on n.oid = c.relnamespace
  left join pg_policies p
    on p.schemaname = 'public' and p.tablename = c.relname
  where n.nspname = 'public'
    and c.relkind = 'r'
  group by c.relname, c.relrowsecurity
  order by c.relname;
end $$;

revoke all on function rls_report() from public;
grant execute on function rls_report() to authenticated;


-- ============================================================================
-- Look at it now
-- ============================================================================

select * from rls_report();

-- What you should see:
--
--   rls_on = true on EVERY row. A false anywhere means that table is readable
--   by anyone holding your publishable key, which is public by design.
--
--   audit_log: delete_rules = 0 and update_rules = 0.
--   That is the append-only guarantee. No policy exists, so no role can
--   delete or alter an audit row through the API — not supervisors, not you.
--
--   jobs: delete_rules = 0. Job cards are cancelled, never deleted.
--
--   mil_w_4088_types: insert/update/delete all 0. The specification is
--   reference data, not something the app can edit.
-- ============================================================================
