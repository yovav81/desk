import { useCallback, useEffect, useRef, useState } from 'react';
import { supabase } from './supabaseClient';
import { theme as t } from './theme';
import Watchlist from './Watchlist';
import News from './News';
import Detail from './Detail';
import { useWatchlist } from './useWatchlist';
import { useIsMobile } from './useIsMobile';

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
  if (!session) return <Login />;
  // THREE-WAY GATE: no session → Login; session but unapproved → Pending;
  // approved → Dashboard. The gate is a wrapper so Dashboard (and its
  // useWatchlist/useNews) never mounts until approval is confirmed.
  return <ApprovalGate session={session} />;
}

// Fetches the caller's OWN profiles row (RLS allows own-row select — sql/005)
// to decide pending vs dashboard. NOTE: this screen is UX only — the real
// enforcement is the sql/005 RLS, which already returns an unapproved user
// ZERO rows everywhere. A user cannot self-approve by defeating this component.
function ApprovalGate({ session }) {
  const [state, setState] = useState('loading'); // loading | approved | pending | error
  const [isAdmin, setIsAdmin] = useState(false);
  const uid = session.user.id;

  const check = useCallback(async () => {
    setState('loading');
    // is_admin is fetched here alongside approved (ONE fetch) and threaded to
    // Dashboard so the admin entry point needs no second query.
    const { data, error } = await supabase
      .from('profiles')
      .select('approved, is_admin')
      .eq('id', uid)
      .maybeSingle();
    if (error) {
      setState('error');
      return;
    }
    setIsAdmin(Boolean(data?.is_admin));
    // No row yet (e.g. the signup trigger hasn't landed) = not approved.
    setState(data?.approved ? 'approved' : 'pending');
  }, [uid]);

  useEffect(() => {
    check();
  }, [check]);

  if (state === 'loading') return <Splash />;
  if (state === 'approved') return <Dashboard session={session} isAdmin={isAdmin} />;
  // pending OR error → the pending screen (error just adds a line + retry).
  return <Pending email={session.user.email} isError={state === 'error'} onRecheck={check} />;
}

