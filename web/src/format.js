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

export function ccySymbol(currency, market) {
  if (currency === 'USD') return '$';
  if (currency === 'ILS') return '₪';
  // Fallback by market if currency is missing.
  return market === 'US' ? '$' : '₪';
}

// Display name (main line) + sub-line, driven by our real data.
export function displayName(sec) {
  return sec.market === 'US' ? sec.symbol : sec.name;
}

export function subLine(sec) {
  if (sec.market === 'US') {
    return `${sec.name} · US`;
  }
  const kind = sec.asset_type === 'bond' ? 'אג"ח' : 'ת"א';
  return `${sec.sec_id} · ${kind}`;
}
