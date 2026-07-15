import { useEffect, useState } from 'react';
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

function Dashboard({ session }) {
  const wl = useWatchlist();
  // Which security's detail page is open (null = the dashboard). One page, so
  // plain state beats pulling in a router.
  const [openSecId, setOpenSecId] = useState(null);
  const watchSecIds = wl.rows.map((r) => r.sec_id);
  // sec_id -> short label (symbol) for the news security tags. Every displayed
  // feed item with a sec_id is a watchlist security, so this map covers them.
  const secLabels = Object.fromEntries(wl.rows.map((r) => [r.sec_id, r.symbol || r.sec_id]));

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
      <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
        <div style={{ width: '56%', display: 'flex', minWidth: 0, minHeight: 0 }}>
          <Watchlist
            rows={wl.rows}
            status={wl.status}
            error={wl.error}
            onAdd={wl.add}
            onRemove={wl.remove}
            onOpen={setOpenSecId}
          />
        </div>
        <div style={{ width: 1, background: t.bd, flexShrink: 0 }} />
        <div style={{ flex: 1, display: 'flex', minWidth: 0, minHeight: 0 }}>
          <News watchSecIds={watchSecIds} secLabels={secLabels} watchReady={wl.status === 'ready'} />
        </div>
      </div>
    </div>
  );
}
