import { useEffect, useRef, useState } from 'react';
import { supabase } from './supabaseClient';

// Per-user watchlist. `watchlist.user_id` references OUR `users` table (integer
// id), not the Supabase Auth uid — `users.auth_uid` bridges the two. So every
// read/write first resolves auth.uid() -> users.id.
//
// This resolution is a convenience, NOT the security boundary: the watchlist
// RLS policies enforce ownership through the same auth.uid() -> users.id hop,
// so a tampered client still cannot read or write another user's rows.

// First login for an auth account that has no users row yet: create one. Keyed
// on auth_uid, so re-logins and second tabs never duplicate. The RLS insert
// policy only permits auth_uid = auth.uid(), so this can only ever create the
// caller's OWN row.
async function resolveUserId(user) {
  const { data, error } = await supabase
    .from('users')
    .select('id')
    .eq('auth_uid', user.id)
    .maybeSingle();
  if (error) throw error;
  if (data) return data.id;

  const { data: created, error: insErr } = await supabase
    .from('users')
    .insert({ username: user.email, auth_uid: user.id })
    .select('id')
    .maybeSingle();
  if (!insErr && created) return created.id;

  // Lost a race with another tab (unique auth_uid) — the row exists now.
  const { data: retry } = await supabase
    .from('users')
    .select('id')
    .eq('auth_uid', user.id)
    .maybeSingle();
  if (retry) return retry.id;
  throw insErr || new Error('could not resolve user');
}

// PostgREST nested embedding (watchlist -> securities -> quotes) silently
// returns null joins here — the FK relationships aren't detected on these
// raw-SQL-created tables — so an embed yields 0 usable rows with no error.
// We instead mirror the known-good SQL: fetch watchlist sec_ids for the user,
// then securities + quotes for those ids, and merge on sec_id in JS.
export function useWatchlist(authUser, refreshTick) {
  const [rows, setRows] = useState([]);
  const [status, setStatus] = useState('loading'); // loading | ready | error
  const [error, setError] = useState('');
  const [ownerId, setOwnerId] = useState(null);
  const [orderError, setOrderError] = useState('');
  const serverPos = useRef(new Map()); // last KNOWN-persisted position per sec_id
  const persistTimer = useRef(null);
  const rowsRef = useRef(rows);
  rowsRef.current = rows;

  useEffect(() => {
    let cancelled = false;
    if (!authUser) return;

    async function load() {
      // 1) resolve THIS logged-in user's users.id (creating it on first login)
      let userId;
      try {
        userId = await resolveUserId(authUser);
      } catch (e) {
        return fail(e);
      }
      if (cancelled) return;
      setOwnerId(userId);
      if (import.meta.env.DEV) console.debug('[watchlist] user id =', userId, 'auth uid =', authUser.id);

      // 2) that user's watchlist sec_ids + manual order (Phase 13B / sql/006)
      const { data: wl, error: e2 } = await supabase
        .from('watchlist')
        .select('sec_id, position')
        .eq('user_id', userId);
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

      // 4) merge on sec_id; manual position is THE default order (NULLs =
      // not-yet-positioned securities append at the end, name-ordered).
      const quoteBySec = new Map((quoteRes.data || []).map((q) => [q.sec_id, q]));
      const posBySec = new Map((wl || []).map((w) => [w.sec_id, w.position]));
      const merged = (secRes.data || [])
        .map((s) => ({ ...s, position: posBySec.get(s.sec_id) ?? null, quote: quoteBySec.get(s.sec_id) ?? null }))
        .sort(byPosition);
      serverPos.current = new Map(merged.map((r) => [r.sec_id, r.position]));

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
    // Depend on the primitive uid/email, not the session object: Supabase hands
    // back a NEW session object on every token refresh, which would otherwise
    // refetch the whole watchlist roughly hourly for no reason. refreshTick is a
    // deliberate refetch trigger (auto-refresh); load() doesn't reset status to
    // 'loading', so a refetch updates rows in place without a flicker.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authUser?.id, authUser?.email, refreshTick]);

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
    // New securities append to the end of the manual order.
    optimistic.position = rows.reduce((m, r) => Math.max(m, r.position ?? 0), 0) + 1;
    const prev = rows;
    setRows((rs) => [...rs, optimistic]);

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
      .upsert(
        { user_id: ownerId, sec_id: cand.sec_id, position: optimistic.position },
        { onConflict: 'user_id,sec_id', ignoreDuplicates: true }
      );
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

  // --- reorder (Phase 13B) -------------------------------------------------
  // Optimistic local move; ONE batched upsert of CHANGED positions, debounced
  // ~1.5s after the last move. On error: toast (orderError) + revert to the
  // last known server state. NOTE: needs the watchlist UPDATE policy (see
  // sql/006) — without it the upsert fails and this revert path runs.
  function reorder(orderedIds) {
    setRows((rs) => {
      const byId = new Map(rs.map((r) => [r.sec_id, r]));
      return orderedIds.map((id, i) => ({ ...byId.get(id), position: i + 1 }));
    });
    clearTimeout(persistTimer.current);
    persistTimer.current = setTimeout(async () => {
      const payload = rowsRef.current
        .filter((r) => serverPos.current.get(r.sec_id) !== r.position)
        .map((r) => ({ user_id: ownerId, sec_id: r.sec_id, position: r.position }));
      if (!payload.length) return;
      const { error: e } = await supabase
        .from('watchlist')
        .upsert(payload, { onConflict: 'user_id,sec_id' });
      if (e) {
        setOrderError('שגיאה בשמירת הסדר — הוחזר הסדר הקודם');
        setTimeout(() => setOrderError(''), 4000);
        setRows((rs) =>
          rs.map((r) => ({ ...r, position: serverPos.current.get(r.sec_id) ?? null })).sort(byPosition)
        );
      } else {
        for (const p of payload) serverPos.current.set(p.sec_id, p.position);
      }
    }, 1500);
  }

  return { rows, status, error, add, remove, reorder, orderError };
}

function displayKey(s) {
  return (s.market === 'US' ? s.symbol : s.name) || s.sec_id || '';
}

// Manual position ascending; position-less rows (pre-migration, or added by
// another session) sink to the end in name order.
function byPosition(a, b) {
  if (a.position == null || b.position == null) {
    return (a.position == null) - (b.position == null) || displayKey(a).localeCompare(displayKey(b));
  }
  return a.position - b.position;
}
