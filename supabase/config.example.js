// ── Supabase connection config (TEMPLATE) ───────────────────────────
// Copy this file to  config.local.js  in the same folder and fill in your
// values. config.local.js is gitignored so your key is NOT committed to
// the public repo's history. The app loads config.local.js at startup.
//
// The "publishable" / anon key is designed to be used in the browser and
// is safe to expose AS LONG AS Row-Level Security is enabled (schema.sql
// does this). Never put the service_role / secret key here.
window.SUPABASE_CONFIG = {
  url: 'https://YOUR-PROJECT.supabase.co',
  anonKey: 'sb_publishable_xxxxxxxxxxxxxxxxxxxxxx',
  // Which source_system's rows the Backlog Review app reads from
  // bl_source_lines. Matches refresh_backlog.py's source_system. Defaults
  // to 'INDELCO_BC'; change when pointing at a different feed (e.g. NetSuite).
  sourceSystem: 'INDELCO_BC',
};
