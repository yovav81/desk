/* GOLD news — minimal PWA service worker.
 *
 * THE RULE: only our own STATIC SHELL is ever cached. Supabase auth/REST/storage
 * responses — i.e. every byte of authenticated user data — are NEVER written to
 * a cache; they fall through to the browser's normal network path (see fetch).
 * Cross-origin requests and non-GET requests are likewise untouched.
 *
 * Bump CACHE only when the shell contract changes; activate() then drops every
 * older cache, so a new deploy can't be served an old shell.
 */
const CACHE = 'gold-shell-v1';

// Stable, unhashed paths. Vite's hashed /assets/* files can't be listed at
// build time from a static file, so they're cached on first use below — safe,
// because a content hash in the name makes them immutable.
const SHELL = [
  '/',
  '/manifest.webmanifest',
  '/icon.svg',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
  '/icons/icon-512-maskable.png',
  '/icons/apple-touch-icon-180.png',
];

// A path we are willing to cache. Everything else is network-only.
function isShell(pathname) {
  return pathname.startsWith('/assets/') || pathname.startsWith('/icons/') || SHELL.includes(pathname);
}

self.addEventListener('install', (event) => {
  // addAll is atomic: one missing file aborts the install rather than leaving a
  // half-populated cache. Failure is tolerated — the app runs fine without a SW.
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()).catch(() => {}));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  const url = new URL(req.url);

  // NETWORK-ONLY, no respondWith at all: anything that isn't a same-origin GET
  // for a shell path. That is every Supabase call (different origin), every
  // POST/auth exchange, and every third-party font — none of it is cacheable
  // here, so no authenticated data can ever land in the cache.
  if (req.method !== 'GET' || url.origin !== self.location.origin) return;

  if (req.mode === 'navigate') {
    // Network-FIRST for the document, cache only as an offline fallback:
    // index.html names HASHED asset files, so a cached copy served after a
    // deploy would point at files that no longer exist.
    event.respondWith(
      fetch(req)
        .then((res) => {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put('/', copy));
          return res;
        })
        .catch(() => caches.match('/')),
    );
    return;
  }

  if (!isShell(url.pathname)) return; // network-only

  // Cache-first for the shell assets (hashed => immutable).
  event.respondWith(
    caches.match(req).then((hit) => hit || fetch(req).then((res) => {
      if (res.ok) {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(req, copy));
      }
      return res;
    })),
  );
});
