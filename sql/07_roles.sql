-- ============================================================================
-- SNM WORKS — FUNCTIONS, ROLES AND ROLE-ASSIGNED WORK
-- Run in the Supabase SQL Editor after 01–06.
-- ============================================================================
--
-- WHY THIS REPLACES profiles.role
--
--   A single role per person does not describe how the works actually runs.
--   Today one person holds Executive, Commercial, Technical, Knowledge and
--   Compliance at once. When you hire, those get redistributed.
--
--   So: a person holds MANY roles, and what they may do is the union of them.
--   Hiring becomes a row in user_roles — not a change to the application.
--
--   Work is assigned to a ROLE, never to a person. Whoever holds that role
--   sees it. Replace the holder and the work reroutes itself.
-- ============================================================================


-- ---------------------------------------------------------------------------
-- 1. The eleven functions
-- ---------------------------------------------------------------------------

create table if not exists functions (
  code        text primary key,
  name        text not null,
  mandate     text,
  sort_order  int not null default 0
);

insert into functions (code, name, mandate, sort_order) values
  ('EXEC','Executive','Decide what the business is for, and what it will not do.',1),
  ('OPS','Operating','Convert the order book into despatched goods, on time, at cost.',2),
  ('QUA','Quality','Guarantee what leaves the gate is what was promised, and provable afterwards.',3),
  ('TEC','Technical','Know how to make things, and make them better.',4),
  ('COM','Commercial','Fill the order book with work worth having.',5),
  ('SCM','Supply Chain','The right material, at the right time, of proven quality.',6),
  ('FIN','Financial','Know what things cost, and keep the business solvent.',7),
  ('PPL','People','Enough of the right people, competent and lawfully employed.',8),
  ('INF','Information','One version of the truth, available where the work happens.',9),
  ('KNW','Knowledge','Make what the business knows independent of who is in the building.',10),
  ('CMP','Compliance','Keep the permissions that let the business exist.',11)
on conflict (code) do update set name = excluded.name, mandate = excluded.mandate;


-- ---------------------------------------------------------------------------
-- 2. Roles within those functions
-- ---------------------------------------------------------------------------

create table if not exists roles (
  code         text primary key,
  function_code text not null references functions(code),
  name         text not null,
  level        text not null check (level in ('chief','manager','operative')),
  description  text,
  active       boolean not null default true
);

insert into roles (code, function_code, name, level, description) values
  ('chief_executive','EXEC','Chief Executive','chief','Direction, capital, key relationships'),
  ('chief_operating','OPS','Chief Operating','chief','Plant, capacity, delivery'),
  ('production_manager','OPS','Production Manager','manager','Scheduling, loom allocation, output'),
  ('shift_supervisor','OPS','Shift Supervisor','operative','Runs a shift, logs downtime, in-process checks'),
  ('machine_operator','OPS','Machine Operator','operative','Runs machines, records readings'),
  ('maintenance_officer','OPS','Maintenance Officer','operative','Breakdowns, preventive schedule, spares'),
  ('chief_quality','QUA','Chief Quality','chief','Releases lots, owns specifications and audits'),
  ('qa_manager','QUA','QA Manager','manager','Inspection plans, CAPA, supplier quality'),
  ('lab_analyst','QUA','Laboratory Analyst','operative','Runs end item tests, records results'),
  ('line_inspector','QUA','Line Inspector','operative','In-process and final visual inspection'),
  ('chief_technical','TEC','Chief Technical','chief','Constructions, process routes, development'),
  ('product_developer','TEC','Product Developer','manager','Trial constructions, samples, lab dips'),
  ('process_engineer','TEC','Process Engineer','manager','Dyeing, coating, finishing parameters'),
  ('chief_commercial','COM','Chief Commercial','chief','Pricing, tenders, customer relationships'),
  ('sales_executive','COM','Sales Executive','operative','Enquiries, quotations, order follow-up'),
  ('export_executive','COM','Export Executive','operative','Export documentation, shipments, incentives'),
  ('chief_supply_chain','SCM','Chief Supply Chain','chief','Suppliers, inventory policy, imports'),
  ('purchase_officer','SCM','Purchase Officer','operative','Purchase orders, supplier follow-up'),
  ('store_keeper','SCM','Store Keeper','operative','Goods receipt, lot numbers, issues, despatch'),
  ('chief_financial','FIN','Chief Financial','chief','Costing standards, working capital, statutory'),
  ('accounts_officer','FIN','Accounts Officer','operative','Books, invoicing, GST, receivables'),
  ('costing_analyst','FIN','Costing Analyst','manager','Cost sheets, variance, price support'),
  ('chief_people','PPL','Chief People','chief','Manning, competency, statutory compliance'),
  ('hr_officer','PPL','HR Officer','operative','Attendance, wages, records, training'),
  ('chief_information','INF','Chief Information','chief','Systems, access, data, backups'),
  ('system_admin','INF','System Administrator','manager','Accounts, access grants, backup checks'),
  ('chief_knowledge','KNW','Chief Knowledge','chief','Specification masterbase, SOPs, training'),
  ('document_controller','KNW','Document Controller','operative','Revision control, distribution, retirement'),
  ('chief_compliance','CMP','Chief Compliance','chief','Registrations, licences, contract obligations')
