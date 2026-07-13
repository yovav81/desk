import { useEffect, useState } from 'react';
import { supabase } from './supabaseClient';

// TODO(auth-mapping): watchlist.user_id references our own `users` table, NOT
// the Supabase Auth uid. Until that mapping is wired, we read the seeded
// "owner" user's watchlist so real rows appear. In a later step, resolve the
// authenticated user's mapped `users.id` and filter by it instead.
const OWNER_USERNAME = 'owner';

// PostgREST nested embedding (watchlist -> securities -> quotes) silently
// returns null joins here — the FK relationships aren't detected on these
// raw-SQL-created tables — so an embed yields 0 usable rows with no error.
// We instead mirror the known-good SQL: fetch watchlist sec_ids for the user,
// then securities + quotes for those ids, and merge on sec_id in JS.
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
      if (import.meta.env.DEV) console.debug('[watchlist] owner user id =', owner.id);

      // 2) that user's watchlist sec_ids
      const { data: wl, error: e2 } = await supabase
        .from('watchlist')
        .select('sec_id')
        .eq('user_id', owner.id);
      if (cancelled) return;
      if (e2) return fail(e2);

      const secIds = (wl || []).map((w) => w.sec_id);
      if (import.meta.env.DEV) console.debug('[watchlist] sec_ids =', secIds);
      if (secIds.length === 0) {
        setRows([]);
        setStatus('ready');
        return;
      }

      // 3) securities + quotes for those sec_ids (two flat queries)
      const [secRes, quoteRes] = await Promise.all([
        supabase
          .from('securities')
          .select('sec_id, symbol, name, asset_type, market, price_source')
          .in('sec_id', secIds),
        supabase
          .from('quotes')
          .select(
            'sec_id, last_price, prev_close, day_change_pct, mtd_pct, qtd_pct, ytd_pct, y12_pct, currency, source, status'
          )
          .in('sec_id', secIds),
      ]);
      if (cancelled) return;
      if (secRes.error) return fail(secRes.error);
      if (quoteRes.error) return fail(quoteRes.error);

      if (import.meta.env.DEV) {
        console.debug('[watchlist] securities rows =', secRes.data?.length, 'quotes rows =', quoteRes.data?.length);
      }

      // 4) merge on sec_id
      const quoteBySec = new Map((quoteRes.data || []).map((q) => [q.sec_id, q]));
      const merged = (secRes.data || [])
        .map((s) => ({ ...s, quote: quoteBySec.get(s.sec_id) ?? null }))
        .sort((a, b) => displayKey(a).localeCompare(displayKey(b)));

      setRows(merged);
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
