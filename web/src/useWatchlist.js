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
  const [ownerId, setOwnerId] = useState(null);

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
      if (!cancelled) setOwnerId(owner.id);
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

  // --- add ----------------------------------------------------------------
  // SHALLOW insert only. The browser deliberately does NOT resolve prices or
  // the MAYA companyId (no yfinance, no MAYA calls from the client) — it writes
  // what the candidate already told us and leaves enrichment to the Python
  // side. Returns { ok, reason } — never throws at the caller.
  async function add(cand) {
    if (!ownerId) return { ok: false, reason: 'no-user' };
    if (rows.some((r) => r.sec_id === cand.sec_id)) return { ok: false, reason: 'duplicate' };

    // Optimistic: show the row now, with no quote (the UI renders that as
    // "ממתין לנתונים"), and roll it back if the write fails.
    const optimistic = {
      sec_id: cand.sec_id,
      symbol: cand.symbol,
      name: cand.name,
      asset_type: cand.asset_type,
      market: cand.market,
      price_source: cand.price_source,
      quote: null,
    };
    const prev = rows;
    setRows((rs) => [...rs, optimistic].sort((a, b) => displayKey(a).localeCompare(displayKey(b))));

    // ignoreDuplicates => INSERT ... ON CONFLICT DO NOTHING, so an existing
    // security is left exactly as it is. Never clobber a good row (e.g. one the
    // collectors already enriched) with our shallower knowledge.
    const { error: e1 } = await supabase.from('securities').upsert(
      {
        sec_id: cand.sec_id,
        symbol: cand.symbol,
        name: cand.name,
        asset_type: cand.asset_type,
        market: cand.market,
        price_source: cand.price_source,
        yahoo_symbol: cand.yahoo_symbol,
        maya_company_id: cand.maya_company_id,
      },
      { onConflict: 'sec_id', ignoreDuplicates: true }
    );
    if (e1) {
      setRows(prev);
      return { ok: false, reason: 'securities-insert', message: e1.message };
    }

    const { error: e2 } = await supabase
      .from('watchlist')
      .upsert({ user_id: ownerId, sec_id: cand.sec_id }, { onConflict: 'user_id,sec_id', ignoreDuplicates: true });
    if (e2) {
      setRows(prev);
      return { ok: false, reason: 'watchlist-insert', message: e2.message };
    }
    return { ok: true };
  }

  // --- remove -------------------------------------------------------------
  // Watchlist row ONLY. The security itself and everything collected against it
  // (news, filings, emails, quotes) are shared across users and must survive —
  // removing them here would delete another user's data.
  async function remove(secId) {
    if (!ownerId) return { ok: false, reason: 'no-user' };
    const prev = rows;
    setRows((rs) => rs.filter((r) => r.sec_id !== secId));

    const { error: e } = await supabase
      .from('watchlist')
      .delete()
      .eq('user_id', ownerId)
      .eq('sec_id', secId);
    if (e) {
      setRows(prev);
      return { ok: false, reason: 'watchlist-delete', message: e.message };
    }
    return { ok: true };
  }

  return { rows, status, error, add, remove };
}

function displayKey(s) {
  return (s.market === 'US' ? s.symbol : s.name) || s.sec_id || '';
}
