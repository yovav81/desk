import { useState } from 'react';
import { theme as t } from './theme';
import { supabase } from './supabaseClient';
import { fmtRelative, fmtSize } from './format';

// One feed item + its source-type badge, shared by the dashboard news panel
// (News.jsx) and the security detail page (Detail.jsx) so the four source types
// always look identical wherever they appear.
//
// EMAIL rows expand in place (accordion) to show the stored plain-text body +
// attachment chips; maya/sec/web rows keep their old behaviour exactly.
// Multi-open by design: state lives inside each row, so there is nothing to
// coordinate across the two parents (News and Detail), and rows keyed by
// item.key keep their open/cache state across the 3-min auto-refresh.
// Lazy: the feed list never loads bodies — the fetch happens on FIRST expand
// only and is cached in the row for the session; signed URLs are minted per
// CLICK on a chip, never pre-minted.

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
  const isEmail = item.type === 'email';
  const [open, setOpen] = useState(false);
  // idle | loading | ready | error — 'ready' is the session cache: collapsing
  // and re-expanding never refetches. 'error' allows a retry on next expand.
  const [content, setContent] = useState({ status: 'idle', body: '', atts: [] });

  async function toggleOpen() {
    const next = !open;
    setOpen(next);
    if (!next || content.status === 'ready' || content.status === 'loading') return;

    setContent({ status: 'loading', body: '', atts: [] });
    // Two flat queries, not a PostgREST embed — FK relationships on the
    // raw-created tables aren't detected (documented gotcha; embeds return
    // null joins). The list query itself is untouched: body stays lazy.
    const [bodyRes, attRes] = await Promise.all([
      supabase.from('emails').select('body_text').eq('id', item.emailId).maybeSingle(),
      supabase
        .from('email_attachments')
        .select('filename, size_bytes, content_type, storage_path')
        .eq('email_id', item.emailId),
    ]);
    if (bodyRes.error || attRes.error) {
      setContent({ status: 'error', body: '', atts: [] });
      return;
    }
    setContent({
      status: 'ready',
      body: bodyRes.data?.body_text || '',
      atts: attRes.data || [],
    });
  }

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
      ) : isEmail ? (
        // Email title row: clickable accordion toggle with a chevron affordance
        // (min 44px tap target via padding on mobile-sized fingers).
        <div
          onClick={toggleOpen}
          style={{
            display: 'flex',
            alignItems: 'flex-start',
            gap: 8,
            cursor: 'pointer',
            padding: '4px 0',
            minHeight: 28,
          }}
        >
          <span
            style={{
              color: t.mut,
              fontSize: 11,
              lineHeight: '20px',
              flexShrink: 0,
              display: 'inline-block',
              transform: open ? 'rotate(90deg)' : 'none',
              transition: 'transform .15s ease',
            }}
          >
            ◄
          </span>
          <div dir="auto" style={{ fontSize: 14, color: t.txt, lineHeight: 1.45, textAlign: 'right', minWidth: 0 }}>
            {item.title}
          </div>
        </div>
      ) : (
        <div dir="auto" style={{ fontSize: 14, color: t.txt, lineHeight: 1.45, textAlign: 'right' }}>
          {item.title}
        </div>
      )}

      {isEmail && open && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, paddingInlineStart: 4 }}>
          {content.status === 'loading' && (
            <div style={{ fontSize: 13, color: t.mut }}>טוען…</div>
          )}
          {content.status === 'error' && (
            <div style={{ fontSize: 13, color: t.red }}>שגיאה בטעינת המייל</div>
          )}
          {content.status === 'ready' && (
            <>
              {content.atts.length > 0 && (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                  {content.atts.map((att) => (
                    <AttachmentChip key={att.filename} att={att} />
                  ))}
                </div>
              )}
              {content.body ? (
                // Plain text only — nothing to sanitize (HTML was stripped at
                // collect time). pre-wrap keeps the newsletter's line breaks;
                // overflowWrap breaks long URLs/English tokens so the panel
                // never scrolls horizontally (mobile requirement).
                <div
                  dir="auto"
                  style={{
                    whiteSpace: 'pre-wrap',
                    overflowWrap: 'anywhere',
                    fontSize: 13,
                    lineHeight: 1.55,
                    color: t.txt,
                    background: t.surf2,
                    border: `1px solid ${t.bd}`,
                    borderRadius: 10,
                    padding: '10px 14px',
                    maxHeight: 320,
                    overflowY: 'auto',
                    textAlign: 'right',
                  }}
                >
                  {content.body}
                </div>
              ) : (
                content.atts.length === 0 && (
                  <div style={{ fontSize: 13, color: t.mut }}>אין תוכן להצגה</div>
                )
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

// One attachment: chip with the ORIGINAL (Hebrew) filename + size. Click mints
// a signed URL (60s) and opens a new tab. storage_path NULL = oversize file
// that was never stored — rendered disabled with the note.
function AttachmentChip({ att }) {
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const stored = Boolean(att.storage_path);

  async function openFile() {
    if (!stored || busy) return;
    setBusy(true);
    setFailed(false);
    // Open the window SYNCHRONOUSLY inside the click gesture, then navigate it
    // after the await — window.open after an await gets popup-blocked (Safari
    // especially). The signed URL is minted per click, never pre-minted.
    const win = window.open('', '_blank');
    if (win) win.opener = null;
    const { data, error } = await supabase.storage
      .from('email-attachments')
      .createSignedUrl(att.storage_path, 60);
    setBusy(false);
    if (error || !data?.signedUrl) {
      if (win) win.close();
      setFailed(true);
      return;
    }
    if (win) win.location = data.signedUrl;
  }

  return (
    <button
      onClick={openFile}
      disabled={!stored}
      title={att.filename}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        maxWidth: '100%',
        padding: '9px 12px', // ~40px tall — thumb-friendly on mobile
        borderRadius: 8,
        border: `1px solid ${stored ? t.accDim : t.bd}`,
        background: stored ? t.accSoft : 'transparent',
        color: stored ? t.txt : t.mut,
        fontSize: 12.5,
        fontFamily: 'Heebo, sans-serif',
        cursor: stored ? 'pointer' : 'default',
        opacity: busy ? 0.6 : 1,
      }}
    >
      <span
        dir="auto"
        style={{
          maxWidth: '40ch',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}
      >
        {att.filename}
      </span>
      <span style={{ color: t.mut, flexShrink: 0 }}>{fmtSize(att.size_bytes)}</span>
      {!stored && (
        <span style={{ color: t.mut, flexShrink: 0 }}>· קובץ גדול מדי — לא נשמר</span>
      )}
      {failed && <span style={{ color: t.red, flexShrink: 0 }}>שגיאה</span>}
    </button>
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
