import { theme as t } from './theme';
import { fmtRelative } from './format';

// One feed item + its source-type badge, shared by the dashboard news panel
// (News.jsx) and the security detail page (Detail.jsx) so the four source types
// always look identical wherever they appear.

// web = the outlet name in gold; email/maya/sec get their own distinguishable
// badge so the four kinds are never confused.
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

// secLabel is omitted on the detail page: inside a single security, tagging
// every item with that security's own name is noise.
export function FeedItem({ item, secLabel }) {
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
        {secLabel && (
          <span
            dir="auto"
            style={{
              fontSize: 11,
              fontWeight: 600,
              color: t.txt,
              border: `1px solid ${t.accDim}`,
              borderRadius: 5,
              padding: '1px 8px',
            }}
          >
            {secLabel}
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

export function Notice({ title, sub }) {
  return (
    <div style={{ padding: '56px 24px', textAlign: 'center', display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ fontSize: 15, fontWeight: 600, color: t.txt }}>{title}</div>
      {sub && <div style={{ fontSize: 13, color: t.mut, wordBreak: 'break-word' }}>{sub}</div>}
    </div>
  );
}
