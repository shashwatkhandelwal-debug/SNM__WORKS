-- ============================================================================
-- SNM WORKS — DATABASE SCHEMA AND SECURITY
-- Swadeshi Niwar Mills, Kanpur
-- Run this ONCE in the Supabase SQL Editor, top to bottom.
-- ============================================================================
--
-- BEFORE YOU RUN THIS
--   1. Go to supabase.com, sign up, create a new project.
--   2. Choose region: South Asia (Mumbai)  ap-south-1
--   3. Save the database password somewhere safe. You cannot recover it.
--   4. Left sidebar > SQL Editor > New query. Paste this whole file. Run.
--
-- AFTER IT RUNS
--   Scroll to the very bottom of this file for the three follow-up steps.
-- ============================================================================


-- ============================================================================
-- SECTION 1 — ROLES AND PEOPLE
-- ============================================================================

create type app_role as enum ('owner','supervisor','qc','store','operator');

create table profiles (
  id          uuid primary key references auth.users on delete cascade,
  full_name   text not null,
  phone       text,
  role        app_role not null default 'operator',
  active      boolean not null default true,
  created_at  timestamptz not null default now()
);

comment on table profiles is
  'One row per person. Never share a login — the audit trail depends on it.';

-- Every new sign-up gets a profile automatically, as an operator.
-- The owner promotes them afterwards. New staff can never self-assign a role.
create or replace function handle_new_user()
returns trigger
language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (id, full_name, role, active)
  values (
    new.id,
    coalesce(new.raw_user_meta_data->>'full_name', split_part(new.email,'@',1)),
    'operator',
    true
  );
  return new;
end $$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function handle_new_user();

-- THE function the whole security model rests on.
-- Returns null for anyone signed out or deactivated, which fails every policy.
create or replace function auth_role()
returns app_role
language sql stable security definer set search_path = public as $$
  select role from profiles where id = auth.uid() and active = true
$$;


-- ============================================================================
-- SECTION 2 — REFERENCE LISTS
-- ============================================================================

create table customers (
  id         uuid primary key default gen_random_uuid(),
  name       text unique not null,
  email      text,
  contact    text,
  gstin      text,
  send_updates boolean not null default false,
  active     boolean not null default true
);

create table masters (
  list_name  text not null,           -- products, specs, machines, defects, ...
  value      text not null,
  sort_order int not null default 0,
  primary key (list_name, value)
);

create table param_library (
  id       bigserial primary key,
  family   text not null,             -- Narrow woven | Fabric | Cordage | Yarn
  name     text not null,
  unit     text,
  method   text                       -- FED-STD-191 4108, ISO 811, ...
);

-- MIL-W-4088K tables II and III. Reference data, read by everyone, edited by none.
create table mil_w_4088_types (
  type             text primary key,
  width_in         numeric not null,
  width_tol_in     numeric not null,
  thick_min_in     numeric not null,
  thick_max_in     numeric not null,
  weight_max_oz_yd numeric not null,
  break_min_lb     integer not null,
  ends_face_back   integer not null,
  ends_binder      integer not null default 0,
  picks_class1     integer not null,
  filling_1a2      integer not null,
  ply_warp         integer,
  ply_binder       integer,
  ply_filling      integer,
  yarn_class1      text,
  yarn_class1a2    text,
  weave            text,
  id_yarns         text,
  intended_use     text
);


-- ============================================================================
-- SECTION 3 — ENGINEERING
-- ============================================================================

create table constructions (
  id            uuid primary key default gen_random_uuid(),
  spec_no       text unique not null,
  created_on    date not null default current_date,
  revision      text not null default 'R0',
  status        text not null default 'Draft',
  family        text not null,
  product       text not null,
  spec          text,
  customer_id   uuid references customers(id),

  -- narrow woven
  width_mm      numeric,
  weave         text,
  warp_denier   numeric,
  weft_denier   numeric,
  warp_ends     integer,
  picks_per_cm  numeric,
  selvedge      text,

  -- broad fabric
  width_cm      numeric,
  epi           numeric,
  ppi           numeric,
  finish        text,

  -- cordage
  cord_type     text,
  diameter_mm   numeric,
  carriers      integer,
  yarns_per_carrier integer,
  core_yarns    integer,
  yarn_denier   numeric,
  tpm           numeric,
  contraction   numeric,

  -- shared
  warp_crimp    numeric,
  weft_crimp    numeric,
  warp_tenacity numeric,
  weft_tenacity numeric,
  efficiency    numeric default 85,
  notes         text,

  created_by    uuid references profiles(id),
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);


