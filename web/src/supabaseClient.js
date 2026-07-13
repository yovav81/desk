import { createClient } from '@supabase/supabase-js';

// Vite exposes only VITE_-prefixed env vars to the frontend. The anon key is
// the PUBLIC frontend key (safe to ship in the browser bundle) — never put
// DESK_DB_URL or any Supabase service/secret key here.
const url = import.meta.env.VITE_SUPABASE_URL;
const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY;

if (!url || !anonKey) {
  // Surfaced in the console so a missing web/.env is obvious during dev.
  console.error(
    'Missing VITE_SUPABASE_URL / VITE_SUPABASE_ANON_KEY — copy web/.env.example to web/.env and fill them in.'
  );
}

export const supabase = createClient(url ?? '', anonKey ?? '');
