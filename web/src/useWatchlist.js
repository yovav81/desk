import { useEffect, useState } from 'react';
import { supabase } from './supabaseClient';

// TODO(auth-mapping): watchlist.user_id references our own `users` table, NOT
// the Supabase Auth uid. Until that mapping is wired, we read the seeded
// "owner" user's watchlist so real rows appear. In a later step, resolve the
// authenticated user's mapped `users.id` and filter by it instead.
const OWNER_USERNAME = 'owner';

function normalizeQuote(q) {
  // Embedded child can come back as an array (0/1 rows) or a single object.
  if (!q) return null;
  return Array.isArray(q) ? q[0] ?? null : q;
}

export function useWatchlist() {
  const [rows, setRows] = useState([]);
  const [status, setStatus] = useState('loading'); // loading | ready | error
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;

    async function load() {
      // 1) resolve the seeded owner's user id
      const { data: owner, error: e1 } = await supabase
        .from('users')
        .select('id')
        .eq('username', OWNER_USERNAME)
        .maybeSingle();
      if (cancelled) return;
      if (e1) return fail(e1);
      if (!owner) {
        setRows([]);
        setStatus('ready');
        return;
      }

      // 2) that user's watchlist, with securities + their quote embedded
      const { data, error: e2 } = await supabase
        .from('watchlist')
        .select(
          'securities(sec_id, symbol, name, asset_type, market, price_source, ' +
            'quotes(last_price, prev_close, day_change_pct, mtd_pct, qtd_pct, ytd_pct, y12_pct, currency, source, status))'
        )
        .eq('user_id', owner.id);
      if (cancelled) return;
      if (e2) return fail(e2);

      const mapped = (data || [])
        .map((w) => w.securities)
        .filter(Boolean)
        .map((s) => ({ ...s, quote: normalizeQuote(s.quotes) }))
        .sort((a, b) => displayKey(a).localeCompare(displayKey(b)));

      setRows(mapped);
      setStatus('ready');
    }

    function fail(err) {
      setError(err?.message || String(err));
      setStatus('error');
    }

    load();
    return () => {
      cancelled = true;
    };
  }, []);

  return { rows, status, error };
}

function displayKey(s) {
  return (s.market === 'US' ? s.symbol : s.name) || s.sec_id || '';
}
