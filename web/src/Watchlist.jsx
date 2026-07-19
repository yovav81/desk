import { useState } from 'react';
import { theme as t } from './theme';
import SearchBox from './SearchBox';
import SecurityCard from './SecurityCard';
import {
  RET_KEYS,
  ccySymbol,
  displayName,
  fmtPct,
  fmtPrice,
  retColor,
  subLine,
} from './format';
// displayName doubles as the sort key for the name column (ticker for
// US/GLOBAL, Hebrew name for TASE — sorts what the user actually sees).

// Desktop watchlist table (right/primary panel in RTL). Reads stay read-only;
// the only writes are watchlist add/remove, which App passes in as handlers.
// Data comes in as props so App can fetch the watchlist once and share the
// sec_ids with the news panel.

// RET_KEYS (return-column keys+labels) lives in format.js, shared by this
// table and the mobile cards (SecurityCard.jsx) so the two can't drift.

// name | price | day | mtd | qtd | ytd | y12 | remove
const GRID =
  'minmax(150px,1.6fr) minmax(84px,110px) minmax(58px,72px) repeat(4, minmax(52px,66px)) 32px';

const mono = "'IBM Plex Mono', monospace";

// Edge shadows for the sticky cells, shown ONLY while x-scrolled so the
// unscrolled view stays pixel-equal to the pre-sticky layout. PHYSICAL values
// on purpose: box-shadow has no logical form, and the app is RTL-only
// (<html dir="rtl">) — the name cell sits at the RIGHT and casts LEFT
// (its inline-end); the ✕ cell sits at the LEFT and casts RIGHT.
const SHADOW_NAME_EDGE = '-8px 0 8px -8px rgba(0, 0, 0, 0.55)';
const SHADOW_REMOVE_EDGE = '8px 0 8px -8px rgba(0, 0, 0, 0.55)';

// Sortable columns (Phase 13). name = Hebrew-aware text; the rest read off the
// quote. Session-only state — default order stays insertion order.
const SORT_COLS = [
  { key: 'name', label: 'נייר', num: false },
  { key: 'last_price', label: 'מחיר', num: true },
  { key: 'day_change_pct', label: 'יומי', num: true },
  ...RET_KEYS.map((r) => ({ key: r.key, label: r.label, num: true })),
];

function sortValue(sec, key) {
  if (key === 'name') return displayName(sec) || null;
  // The daily cell renders '—' for manual-tier rows — sort them as NULL too.
  if (key === 'day_change_pct' && sec.price_source === 'manual') return null;
  return sec.quote?.[key] ?? null;
}

function applySort(rows, sort) {
  if (!sort) return rows;
  return [...rows].sort((a, b) => {
    const va = sortValue(a, sort.key);
    const vb = sortValue(b, sort.key);
    if (va == null || vb == null) return (va == null) - (vb == null); // NULLs last, both directions
    const c = sort.key === 'name' ? String(va).localeCompare(String(vb), 'he') : va - vb;
    return sort.dir === 'asc' ? c : -c;
  });
}

