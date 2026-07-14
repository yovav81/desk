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
