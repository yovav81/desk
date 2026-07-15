import { theme as t } from './theme';

// Presentation helpers for the watchlist. Numbers come straight from the DB;
// quotes are already ILS-converted by the collector — never divide by 100 here.

export function fmtPrice(v) {
  if (v == null) return '—';
  return Number(v).toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

export function fmtPct(v) {
  if (v == null) return '—';
  return (v > 0 ? '+' : '') + Number(v).toFixed(1) + '%';
}

// Green gains, red losses, muted for zero/NULL. These are the FUNCTIONAL colors
// (never the gold accent).
export function retColor(v) {
  if (v == null) return t.mut;
  if (v > 0) return t.grn;
  if (v < 0) return t.red;
  return t.mut;
}

// quotes.currency is authoritative and already post-conversion (ILS never ILA,
// GBP never GBp) — trust it over the market. An unmapped currency shows its
// code rather than a wrong symbol.
const CCY_SYMBOL = {
  USD: '$',
  ILS: '₪',
  EUR: '€',
  GBP: '£',
  JPY: '¥',
  CHF: 'Fr',
};

export function ccySymbol(currency, market) {
  if (currency) return CCY_SYMBOL[currency] || currency;
  // Fallback only when currency is missing. GLOBAL spans many currencies —
  // guessing one would be a lie, so it gets nothing.
  if (market === 'US') return '$';
  if (market === 'TASE') return '₪';
  return '';
}

// Display name (main line) + sub-line, driven by our real data.
// GLOBAL must be handled explicitly everywhere: this file predates the GLOBAL
// market (added in 4a), and a bare `market === 'US' ? … : …` silently rendered
// every global security as TASE — DLGM.XD showed up as `DLGM.XD · ת"א`.
export function displayName(sec) {
  // Latin-market identity is the ticker; TASE's is the Hebrew name.
  return sec.market === 'US' || sec.market === 'GLOBAL' ? sec.symbol : sec.name;
}

export function subLine(sec) {
  if (sec.market === 'US') return `${sec.name} · US`;
  if (sec.market === 'GLOBAL') return `${sec.name} · GLOBAL`;
  const kind = sec.asset_type === 'bond' ? 'אג"ח' : 'ת"א';
  return `${sec.sec_id} · ${kind}`;
}

// Hebrew relative timestamp for feed items. null -> '' (rendered as nothing).
export function fmtRelative(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return '';
  const now = new Date();
  const diffMin = Math.floor((now - d) / 60000);
  if (diffMin < 1) return 'עכשיו';
  if (diffMin < 60) return `לפני ${diffMin} דק׳`;
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  if (d.toDateString() === now.toDateString()) {
    return `לפני ${Math.floor(diffMin / 60)} שעות`;
  }
  const yest = new Date(now);
  yest.setDate(now.getDate() - 1);
  if (d.toDateString() === yest.toDateString()) return `אתמול ${hh}:${mm}`;
  const dd = String(d.getDate()).padStart(2, '0');
  const mo = String(d.getMonth() + 1).padStart(2, '0');
  return `${dd}.${mo}.${d.getFullYear()}`;
}

export function tsValue(ts) {
  const v = ts ? Date.parse(ts) : NaN;
  return Number.isNaN(v) ? 0 : v; // nulls/invalid sort last
}
