import { useEffect, useState } from 'react';
import { supabase } from './supabaseClient';

// Fetches the four source types into ONE unified, type-tagged list (fetched
// once; the panel filters client-side per tab so tab switches are instant):
//   - web news:    news (category 'stock' | 'macro')      -> type 'web'
//   - email:       emails (subject/sender, no url)         -> type 'email'
//   - maya filing: filings where source='maya'             -> type 'maya'
//   - sec filing:  filings where source='sec'              -> type 'sec'
// Read-only via the anon client. If a table returns 0 rows with NO error, that
// is RLS (needs a read policy) — same as securities/quotes/watchlist/users.

// One mapping per source type, shared by the dashboard feed (useNews, fetches
// everything) and the detail page (useSecurityFeed, fetches one sec_id) so both
// speak the identical item shape.
const mapWeb = (n) => ({
  key: `news-${n.id}`,
  type: 'web',
  category: n.category, // 'stock' | 'macro'
  title: n.title,
  source: n.source,
  url: n.url,
  sec_id: n.sec_id,
  ts: n.published_at,
});
const mapEmail = (e) => ({
  key: `email-${e.id}`,
  type: 'email',
  category: null,
  title: e.subject,
  source: e.sender,
  url: null,
  sec_id: e.sec_id,
  ts: e.received_at,
});
const mapFiling = (f) => ({
  key: `filing-${f.id}`,
  type: f.source === 'sec' ? 'sec' : 'maya',
  category: null,
  title: f.title,
  source: f.source,
  url: f.doc_url,
  sec_id: f.sec_id,
  ts: f.published_at,
});

export function useNews(refreshTick) {
  const [items, setItems] = useState([]);
  const [status, setStatus] = useState('loading'); // loading | ready | error
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;

    async function load() {
      const [newsRes, emailRes, filingRes] = await Promise.all([
        supabase.from('news').select('id, sec_id, source, title, url, published_at, category'),
        supabase.from('emails').select('id, sec_id, sender, subject, received_at'),
        supabase.from('filings').select('id, sec_id, source, title, doc_url, published_at'),
      ]);
      if (cancelled) return;

      const firstError = newsRes.error || emailRes.error || filingRes.error;
      if (firstError) {
        setError(firstError.message || String(firstError));
        setStatus('error');
        return;
      }

      const web = (newsRes.data || []).map(mapWeb);
      const emails = (emailRes.data || []).map(mapEmail);
      const filings = (filingRes.data || []).map(mapFiling);

      if (import.meta.env.DEV) {
        console.debug('[news] web=%d email=%d filings=%d', web.length, emails.length, filings.length);
      }

      setItems([...web, ...emails, ...filings]);
      setStatus('ready');
    }

    load();
    return () => {
      cancelled = true;
    };
    // refreshTick re-runs the fetch (auto-refresh). load() never sets status
    // back to 'loading', so a refetch swaps items in without a flicker; the
    // cancelled guard drops a superseded fetch.
  }, [refreshTick]);

  return { items, status, error };
}

// Detail page: the four source types for ONE security, newest first. Filtered
// server-side by sec_id rather than reusing useNews() — no reason to pull every
// security's feed to show one. Macro news can't leak in (it has sec_id NULL).
export function useSecurityFeed(secId) {
  const [items, setItems] = useState([]);
  const [status, setStatus] = useState('loading'); // loading | ready | error
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    if (!secId) {
      setItems([]);
      setStatus('ready');
      return;
    }
    setStatus('loading');

    async function load() {
      const [newsRes, emailRes, filingRes] = await Promise.all([
        supabase
          .from('news')
          .select('id, sec_id, source, title, url, published_at, category')
          .eq('sec_id', secId)
          .eq('category', 'stock'),
        supabase.from('emails').select('id, sec_id, sender, subject, received_at').eq('sec_id', secId),
        supabase.from('filings').select('id, sec_id, source, title, doc_url, published_at').eq('sec_id', secId),
      ]);
      if (cancelled) return;

      const firstError = newsRes.error || emailRes.error || filingRes.error;
      if (firstError) {
        setError(firstError.message || String(firstError));
        setStatus('error');
        return;
      }

      setItems([
        ...(newsRes.data || []).map(mapWeb),
        ...(emailRes.data || []).map(mapEmail),
        ...(filingRes.data || []).map(mapFiling),
      ]);
      setStatus('ready');
    }

    load();
    return () => {
      cancelled = true;
    };
  }, [secId]);

  return { items, status, error };
}