-- ============================================================================
-- SECTION 4 — PRODUCTION
-- ============================================================================

create table jobs (
  id            uuid primary key default gen_random_uuid(),
  job_no        text unique not null,          -- SNM/26-27/0001
  raised_on     date not null default current_date,
  customer_id   uuid references customers(id),
  po_ref        text,
  product       text not null,
  spec          text,
  construction_id uuid references constructions(id),
  width_mm      numeric,
  colour        text,
  qty_ordered   numeric not null check (qty_ordered > 0),
  unit          text not null default 'm',
  qty_produced  numeric not null default 0 check (qty_produced >= 0),
  machine       text,
  delivery_due  date,
  status        text not null default 'Planned',
  remarks       text,
  created_by    uuid references profiles(id),
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create index jobs_customer_idx on jobs(customer_id);
create index jobs_status_idx on jobs(status);

create table downtime (
  id          uuid primary key default gen_random_uuid(),
  log_no      text unique not null,
  logged_on   date not null default current_date,
  shift       text not null,
  machine     text not null,
  reason      text not null,
  minutes     numeric not null check (minutes >= 0),
  job_id      uuid references jobs(id),
  operator_id uuid references profiles(id),
  remarks     text,
  created_at  timestamptz not null default now()
);

create table despatch (
  id           uuid primary key default gen_random_uuid(),
  despatch_no  text unique not null,
  despatched_on date not null default current_date,
  job_id       uuid references jobs(id),
  invoice_no   text,
  eway_bill    text,
  qty          numeric,
  unit         text,
  rolls        integer,
  gross_wt     numeric,
  transporter  text,
  lr_no        text,
  destination  text,
  status       text not null default 'Packed',
  override_reason text,               -- required if the job was on QC hold
  created_by   uuid references profiles(id),
  created_at   timestamptz not null default now()
);


-- ============================================================================
-- SECTION 5 — QUALITY
-- ============================================================================

-- MIL specs mix limit kinds in one table: width is nominal ±, thickness is a
-- range, weight is a ceiling, breaking strength is a floor. One enum, four rules.
create type limit_kind as enum ('nominal','minimum','maximum','range');

create table qc_checks (
  id           uuid primary key default gen_random_uuid(),
  check_no     text unique not null,
  job_id       uuid references jobs(id) on delete cascade,
  checked_on   date not null default current_date,
  stage        text not null,
  family       text,
  parameter    text not null,
  unit         text,
  method       text,
  limit_type   limit_kind not null default 'nominal',
  spec_value   numeric not null,
  tolerance    numeric,
  upper_limit  numeric,
  actual       numeric,
  defect_code  text,
  action_taken text,
  inspector_id uuid references profiles(id),
  created_at   timestamptz not null default now()
);

create index qc_job_idx on qc_checks(job_id);

-- Verdict is computed HERE, not in the browser. A tampered client cannot
-- record a FAIL as a PASS, because the client never sends a verdict at all.
create or replace function qc_verdict(
  p_limit limit_kind, p_spec numeric, p_tol numeric,
  p_upper numeric, p_actual numeric
) returns text
language sql immutable as $$
  select case
    when p_actual is null then null
    when p_limit = 'minimum' then case when p_actual >= p_spec then 'PASS' else 'FAIL' end
    when p_limit = 'maximum' then case when p_actual <= p_spec then 'PASS' else 'FAIL' end
    when p_limit = 'range'   then case
        when p_upper is null then null
        when p_actual between p_spec and p_upper then 'PASS' else 'FAIL' end
    else case when abs(p_actual - p_spec) <= coalesce(p_tol,0) then 'PASS' else 'FAIL' end
  end
$$;

-- A generated column, so the verdict is stored and queryable, never guessed.
alter table qc_checks add column verdict text
  generated always as (qc_verdict(limit_type, spec_value, tolerance, upper_limit, actual)) stored;

create table lab_tests (
  id          uuid primary key default gen_random_uuid(),
  test_id     text unique not null,
  job_id      uuid references jobs(id) on delete cascade,
  tested_on   date not null default current_date,
  test_type   text not null,
  standard    text,
  lab         text,                    -- In-house, SASMIRA, SITRA, NITRA
  report_no   text,
  requirement text,
  result      text,
  unit        text,
  verdict     text not null default 'Pending' check (verdict in ('PASS','FAIL','Pending')),
  retest_ref  text,
  report_filed boolean not null default false,
  remarks     text,
  created_by  uuid references profiles(id),
  created_at  timestamptz not null default now()
);

create table capa (
  id          uuid primary key default gen_random_uuid(),
  capa_no     text unique not null,
  raised_on   date not null default current_date,
  source      text not null,
  reference   text,
  job_id      uuid references jobs(id),
  problem     text not null,
  root_cause  text,
  correction  text,
  preventive  text,
  owner_id    uuid references profiles(id),
  due_date    date,
  status      text not null default 'Open',
  verified_by uuid references profiles(id),
  created_at  timestamptz not null default now()
);

create table dye_recipes (
  id           uuid primary key default gen_random_uuid(),
  recipe_no    text unique not null,
  dyed_on      date not null default current_date,
  job_id       uuid references jobs(id),
  substrate    text,
  target_shade text,
  batch_kg     numeric,
  dye1 text, pct1 numeric,
  dye2 text, pct2 numeric,
  dye3 text, pct3 numeric,
  dye4 text, pct4 numeric,
  liquor_ratio text,
  temp_c       numeric,
  time_min     numeric,
  ph           numeric,
  auxiliaries  text,
  shade_result text,
  approved_by  uuid references profiles(id),
  created_at   timestamptz not null default now()
);

-- Is this job clear to despatch? One definition, used everywhere.
create or replace function job_on_hold(p_job uuid)
returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from qc_checks where job_id = p_job and verdict = 'FAIL')
      or exists (select 1 from lab_tests where job_id = p_job and verdict = 'FAIL')
