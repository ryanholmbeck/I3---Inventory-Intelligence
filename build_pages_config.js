// Cloudflare Pages build step — writes supabase/config.js from environment
// secrets so the Supabase key is NEVER committed to the (public) repo.
//
// In the Pages project: Settings -> Environment variables, add:
//   SUPABASE_URL       = https://YOUR-PROJECT.supabase.co
//   SUPABASE_ANON_KEY  = sb_publishable_...   (the publishable key)
//   SOURCE_SYSTEM      = INDELCO_BC           (optional; defaults below)
// and set the build command to:  node build_pages_config.js
const fs = require('fs');
const cfg = {
  url: process.env.SUPABASE_URL || '',
  anonKey: process.env.SUPABASE_ANON_KEY || '',
  sourceSystem: process.env.SOURCE_SYSTEM || 'INDELCO_BC',
};
if (!cfg.url || !cfg.anonKey) {
  console.error('WARNING: SUPABASE_URL / SUPABASE_ANON_KEY not set — the hosted app will have no cloud connection.');
}
fs.writeFileSync('supabase/config.js', 'window.SUPABASE_CONFIG=' + JSON.stringify(cfg) + ';\n');
console.log('wrote supabase/config.js for', cfg.url || '(no url set)');