on conflict (code) do update set
  function_code = excluded.function_code, name = excluded.name,
  level = excluded.level, description = excluded.description;


-- ---------------------------------------------------------------------------
-- 3. Who holds which roles — many to many
-- ---------------------------------------------------------------------------

create table if not exists user_roles (
  user_id    uuid not null references profiles(id) on delete cascade,
  role_code  text not null references roles(code),
  granted_at timestamptz not null default now(),
  granted_by uuid references profiles(id),
  active     boolean not null default true,
  primary key (user_id, role_code)
);

create index if not exists user_roles_user_idx on user_roles(user_id) where active;


-- ---------------------------------------------------------------------------
-- 4. What each role may do — permissions as data, not code
-- ---------------------------------------------------------------------------

create table if not exists role_permissions (
  role_code text not null references roles(code) on delete cascade,
  module    text not null,
  action    text not null check (action in ('read','create','update','approve','release')),
  primary key (role_code, module, action)
);

insert into role_permissions (role_code, module, action) values
  ('chief_executive','jobs','read'),
  ('chief_executive','qc','read'),
  ('chief_executive','tests','read'),
  ('chief_executive','capa','read'),
  ('chief_executive','recipes','read'),
  ('chief_executive','constructions','read'),
  ('chief_executive','specifications','read'),
  ('chief_executive','skus','read'),
  ('chief_executive','customers','read'),
  ('chief_executive','costing','read'),
  ('chief_executive','costing','create'),
  ('chief_executive','costing','update'),
  ('chief_executive','costing','approve'),
  ('chief_executive','downtime','read'),
  ('chief_executive','despatch','read'),
  ('chief_executive','stock','read'),
  ('chief_executive','purchase','read'),
  ('chief_executive','people','read'),
  ('chief_executive','tasks','read'),
  ('chief_executive','tasks','create'),
  ('chief_executive','tasks','update'),
  ('chief_executive','tasks','approve'),
  ('chief_executive','systems','read'),
  ('chief_executive','audit','read'),
  ('chief_operating','jobs','read'),
  ('chief_operating','jobs','create'),
  ('chief_operating','jobs','update'),
  ('chief_operating','jobs','approve'),
  ('chief_operating','downtime','read'),
  ('chief_operating','downtime','create'),
  ('chief_operating','downtime','update'),
  ('chief_operating','despatch','read'),
  ('chief_operating','despatch','create'),
  ('chief_operating','despatch','update'),
  ('chief_operating','qc','read'),
  ('chief_operating','tests','read'),
  ('chief_operating','capa','read'),
  ('chief_operating','capa','create'),
  ('chief_operating','capa','update'),
  ('chief_operating','constructions','read'),
  ('chief_operating','specifications','read'),
  ('chief_operating','stock','read'),
  ('chief_operating','tasks','read'),
  ('chief_operating','tasks','create'),
  ('chief_operating','tasks','update'),
  ('chief_operating','tasks','approve'),
  ('chief_operating','skus','read'),
  ('production_manager','jobs','read'),
  ('production_manager','jobs','create'),
  ('production_manager','jobs','update'),
  ('production_manager','downtime','read'),
  ('production_manager','downtime','create'),
  ('production_manager','downtime','update'),
  ('production_manager','despatch','read'),
  ('production_manager','despatch','create'),
  ('production_manager','qc','read'),
  ('production_manager','constructions','read'),
  ('production_manager','specifications','read'),
  ('production_manager','stock','read'),
  ('production_manager','tasks','read'),
  ('production_manager','tasks','create'),
  ('production_manager','tasks','update'),
  ('shift_supervisor','jobs','read'),
  ('shift_supervisor','jobs','update'),
  ('shift_supervisor','qc','read'),
  ('shift_supervisor','qc','create'),
  ('shift_supervisor','downtime','read'),
  ('shift_supervisor','downtime','create'),
  ('shift_supervisor','constructions','read'),
  ('shift_supervisor','specifications','read'),
  ('shift_supervisor','tasks','read'),
  ('shift_supervisor','tasks','update'),
  ('machine_operator','jobs','read'),
  ('machine_operator','qc','read'),
  ('machine_operator','qc','create'),
  ('machine_operator','downtime','read'),
  ('machine_operator','downtime','create'),
  ('machine_operator','tasks','read'),
  ('machine_operator','tasks','update'),
  ('maintenance_officer','downtime','read'),
  ('maintenance_officer','downtime','create'),
  ('maintenance_officer','downtime','update'),
  ('maintenance_officer','tasks','read'),
  ('maintenance_officer','tasks','create'),
  ('maintenance_officer','tasks','update'),
  ('chief_quality','qc','read'),
  ('chief_quality','qc','create'),
  ('chief_quality','qc','update'),
  ('chief_quality','qc','approve'),
  ('chief_quality','tests','read'),
  ('chief_quality','tests','create'),
  ('chief_quality','tests','update'),
  ('chief_quality','tests','approve'),
  ('chief_quality','tests','release'),
  ('chief_quality','capa','read'),
  ('chief_quality','capa','create'),
  ('chief_quality','capa','update'),
  ('chief_quality','capa','approve'),
  ('chief_quality','specifications','read'),
  ('chief_quality','specifications','create'),
  ('chief_quality','specifications','update'),
  ('chief_quality','specifications','approve'),
  ('chief_quality','constructions','read'),
  ('chief_quality','jobs','read'),
  ('chief_quality','despatch','read'),
  ('chief_quality','despatch','approve'),
  ('chief_quality','recipes','read'),
  ('chief_quality','tasks','read'),
  ('chief_quality','tasks','create'),
  ('chief_quality','tasks','update'),
  ('chief_quality','tasks','approve'),
  ('chief_quality','audit','read'),
  ('qa_manager','qc','read'),
  ('qa_manager','qc','create'),
  ('qa_manager','qc','update'),
  ('qa_manager','tests','read'),
  ('qa_manager','tests','create'),
  ('qa_manager','tests','update'),
  ('qa_manager','capa','read'),
  ('qa_manager','capa','create'),
  ('qa_manager','capa','update'),
  ('qa_manager','specifications','read'),
  ('qa_manager','specifications','create'),
  ('qa_manager','specifications','update'),
  ('qa_manager','jobs','read'),
  ('qa_manager','constructions','read'),
  ('qa_manager','tasks','read'),
  ('qa_manager','tasks','create'),
  ('qa_manager','tasks','update'),
  ('lab_analyst','tests','read'),
  ('lab_analyst','tests','create'),
  ('lab_analyst','tests','update'),
  ('lab_analyst','qc','read'),
  ('lab_analyst','specifications','read'),
  ('lab_analyst','tasks','read'),
  ('lab_analyst','tasks','update'),
  ('line_inspector','qc','read'),
  ('line_inspector','qc','create'),
  ('line_inspector','qc','update'),
  ('line_inspector','specifications','read'),
  ('line_inspector','jobs','read'),
  ('line_inspector','tasks','read'),
  ('line_inspector','tasks','update'),
  ('chief_technical','constructions','read'),
  ('chief_technical','constructions','create'),
  ('chief_technical','constructions','update'),
  ('chief_technical','constructions','approve'),
  ('chief_technical','recipes','read'),
  ('chief_technical','recipes','create'),
  ('chief_technical','recipes','update'),
  ('chief_technical','recipes','approve'),
  ('chief_technical','specifications','read'),
  ('chief_technical','specifications','create'),
  ('chief_technical','specifications','update'),
  ('chief_technical','qc','read'),
  ('chief_technical','tests','read'),
  ('chief_technical','capa','read'),
  ('chief_technical','capa','create'),
  ('chief_technical','capa','update'),
  ('chief_technical','jobs','read'),
  ('chief_technical','skus','read'),
  ('chief_technical','skus','create'),
  ('chief_technical','skus','update'),
  ('chief_technical','tasks','read'),
  ('chief_technical','tasks','create'),
  ('chief_technical','tasks','update'),
  ('chief_technical','tasks','approve'),
  ('product_developer','constructions','read'),
  ('product_developer','constructions','create'),
  ('product_developer','constructions','update'),
  ('product_developer','recipes','read'),
  ('product_developer','recipes','create'),
  ('product_developer','recipes','update'),
  ('product_developer','skus','read'),
  ('product_developer','skus','create'),
  ('product_developer','skus','update'),
  ('product_developer','specifications','read'),
  ('product_developer','tests','read'),
  ('product_developer','tasks','read'),
  ('product_developer','tasks','create'),
  ('product_developer','tasks','update'),
  ('process_engineer','recipes','read'),
  ('process_engineer','recipes','create'),
  ('process_engineer','recipes','update'),
  ('process_engineer','constructions','read'),
  ('process_engineer','constructions','update'),
  ('process_engineer','qc','read'),
  ('process_engineer','specifications','read'),
  ('process_engineer','tasks','read'),
  ('process_engineer','tasks','create'),
  ('process_engineer','tasks','update'),
  ('chief_commercial','customers','read'),
  ('chief_commercial','customers','create'),
  ('chief_commercial','customers','update'),
  ('chief_commercial','customers','approve'),
  ('chief_commercial','skus','read'),
  ('chief_commercial','skus','create'),
  ('chief_commercial','skus','update'),
  ('chief_commercial','jobs','read'),
  ('chief_commercial','jobs','create'),
  ('chief_commercial','despatch','read'),
  ('chief_commercial','specifications','read'),
  ('chief_commercial','tasks','read'),
  ('chief_commercial','tasks','create'),
  ('chief_commercial','tasks','update'),
  ('chief_commercial','tasks','approve'),
  ('sales_executive','customers','read'),
  ('sales_executive','customers','create'),
  ('sales_executive','customers','update'),
  ('sales_executive','jobs','read'),
  ('sales_executive','jobs','create'),
  ('sales_executive','skus','read'),
  ('sales_executive','tasks','read'),
  ('sales_executive','tasks','create'),
  ('sales_executive','tasks','update'),
  ('export_executive','customers','read'),
  ('export_executive','customers','update'),
  ('export_executive','despatch','read'),
  ('export_executive','despatch','create'),
  ('export_executive','despatch','update'),
  ('export_executive','jobs','read'),
  ('export_executive','tasks','read'),
  ('export_executive','tasks','create'),
  ('export_executive','tasks','update'),
  ('chief_supply_chain','purchase','read'),
  ('chief_supply_chain','purchase','create'),
  ('chief_supply_chain','purchase','update'),
  ('chief_supply_chain','purchase','approve'),
  ('chief_supply_chain','stock','read'),
  ('chief_supply_chain','stock','create'),
  ('chief_supply_chain','stock','update'),
  ('chief_supply_chain','stock','approve'),
  ('chief_supply_chain','customers','read'),
  ('chief_supply_chain','jobs','read'),
  ('chief_supply_chain','tasks','read'),
  ('chief_supply_chain','tasks','create'),
  ('chief_supply_chain','tasks','update'),
  ('chief_supply_chain','tasks','approve'),
  ('purchase_officer','purchase','read'),
  ('purchase_officer','purchase','create'),
  ('purchase_officer','purchase','update'),
  ('purchase_officer','stock','read'),
  ('purchase_officer','tasks','read'),
  ('purchase_officer','tasks','create'),
  ('purchase_officer','tasks','update'),
  ('store_keeper','stock','read'),
  ('store_keeper','stock','create'),
  ('store_keeper','stock','update'),
  ('store_keeper','despatch','read'),
  ('store_keeper','despatch','create'),
  ('store_keeper','despatch','update'),
  ('store_keeper','jobs','read'),
  ('store_keeper','purchase','read'),
  ('store_keeper','tasks','read'),
  ('store_keeper','tasks','update'),
  ('chief_financial','costing','read'),
  ('chief_financial','costing','create'),
  ('chief_financial','costing','update'),
  ('chief_financial','costing','approve'),
  ('chief_financial','customers','read'),
  ('chief_financial','customers','update'),
  ('chief_financial','jobs','read'),
  ('chief_financial','despatch','read'),
  ('chief_financial','purchase','read'),
  ('chief_financial','purchase','approve'),
  ('chief_financial','tasks','read'),
  ('chief_financial','tasks','create'),
  ('chief_financial','tasks','update'),
  ('chief_financial','tasks','approve'),
  ('accounts_officer','despatch','read'),
  ('accounts_officer','despatch','update'),
  ('accounts_officer','customers','read'),
  ('accounts_officer','purchase','read'),
  ('accounts_officer','tasks','read'),
  ('accounts_officer','tasks','update'),
  ('costing_analyst','costing','read'),
  ('costing_analyst','costing','create'),
  ('costing_analyst','costing','update'),
  ('costing_analyst','jobs','read'),
  ('costing_analyst','constructions','read'),
  ('costing_analyst','tasks','read'),
  ('costing_analyst','tasks','create'),
  ('costing_analyst','tasks','update'),
  ('chief_people','people','read'),
  ('chief_people','people','create'),
  ('chief_people','people','update'),
  ('chief_people','people','approve'),
  ('chief_people','tasks','read'),
  ('chief_people','tasks','create'),
  ('chief_people','tasks','update'),
  ('chief_people','tasks','approve'),
  ('chief_people','downtime','read'),
  ('hr_officer','people','read'),
  ('hr_officer','people','create'),
  ('hr_officer','people','update'),
  ('hr_officer','tasks','read'),
  ('hr_officer','tasks','update'),
  ('chief_information','systems','read'),
  ('chief_information','systems','create'),
  ('chief_information','systems','update'),
  ('chief_information','systems','approve'),
  ('chief_information','audit','read'),
  ('chief_information','people','read'),
  ('chief_information','tasks','read'),
  ('chief_information','tasks','create'),
  ('chief_information','tasks','update'),
  ('chief_information','tasks','approve'),
  ('system_admin','systems','read'),
  ('system_admin','systems','create'),
  ('system_admin','systems','update'),
  ('system_admin','audit','read'),
  ('system_admin','tasks','read'),
  ('system_admin','tasks','update'),
  ('chief_knowledge','specifications','read'),
  ('chief_knowledge','specifications','create'),
  ('chief_knowledge','specifications','update'),
  ('chief_knowledge','specifications','approve'),
  ('chief_knowledge','constructions','read'),
  ('chief_knowledge','skus','read'),
  ('chief_knowledge','tasks','read'),
  ('chief_knowledge','tasks','create'),
  ('chief_knowledge','tasks','update'),
  ('chief_knowledge','tasks','approve'),
  ('document_controller','specifications','read'),
  ('document_controller','specifications','create'),
  ('document_controller','specifications','update'),
  ('document_controller','tasks','read'),
  ('document_controller','tasks','update'),
  ('chief_compliance','specifications','read'),
  ('chief_compliance','audit','read'),
  ('chief_compliance','despatch','read'),
  ('chief_compliance','customers','read'),
  ('chief_compliance','tasks','read'),
  ('chief_compliance','tasks','create'),
  ('chief_compliance','tasks','update'),
  ('chief_compliance','tasks','approve')
