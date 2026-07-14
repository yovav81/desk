import { useMemo, useState } from 'react';
import { theme as t } from './theme';
import { useNews } from './useNews';
import { fmtRelative, tsValue } from './format';

// Left panel: unified feed of four source types with three filter tabs.
// READ-ONLY. Watchlist sec_ids come in as a prop (reused from the watchlist
// fetch) to decide "my stocks" membership.

const TABS = [
  { key: 'mine', label: 'המניות שלי' },
  { key: 'macro', label: 'מאקרו וסקירות' },
  { key: 'all', label: 'הכל' },
];

// Source-type badge styling. web = the outlet name in gold; email/maya/sec get
// their own distinguishable badge so the four kinds are never confused.
function badgeFor(item) {
  switch (item.type) {
    case 'email':
      return { label: 'מייל', col: '#F0B429', bg: 'rgba(240,180,41,.14)', sub: item.source };
    case 'maya':
      return { label: 'מאיה', col: t.grn, bg: 'rgba(43,217,128,.12)' };
    case 'sec':
      return { label: 'SEC', col: '#6FA9FF', bg: 'rgba(111,169,255,.14)' };
    default: // web news — badge is the outlet name
      return { label: item.source || 'חדשות', col: t.acc, bg: t.accSoft };
  }
}

export default function News({ watchSecIds = [], watchReady = true }) {
  const { items, status, error } = useNews();
  const [tab, setTab] = useState('all'); // default: הכל

  const shown = useMemo(() => {
    const watch = new Set(watchSecIds);
    const inWatch = (it) => it.sec_id != null && watch.has(it.sec_id);

    // "My stocks": all four types whose sec_id is in the watchlist (web news
    // must be category 'stock'); "Macro": macro web news + unassigned emails.
    const mine = items.filter((it) =>
      it.type === 'web' ? it.category === 'stock' && inWatch(it) : inWatch(it)
    );
    const macro = items.filter((it) =>
      (it.type === 'web' && it.category === 'macro') || (it.type === 'email' && it.sec_id == null)
    );

    let list;
    if (tab === 'mine') list = mine;
    else if (tab === 'macro') list = macro;
    else {
      const seen = new Set();
      list = [...mine, ...macro].filter((it) => (seen.has(it.key) ? false : seen.add(it.key)));
    }
    return [...list].sort((a, b) => tsValue(b.ts) - tsValue(a.ts));
  }, [items, watchSecIds, tab]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minWidth: 0, minHeight: 0 }}>
      {/* header + tabs */}
      <div
        style={{
          padding: '18px 24px 12px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 12,
          flexWrap: 'wrap',
        }}
      >
        <div style={{ fontSize: 16, fontWeight: 700 }}>חדשות</div>
        <div style={{ display: 'flex', gap: 6 }}>
          {TABS.map((tb) => {
            const active = tab === tb.key;
            return (
              <button
                key={tb.key}
                onClick={() => setTab(tb.key)}
                style={{
                  padding: '6px 13px',
                  borderRadius: 999,
                  fontSize: 12.5,
                  fontWeight: 500,
                  fontFamily: 'Heebo, sans-serif',
                  cursor: 'pointer',
                  whiteSpace: 'nowrap',
                  border: `1px solid ${active ? t.acc : t.bd}`,
                  background: active ? t.accSoft : 'transparent',
                  color: active ? t.acc : t.mut,
                }}
              >
                {tb.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* body */}
      <div style={{ flex: 1, overflowY: 'auto', minHeight: 0, padding: '0 24px 24px', display: 'flex', flexDirection: 'column' }}>
        {status === 'loading' && <Notice title="טוען…" />}
        {status === 'error' && <Notice title="שגיאה בטעינת החדשות" sub={error} />}
        {status === 'ready' && (!watchReady && tab !== 'macro') && <Notice title="טוען…" />}
        {status === 'ready' && (watchReady || tab === 'macro') && shown.length === 0 && (
          <Notice title="אין חדשות להצגה" />
        )}
        {status === 'ready' &&
          (watchReady || tab === 'macro') &&
          shown.map((item) => <FeedItem key={item.key} item={item} />)}
      </div>
    </div>
  );
}

function FeedItem({ item }) {
  const b = badgeFor(item);
  const time = fmtRelative(item.ts);
  return (
    <div style={{ padding: '14px 0', borderBottom: `1px solid ${t.bd}`, display: 'flex', flexDirection: 'column', gap: 6 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span
          dir="auto"
          style={{ fontSize: 11, fontWeight: 600, color: b.col, background: b.bg, borderRadius: 5, padding: '2px 8px' }}
        >
          {b.label}
        </span>
        {b.sub && (
          <span dir="auto" style={{ fontSize: 11.5, color: t.mut, maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {b.sub}
          </span>
        )}
        {time && <span style={{ fontSize: 11.5, color: t.mut }}>{time}</span>}
      </div>
      {item.url ? (
        <a
          href={item.url}
          target="_blank"
          rel="noreferrer"
          dir="auto"
          style={{ fontSize: 14, color: t.txt, lineHeight: 1.45, textAlign: 'right', textDecoration: 'none' }}
        >
          {item.title}
        </a>
      ) : (
        <div dir="auto" style={{ fontSize: 14, color: t.txt, lineHeight: 1.45, textAlign: 'right' }}>
          {item.title}
        </div>
      )}
    </div>
  );
}

function Notice({ title, sub }) {
  return (
    <div style={{ padding: '56px 24px', textAlign: 'center', display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ fontSize: 15, fontWeight: 600, color: t.txt }}>{title}</div>
      {sub && <div style={{ fontSize: 13, color: t.mut, wordBreak: 'break-word' }}>{sub}</div>}
    </div>
  );
}
