import { useEffect, useRef, useState } from 'react';
import { supabase } from './supabaseClient';

// Securities search — three sources, routed by what the user typed.
//
//   Hebrew / bare digits -> `tase_securities` DIRECTLY (557 rows, refreshed
//                           daily by desk/collect_tase_list.py). Local table =
//                           instant typeahead, no live MAYA call per keystroke.
//   Latin ticker/name    -> the `search` Edge Function (Yahoo global + SEC US).
//                           The browser CANNOT call those upstreams itself:
//                           Yahoo sends no CORS and both need a User-Agent a
//                           browser may not set. functions.invoke() sends the
//                           anon key/JWT automatically.
//
// NEVER auto-picks. Both paths return a LIST for the user to choose from —
// that is the whole reason global search exists (SAP -> SAP SE vs SAP.DE vs
// SAP.TO/Saputo are all valid, and only a human can tell which was meant).
// See research/GLOBAL_COVERAGE_FINDINGS.md.

const HEBREW_RE = /[֐-׿]/;
const DIGITS_RE = /^\d+$/;
const DEBOUNCE_MS = 300;
const LIMIT = 10;

export function routeQuery(q) {
  const s = (q || '').trim();
  if (!s) return 'none';
  // Yahoo 400s on Hebrew, and a TASE security number means nothing to it.
  // Hebrew/number searches are substring/prefix matches over a 557-row table,
  // so a single character floods the dropdown — keep a 2-char floor there.
  if (HEBREW_RE.test(s) || DIGITS_RE.test(s)) return s.length >= 2 ? 'tase' : 'none';
  // Latin: ONE character is a legitimate query. C (Citigroup), F (Ford),
  // T (AT&T) and V (Visa) are real tickers, and the Edge Function ranks an
  // exact ticker match first, so "C" surfaces Citigroup at the top.
  return 'edge';
}

// `_` and `%` are LIKE wildcards; a user typing them must not build a pattern.
function escapeLike(s) {
  return s.replace(/[\\%_]/g, (c) => '\\' + c);
}

// --- candidate shape -------------------------------------------------------
// Everything downstream (picker + add) speaks this one shape:
//   { key, market, sec_id, symbol, name, asset_type, yahoo_symbol,
//     maya_company_id, price_source, badge, sub }

// SEC titles carry a state-of-incorporation suffix: "BANK OF AMERICA CORP /DE/"
// means Delaware, NOT Germany — displaying it next to a GLOBAL badge system is
// actively confusing. Strip trailing "/XX/" groups (some rows carry more than
// one, and the closing slash is sometimes missing). Cosmetic only; the ticker,
// market and sec_id are untouched. Falls back to the raw title if a name were
// to reduce to nothing.
export function cleanSecName(name) {
  const s = String(name ?? '');
  const cleaned = s.replace(/(\s*\/[A-Za-z]{2,4}\/?)+\s*$/, '').trim();
  return cleaned || s;
}

export function taseCandidate(row) {
  // collect_tase_list.py never populates `symbol` (there is no free
  // number->letter-ticker source), so yahoo_symbol is unknown here and
  // `<number>.TA` is NOT a valid guess — yfinance 404s it. price_source
  // therefore starts as 'manual', matching onboarding.py's documented fallback
  // for unknown TASE securities; `python -m desk.onboard_cli` can later upgrade
  // it to yfinance (add_to_db never downgrades).
  const hasTicker = Boolean(row.symbol);
  return {
    key: `TASE:${row.security_number}`,
    market: 'TASE',
    sec_id: String(row.security_number),
    symbol: row.symbol || String(row.security_number), // securities.symbol is NOT NULL
    name: row.name,
    asset_type: 'stock',
    yahoo_symbol: hasTicker ? `${row.symbol}.TA` : null,
    maya_company_id: row.company_id ?? null,
    price_source: hasTicker ? 'yfinance' : 'manual',
    badge: 'ת"א',
    sub: `${row.security_number} · ת"א`,
  };
}

export function edgeCandidate(c) {
  // Only SEC titles carry the /XX/ suffix; Yahoo names don't.
  const name = c.market === 'US' ? cleanSecName(c.name) : c.name;
  return {
    key: `${c.market}:${c.symbol}`,
    // Carried through to the insert VERBATIM — US stays US, GLOBAL stays
    // GLOBAL. Never inferred downstream.
    market: c.market, // US | GLOBAL
    sec_id: c.symbol,
    symbol: c.symbol,
    name,
    asset_type: 'stock',
    // A picked Yahoo symbol is exactly what the collector needs to price it.
    yahoo_symbol: c.symbol,
    maya_company_id: null,
    price_source: 'yfinance',
    badge: c.market === 'US' ? 'US' : 'GLOBAL',
    sub: c.market === 'US' ? `${name} · US` : `${c.symbol} · ${c.exchange || 'GLOBAL'}`,
  };
}

// --- the two source queries ------------------------------------------------
async function searchTase(q) {
  const s = q.trim();
  let req = supabase
    .from('tase_securities')
    .select('security_number, name, symbol, company_id, security_type, is_primary_stock')
    .limit(LIMIT);

  req = DIGITS_RE.test(s)
    ? // Prefix, not equality: typing a security number should match as you go.
      req.ilike('security_number', `${escapeLike(s)}%`)
    : req.ilike('name', `%${escapeLike(s)}%`);

  const { data, error } = await req;
  if (error) throw error;
  return (data || []).map(taseCandidate);
}

async function searchEdge(q) {
  const { data, error } = await supabase.functions.invoke('search', {
    body: { q: q.trim(), limit: LIMIT },
  });
  if (error) throw error;
  return {
    candidates: (data?.results || []).map(edgeCandidate),
    // The function is fail-soft: one dead upstream still returns the other's
    // results plus a note. Surface the note rather than pretending it's whole.
    notes: data?.notes || [],
  };
}

export function useSearch() {
  const [query, setQuery] = useState('');
  const [candidates, setCandidates] = useState([]);
  const [status, setStatus] = useState('idle'); // idle | loading | ready | error
  const [notes, setNotes] = useState([]);
  const [error, setError] = useState('');
  // Debounced + out-of-order guard: a slow request for "SA" must never
  // overwrite the results for "SAP".
  const seq = useRef(0);

  useEffect(() => {
    const route = routeQuery(query);
    if (route === 'none') {
      setCandidates([]);
      setNotes([]);
      setStatus('idle');
      return;
    }

    const mine = ++seq.current;
    setStatus('loading');
    const timer = setTimeout(async () => {
      try {
        if (route === 'tase') {
          const rows = await searchTase(query);
          if (mine !== seq.current) return;
          setCandidates(rows);
          setNotes([]);
        } else {
          const { candidates: rows, notes: n } = await searchEdge(query);
          if (mine !== seq.current) return;
          setCandidates(rows);
          setNotes(n);
        }
        setError('');
        setStatus('ready');
      } catch (e) {
        if (mine !== seq.current) return;
        console.error('[search] failed', e);
        setCandidates([]);
        setError(e?.message || String(e));
        setStatus('error');
      }
    }, DEBOUNCE_MS);

    return () => clearTimeout(timer);
  }, [query]);

  function reset() {
    seq.current++; // cancel any in-flight result
    setQuery('');
    setCandidates([]);
    setNotes([]);
    setStatus('idle');
  }

  return { query, setQuery, candidates, status, notes, error, reset };
}