on conflict do nothing;


-- ---------------------------------------------------------------------------
-- 5. Segregation of duties
-- ---------------------------------------------------------------------------
-- Some pairs should not sit with one person. At your present size some of
-- these are unavoidable, so holding them is permitted but flagged, and the
-- real protection is enforced per record: you cannot approve what you entered.

create table if not exists role_conflicts (
  role_a  text not null references roles(code),
  role_b  text not null references roles(code),
  reason  text not null,
  severity text not null default 'warn' check (severity in ('warn','block')),
  primary key (role_a, role_b)
);

insert into role_conflicts (role_a, role_b, reason, severity) values
  ('chief_operating','chief_quality',
   'The person judged on despatching must not be the person who can stop a despatch','warn'),
  ('lab_analyst','chief_quality',
   'A test result must be approved by someone other than whoever ran it','warn'),
  ('chief_commercial','chief_financial',
   'Sales sets price, finance sets the floor. One holder and the floor moves','warn'),
  ('purchase_officer','store_keeper',
   'Ordering and receiving in one pair of hands removes the check on both','warn'),
  ('purchase_officer','accounts_officer',
   'Ordering and paying in one pair of hands removes the check on both','block')
on conflict do nothing;


-- ---------------------------------------------------------------------------
-- 6. The functions the whole system asks
-- ---------------------------------------------------------------------------

