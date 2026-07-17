import { useEffect, useRef, useState } from 'react';
import { supabase } from './supabaseClient';
import { theme as t } from './theme';
import Watchlist from './Watchlist';
import News from './News';
import Detail from './Detail';
import { useWatchlist } from './useWatchlist';

// STEP 5b: login + two-panel dashboard — watchlist table (right, with search +
// add/remove) and the unified news/email/filings feed (left) with three filter
// tabs — plus the full-screen security detail page (chart + numbers + that
// security's feed) reached by clicking a watchlist row. Reads are READ-ONLY
// from Supabase; the only writes are watchlist add/remove (see useWatchlist).
// Styling mirrors design_reference/ (Ocean theme, gold accent), our own clean
// implementation.

export default function App() {
  const [session, setSession] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setLoading(false);
    });
    const { data: sub } = supabase.auth.onAuthStateChange((_event, s) => {
      setSession(s);
    });
    return () => sub.subscription.unsubscribe();
  }, []);

  if (loading) return <Splash />;
  return session ? <Dashboard session={session} /> : <Login />;
}

function Splash() {
  return (
    <div style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', color: t.mut }}>
      טוען…
    </div>
  );
}

function Brand({ size = 22, dotSize = 12 }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
      <div style={{ width: dotSize, height: dotSize, borderRadius: 4, background: t.acc }} />
      <div style={{ fontSize: size, fontWeight: 700, color: t.txt, letterSpacing: '-0.3px' }}>
        GOLD
      </div>
      <div style={{ fontSize: 13, color: t.mut, marginTop: 3 }}>מעקב ניירות ערך</div>
    </div>
  );
}

function Login() {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  async function onSubmit(e) {
    e.preventDefault();
    setErr('');
    if (!username.trim() || !password.trim()) {
      setErr('יש להזין שם משתמש וסיסמה');
      return;
    }
    setBusy(true);
    // Supabase Auth is email-based: the "שם משתמש" value is the user's email.
    const { error } = await supabase.auth.signInWithPassword({
      email: username.trim(),
      password,
    });
    setBusy(false);
    if (error) setErr('התחברות נכשלה — בדקו שם משתמש וסיסמה');
    // On success, onAuthStateChange swaps to the placeholder.
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: `radial-gradient(1200px 600px at 50% -10%, ${t.accSoft}, transparent 60%), ${t.bg}`,
        padding: 24,
      }}
    >
      <form
        onSubmit={onSubmit}
        style={{
          width: '100%',
          maxWidth: 380,
          background: t.surf,
          border: `1px solid ${t.bd}`,
          borderRadius: 18,
          padding: '36px 32px',
          display: 'flex',
          flexDirection: 'column',
          gap: 20,
          animation: 'fadeUp .4s ease',
        }}
      >
        <Brand />

        <Field
          label="שם משתמש"
          value={username}
          onChange={(e) => {
            setUsername(e.target.value);
            setErr('');
          }}
          autoComplete="username"
        />
        <Field
          label="סיסמה"
          type="password"
          value={password}
          onChange={(e) => {
            setPassword(e.target.value);
            setErr('');
          }}
          autoComplete="current-password"
        />

        {err && <div style={{ fontSize: 13, color: t.red }}>{err}</div>}

        <button
          type="submit"
          disabled={busy}
          style={{
            background: t.acc,
            color: '#08101F',
            border: 'none',
            borderRadius: 10,
            padding: 13,
            fontSize: 15,
            fontWeight: 600,
            fontFamily: 'Heebo, sans-serif',
            cursor: busy ? 'default' : 'pointer',
            opacity: busy ? 0.7 : 1,
          }}
        >
          {busy ? 'מתחבר…' : 'כניסה'}
        </button>
      </form>
    </div>
  );
}

function Field({ label, ...props }) {
  const [focus, setFocus] = useState(false);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <label style={{ fontSize: 13, color: t.mut }}>{label}</label>
      <input
        {...props}
        onFocus={() => setFocus(true)}
        onBlur={() => setFocus(false)}
        style={{
          background: t.surf2,
          border: `1px solid ${focus ? t.acc : t.bd}`,
          borderRadius: 10,
          padding: '12px 14px',
          fontSize: 15,
          color: t.txt,
          fontFamily: 'Heebo, sans-serif',
          outline: 'none',
        }}
      />
    </div>
  );
}

const REFRESH_MS = 3 * 60 * 1000; // slow poll — collectors write every 74-128 min

// Panel split bounds: the watchlist may occupy 25%–75% of the row. ONE clamp
// shared by the drag and keyboard paths.
const SPLIT_MIN = 25;
const SPLIT_MAX = 75;
const clampSplit = (pct) => Math.min(SPLIT_MAX, Math.max(SPLIT_MIN, pct));

