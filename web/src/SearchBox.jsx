import { useEffect, useRef, useState } from 'react';
import { theme as t } from './theme';
import { routeQuery, useSearch } from './useSearch';

// Search + candidate picker for adding a security. Layout/colors mirror
// design_reference (search input + absolutely-positioned dropdown).
//
// NEVER auto-picks: even a single candidate is presented as a list item the
// user must click. Same-ticker collisions (SAP SE vs Saputo) are valid-but-
// different companies and only a human can disambiguate them.

const BADGE_LABEL = { 'ת"א': 'ת"א', US: 'US', GLOBAL: 'GLOBAL' };

export default function SearchBox({ onAdd, existingIds = [] }) {
  const { query, setQuery, candidates, status, notes, error, reset } = useSearch();
  const [focus, setFocus] = useState(false);
  const [open, setOpen] = useState(false);
  const [msg, setMsg] = useState(null); // { kind: 'ok'|'warn'|'err', text }
  const boxRef = useRef(null);

  // Close the dropdown on an outside click / Escape.
  useEffect(() => {
    function onDocDown(e) {
      if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false);
    }
    function onKey(e) {
      if (e.key === 'Escape') setOpen(false);
    }
    document.addEventListener('mousedown', onDocDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDocDown);
      document.removeEventListener('keydown', onKey);
    };
  }, []);

  async function pick(cand) {
    if (existingIds.includes(cand.sec_id)) {
      setMsg({ kind: 'warn', text: 'הנייר כבר ברשימה' });
      return;
    }
    setMsg(null);
    const res = await onAdd(cand);
    if (res?.ok) {
      reset();
      setOpen(false);
      setMsg({ kind: 'ok', text: `${cand.name} נוסף לרשימה` });
    } else if (res?.reason === 'duplicate') {
      setMsg({ kind: 'warn', text: 'הנייר כבר ברשימה' });
    } else {
      // Fail-soft: keep the query so the user can retry, and say what broke.
      setMsg({ kind: 'err', text: `ההוספה נכשלה: ${res?.message || 'שגיאה'}` });
    }
  }

  // Gate on the same rule that decides whether a search actually runs — a
  // hardcoded length here would re-break single-letter tickers ("C").
  const showDropdown = open && routeQuery(query) !== 'none';

  return (
    <div ref={boxRef} style={{ position: 'relative', padding: '0 24px 8px' }}>
      <input
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
          setMsg(null);
        }}
        onFocus={() => {
          setFocus(true);
          setOpen(true);
        }}
        onBlur={() => setFocus(false)}
        placeholder="הוסף נייר — שם, סימבול או מספר נייר"
        style={{
          width: '100%',
          background: t.surf2,
          border: `1px solid ${focus ? t.acc : t.bd}`,
          borderRadius: 10,
          padding: '11px 14px',
          fontSize: 14,
          color: t.txt,
          fontFamily: 'Heebo, sans-serif',
          outline: 'none',
        }}
      />

      {msg && (
        <div
          style={{
            fontSize: 12,
            marginTop: 6,
            color: msg.kind === 'err' ? t.red : msg.kind === 'warn' ? t.mut : t.acc,
          }}
        >
          {msg.text}
        </div>
      )}

      {showDropdown && (
        <div
          style={{
            position: 'absolute',
            top: '100%',
            right: 24,
            left: 24,
            marginTop: -4,
            background: t.surf,
            border: `1px solid ${t.bd}`,
            borderRadius: 12,
            boxShadow: '0 16px 40px rgba(0,0,0,.5)',
            zIndex: 30,
            overflow: 'hidden',
            maxHeight: 340,
            overflowY: 'auto',
          }}
        >
          {status === 'loading' && <Info text="מחפש…" />}
          {status === 'error' && <Info text={`החיפוש נכשל: ${error}`} tone="err" />}
          {status === 'ready' && candidates.length === 0 && <Info text="לא נמצאו תוצאות" />}

          {candidates.map((c) => (
            <Candidate key={c.key} c={c} onPick={pick} already={existingIds.includes(c.sec_id)} />
          ))}

          {/* The Edge Function degraded (one upstream down) — say so rather than
              silently showing a short list. */}
          {notes.map((n) => (
            <Info key={n} text={n} tone="warn" />
          ))}
        </div>
      )}
    </div>
  );
}

function Candidate({ c, onPick, already }) {
  const [hover, setHover] = useState(false);
  return (
    <div
      onClick={() => onPick(c)}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: 10,
        padding: '11px 16px',
        cursor: 'pointer',
        borderBottom: `1px solid ${t.bd}`,
        background: hover ? t.accSoft : 'transparent',
      }}
    >
      <div style={{ minWidth: 0, display: 'flex', alignItems: 'center', gap: 8 }}>
        <Badge label={BADGE_LABEL[c.badge] || c.badge} />
        <div style={{ minWidth: 0 }}>
          <div
            dir="auto"
            style={{
              fontSize: 14,
              fontWeight: 600,
              color: t.txt,
              textAlign: 'right',
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}
          >
            {c.name}
          </div>
          <div
            dir="auto"
            style={{
              fontSize: 12,
              color: t.mut,
              textAlign: 'right',
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}
          >
            {c.sub}
          </div>
        </div>
      </div>
      <div style={{ fontSize: 13, color: already ? t.mut : t.acc, flexShrink: 0 }}>
        {already ? 'ברשימה' : '+ הוסף'}
      </div>
    </div>
  );
}

function Badge({ label }) {
  return (
    <div
      dir="auto"
      style={{
        fontSize: 10,
        fontWeight: 600,
        color: t.mut,
        border: `1px solid ${t.bd}`,
        borderRadius: 5,
        padding: '2px 6px',
        flexShrink: 0,
        whiteSpace: 'nowrap',
      }}
    >
      {label}
    </div>
  );
}

function Info({ text, tone }) {
  return (
    <div
      dir="auto"
      style={{
        padding: '14px 16px',
        fontSize: 13,
        color: tone === 'err' ? t.red : t.mut,
        textAlign: 'right',
      }}
    >
      {text}
    </div>
  );
}