-- every role a person currently holds
create or replace function my_roles()
returns setof text
language sql stable security definer set search_path = public as $$
  select ur.role_code
  from user_roles ur
  join profiles p on p.id = ur.user_id
  where ur.user_id = auth.uid() and ur.active and p.active
$$;

-- the union of everything those roles permit
create or replace function auth_can(p_module text, p_action text)
returns boolean
language sql stable security definer set search_path = public as $$
  select exists (
    select 1
    from user_roles ur
    join role_permissions rp on rp.role_code = ur.role_code
    join profiles p on p.id = ur.user_id
    where ur.user_id = auth.uid()
      and ur.active and p.active
      and rp.module = p_module
      and rp.action = p_action
  )
$$;

create or replace function has_role(p_role text)
returns boolean
language sql stable security definer set search_path = public as $$
  select exists (
    select 1 from user_roles ur join profiles p on p.id = ur.user_id
    where ur.user_id = auth.uid() and ur.role_code = p_role
      and ur.active and p.active
  )
$$;

grant execute on function my_roles(), auth_can(text,text), has_role(text) to authenticated;


-- ---------------------------------------------------------------------------
-- 7. Work assigned to roles, not people
-- ---------------------------------------------------------------------------

create table if not exists tasks (
  id            uuid primary key default gen_random_uuid(),
  title         text not null,
  detail        text,
  assigned_role text references roles(code),
  assigned_user uuid references profiles(id),      -- optional, once picked up
  raised_by     uuid references profiles(id),
  source_table  text,                               -- qc_checks, lab_tests, jobs
  source_id     uuid,
  due_on        date,
  priority      text not null default 'normal'
                  check (priority in ('low','normal','high','stop-the-line')),
  status        text not null default 'open'
                  check (status in ('open','in progress','blocked','done','cancelled')),
  closed_at     timestamptz,
  created_at    timestamptz not null default now()
);

