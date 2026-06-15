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
};