export default function Watchlist({ rows = [], status = 'loading', error = '', onAdd, onRemove, onOpen, onReorder, orderError = '', mobile = false }) {
  const existingIds = rows.map((r) => r.sec_id);
  // True while the table is horizontally scrolled — gates the sticky cells'
  // edge shadow so the UNscrolled (incl. full-width) view stays pixel-equal to
  // the pre-sticky layout. Math.abs: in RTL, browsers report leftward scroll
  // as NEGATIVE scrollLeft. React no-ops setState on an unchanged boolean, so
  // this is cheap even though vertical scrolling also fires the handler.
  const [xScrolled, setXScrolled] = useState(false);
  function onTableScroll(e) {
    setXScrolled(Math.abs(e.currentTarget.scrollLeft) > 1);
  }

  // Phase 13: filter THEN sort (they compose; both presentation-only).
  const [sort, setSort] = useState(null);
  const [query, setQuery] = useState('');
  function toggleSort(key, num) {
    setSort((s) =>
      s?.key === key ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: num ? 'desc' : 'asc' }
    );
  }
  // Reorder mode (Phase 13B): arrows move rows in the FULL manual order, so
  // entering it clears sort+filter (moving inside a filtered/sorted view would
  // be ambiguous). sort=null ⇒ rows arrive already in manual position order.
  const [editMode, setEditMode] = useState(false);
  function toggleEdit() {
    setEditMode((m) => !m);
    setSort(null);
    setQuery('');
  }
  function move(secId, delta) {
    const ids = rows.map((r) => r.sec_id);
    const i = ids.indexOf(secId);
    const j = i + delta;
    if (i < 0 || j < 0 || j >= ids.length) return;
    [ids[i], ids[j]] = [ids[j], ids[i]];
    onReorder?.(ids);
  }
  const q = query.trim().toLowerCase();
  const view = editMode
    ? rows
    : applySort(
        q
          ? rows.filter(
              (r) =>
                (r.name || '').toLowerCase().includes(q) || (r.symbol || '').toLowerCase().includes(q)
            )
          : rows,
        sort
      );
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
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', padding: mobile ? '0 16px 8px' : '0 24px 8px' }}>
          <button onClick={toggleEdit} style={ctlBtn(editMode)}>
            {editMode ? 'סיום סידור' : 'סידור'}
          </button>
          {!editMode && sort && (
            <button onClick={() => setSort(null)} style={ctlBtn(false)}>
              הסדר שלי
            </button>
          )}
          {/* In-list filter — clears via ✕ (inline-end in RTL = left) or Escape */}
          {!editMode && (
          <div style={{ position: 'relative', flex: 1, maxWidth: 320 }}>
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Escape' && setQuery('')}
              placeholder="סינון לפי שם או סימול…"
              style={{
                width: '100%', boxSizing: 'border-box', background: t.surf,
                border: `1px solid ${t.bd}`, borderRadius: 8, color: t.txt,
                fontSize: 13, fontFamily: 'Heebo, sans-serif',
                padding: '7px 12px', paddingInlineEnd: 30, outline: 'none',
              }}
            />
            {query && (
              <button
                onClick={() => setQuery('')}
                title="ניקוי"
                style={{
                  position: 'absolute', insetInlineEnd: 4, top: '50%', transform: 'translateY(-50%)',
                  background: 'none', border: 'none', color: t.mut, fontSize: 14,
                  cursor: 'pointer', padding: '2px 6px', fontFamily: 'Heebo, sans-serif',
                }}
              >
                ×
              </button>
            )}
          </div>
          )}
          {/* Mobile has no headers to click — sorting via a compact select */}
          {mobile && !editMode && (
            <select
              value={sort ? sort.key : ''}
              onChange={(e) => {
                const col = SORT_COLS.find((c) => c.key === e.target.value);
                setSort(col ? { key: col.key, dir: col.num ? 'desc' : 'asc' } : null);
              }}
              style={{
                background: t.surf, border: `1px solid ${t.bd}`, borderRadius: 8,
                color: t.txt, fontSize: 13, fontFamily: 'Heebo, sans-serif', padding: '7px 8px',
              }}
            >
              <option value="">הסדר שלי</option>
              {SORT_COLS.map((c) => (
                <option key={c.key} value={c.key}>{c.label}</option>
              ))}
            </select>
          )}
        </div>
      )}
      {orderError && (
        <div style={{ padding: mobile ? '0 16px 6px' : '0 24px 6px', fontSize: 12, color: t.red }}>{orderError}</div>
      )}
      {status === 'ready' && rows.length > 0 && view.length === 0 && <Notice title="אין תוצאות לסינון" />}

      {/* MOBILE: cards per the design_reference mockup (container: column,
          gap 10, padding 6/16/16, y-scroll with momentum). The desktop branch
          below is byte-identical to before the mobile build. */}
      {status === 'ready' && rows.length > 0 && mobile && (
        <div
          className="momentum"
          style={{
            flex: 1,
            minHeight: 0,
            overflowY: 'auto',
            overflowX: 'hidden',
            display: 'flex',
            flexDirection: 'column',
            gap: 10,
            padding: '6px 16px 16px',
          }}
        >
          {view.map((sec) => (
            <SecurityCard key={sec.sec_id} sec={sec} onRemove={onRemove} onOpen={onOpen} editMode={editMode} onMove={move} />
          ))}
        </div>
      )}

      {status === 'ready' && rows.length > 0 && !mobile && (
        // ONE shared scroller for BOTH axes: header + rows live inside it, so
        // they x-scroll together (kills the header-bleed bug where the header
        // stayed in flow while rows scrolled). The header keeps its old
        // "fixed above the rows" feel via position:sticky top:0.
        <div onScroll={onTableScroll} style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
          {/* ALIGNMENT GUARANTEE: header and every row share the same GRID
              template AND stretch to this wrapper's width (flex column,
              default align-stretch). minWidth:'min-content' makes the wrapper
              at least as wide as the widest child's track floors, so all
              children resolve identical track sizes — they cannot misalign,
              because neither the template nor the available width can differ. */}
          <div style={{ minWidth: 'min-content', display: 'flex', flexDirection: 'column' }}>
            <HeaderRow xScrolled={xScrolled} sort={sort} onSort={toggleSort} />
            {view.map((sec) => (
              <Row key={sec.sec_id} sec={sec} onRemove={onRemove} onOpen={onOpen} xScrolled={xScrolled} editMode={editMode} onMove={move} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// Clickable sort header — keyboard accessible (focusable, Enter/Space toggles).
// The ▲/▼ rides the text flow, so RTL places it on the label's inline-end side
// with no absolute positioning to break the sticky column.
function SortLabel({ col, sort, onSort, style }) {
  const active = sort?.key === col.key;
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => onSort(col.key, col.num)}
      onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), onSort(col.key, col.num))}
      style={{ cursor: 'pointer', userSelect: 'none', color: active ? t.txt : t.mut, ...style }}
    >
      {col.label}
      {active && <span style={{ fontSize: 8, marginInlineStart: 3 }}>{sort.dir === 'asc' ? '▲' : '▼'}</span>}
    </div>
  );
}