// Draggable boundary between the panels. Desktop-only via CSS (index.css:
// below 760px it degrades to the old inert 1px line). RTL-SAFE MATH: the
// watchlist is the RIGHT panel, so its width is the distance from the pointer
// to the container's RIGHT edge — absolute viewport geometry (clientX and
// getBoundingClientRect ignore dir=rtl), NEVER movementX deltas, whose sign
// conventions are the classic inverted-drag bug.
function SplitDivider({ containerRef, pct, onResize }) {
  // If Dashboard unmounts mid-drag (e.g. logout), don't leave text selection
  // disabled on the whole page.
  useEffect(() => () => {
    document.body.style.userSelect = '';
  }, []);

  function dragTo(e) {
    const rect = containerRef.current?.getBoundingClientRect();
    if (!rect || rect.width === 0) return;
    onResize(clampSplit(((rect.right - e.clientX) / rect.width) * 100));
  }

  return (
    <div
      className="split-divider"
      role="separator"
      aria-orientation="vertical"
      aria-label="שינוי חלוקת הפאנלים"
      aria-valuemin={SPLIT_MIN}
      aria-valuemax={SPLIT_MAX}
      aria-valuenow={Math.round(pct)}
      tabIndex={0}
      onPointerDown={(e) => {
        e.preventDefault();
        // Capture routes every subsequent pointer event to this element until
        // release — no window listeners, nothing to leak.
        e.currentTarget.setPointerCapture(e.pointerId);
        document.body.style.userSelect = 'none';
      }}
      onPointerMove={(e) => {
        if (!e.currentTarget.hasPointerCapture(e.pointerId)) return; // hover, not drag
        dragTo(e);
      }}
      onPointerUp={() => {
        // Capture auto-releases on pointerup; just restore selection.
        document.body.style.userSelect = '';
      }}
      onPointerCancel={() => {
        document.body.style.userSelect = '';
      }}
      onKeyDown={(e) => {
        // Locked mapping: the arrow points at the panel that GROWS. The
        // watchlist sits on the RIGHT (RTL), so ArrowRight enlarges it.
        if (e.key === 'ArrowRight') {
          e.preventDefault();
          onResize(clampSplit(pct + 2));
        } else if (e.key === 'ArrowLeft') {
          e.preventDefault();
          onResize(clampSplit(pct - 2));
        }
      }}
    >
      <div className="split-divider-line" />
    </div>
  );
}

function Dashboard({ session }) {
  // Bumped to trigger a refetch; threaded into the data hooks' effect deps so a
  // single timer drives both (no duplicate timers, no duplicated query logic).
  const [refreshTick, setRefreshTick] = useState(0);
  // The logged-in auth user drives the watchlist — no hardcoded 'owner'.
  const wl = useWatchlist(session.user, refreshTick);
  // Which security's detail page is open (null = the dashboard). One page, so
  // plain state beats pulling in a router.
  const [openSecId, setOpenSecId] = useState(null);
  // Panel split: % of the row the watchlist occupies. Component state ONLY —
  // resets on reload by design (persisting would need a server-side per-user
  // pref; localStorage is banned by project rule).
  const [wlPct, setWlPct] = useState(56);
  const splitRef = useRef(null);

  // Auto-refresh: refetch when the tab regains visibility, plus a slow interval
  // as a backstop for a tab left open. Gated on visibilityState so a hidden tab
  // issues zero requests (free-tier friendly). One interval + one listener,
  // both torn down on unmount — no leak, no stacking.
  useEffect(() => {
    const bump = () => {
      if (document.visibilityState === 'visible') setRefreshTick((n) => n + 1);
    };
    document.addEventListener('visibilitychange', bump);
    const id = setInterval(bump, REFRESH_MS);
    return () => {
      document.removeEventListener('visibilitychange', bump);
      clearInterval(id);
    };
  }, []);
  const watchSecIds = wl.rows.map((r) => r.sec_id);
  // sec_id -> label for the news security tags. Prefer the registered name
  // (securities.name — already fetched), so a TASE tag reads "בנק לאומי לישראל
  // בע\"מ", not the bare number 604611. Fall back to symbol then sec_id so a
  // security missing its name never renders blank. Every displayed feed item
  // with a sec_id is a watchlist security, so this map covers them.
  const secLabels = Object.fromEntries(wl.rows.map((r) => [r.sec_id, r.name || r.symbol || r.sec_id]));

  async function onLogout() {
    await supabase.auth.signOut();
  }

  // Resolve against the live rows so a security removed elsewhere can't leave a
  // detail page open over a row that no longer exists.
  const openSec = openSecId ? wl.rows.find((r) => r.sec_id === openSecId) : null;
  if (openSec) {
    return <Detail sec={openSec} onBack={() => setOpenSecId(null)} />;
  }

  return (
    <div
      style={{
        height: '100vh',
        display: 'flex',
        flexDirection: 'column',
        background: t.bg,
        color: t.txt,
        overflow: 'hidden',
      }}
    >
      {/* top bar */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '14px 24px',
          borderBottom: `1px solid ${t.bd}`,
          flexShrink: 0,
        }}
      >
        <Brand size={17} dotSize={10} />
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <span dir="ltr" style={{ fontSize: 13, color: t.mut, fontFamily: "'IBM Plex Mono', monospace" }}>
            {session.user.email}
          </span>
          <button
            onClick={onLogout}
            style={{
              background: 'none',
              border: `1px solid ${t.bd}`,
              borderRadius: 8,
              padding: '6px 14px',
              fontSize: 13,
              color: t.mut,
              fontFamily: 'Heebo, sans-serif',
              cursor: 'pointer',
            }}
          >
            יציאה
          </button>
        </div>
      </div>

      {/* content: watchlist (right, primary in RTL) + news feed (left) */}
      <div ref={splitRef} style={{ flex: 1, display: 'flex', minHeight: 0 }}>
        <div style={{ width: `${wlPct}%`, display: 'flex', minWidth: 0, minHeight: 0 }}>
          <Watchlist
            rows={wl.rows}
            status={wl.status}
            error={wl.error}
            onAdd={wl.add}
            onRemove={wl.remove}
            onOpen={setOpenSecId}
          />
        </div>
        <SplitDivider containerRef={splitRef} pct={wlPct} onResize={setWlPct} />
        <div style={{ flex: 1, display: 'flex', minWidth: 0, minHeight: 0 }}>
          <News watchSecIds={watchSecIds} secLabels={secLabels} watchReady={wl.status === 'ready'} refreshTick={refreshTick} />
        </div>
      </div>
    </div>
  );
}
