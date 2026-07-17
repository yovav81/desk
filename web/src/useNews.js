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

// THE 1000-ROW TRAP: PostgREST silently caps every query at the server's
// max_rows (1000). A query with NO order and NO limit therefore returns an
// ARBITRARY 1000-row window once the table outgrows it — in practice roughly
// insertion order, i.e. the OLDEST rows. That is exactly how the main feed
// froze at 15.07 while `news` sat at 1444+ rows: fresh items existed but fell
// outside the unordered window, and the client-side sort can't recover rows it
// never received. EVERY feed query must order newest-first server-side with an
// explicit limit. 500/table renders no more DOM than the old cap did, and
// covers ~a day of news + years of emails/filings at current volumes.
const FEED_LIMIT = 500;

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
  emailId: e.id, // for the expand-in-place lazy fetch (body + attachments)
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
      // nullsFirst:false matters: DESC ordering puts NULL dates FIRST by
      // default, which would burn window slots on dateless rows.
      const [newsRes, emailRes, filingRes] = await Promise.all([
        supabase
          .from('news')
          .select('id, sec_id, source, title, url, published_at, category')
          .order('published_at', { ascending: false, nullsFirst: false })
          .limit(FEED_LIMIT),
        supabase
          .from('emails')
          .select('id, sec_id, sender, subject, received_at')
          .order('received_at', { ascending: false, nullsFirst: false })
          .limit(FEED_LIMIT),
        supabase
          .from('filings')
          .select('id, sec_id, source, title, doc_url, published_at')
          .order('published_at', { ascending: false, nullsFirst: false })
          .limit(FEED_LIMIT),
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
      // Same 1000-row trap as the main feed (see FEED_LIMIT above): per-security
      // subsets are far below the limit today, so results are identical — but a
      // chatty security must age out its OLDEST items, never arbitrary ones.
      const [newsRes, emailRes, filingRes] = await Promise.all([
        supabase
          .from('news')
          .select('id, sec_id, source, title, url, published_at, category')
          .eq('sec_id', secId)
          .eq('category', 'stock')
          .order('published_at', { ascending: false, nullsFirst: false })
          .limit(FEED_LIMIT),
        supabase
          .from('emails')
          .select('id, sec_id, sender, subject, received_at')
          .eq('sec_id', secId)
          .order('received_at', { ascending: false, nullsFirst: false })
          .limit(FEED_LIMIT),
        supabase
          .from('filings')
          .select('id, sec_id, source, title, doc_url, published_at')
          .eq('sec_id', secId)
          .order('published_at', { ascending: false, nullsFirst: false })
          .limit(FEED_LIMIT),
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