function HeaderRow({ xScrolled, sort, onSort }) {
  const cell = { textAlign: 'left', fontSize: 11, color: t.mut };
  const [nameCol, priceCol, ...retCols] = SORT_COLS;
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
        // The header lives INSIDE the shared scroller now; sticky top keeps its
        // old fixed-above-the-rows behaviour while y-scrolling. Opaque bg so
        // rows never show through. zIndex 2 = above the rows' sticky cells (1).
        position: 'sticky',
        top: 0,
        zIndex: 2,
        background: t.bg,
      }}
    >
      {/* Corner cell: sticky on BOTH axes (top via the parent, inline via
          itself). insetInlineStart resolves to RIGHT under dir=rtl. */}
      <SortLabel
        col={nameCol}
        sort={sort}
        onSort={onSort}
        style={{
          position: 'sticky',
          insetInlineStart: 0,
          zIndex: 1,
          background: t.bg,
          boxShadow: xScrolled ? SHADOW_NAME_EDGE : 'none',
        }}
      />
      <SortLabel col={priceCol} sort={sort} onSort={onSort} style={cell} />
      {retCols.map((c) => (
        <SortLabel key={c.key} col={c} sort={sort} onSort={onSort} style={cell} />
      ))}
      {/* The ✕ column's header slot — sticky at the opposite edge, like the
          cells below it, so the corner stays clean while x-scrolling. */}
      <div
        style={{
          position: 'sticky',
          insetInlineEnd: 0,
          zIndex: 1,
          background: t.bg,
          boxShadow: xScrolled ? SHADOW_REMOVE_EDGE : 'none',
        }}
      />
    </div>
  );
}

function Row({ sec, onRemove, onOpen, xScrolled, editMode, onMove }) {
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
      {/* name + sub + manual/pending tag — STICKY: never scrolls out of view.
          insetInlineStart = RIGHT under dir=rtl. Opaque bg (it slides over the
          other cells) must track the row's hover color or it would visibly
          mismatch while scrolled; the edge shadow only appears when actually
          scrolled, keeping the resting view pixel-equal to before. */}
      <div
        style={{
          minWidth: 0,
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          position: 'sticky',
          insetInlineStart: 0,
          zIndex: 1,
          background: hover ? t.surf2 : t.bg,
          boxShadow: xScrolled ? SHADOW_NAME_EDGE : 'none',
        }}
      >
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

      {/* remove — watchlist row only; the security and its news/filings stay.
          Sticky at the OPPOSITE edge (insetInlineEnd = LEFT in RTL): removing a
          security must never require scrolling back, and since this column
          already sits at the far left, pinning it changes nothing at full
          width. The ידני/pending tag travels with the name cell above. */}
      <div
        style={{
          textAlign: 'center',
          position: 'sticky',
          insetInlineEnd: 0,
          zIndex: 1,
          background: hover ? t.surf2 : t.bg,
          boxShadow: xScrolled ? SHADOW_REMOVE_EDGE : 'none',
        }}
      >
        {editMode ? (
          <MoveArrows onUp={() => onMove(sec.sec_id, -1)} onDown={() => onMove(sec.sec_id, 1)} />
        ) : (
          <RemoveButton
            onClick={(e) => {
              // The row opens the detail page; the × must not do both.
              e.stopPropagation();
              onRemove?.(sec.sec_id);
            }}
          />
        )}
      </div>
    </div>
  );
}

// Control-button style ("סידור" / "הסדר שלי"). Gold accent marks the ACTIVE
// reorder mode (per theme rules: accent for active controls, never grn/red).
function ctlBtn(active) {
  return {
    background: active ? t.accSoft : t.surf,
    border: `1px solid ${active ? t.accDim : t.bd}`,
    color: active ? t.acc : t.txt,
    borderRadius: 8, fontSize: 13, fontFamily: 'Heebo, sans-serif',
    padding: '7px 12px', cursor: 'pointer', whiteSpace: 'nowrap', flexShrink: 0,
  };
}

// Native <button>s: focusable, Enter/Space activate for free. stopPropagation
// on the wrapper so a move never also opens the detail page.
export function MoveArrows({ onUp, onDown }) {
  const b = {
    background: 'none', border: `1px solid ${t.bd}`, borderRadius: 5, color: t.txt,
    fontSize: 10, cursor: 'pointer', padding: '2px 5px', fontFamily: 'Heebo, sans-serif',
  };
  return (
    <div style={{ display: 'flex', gap: 3, flexShrink: 0 }} onClick={(e) => e.stopPropagation()}>
      <button title="הזזה למעלה" onClick={onUp} style={b}>▲</button>
      <button title="הזזה למטה" onClick={onDown} style={b}>▼</button>
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