$$;


-- ============================================================================
-- SECTION 6 — PRODUCTS AND CATALOGUE
-- ============================================================================

create table skus (
  id              uuid primary key default gen_random_uuid(),
  sku_code        text unique not null,
  created_on      date not null default current_date,
  family          text not null,
  product         text,
  title           text,
  standard        text,
  material        text,
  colour          text,
  construction_id uuid references constructions(id),
  blurb           text,
  extra_props     text,
  status          text not null default 'Draft',
  photo_path      text,               -- object key in the sku-photos bucket
  photo_at        timestamptz,
  published_at    timestamptz,
  created_by      uuid references profiles(id)
);


-- ============================================================================
-- SECTION 7 — COSTING  (owner only, enforced in the database)
-- ============================================================================

create table costing (
  job_id           uuid primary key references jobs(id) on delete cascade,
  qty              numeric,
  unit             text,
  yarn_rate        numeric,
  yarn_consumption numeric,
  wastage_pct      numeric,
  dyeing           numeric,
  coating          numeric,
  labour           numeric,
  overhead         numeric,
  packing          numeric,
  freight          numeric,
  margin_pct       numeric,
  updated_by       uuid references profiles(id),
  updated_at       timestamptz not null default now()
);


-- ============================================================================
-- SECTION 8 — AUDIT LOG  (append only, by design)
-- ============================================================================

create table audit_log (
  id         bigserial primary key,
  at         timestamptz not null default now(),
  actor_id   uuid,
  actor_name text,
  action     text not null,
  entity     text not null,
  entity_ref text,
  before     jsonb,
  after      jsonb
);

create index audit_at_idx on audit_log(at desc);

-- Written by triggers, never by the client. A client that can choose not to
-- log is not an audit trail.
create or replace function log_change()
returns trigger
language plpgsql security definer set search_path = public as $$
declare
  v_name text;
  v_ref  text;
