import { useState } from 'react';
import { theme as t } from './theme';
import SearchBox from './SearchBox';
import {
  ccySymbol,
  displayName,
  fmtPct,
  fmtPrice,
  retColor,
  subLine,
} from './format';

// Desktop watchlist table (right/primary panel in RTL). Reads stay read-only;
// the only writes are watchlist add/remove, which App passes in as handlers.
// Data comes in as props so App can fetch the watchlist once and share the
// sec_ids with the news panel.

const RET_KEYS = [
  { key: 'mtd_pct', label: 'חודש' },
  { key: 'qtd_pct', label: 'רבעון' },
  { key: 'ytd_pct', label: 'שנה' },
  { key: 'y12_pct', label: "12ח׳" },
];

// name | price | day | mtd | qtd | ytd | y12 | remove
const GRID =
  'minmax(150px,1.6fr) minmax(84px,110px) minmax(58px,72px) repeat(4, minmax(52px,66px)) 32px';

const mono = "'IBM Plex Mono', monospace";

export default function Watchlist({ rows = [], status = 'loading', error = '', onAdd, onRemove, onOpen }) {
  const existingIds = rows.map((r) => r.sec_id);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0, minWidth: 0 }}>
      <div style={{ padding: '18px 24px 12px', display: 'flex', alignItems: 'baseline', gap: 10 }}>
        <div style={{ fontSize: 16, fontWeight: 700 }}>רשימת מעקב</div>
        <div style={{ fontSize: 12, color: t.mut }}>
          {status === 'ready' ? `· ${rows.length} ניירות` : ''}
        </div>
      </div>

      <SearchBox onAdd={onAdd} existingIds={existingIds} />

      {status === 'loading' && <Notice title="טוען…" />}
      {status === 'error' && (
        <Notice
          title="שגיאה בטעינת הנתונים"
          sub={error}
        />
      )}
      {status === 'ready' && rows.length === 0 && <Notice title="אין ניירות ברשימה" />}

      {status === 'ready' && rows.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
          <HeaderRow />
          <div style={{ display: 'flex', flexDirection: 'column', flex: 1, overflowY: 'auto', minHeight: 0 }}>
            {rows.map((sec) => (
              <Row key={sec.sec_id} sec={sec} onRemove={onRemove} onOpen={onOpen} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function HeaderRow() {
  const cell = { textAlign: 'left', fontSize: 11, color: t.mut };
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: GRID,
        gap: 6,
        alignItems: 'center',
        padding: '8px 24px',
        fontSize: 11,
        color: t.mut,
        borderBottom: `1px solid ${t.bd}`,
      }}
    >
      <div>נייר</div>
      <div style={cell}>מחיר</div>
      <div style={cell}>יומי</div>
      {RET_KEYS.map((r) => (
        <div key={r.key} style={cell}>
          {r.label}
        </div>
      ))}
      <div />
    </div>
  );
}

function Row({ sec, onRemove, onOpen }) {
  const [hover, setHover] = useState(false);
  const q = sec.quote;
  const manual = sec.price_source === 'manual';
  // No quotes row yet = the collectors haven't priced it (a just-added security,
  // up to ~15 min). Show that explicitly — a blank price is indistinguishable
  // from a broken one.
  const pending = q == null;
  const dayText = manual ? '—' : fmtPct(q?.day_change_pct);
  const dayColor = manual ? t.mut : retColor(q?.day_change_pct);

  return (
    <div
      onClick={() => onOpen?.(sec.sec_id)}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: 'grid',
        gridTemplateColumns: GRID,
        gap: 6,
        alignItems: 'center',
        padding: '11px 24px',
        borderBottom: `1px solid ${t.bd}`,
        cursor: onOpen ? 'pointer' : 'default',
        background: hover ? t.surf2 : 'transparent',
      }}
    >
      {/* name + sub + manual tag */}
      <div style={{ minWidth: 0, display: 'flex', alignItems: 'center', gap: 8 }}>
        <div style={{ minWidth: 0 }}>
          <div
            dir="auto"
            style={{
              fontSize: 14,
              fontWeight: 600,
              color: t.txt,
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              textAlign: 'right',
            }}
          >
            {displayName(sec)}
          </div>
          <div
            dir="auto"
            style={{
              fontSize: 11,
              color: t.mut,
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              textAlign: 'right',
            }}
          >
            {subLine(sec)}
          </div>
        </div>
        {pending ? (
          <div
            style={{
              fontSize: 10,
              fontWeight: 600,
              color: t.acc,
              border: `1px solid ${t.accDim}`,
              background: t.accSoft,
              borderRadius: 5,
              padding: '2px 6px',
              flexShrink: 0,
              whiteSpace: 'nowrap',
            }}
          >
            ממתין לנתונים
          </div>
        ) : (
          manual && (
            <div
              style={{
                fontSize: 10,
                fontWeight: 600,
                color: t.mut,
                border: `1px solid ${t.bd}`,
                borderRadius: 5,
                padding: '2px 6px',
                flexShrink: 0,
              }}
            >
              ידני
            </div>
          )
        )}
      </div>

      {/* price */}
      <div style={{ textAlign: 'left', whiteSpace: 'nowrap' }}>
        <span dir="ltr" style={{ fontFamily: mono, fontSize: 13, fontWeight: 500, color: t.txt }}>
          {fmtPrice(q?.last_price)}
        </span>
        {q?.last_price != null && (
          <span style={{ fontSize: 11, color: t.mut }}> {ccySymbol(q?.currency, sec.market)}</span>
        )}
      </div>

      {/* daily */}
      <div dir="ltr" style={{ textAlign: 'left', fontFamily: mono, fontSize: 12, fontWeight: 500, color: dayColor }}>
        {dayText}
      </div>

      {/* returns */}
      {RET_KEYS.map((r) => (
        <div
          key={r.key}
          dir="ltr"
          style={{ textAlign: 'left', fontFamily: mono, fontSize: 12, color: retColor(q?.[r.key]) }}
        >
          {fmtPct(q?.[r.key])}
        </div>
      ))}

      {/* remove — watchlist row only; the security and its news/filings stay */}
      <div style={{ textAlign: 'center' }}>
        <RemoveButton
          onClick={(e) => {
            // The row opens the detail page; the × must not do both.
            e.stopPropagation();
            onRemove?.(sec.sec_id);
          }}
        />
      </div>
    </div>
  );
}

function RemoveButton({ onClick }) {
  const [hover, setHover] = useState(false);
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      title="הסרה מהרשימה"
      style={{
        background: 'none',
        border: 'none',
        color: hover ? t.red : t.mut,
        fontSize: 15,
        cursor: 'pointer',
        padding: '2px 6px',
        borderRadius: 6,
        fontFamily: 'Heebo, sans-serif',
      }}
    >
      ×
    </button>
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
