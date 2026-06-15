-- ════════════════════════════════════════════════════════════════════
--  I3 Inventory Intelligence — Supabase schema (user state, Hybrid model)
-- ════════════════════════════════════════════════════════════════════
--  Run this ONCE in the Supabase SQL Editor (Dashboard -> SQL -> New query).
--
--  Scope: ONLY user-editable / shared state lives here. The heavy
--  analytical data (ILE, value_entries, items, ...) stays in the local
--  sql.js database, rebuilt daily from BC — there is no reason to store
--  read-only derived data in the cloud.
--
--  Tables:
--    exclusion_rules     category/field rules (was localStorage)
--    item_exclusions     per-item manual exclude (was in-memory only -> lost)
--    kpi_goals           dashboard targets (was localStorage)
--    item_supplement     returnable / NNCR / contract / notes (was lost)
--    box_qty_override    manual pack-size overrides (was lost)
--    app_parameters      tuning params (was lost on reload)
--    buyer_xref          purchaser code -> name/email
--    kpi_snapshots       dated KPI actual-vs-target history (NEW: trend board)
--
--  SECURITY: RLS is enabled on every table. The starter policies below
--  allow the anon (publishable) key full access — fine for a single
--  trusted team while we stand this up. When Supabase Auth is added,
--  REPLACE the "anon_all" policies with auth.uid()-scoped policies.
-- ════════════════════════════════════════════════════════════════════

-- helper: auto-update updated_at
create or replace function set_updated_at()
returns trigger language plpgsql as $$
begin new.updated_at = now(); return new; end; $$;

-- ── exclusion_rules ──────────────────────────────────────────────────
create table if not exists exclusion_rules (
  id           bigint generated always as identity primary key,
  field_name   text not null,
  operator     text not null,
  field_value  text not null,
  reason       text,
  is_active    boolean not null default true,
  updated_at   timestamptz not null default now(),
  unique (field_name, operator, field_value)
);

-- ── item_exclusions (per-item manual exclude) ────────────────────────
create table if not exists item_exclusions (
  item_no       text not null,
  location_code text not null,
  excluded      boolean not null default true,
  reason        text,
  updated_at    timestamptz not null default now(),
  primary key (item_no, location_code)
);

-- ── kpi_goals ────────────────────────────────────────────────────────
create table if not exists kpi_goals (
  goal_key   text primary key,
  goal_value double precision,
  updated_at timestamptz not null default now()
);

-- ── item_supplement (BC-absent fields) ───────────────────────────────
create table if not exists item_supplement (
  item_no                  text primary key,
  returnable               boolean,
  return_window_days       integer,
  nncr_flag                boolean default false,
  customer_contract_flag   boolean default false,
  drop_ship_only           boolean default false,
  physical_space_available boolean default true,
  customer_count           integer,
  stocking_override        text,
  notes                    text,
  updated_at               timestamptz not null default now()
);

-- ── box_qty_override (manual pack size; BC Order Multiple wins if set) ─
create table if not exists box_qty_override (
  item_no    text primary key,
  box_qty    double precision not null default 1,
  updated_at timestamptz not null default now()
);

-- ── app_parameters ───────────────────────────────────────────────────
create table if not exists app_parameters (
  param_key   text primary key,
  param_value text,
  updated_at  timestamptz not null default now()
);

-- ── buyer_xref ───────────────────────────────────────────────────────
create table if not exists buyer_xref (
  purchaser_code text primary key,
  buyer_name     text,
  email          text,
  notes          text,
  updated_at     timestamptz not null default now()
);

-- ── kpi_snapshots (dated history for the Green/Red trend board) ───────
create table if not exists kpi_snapshots (
  id            bigint generated always as identity primary key,
  snapshot_date date not null default current_date,
  location_code text,
  buyer         text,
  kpi_key       text not null,
  actual        double precision,
  target        double precision,
  created_at    timestamptz not null default now(),
  unique (snapshot_date, location_code, buyer, kpi_key)
);
create index if not exists idx_kpi_snap_key_date on kpi_snapshots(kpi_key, snapshot_date);

-- ── updated_at triggers ──────────────────────────────────────────────
do $$
declare t text;
begin
  foreach t in array array[
    'exclusion_rules','item_exclusions','kpi_goals','item_supplement',
    'box_qty_override','app_parameters','buyer_xref'
  ] loop
    execute format(
      'drop trigger if exists trg_%1$s_updated on %1$s;
       create trigger trg_%1$s_updated before update on %1$s
       for each row execute function set_updated_at();', t);
  end loop;
end $$;

-- ── Row-Level Security ───────────────────────────────────────────────
-- Enable on every table, then add a permissive starter policy for the
-- anon key. TIGHTEN THESE when Supabase Auth is introduced.
do $$
declare t text;
begin
  foreach t in array array[
    'exclusion_rules','item_exclusions','kpi_goals','item_supplement',
    'box_qty_override','app_parameters','buyer_xref','kpi_snapshots'
  ] loop
    execute format('alter table %I enable row level security;', t);
    execute format('drop policy if exists anon_all on %I;', t);
    -- Starter policy: anon key may do everything. Replace with
    -- auth.uid()-scoped policies once authentication is in place.
    execute format(
      'create policy anon_all on %I for all to anon using (true) with check (true);', t);
  end loop;
end $$;