function Pending({ email, isError, onRecheck }) {
  const [busy, setBusy] = useState(false);
  async function recheck() {
    setBusy(true);
    // The just-approved advance path: an admin flips approved=true (SQL/admin
    // page); this re-fetches the profile and, if approved, ApprovalGate swaps
    // to the dashboard. No realtime, no re-login needed — approved is read
    // live by RLS, never cached in the JWT.
    await onRecheck();
    setBusy(false);
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
      <div
        style={{
          width: '100%',
          maxWidth: 400,
          background: t.surf,
          border: `1px solid ${t.bd}`,
          borderRadius: 18,
          padding: '36px 32px',
          display: 'flex',
          flexDirection: 'column',
          gap: 16,
          textAlign: 'center',
          animation: 'fadeUp .4s ease',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'center' }}>
          <Brand />
        </div>
        <div style={{ fontSize: 18, fontWeight: 700, color: t.txt }}>החשבון שלך ממתין לאישור</div>
        <div style={{ fontSize: 14, color: t.mut, lineHeight: 1.6 }}>
          נרשמת בהצלחה. מנהל המערכת יבדוק ויאשר את הגישה שלך בקרוב. אין צורך להירשם שוב — לחצו
          "בדוק שוב" לאחר האישור.
        </div>
        {email && (
          <div dir="ltr" style={{ fontSize: 12, color: t.mut, fontFamily: "'IBM Plex Mono', monospace" }}>
            {email}
          </div>
        )}
        {isError && (
          <div style={{ fontSize: 13, color: t.red }}>שגיאה בבדיקת ההרשאה — נסו שוב.</div>
        )}
        <button
          onClick={recheck}
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
          {busy ? 'בודק…' : 'בדוק שוב'}
        </button>
        <button
          onClick={() => supabase.auth.signOut()}
          style={{
            background: 'none',
            border: `1px solid ${t.bd}`,
            borderRadius: 10,
            padding: 11,
            fontSize: 14,
            color: t.mut,
            fontFamily: 'Heebo, sans-serif',
            cursor: 'pointer',
          }}
        >
          יציאה
        </button>
      </div>
    </div>
  );
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

// Supabase's built-in errors are English; map the ones a user can hit to
// Hebrew rather than leaking raw text.
function hebrewAuthError(error, mode) {
  const m = (error?.message || '').toLowerCase();
  if (m.includes('already registered') || m.includes('already been registered'))
    return 'האימייל כבר רשום — נסו להתחבר';
  if (m.includes('password')) return 'הסיסמה חייבת להכיל לפחות 6 תווים';
  if (m.includes('email') && (m.includes('invalid') || m.includes('valid')))
    return 'כתובת אימייל לא תקינה';
  if (m.includes('invalid login credentials')) return 'התחברות נכשלה — בדקו אימייל וסיסמה';
  if (m.includes('email not confirmed')) return 'האימייל טרם אומת — בדקו את תיבת הדואר ואשרו';
  return mode === 'signup' ? 'ההרשמה נכשלה — נסו שוב' : 'ההתחברות נכשלה — נסו שוב';
}

const MIN_PASSWORD = 6; // Supabase default minimum

function Login() {
  const [mode, setMode] = useState('login'); // login | signup
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [err, setErr] = useState('');
  const [notice, setNotice] = useState(''); // success message (signup confirmation)
  const [busy, setBusy] = useState(false);
  const isSignup = mode === 'signup';

  function switchMode(next) {
    setMode(next);
    setErr('');
    setNotice('');
  }

  async function onSubmit(e) {
    e.preventDefault();
    setErr('');
    setNotice('');
    const mail = email.trim();
    if (!mail || !password) {
      setErr('יש להזין אימייל וסיסמה');
      return;
    }
    if (isSignup && password.length < MIN_PASSWORD) {
      setErr(`הסיסמה חייבת להכיל לפחות ${MIN_PASSWORD} תווים`);
      return;
    }
    setBusy(true);

    if (isSignup) {
      const { error } = await supabase.auth.signUp({ email: mail, password });
      setBusy(false);
      if (error) {
        setErr(hebrewAuthError(error, 'signup'));
        return;
      }
      // Email confirmation is ON, so there is NO active session yet — Supabase
      // sent a confirmation link. (Supabase deliberately returns an obfuscated
      // success for an ALREADY-registered email — identities: [] — to prevent
      // enumeration; we show the same confirmation message either way, which is
      // the privacy-preserving behaviour.) Switch to login for after they confirm.
      setMode('login');
      setNotice('נשלח מייל אימות לכתובת שלך. אשר אותו ואז התחבר.');
      return;
    }

    const { error } = await supabase.auth.signInWithPassword({ email: mail, password });
    setBusy(false);
    if (error) setErr(hebrewAuthError(error, 'login'));
    // On success, onAuthStateChange sets the session → ApprovalGate decides.
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

        <div style={{ fontSize: 15, fontWeight: 600, color: t.txt }}>
          {isSignup ? 'הרשמה' : 'התחברות'}
        </div>

        <Field
          label="אימייל"
          type="email"
          value={email}
          onChange={(e) => {
            setEmail(e.target.value);
            setErr('');
          }}
          autoComplete="email"
        />
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <Field
            label="סיסמה"
            type="password"
            value={password}
            onChange={(e) => {
              setPassword(e.target.value);
              setErr('');
            }}
            autoComplete={isSignup ? 'new-password' : 'current-password'}
          />
          {isSignup && (
            <div style={{ fontSize: 11.5, color: t.mut }}>לפחות {MIN_PASSWORD} תווים</div>
          )}
        </div>

        {err && <div style={{ fontSize: 13, color: t.red }}>{err}</div>}
        {notice && <div style={{ fontSize: 13, color: t.acc, lineHeight: 1.5 }}>{notice}</div>}

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
          {busy ? (isSignup ? 'נרשם…' : 'מתחבר…') : isSignup ? 'הרשמה' : 'כניסה'}
        </button>

        <div style={{ fontSize: 13, color: t.mut, textAlign: 'center' }}>
          {isSignup ? 'יש לך כבר חשבון? ' : 'אין לך חשבון? '}
          <span
            onClick={() => switchMode(isSignup ? 'login' : 'signup')}
            style={{ color: t.acc, cursor: 'pointer', fontWeight: 600 }}
          >
            {isSignup ? 'התחברות' : 'הרשמה'}
          </span>
        </div>
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

function Dashboard({ session, isAdmin = false }) {
  // Bumped to trigger a refetch; threaded into the data hooks' effect deps so a
  // single timer drives both (no duplicate timers, no duplicated query logic).
  const [refreshTick, setRefreshTick] = useState(0);
  // Admin view toggle (App-level state, no router — same pattern as openSecId).
  // Guarded by isAdmin at render, but that is UI only: the profiles queries the
  // admin view runs are RLS-gated on is_admin() (sql/005), so a non-admin who
  // forced adminOpen or hit PostgREST directly still gets nothing.
  const [adminOpen, setAdminOpen] = useState(false);
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
  // MOBILE (<=760px, the mockup's breakpoint): one panel at a time behind two
  // tabs. Both hooks are unconditional (React hook rules); the branch happens
  // at the RETURN below, so the desktop tree stays byte-identical to today.
  const isMobile = useIsMobile();
  const [mobileTab, setMobileTab] = useState('watch');

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

  // Admin view — full-screen sibling, before the detail/mobile branches so it
  // works identically on desktop and the mobile tab layout.
  if (adminOpen && isAdmin) {
    return <Admin onBack={() => setAdminOpen(false)} currentUserId={session.user.id} />;
  }

  // Resolve against the live rows so a security removed elsewhere can't leave a
  // detail page open over a row that no longer exists.
  const openSec = openSecId ? wl.rows.find((r) => r.sec_id === openSecId) : null;
  if (openSec) {
    return <Detail sec={openSec} onBack={() => setOpenSecId(null)} />;
  }

  // MOBILE TREE. The single branch point (here, not scattered): desktop below
  // renders the exact tree it did before this step, divider included; mobile
  // renders tabs + both panels. Both panels stay MOUNTED (display toggling),
  // so flipping tabs never remounts hooks and never refetches. SplitDivider is
  // simply absent here — which also removes its former below-760px
  // tab-focusable wart.
  if (isMobile) {
    const tabs = [
      ['watch', 'רשימת מעקב'],
      ['news', 'חדשות'],
    ];
    return (
      <div
        className="vh-page mobile-safe"
        style={{ display: 'flex', flexDirection: 'column', background: t.bg, color: t.txt, overflow: 'hidden' }}
      >
        {/* compact top bar — the email is dropped on mobile for space */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '10px 16px',
            borderBottom: `1px solid ${t.bd}`,
            flexShrink: 0,
          }}
        >
          <Brand size={16} dotSize={9} />
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {isAdmin && <AdminLink onClick={() => setAdminOpen(true)} />}
            <button
              onClick={onLogout}
              style={{
                background: 'none',
                border: `1px solid ${t.bd}`,
                borderRadius: 8,
                padding: '8px 16px',
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

        {/* the two panel tabs — 48px targets, active = house gold */}
        <div style={{ display: 'flex', flexShrink: 0, borderBottom: `1px solid ${t.bd}` }}>
          {tabs.map(([key, label]) => {
            const active = mobileTab === key;
            return (
              <button
                key={key}
                onClick={() => setMobileTab(key)}
                style={{
                  flex: 1,
                  minHeight: 48,
                  background: active ? t.accSoft : 'none',
                  border: 'none',
                  borderBottom: `2px solid ${active ? t.acc : 'transparent'}`,
                  color: active ? t.acc : t.mut,
                  fontSize: 15,
                  fontWeight: 600,
                  fontFamily: 'Heebo, sans-serif',
                  cursor: 'pointer',
                }}
              >
                {label}
              </button>
            );
          })}
        </div>

        {/* both panels always mounted; the inactive one is display:none */}
        <div style={{ flex: 1, minHeight: 0, display: mobileTab === 'watch' ? 'flex' : 'none' }}>
          <Watchlist
            mobile
            rows={wl.rows}
            status={wl.status}
            error={wl.error}
            onAdd={wl.add}
            onRemove={wl.remove}
            onReorder={wl.reorder}
            orderError={wl.orderError}
            onOpen={setOpenSecId}
          />
        </div>
        <div style={{ flex: 1, minHeight: 0, display: mobileTab === 'news' ? 'flex' : 'none' }}>
          <News
            mobile
            watchSecIds={watchSecIds}
            secLabels={secLabels}
            watchReady={wl.status === 'ready'}
            refreshTick={refreshTick}
          />
        </div>
      </div>
    );
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
          {isAdmin && <AdminLink onClick={() => setAdminOpen(true)} />}
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
            onReorder={wl.reorder}
            orderError={wl.orderError}
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

// Admin entry point — rendered only when isAdmin (cosmetic; RLS is the boundary).
function AdminLink({ onClick }) {
  return (
    <button
      onClick={onClick}
      style={{
        background: t.accSoft,
        border: `1px solid ${t.accDim}`,
        borderRadius: 8,
        padding: '6px 14px',
        fontSize: 13,
        fontWeight: 600,
        color: t.acc,
        fontFamily: 'Heebo, sans-serif',
        cursor: 'pointer',
      }}
    >
      ניהול
    </button>
  );
}

// Admin page: the user-approval console. This is UI over the sql/005 RLS — the
// profiles select-all and update are gated on is_admin() in the DB, so a
// non-admin who reached these queries (forced adminOpen, direct PostgREST)
// reads nothing and writes nothing. The hidden button is convenience, not the
// security boundary. `email` comes straight off profiles (the signup trigger
// copies it), so no auth.users read / definer function is needed.
function Admin({ onBack, currentUserId }) {
  const [rows, setRows] = useState([]);
  const [status, setStatus] = useState('loading'); // loading | ready | error
  const [err, setErr] = useState('');

  async function load() {
    setStatus('loading');
    const { data, error } = await supabase
      .from('profiles')
      .select('id, email, approved, can_see_emails, is_admin, created_at')
      .order('created_at', { ascending: false });
    if (error) {
      setErr('שגיאה בטעינת המשתמשים');
      setStatus('error');
      return;
    }
    setRows(data || []);
    setStatus('ready');
  }
  useEffect(() => {
    load();
  }, []);

  async function setFlag(id, field, value) {
    setErr('');
    const prev = rows;
    setRows((rs) => rs.map((r) => (r.id === id ? { ...r, [field]: value } : r))); // optimistic
    const { error } = await supabase.from('profiles').update({ [field]: value }).eq('id', id);
    if (error) {
      setRows(prev); // rollback
      setErr('העדכון נכשל — נסו שוב');
    }
  }

  const pending = rows.filter((r) => !r.approved);
  const approved = rows.filter((r) => r.approved);

  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column', background: t.bg, color: t.txt, overflow: 'hidden' }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 14,
          padding: '14px 24px',
          borderBottom: `1px solid ${t.bd}`,
          flexShrink: 0,
        }}
      >
        <button
          onClick={onBack}
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
          חזרה לדשבורד →
        </button>
        <div style={{ fontSize: 17, fontWeight: 700 }}>ניהול משתמשים</div>
      </div>

      <div style={{ flex: 1, overflowY: 'auto', minHeight: 0, padding: '20px 24px 40px' }}>
        {status === 'loading' && <div style={{ color: t.mut }}>טוען…</div>}
        {status === 'error' && <div style={{ color: t.red }}>{err}</div>}
        {status === 'ready' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 26, maxWidth: 720 }}>
            {err && <div style={{ fontSize: 13, color: t.red }}>{err}</div>}

            <AdminSection
              title="ממתינים לאישור"
              count={pending.length}
              rows={pending}
              currentUserId={currentUserId}
              onFlag={setFlag}
              emptyText="אין משתמשים הממתינים לאישור"
            />
            <AdminSection
              title="מאושרים"
              count={approved.length}
              rows={approved}
              currentUserId={currentUserId}
              onFlag={setFlag}
              emptyText="אין משתמשים מאושרים"
            />
          </div>
        )}
      </div>
    </div>
  );
}

