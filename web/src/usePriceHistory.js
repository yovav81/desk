import { useEffect, useState } from 'react';
import { supabase } from './supabaseClient';

// Daily closes for ONE security, oldest first. Fetched ONCE for the full stored
// window (~1 year); the period selector slices this array client-side, so
// switching חודש/רבעון/שנה never refetches.
//
// price_history.close is already the normalized major-currency value written by
// collect_prices (post ILA→ILS / GBp→GBP). NEVER divide again here.
//
// Needs a read policy on price_history — without one this returns an empty
// array with NO error, which looks exactly like "no history yet".
export function usePriceHistory(secId) {
  const [points, setPoints] = useState([]); // [{ date: Date, close: number }]
  const [status, setStatus] = useState('loading'); // loading | ready | error
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    if (!secId) {
      setPoints([]);
      setStatus('ready');
      return;
    }
    setStatus('loading');

    async function load() {
      const { data, error: e } = await supabase
        .from('price_history')
        .select('price_date, close')
        .eq('sec_id', secId)
        .order('price_date', { ascending: true });
      if (cancelled) return;
      if (e) {
        setError(e.message || String(e));
        setStatus('error');
        return;
      }
      setPoints(
        (data || [])
          .map((r) => ({ date: new Date(r.price_date), close: Number(r.close) }))
          .filter((p) => !Number.isNaN(p.close) && !Number.isNaN(p.date.getTime()))
      );
      setStatus('ready');
    }

    load();
    return () => {
      cancelled = true;
    };
  }, [secId]);

  return { points, status, error };
}
