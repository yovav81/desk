import { useMemo, useState } from 'react';
import { theme as t } from './theme';
import { useNews } from './useNews';
import { FeedItem, Notice } from './FeedItem';
import { tsValue } from './format';

// Left panel: unified feed of four source types with three filter tabs.
// READ-ONLY. Watchlist sec_ids come in as a prop (reused from the watchlist
// fetch) to decide "my stocks" membership. Item rendering + badges live in
// FeedItem.jsx, shared with the detail page.

const TABS = [
  { key: 'mine', label: 'המניות שלי' },
  { key: 'macro', label: 'מאקרו וסקירות' },
  { key: 'all', label: 'הכל' },
];

export default function News({ watchSecIds = [], secLabels = {}, watchReady = true }) {
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
          shown.map((item) => (
            <FeedItem key={item.key} item={item} secLabel={item.sec_id ? secLabels[item.sec_id] : null} />
          ))}
      </div>
    </div>
  );
}