begin
  select full_name into v_name from profiles where id = auth.uid();

  v_ref := coalesce(
    to_jsonb(coalesce(new, old)) ->> 'job_no',
    to_jsonb(coalesce(new, old)) ->> 'check_no',
    to_jsonb(coalesce(new, old)) ->> 'sku_code',
    to_jsonb(coalesce(new, old)) ->> 'spec_no',
    to_jsonb(coalesce(new, old)) ->> 'test_id',
    to_jsonb(coalesce(new, old)) ->> 'id'
  );

  insert into audit_log (actor_id, actor_name, action, entity, entity_ref, before, after)
  values (
    auth.uid(),
    coalesce(v_name, 'system'),
    lower(tg_op),
    tg_table_name,
    v_ref,
    case when tg_op = 'INSERT' then null else to_jsonb(old) end,
    case when tg_op = 'DELETE' then null else to_jsonb(new) end
  );
  return coalesce(new, old);
end $$;

create trigger audit_jobs        after insert or update or delete on jobs        for each row execute function log_change();
create trigger audit_qc          after insert or update or delete on qc_checks   for each row execute function log_change();
create trigger audit_tests       after insert or update or delete on lab_tests   for each row execute function log_change();
create trigger audit_capa        after insert or update or delete on capa        for each row execute function log_change();
create trigger audit_despatch    after insert or update or delete on despatch    for each row execute function log_change();
create trigger audit_costing     after insert or update or delete on costing     for each row execute function log_change();
create trigger audit_skus        after insert or update or delete on skus        for each row execute function log_change();
create trigger audit_constructions after insert or update or delete on constructions for each row execute function log_change();
create trigger audit_profiles    after insert or update or delete on profiles    for each row execute function log_change();


-- ============================================================================
-- SECTION 9 — KEEP updated_at HONEST
-- ============================================================================

create or replace function touch_updated_at()
returns trigger language plpgsql as $$
begin new.updated_at := now(); return new; end $$;

create trigger touch_jobs          before update on jobs          for each row execute function touch_updated_at();
create trigger touch_constructions before update on constructions for each row execute function touch_updated_at();
create trigger touch_costing       before update on costing       for each row execute function touch_updated_at();


-- ============================================================================
-- SECTION 10 — ROW LEVEL SECURITY
-- This is the actual security. Everything above is just tables.
-- ============================================================================

alter table profiles         enable row level security;
alter table customers        enable row level security;
alter table masters          enable row level security;
alter table param_library    enable row level security;
alter table mil_w_4088_types enable row level security;
alter table constructions    enable row level security;
alter table jobs             enable row level security;
alter table downtime         enable row level security;
alter table despatch         enable row level security;
alter table qc_checks        enable row level security;
alter table lab_tests        enable row level security;
alter table capa             enable row level security;
alter table dye_recipes      enable row level security;
alter table skus             enable row level security;
alter table costing          enable row level security;
alter table audit_log        enable row level security;

-- ---- profiles -------------------------------------------------------------
create policy profiles_self on profiles for select
  using (id = auth.uid() or auth_role() = 'owner');
create policy profiles_owner_write on profiles for all
  using (auth_role() = 'owner') with check (auth_role() = 'owner');

-- ---- reference data: everyone signed in reads, owner writes ---------------
create policy customers_read on customers for select using (auth_role() is not null);
create policy customers_write on customers for all
  using (auth_role() in ('owner','supervisor')) with check (auth_role() in ('owner','supervisor'));

create policy masters_read on masters for select using (auth_role() is not null);
create policy masters_write on masters for all
  using (auth_role() = 'owner') with check (auth_role() = 'owner');

create policy params_read on param_library for select using (auth_role() is not null);
create policy params_write on param_library for all
  using (auth_role() = 'owner') with check (auth_role() = 'owner');

create policy mil_read on mil_w_4088_types for select using (auth_role() is not null);
-- deliberately no write policy: the spec is not editable from the app

-- ---- production -----------------------------------------------------------
create policy jobs_read on jobs for select using (auth_role() is not null);
create policy jobs_insert on jobs for insert with check (auth_role() in ('owner','supervisor'));
create policy jobs_update on jobs for update using (auth_role() in ('owner','supervisor'));
-- no delete policy: a job card is cancelled, never deleted

create policy constructions_read on constructions for select using (auth_role() is not null);
create policy constructions_write on constructions for all
  using (auth_role() in ('owner','supervisor')) with check (auth_role() in ('owner','supervisor'));

