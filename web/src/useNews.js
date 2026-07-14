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
export function useNews() {
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

      const web = (newsRes.data || []).map((n) => ({
        key: `news-${n.id}`,
        type: 'web',
        category: n.category, // 'stock' | 'macro'
        title: n.title,
        source: n.source,
        url: n.url,
        sec_id: n.sec_id,
        ts: n.published_at,
      }));
      const emails = (emailRes.data || []).map((e) => ({
        key: `email-${e.id}`,
        type: 'email',
        category: null,
        title: e.subject,
        source: e.sender,
        url: null,
        sec_id: e.sec_id,
        ts: e.received_at,
      }));
      const filings = (filingRes.data || []).map((f) => ({
        key: `filing-${f.id}`,
        type: f.source === 'sec' ? 'sec' : 'maya',
        category: null,
        title: f.title,
        source: f.source,
        url: f.doc_url,
        sec_id: f.sec_id,
        ts: f.published_at,
      }));

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
  }, []);

  return { items, status, error };
}
