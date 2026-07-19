import { useState } from 'react';
import { theme as t } from './theme';
import { RET_KEYS, ccySymbol, displayName, fmtPct, fmtPrice, retColor, subLine } from './format';
// Circular-safe: MoveArrows is a hoisted function export, only touched at render.
import { MoveArrows } from './Watchlist';

// Mobile watchlist card — follows the design_reference mockup's card intent
// (name+sub+badge / price+day / 4-col returns grid under a top border).
// Deviations from the mockup, because live data wins:
//   - a ✕ remove affordance (the mockup has none; removing must work on mobile)
//   - a "ממתין לנתונים" state for quote-less securities (mockup ignores it)
//   - dashes for manual-tier day change / NULL returns (mockup shows numbers)
//   - the NAME itself ellipsizes (live TASE names are full registered names,
//     e.g. בנק לאומי לישראל בע"מ — the mockup used short brands)

const mono = "'IBM Plex Mono', monospace";

export default function SecurityCard({ sec, onRemove, onOpen, editMode, onMove }) {
  const [pressed, setPressed] = useState(false);
  const q = sec.quote;
  const manual = sec.price_source === 'manual';
  const pending = q == null;

  return (
    <div
      onClick={() => onOpen?.(sec.sec_id)}
      onTouchStart={() => setPressed(true)}
      onTouchEnd={() => setPressed(false)}
      style={{
        background: t.surf,
        border: `1px solid ${pressed ? t.mut : t.bd}`,
        borderRadius: 12,
        padding: '14px 16px',
        display: 'flex',
        flexDirection: 'column',
        gap: 10,
        cursor: 'pointer',
        flexShrink: 0,
      }}
    >
      {/* name + sub + status badge + remove */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <div style={{ minWidth: 0, flex: 1, display: 'flex', alignItems: 'center', gap: 8 }}>
          <div
            dir="auto"
            style={{
              fontSize: 15,
              fontWeight: 700,
              color: t.txt,
              textAlign: 'right',
              minWidth: 0,
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}
          >
            {displayName(sec)}
          </div>
          <div
            dir="auto"
            style={{
              fontSize: 12,
              color: t.mut,
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              minWidth: 0,
              flexShrink: 0,
              maxWidth: '45%',
            }}
          >
            · {subLine(sec)}
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
        {editMode ? (
          <MoveArrows onUp={() => onMove(sec.sec_id, -1)} onDown={() => onMove(sec.sec_id, 1)} />
        ) : (
        <button
          onClick={(e) => {
            e.stopPropagation();
            onRemove?.(sec.sec_id);
          }}
          title="הסרה מהרשימה"
          style={{
            background: 'none',
            border: 'none',
            color: t.mut,
            fontSize: 18,
            cursor: 'pointer',
            width: 40,
            height: 40,
            margin: '-10px -12px -10px 0',
            flexShrink: 0,
            fontFamily: 'Heebo, sans-serif',
          }}
        >
          ×
        </button>
        )}
      </div>

      {/* price + day change */}
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12 }}>
        <div style={{ whiteSpace: 'nowrap' }}>
          <span dir="ltr" style={{ fontFamily: mono, fontSize: 17, fontWeight: 600, color: t.txt }}>
            {fmtPrice(q?.last_price)}
          </span>
          {q?.last_price != null && (
            <span style={{ fontSize: 12, color: t.mut }}> {ccySymbol(q?.currency, sec.market)}</span>
          )}
        </div>
        <div
          dir="ltr"
          style={{
            fontFamily: mono,
            fontSize: 13,
            fontWeight: 500,
            color: manual ? t.mut : retColor(q?.day_change_pct),
          }}
        >
          {manual ? '—' : fmtPct(q?.day_change_pct)}
        </div>
      </div>

      {/* returns */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(4, 1fr)',
          gap: 8,
          borderTop: `1px solid ${t.bd}`,
          paddingTop: 10,
        }}
      >
        {RET_KEYS.map((r) => (
          <div key={r.key} style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            <div style={{ fontSize: 10.5, color: t.mut }}>{r.label}</div>
            <div
              dir="ltr"
              style={{
                fontFamily: mono,
                fontSize: 12.5,
                fontWeight: 500,
                color: retColor(q?.[r.key]),
                textAlign: 'right',
              }}
            >
              {fmtPct(q?.[r.key])}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