create policy downtime_read on downtime for select
  using (auth_role() in ('owner','supervisor','operator'));
create policy downtime_write on downtime for insert
  with check (auth_role() in ('owner','supervisor','operator'));
create policy downtime_update on downtime for update
  using (auth_role() in ('owner','supervisor'));

create policy despatch_read on despatch for select
  using (auth_role() in ('owner','supervisor','store'));
create policy despatch_write on despatch for all
  using (auth_role() in ('owner','supervisor','store'))
  with check (auth_role() in ('owner','supervisor','store'));

-- ---- quality --------------------------------------------------------------
create policy qc_read on qc_checks for select using (auth_role() is not null);
create policy qc_insert on qc_checks for insert
  with check (auth_role() in ('owner','supervisor','qc','operator'));
create policy qc_update on qc_checks for update
  using (auth_role() in ('owner','supervisor','qc'));

create policy tests_read on lab_tests for select
  using (auth_role() in ('owner','supervisor','qc'));
create policy tests_write on lab_tests for all
  using (auth_role() in ('owner','qc')) with check (auth_role() in ('owner','qc'));

create policy capa_read on capa for select
  using (auth_role() in ('owner','supervisor','qc'));
create policy capa_write on capa for all
  using (auth_role() in ('owner','supervisor','qc'))
  with check (auth_role() in ('owner','supervisor','qc'));

create policy recipes_read on dye_recipes for select
  using (auth_role() in ('owner','supervisor','qc'));
create policy recipes_write on dye_recipes for all
  using (auth_role() in ('owner','supervisor'))
  with check (auth_role() in ('owner','supervisor'));

-- ---- products -------------------------------------------------------------
create policy skus_read on skus for select using (auth_role() is not null);
create policy skus_write on skus for all
  using (auth_role() in ('owner','supervisor','store'))
  with check (auth_role() in ('owner','supervisor','store'));

-- ---- costing: the one that matters ---------------------------------------
-- A supervisor calling the API directly with their own token gets zero rows.
create policy costing_owner_only on costing for all
  using (auth_role() = 'owner') with check (auth_role() = 'owner');

-- ---- audit log: append only ----------------------------------------------
-- Note there is NO update and NO delete policy. Not for owner, not for anyone.
-- History cannot be rewritten through the API.
create policy audit_read on audit_log for select
  using (auth_role() in ('owner','supervisor'));
create policy audit_insert on audit_log for insert
  with check (auth.uid() is not null);


-- ============================================================================
-- SECTION 11 — DID IT WORK?
-- ============================================================================

select
  (select count(*) from information_schema.tables
     where table_schema = 'public') as tables_created,
  (select count(*) from pg_policies
     where schemaname = 'public') as policies_created,
  (select count(*) from information_schema.triggers
     where trigger_schema = 'public') as triggers_created;

-- Expect roughly: 16 tables, 30+ policies, 12+ triggers.


-- ============================================================================
-- WHAT TO DO NEXT — three steps, in this order
-- ============================================================================
--
-- STEP 1 — Create your own login
--   Left sidebar > Authentication > Users > "Add user" > "Create new user".
--   Use your real email and a strong password. Tick "Auto Confirm User".
--
-- STEP 2 — Make yourself the owner
--   The trigger created you as an operator, which is correct and deliberate.
--   Come back to the SQL Editor and run, with your own email:
--
--     update profiles
--     set role = 'owner', full_name = 'Yash'
--     where id = (select id from auth.users where email = 'you@example.com');
--
--   Then confirm it:
--     select full_name, role, active from profiles;
--
-- STEP 3 — Get your keys, and understand the difference
--   Settings > API Keys.
--
--   Projects created since November 2025 use the NEW key format. You need:
--
--     Project URL                    https://xxxx.supabase.co
--     Publishable key   sb_publishable_...   goes in the app. Safe. RLS constrains it.
--     Secret key        sb_secret_...        NEVER in the app. Bypasses every
--                                            policy in this file.
--
--   (Older projects show "anon" and "service_role" instead. Same roles:
--    anon = publishable, service_role = secret.)
--
--   Send me the Project URL and the PUBLISHABLE key when you are ready for
--   the frontend. Never send the secret key to anyone, including me.
--
-- ============================================================================