function AdminSection({ title, count, rows, currentUserId, onFlag, emptyText }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <div style={{ fontSize: 15, fontWeight: 700 }}>{title}</div>
        <div
          style={{
            fontSize: 12,
            fontWeight: 600,
            color: t.acc,
            background: t.accSoft,
            border: `1px solid ${t.accDim}`,
            borderRadius: 999,
            padding: '1px 9px',
          }}
        >
          {count}
        </div>
      </div>
      {rows.length === 0 ? (
        <div style={{ fontSize: 13, color: t.mut }}>{emptyText}</div>
      ) : (
        rows.map((r) => <AdminRow key={r.id} r={r} isSelf={r.id === currentUserId} onFlag={onFlag} />)
      )}
    </div>
  );
}

function AdminRow({ r, isSelf, onFlag }) {
  const dt = r.created_at ? new Date(r.created_at) : null;
  const date = dt && !Number.isNaN(dt.getTime())
    ? `${String(dt.getDate()).padStart(2, '0')}.${String(dt.getMonth() + 1).padStart(2, '0')}.${dt.getFullYear()}`
    : '';
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        flexWrap: 'wrap',
        padding: '12px 14px',
        border: `1px solid ${t.bd}`,
        borderRadius: 10,
        background: t.surf,
      }}
    >
      <div style={{ minWidth: 0, flex: 1 }}>
        <div dir="ltr" style={{ fontSize: 14, color: t.txt, textAlign: 'right', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {r.email || r.id}
          {isSelf && <span style={{ color: t.mut }}> (אתה)</span>}
          {r.is_admin && <span style={{ color: t.acc }}> · מנהל</span>}
        </div>
        {date && <div style={{ fontSize: 11.5, color: t.mut }}>נרשם {date}</div>}
      </div>
      {/* approved: disabled on the admin's OWN row — un-approving yourself would
          drop you to the pending screen (which has no admin button) = lockout. */}
      <FlagToggle
        label="מאושר"
        on={r.approved}
        disabled={isSelf}
        disabledNote={isSelf ? 'לא ניתן לבטל אישור עצמי' : ''}
        onToggle={() => onFlag(r.id, 'approved', !r.approved)}
      />
      {/* email access requires approved (encoded in the DB's can_see_emails());
          granting it to an unapproved user has no effect, so the toggle is
          disabled until approved — honest about what the flag can do. */}
      <FlagToggle
        label="רואה מיילים"
        on={r.can_see_emails}
        disabled={!r.approved}
        disabledNote={!r.approved ? 'דורש אישור תחילה' : ''}
        onToggle={() => onFlag(r.id, 'can_see_emails', !r.can_see_emails)}
      />
    </div>
  );
}

// Two-state pill. "on" uses the GOLD accent (grn/red stay reserved for returns).
function FlagToggle({ label, on, disabled, disabledNote, onToggle }) {
  return (
    <button
      onClick={disabled ? undefined : onToggle}
      disabled={disabled}
      title={disabledNote || ''}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        padding: '7px 12px',
        borderRadius: 999,
        fontSize: 12.5,
        fontWeight: 600,
        fontFamily: 'Heebo, sans-serif',
        border: `1px solid ${on ? t.accDim : t.bd}`,
        background: on ? t.accSoft : 'transparent',
        color: disabled ? t.mut : on ? t.acc : t.txt,
        cursor: disabled ? 'default' : 'pointer',
        opacity: disabled ? 0.55 : 1,
        whiteSpace: 'nowrap',
      }}
    >
      <span>{on ? '✓' : '○'}</span>
      {label}
    </button>
  );
}