create index if not exists tasks_role_idx on tasks(assigned_role) where status <> 'done';

-- what is on my desk, across every role I hold
create or replace function my_tasks()
returns setof tasks
language sql stable security definer set search_path = public as $$
  select t.* from tasks t
  where t.status not in ('done','cancelled')
    and (t.assigned_user = auth.uid()
         or t.assigned_role in (select my_roles()))
  order by
    case t.priority when 'stop-the-line' then 0 when 'high' then 1
                    when 'normal' then 2 else 3 end,
    t.due_on nulls last, t.created_at
$$;

grant execute on function my_tasks() to authenticated;


-- ---------------------------------------------------------------------------
-- 8. Automatic handover between functions
-- ---------------------------------------------------------------------------
-- A failing QC reading is Quality's problem to investigate and Technical's to
-- fix. Nobody has to remember to tell them.

create or replace function on_qc_fail()
returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if new.verdict = 'FAIL' and (old is null or old.verdict is distinct from 'FAIL') then
    insert into tasks (title, detail, assigned_role, raised_by,
                       source_table, source_id, priority, due_on)
    values (
      'Investigate failed check ' || new.check_no,
      new.parameter || ' read ' || coalesce(new.actual::text,'?') ||
        ' against a requirement of ' || new.spec_value::text ||
        coalesce(' ' || new.unit, ''),
      'qa_manager', auth.uid(), 'qc_checks', new.id,
      'high', current_date + 1
    );
  end if;
  return new;
