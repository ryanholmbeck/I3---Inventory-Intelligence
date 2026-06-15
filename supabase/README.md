# Supabase setup — I3 Inventory Intelligence

We use Supabase for **shared, persistent user state** (exclusion rules, KPI
goals, per-item exclusions, item supplement data, pack-size overrides,
parameters, buyer cross-reference, and dated KPI history). The heavy
analytical data (ILE, value entries, items) stays in the local sql.js
database, rebuilt daily from BC.

This fixes the "my edits disappear on reload" problem and lets your whole
team share the same rules/goals/overrides.

## One-time setup

### 1. Create the tables
1. Supabase Dashboard → **SQL Editor** → **New query**
2. Paste the entire contents of **`schema.sql`** → **Run**
3. You should see "Success. No rows returned." All tables are created with
   Row-Level Security enabled.

### 2. Local config
Your keys live in **`config.local.js`** (already created, gitignored so it
never reaches the public repo). It survives `Update_App.bat`. If you ever
need to recreate it, copy `config.example.js` → `config.local.js` and fill
in your project URL + publishable key.

### 3. Test the connection
With `server.py` running and the app open, open the browser console (F12)
and paste:

```js
(async () => {
  const c = window.SUPABASE_CONFIG;
  const r = await fetch(`${c.url}/rest/v1/kpi_goals?select=*`, {
    headers: { apikey: c.anonKey, Authorization: 'Bearer ' + c.anonKey }
  });
  console.log('Supabase status:', r.status, await r.json());
})();
```

- **status 200** + `[]` → connected, tables exist, RLS allows access. 
- **status 401/403** → key wrong, or RLS policy missing.
- **status 404** → table not created (re-run schema.sql).
- **network/CORS error** → URL wrong, or the browser can't reach Supabase.

Paste the result back and we wire the app's persistence layer to it.

## Security notes
- The **publishable / anon key** in `config.local.js` is safe for the
  browser **because RLS is on**. Never put the **service_role** key here.
- Starter RLS policies (`anon_all`) allow the anon key full access — fine
  for a single trusted team during rollout. When we add Supabase Auth,
  we replace those with `auth.uid()`-scoped policies so users only see
  their permitted scope.

## How data talks to Supabase
No library to vendor — we use the built-in **PostgREST** API over plain
`fetch()` (`/rest/v1/<table>`), which fits the app's offline-vendored,
no-CDN approach. Upserts use `Prefer: resolution=merge-duplicates`.
