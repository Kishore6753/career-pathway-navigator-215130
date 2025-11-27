# Supabase Integration Guide (Seeding + RLS)

This project seeds the following tables in your Supabase Postgres using a privileged service role key:

- competencies
- roles
- role_competencies
- role_adjacency
- learning_resources (optional, when links are found)

The seeding script uses upserts with conflict keys so it is idempotent.

## Environment variables

Create a `.env` at repo root (copy from `.env.example`) and set:

- SUPABASE_URL = https://YOUR_PROJECT.ref.supabase.co
- SUPABASE_SERVICE_ROLE_KEY = <service-role-key>

The service role key bypasses RLS, so you do not need to disable RLS for seeding.

## Expected table schema (DDL reference)

Use these as a reference; your project may already include these tables. Run in SQL editor if you need to create or align schemas.

```sql
-- competencies
create table if not exists public.competencies (
  id bigserial primary key,
  name text not null unique
);

-- roles
create table if not exists public.roles (
  id bigserial primary key,
  name text not null unique,
  description text,
  category text
);

-- role_competencies (link table)
create table if not exists public.role_competencies (
  id bigserial primary key,
  role_id bigint not null references public.roles(id) on delete cascade,
  competency_id bigint not null references public.competencies(id) on delete cascade,
  -- optional: level text,
  unique (role_id, competency_id)
);

-- role_adjacency (directed adjacency with score 0..100)
create table if not exists public.role_adjacency (
  id bigserial primary key,
  source_role_id bigint not null references public.roles(id) on delete cascade,
  target_role_id bigint not null references public.roles(id) on delete cascade,
  score numeric not null,
  check (source_role_id <> target_role_id),
  unique (source_role_id, target_role_id)
);

-- learning_resources (optional)
create table if not exists public.learning_resources (
  id bigserial primary key,
  competency_id bigint not null references public.competencies(id) on delete cascade,
  url text not null,
  title text,
  unique (competency_id, url)
);
```

## RLS (Row Level Security)

Enable RLS and add read policies. Writes are restricted; seeding uses the service role key (bypasses RLS).

```sql
-- Enable RLS
alter table public.competencies enable row level security;
alter table public.roles enable row level security;
alter table public.role_competencies enable row level security;
alter table public.role_adjacency enable row level security;
alter table public.learning_resources enable row level security;

-- Read policies (authenticated users)
create policy "read_competencies" on public.competencies for select to authenticated using (true);
create policy "read_roles" on public.roles for select to authenticated using (true);
create policy "read_role_competencies" on public.role_competencies for select to authenticated using (true);
create policy "read_role_adjacency" on public.role_adjacency for select to authenticated using (true);
create policy "read_learning_resources" on public.learning_resources for select to authenticated using (true);

-- If you need anonymous read for public pages, add `to anon` for select policies instead of (or in addition to) authenticated.
```

Note: You do NOT need to disable RLS to seed if you use the service role key.

## Running the seed

Dry-run (parses attachments and prints counts without writing):

```bash
python scripts/seed_supabase.py --dry-run
```

Full seed (upsert records; add --reset to clear tables first):

```bash
# Optionally reset to start clean (child tables are purged first to honor FKs):
python scripts/seed_supabase.py --reset

# Seed without reset (idempotent upserts)
python scripts/seed_supabase.py
```

By default, this script looks for the supplied attachments at:
- Competencies: attachments/20251127_064753_Competency_mapping.xlsx
- Role adjacency: attachments/20251127_064751_CA_Role_Adjacency.xlsx and ...29.xlsx
- Role navigator: attachments/20251127_064756_Role_Navigator_Worksheet.xlsx
- Role cards: multiple .txt files under attachments/

You can override paths with CLI flags (see `--help`).

## Verification queries

After the script completes, it also prints exact table counts using Supabase's RPC.

You can verify via SQL:

```sql
select count(*) as competencies from public.competencies;
select count(*) as roles from public.roles;
select count(*) as role_competencies from public.role_competencies;
select count(*) as role_adjacency from public.role_adjacency;
select count(*) as learning_resources from public.learning_resources;
```

Sanity examples:

```sql
-- spot-check adjacency values
select r1.name as source, r2.name as target, a.score
from public.role_adjacency a
join public.roles r1 on r1.id = a.source_role_id
join public.roles r2 on r2.id = a.target_role_id
order by r1.name, a.score desc
limit 20;

-- sample role-competencies
select r.name as role, c.name as competency
from public.role_competencies rc
join public.roles r on r.id = rc.role_id
join public.competencies c on c.id = rc.competency_id
order by r.name, c.name
limit 20;
```

## Expected counts (from dry-run with current attachments)

These are the planned rows before upsert (your actual counts may differ based on schema/data):

- competencies: 31
- roles: 41
- role_competencies: 708
- role_adjacency: 264
- learning_resources: (data-dependent; extracted from the Role Navigator sheet if links present)

## Troubleshooting

- Missing SUPABASE_SERVICE_ROLE_KEY:
  - The script will fail when attempting to connect. Set it in `.env` and retry.
- Table missing:
  - Create with the DDL above and rerun the script (it is idempotent).
- RLS preventing reads in your app:
  - Ensure the read policies are created for the appropriate roles (authenticated/anon).
