-- ════════════════════════════════════════════════════════════════════
--  BACKLOG REVIEW — Supabase schema (multi-source, workstation identity)
-- ════════════════════════════════════════════════════════════════════
--  Run once in the Supabase SQL Editor. Prefix bl_ keeps these separate
--  from the inventory-intelligence tables in the same project.
--
--  Model: read-only ORDER DATA comes from the daily extract (BC/DDI) and
--  is loaded into the app; only USER STATE lives here — the review
--  decisions, pacing inputs, snapshots, and an audit trail. Reviews are
--  keyed by document_no and persist across daily refreshes automatically
--  (this replaces refresh_backlog_review.py's "preserve by Document No").
--
--  Identity: reviewed_by / changed_by = the Windows workstation user
--  (e.g. FLUIDFLOW\JOHNBAUMANN), captured via server.py /whoami. Anyone
--  may edit any order; every edit records who + when.
--
--  RLS: enabled on every table with a permissive anon starter policy
--  (trusted internal team). Tighten to auth.uid() scopes if/when we add
--  Supabase Auth.
-- ════════════════════════════════════════════════════════════════════

create or replace function bl_set_updated_at()
returns trigger language plpgsql as $$
begin new.updated_at = now(); return new; end; $$;

-- ── Review decisions (one row per order) ─────────────────────────────
create table if not exists bl_order_review (
  document_no        text primary key,
  source_system      text,                    -- INDELCO_BC | CENTREX_DDI | QS
  ship_this_month    text,                    -- Yes | No | '' (unreviewed)
  ship_fiscal_year   text,
  material_ordered   text,
  if_not_shipping    text,
  carryover_notes    text,
  reviewed_ship_date date,                     -- push-ready for future BC write-back
  review_date        date,
  owner_initials     text,
  reviewed_by        text,                     -- workstation user who last reviewed
  updated_at         timestamptz not null default now(),
  updated_by         text
);

-- ── Pacing inputs (per period × budget group) ────────────────────────
create table if not exists bl_pacing_input (
  period           text not null,              -- 'YYYY-MM'
  budget_group     text not null,              -- MN | Branches | QS | Ayer | CorrTech | Centrex
  monthly_budget   double precision default 0,
  monthly_forecast double precision default 0,
  current_sni      double precision default 0, -- shipped not invoiced
  notes            text,
  updated_at       timestamptz not null default now(),
  updated_by       text,
  primary key (period, budget_group)
);

-- ── Daily invoiced shipments (drives the run-rate/pace) ──────────────
create table if not exists bl_daily_invoiced (
  invoice_date  date not null,
  budget_group  text not null,
  amount        double precision default 0,
  updated_at    timestamptz not null default now(),
  updated_by    text,
  primary key (invoice_date, budget_group)
);

-- ── Location → Budget Group map (maintainable config) ────────────────
create table if not exists bl_budget_group_map (
  location_code text primary key,
  location_name text,
  budget_group  text
);

-- ── Dated projection snapshots (month-end tracking / history) ────────
create table if not exists bl_snapshot (
  id                  bigint generated always as identity primary key,
  snapshot_date       date not null default current_date,
  budget_group        text not null,
  budget              double precision,
  forecast            double precision,
  actual_invoiced_mtd double precision,
  current_sni         double precision,
  confirmed_backlog   double precision,
  projected_month_end double precision,
  gap_to_forecast     double precision,
  gap_to_budget       double precision,
  created_at          timestamptz not null default now(),
  unique (snapshot_date, budget_group)
);

-- ── Change audit (who changed what, when) ────────────────────────────
create table if not exists bl_audit (
  id           bigint generated always as identity primary key,
  document_no  text,
  field        text,
  old_value    text,
  new_value    text,
  changed_by   text,
  changed_at   timestamptz not null default now()
);
create index if not exists idx_bl_audit_doc on bl_audit(document_no, changed_at);

-- ── updated_at triggers ──────────────────────────────────────────────
do $$
declare t text;
begin
  foreach t in array array[
    'bl_order_review','bl_pacing_input','bl_daily_invoiced'
  ] loop
    execute format(
      'drop trigger if exists trg_%1$s_upd on %1$s;
       create trigger trg_%1$s_upd before update on %1$s
       for each row execute function bl_set_updated_at();', t);
  end loop;
end $$;

-- ── Row-Level Security (permissive starter — tighten with Auth) ──────
do $$
declare t text;
begin
  foreach t in array array[
    'bl_order_review','bl_pacing_input','bl_daily_invoiced',
    'bl_budget_group_map','bl_snapshot','bl_audit'
  ] loop
    execute format('alter table %I enable row level security;', t);
    execute format('drop policy if exists anon_all on %I;', t);
    execute format(
      'create policy anon_all on %I for all to anon using (true) with check (true);', t);
  end loop;
end $$;

-- ── Seed the budget-group map from the workbook's Budget_Group_Map ────
insert into bl_budget_group_map (location_code, location_name, budget_group) values
  ('00','Indelco-Minneapolis','MN'),      ('01','Indelco-Denver','Branches'),
  ('02','Indelco-Chicago','Branches'),    ('03','Indelco-Nebraska','Branches'),
  ('04','Indelco - Wisconsin','Branches'),('05','Indelco - Euclid','Branches'),
  ('06','Indelco-St Louis','Branches'),   ('07','Indelco-Kansas City','Branches'),
  ('08','Indelco-Louisville','Branches'), ('09','Indelco-Decatur','Branches'),
  ('10','Indelco-Fab','MN'),              ('11','Indelco-Indiana','Branches'),
  ('12','Indelco - Memphis','Branches'),  ('14','Indelco-Michigan','Branches'),
  ('21','Corr Tech - Dallas TX','CorrTech'),   ('22','Corr Tech - Houston','CorrTech'),
  ('23','Corr Tech - Gonzales LA','CorrTech'), ('24','Corr Tech - Sulphur LA','CorrTech'),
  ('25','Corr Tech - Austin TX','CorrTech'),   ('27','Corr Tech - San Antonio','CorrTech'),
  ('35','Ayer Sales - Woburn','Ayer'),    ('36','Ayer Sales - Selkirk','Ayer'),
  ('37','Ayer Sales - Syracuse','Ayer'),  ('40','Quality Stainless','QS')
on conflict (location_code) do update set
  location_name = excluded.location_name, budget_group = excluded.budget_group;

-- ── bl_admins (optional) — who may see Projection + Pacing Inputs ────
-- Add rows to restrict those tabs. While EMPTY, everyone sees them
-- (so nobody is locked out before you populate it). Username must match
-- the workstation identity exactly, e.g. 'FLUIDFLOW\RYANHOLMBECK'.
create table if not exists bl_admins (
  username text primary key,
  note     text
);
alter table bl_admins enable row level security;
drop policy if exists anon_all on bl_admins;
create policy anon_all on bl_admins for all to anon using (true) with check (true);
-- Example (uncomment + edit):
-- insert into bl_admins(username,note) values ('FLUIDFLOW\RYANHOLMBECK','owner') on conflict do nothing;

-- ── bl_mtd_invoiced — cumulative MTD invoiced $ per date × group ─────
-- Flow360 reports MTD (not daily), so we enter the running MTD number
-- each day; the app derives "yesterday's daily" as today's MTD minus the
-- prior working day's MTD. Invoiced-MTD for the projection = the latest
-- MTD value in the current month.
create table if not exists bl_mtd_invoiced (
  invoice_date date not null,
  budget_group text not null,
  mtd_amount   double precision default 0,
  updated_at   timestamptz not null default now(),
  updated_by   text,
  primary key (invoice_date, budget_group)
);
alter table bl_mtd_invoiced enable row level security;
drop policy if exists anon_all on bl_mtd_invoiced;
create policy anon_all on bl_mtd_invoiced for all to anon using (true) with check (true);

-- ── bl_source_lines — order-line data pushed by refresh_backlog.py ───
-- The direct-from-BC replacement for the CSV drop. The refresh script
-- pulls the 4 reports, enriches, and upserts open order lines here; the
-- app reads order data from this table (CSV drop stays as a fallback).
create table if not exists bl_source_lines (
  source_system        text not null default 'INDELCO_BC',
  document_no          text not null,
  line_no              integer not null,
  customer_no          text,
  customer_name        text,
  location_code        text,
  branch_name          text,
  salesperson          text,
  buyer_name           text,
  vendor_no            text,
  vendor_name          text,
  item_no              text,
  description          text,
  quantity             double precision,
  outstanding_quantity double precision,
  uom                  text,
  outstanding_amount   double precision,
  shipment_date        date,
  order_date           date,
  qty_on_hand          double precision,
  status               text,          -- In Stock; Ship / On PO / Needs PO / None
  is_drop_ship         boolean,
  refreshed_at         timestamptz not null default now(),
  primary key (source_system, document_no, line_no)
);
alter table bl_source_lines enable row level security;
drop policy if exists anon_all on bl_source_lines;
create policy anon_all on bl_source_lines for all to anon using (true) with check (true);
create index if not exists idx_bl_sl_ship on bl_source_lines(shipment_date);
-- patch older tables that predate the customer_name column
alter table bl_source_lines add column if not exists customer_name text;