end $$;

drop trigger if exists qc_fail_task on qc_checks;
create trigger qc_fail_task
  after insert or update on qc_checks
  for each row execute function on_qc_fail();

create or replace function on_test_fail()
returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if new.verdict = 'FAIL' and (old is null or old.verdict is distinct from 'FAIL') then
    insert into tasks (title, detail, assigned_role, raised_by,
                       source_table, source_id, priority, due_on)
    values (
      'Raise corrective action for test ' || new.test_id,
      coalesce(new.test_type,'Test') || ' failed against ' ||
        coalesce(new.standard,'specification') ||
        '. Lot cannot be released until closed.',
      'chief_quality', auth.uid(), 'lab_tests', new.id,
      'stop-the-line', current_date
    );
  end if;
  return new;
end $$;

drop trigger if exists test_fail_task on lab_tests;
create trigger test_fail_task
  after insert or update on lab_tests
  for each row execute function on_test_fail();


-- ---------------------------------------------------------------------------
-- 9. Security on the new tables
-- ---------------------------------------------------------------------------

alter table functions        enable row level security;
alter table roles            enable row level security;
alter table user_roles       enable row level security;
alter table role_permissions enable row level security;
alter table role_conflicts   enable row level security;
alter table tasks            enable row level security;

create policy functions_read   on functions   for select using (auth.uid() is not null);
create policy roles_read       on roles       for select using (auth.uid() is not null);
create policy perms_read       on role_permissions for select using (auth.uid() is not null);
create policy conflicts_read   on role_conflicts   for select using (auth.uid() is not null);

