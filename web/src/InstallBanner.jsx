import { useEffect, useState } from 'react';
import { theme as t } from './theme';

// PHASE 25 — install banner. The PWA (17B) installs fine; people never found
// the path. Five states, decided by what the platform can actually do:
// 'android' has a live beforeinstallprompt and gets a one-tap button;
// 'androidManual' is the SAME platform WITHOUT that event — MEASURED on the
// user's phone (deferred=false, everything else clean): Chrome suppresses the
// event once the app was ever installed on that device, so gating the banner on
// it hid the app from a whole class of users; it gets ⋮-menu instructions
// instead. 'ios' never fires the event at all, an in-app webview cannot install
// and is told to leave, anything else renders nothing.
// Dismissal is React state — per-session by design; NO localStorage/
// sessionStorage anywhere (project rule), so it returns on a later visit.
// Mounted ONLY by the mobile tree, as the shell's LAST flex child: it takes its
// own row instead of overlaying, so the feed's last item is never covered.
const ua = () => navigator.userAgent || '';
const IN_APP = /FBAN|FBAV|FB_IAB|Instagram|WhatsApp|Line\/|MicroMessenger/i;
// iPadOS 13+ claims to be a Mac; maxTouchPoints is what separates it from one.
const isIOS = () => /iPad|iPhone|iPod/.test(ua()) || (/Macintosh/.test(ua()) && navigator.maxTouchPoints > 1);
// Chrome/Firefox/Edge on iOS are WebKit skins that CANNOT add to home screen.
const isIOSSafari = () => isIOS() && !/CriOS|FxiOS|EdgiOS|OPiOS/.test(ua());
const isStandalone = () =>
  window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;

const TEXT = {
  android: 'התקינו את GOLD למסך הבית',
  androidManual: "התקינו את GOLD: תפריט ⋮ ואז 'הוספה למסך הבית'",
  ios: "התקינו את GOLD: כפתור השיתוף ⬆️ ואז 'הוסף למסך הבית'",
  inapp: 'לפתיחה כאפליקציה — פתחו את הקישור בדפדפן',
};
const BTN = { border: 'none', fontFamily: 'Heebo, sans-serif', cursor: 'pointer', flexShrink: 0 };

export default function InstallBanner({ dismissed, onDismiss }) {
  const [deferred, setDeferred] = useState(null);
  const [installed, setInstalled] = useState(isStandalone);

  useEffect(() => {
    const onPrompt = (e) => {
      e.preventDefault(); // suppress Chrome's mini-infobar; we drive the prompt
      setDeferred(e);
    };
    const onInstalled = () => { setInstalled(true); setDeferred(null); };
    window.addEventListener('beforeinstallprompt', onPrompt);
    window.addEventListener('appinstalled', onInstalled);
    return () => {
      window.removeEventListener('beforeinstallprompt', onPrompt);
      window.removeEventListener('appinstalled', onInstalled);
    };
  }, []);

  const mode = IN_APP.test(ua())
    ? 'inapp'
    : deferred
      ? 'android'
      : isIOSSafari()
        ? 'ios'
        : /Android/.test(ua())
          ? 'androidManual'
          : null;
  if (dismissed || installed || !mode || !navigator.maxTouchPoints) return null;

  async function install() {
    const e = deferred;
    setDeferred(null); // a prompt event is single-use, accepted or not
    await e.prompt();
  }

  return (
    <div
      style={{
        display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0,
        padding: '10px 14px', background: t.surf, borderTop: `1px solid ${t.accDim}`,
        fontSize: 13, color: t.txt, lineHeight: 1.4,
      }}
    >
      <span style={{ flex: 1, minWidth: 0 }}>{TEXT[mode]}</span>
      {mode === 'android' && (
        <button
          onClick={install}
          style={{ ...BTN, background: t.acc, color: t.onAcc, borderRadius: 8,
            padding: '8px 16px', fontSize: 13, fontWeight: 600 }}
        >
          התקנה
        </button>
      )}
      <button onClick={onDismiss} aria-label="סגירה"
        style={{ ...BTN, background: 'none', color: t.mut, fontSize: 18, padding: '4px 6px', lineHeight: 1 }}>
        ✕
      </button>
    </div>
  );
}