-- you can see your own roles; the People and Information functions see all
create policy user_roles_read on user_roles for select
  using (user_id = auth.uid() or auth_can('people','read') or auth_can('systems','read'));

create policy user_roles_write on user_roles for all
  using (auth_can('people','approve') or auth_can('systems','approve'))
  with check (auth_can('people','approve') or auth_can('systems','approve'));

create policy tasks_read on tasks for select
  using (auth.uid() is not null);
create policy tasks_write on tasks for insert
  with check (auth_can('tasks','create'));
create policy tasks_update on tasks for update
  using (auth_can('tasks','update'));

create trigger audit_user_roles after insert or update or delete on user_roles
  for each row execute function log_change();


-- ---------------------------------------------------------------------------
-- 10. Give yourself every chief role, for now
-- ---------------------------------------------------------------------------

insert into user_roles (user_id, role_code)
select p.id, r.code
from profiles p
cross join roles r
where p.role = 'owner' and r.level = 'chief'
on conflict do nothing;


-- ---------------------------------------------------------------------------
-- 11. Check
-- ---------------------------------------------------------------------------

select
  (select count(*) from functions)        as functions,
  (select count(*) from roles)            as roles,
  (select count(*) from role_permissions) as permissions,
  (select count(*) from user_roles)       as roles_held,
  (select count(*) from role_conflicts)   as conflicts;

-- who holds what
select p.full_name, f.name as function, r.name as role
from user_roles ur
join profiles p on p.id = ur.user_id
join roles r on r.code = ur.role_code
join functions f on f.code = r.function_code
order by f.sort_order, r.level;

-- Expect: 11 functions, 29 roles, 336 permissions,
--         11 roles held by you, 5 conflicts defined.
-- ============================================================================
