# Layout: divider + sticky column + mobile — Phase 7 step 2 investigation

**Date:** 2026-07-17 · **Scope:** investigation only, nothing modified.
MEASURED (from code) / INFERRED marked throughout.

## Headline: the mobile cards layout DOES NOT EXIST in the app

The "cards instead of table — already built" claim is **false for the app**
(MEASURED: zero `@media` / `matchMedia` / `innerWidth` / card markup anywhere in
`web/src`). The cards exist only in `design_reference/` — the visual mockup has
an `isMobile` runtime, a **`max-width: 760px`** breakpoint, and full card markup
(`mobRowsDisp`). The belief came from the mockup, not the code — the same
documented-guess pattern that bit us with Sano this week. Mobile is a **build**,
not a verify.

---

## A. Current layout

### A1. The two-panel split (MEASURED)
`web/src/App.jsx` `Dashboard`, lines ~269–283 — plain **flexbox with inline
styles**, no CSS classes:

```jsx
<div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
  <div style={{ width: '56%', ... }}>       // watchlist — FIRST child = RIGHT in RTL
  <div style={{ width: 1, background: t.bd, flexShrink: 0 }} />   // the divider: 1px, VISUAL ONLY
  <div style={{ flex: 1, ... }}>            // news
```

RTL comes from **`<html lang="he" dir="rtl">`** (`index.html:2`) — nothing in
components sets direction; the flex row lays out right-to-left, which is why
the first child sits on the right. The page is `height: 100vh` column with the
topbar above. `index.css` is only a reset + scrollbar styling + one keyframe —
**no responsive CSS anywhere**. Viewport meta is standard.

### A2. Watchlist structure (MEASURED) — divs, not a `<table>`
Each row is its **own CSS grid `<div>`** (`Watchlist.jsx`), all sharing:

```
GRID = 'minmax(150px,1.6fr) minmax(84px,110px) minmax(58px,72px) repeat(4, minmax(52px,66px)) 32px'
        name                price            daily            4 returns              remove ×
```

`HeaderRow` is a separate grid div ABOVE the rows' vertical-scroll container
(`overflowY: auto`). Minimum content width of a row (MEASURED arithmetic):
532px columns + 42px gaps (7×6) + 48px padding ≈ **622px**.

Consequences for sticky (INFERRED, needs a browser to confirm): because
`overflow-y: auto` forces the same container's overflow-x to compute to `auto`,
when the panel drops below ~622px the **rows scroll horizontally but the header
does not** (it lives outside the scroller) — a latent misalignment bug that the
sticky work will fix by restructuring anyway. In RTL the overflow extends
LEFT, so the name column is visible initially and **scrolls away** — exactly
what the sticky requirement forbids. Being div-grids is good news:
`position: sticky` works on grid items, so no table rework is needed.

### A3. Mobile handling today (MEASURED)
**None.** No media queries, no conditional rendering, no width state. The only
mobile artifacts are in `design_reference/` (breakpoint `max-width: 760px`,
card markup with name/price/day + a 4-column returns grid under a top border,
`screenshots/mobile.png`). The app renders the same two-panel desktop layout at
every width.

## B. Divider design

### B1. Minimal implementation (no dependency — none needed)
A ~10px-wide **hit zone** wrapping the existing 1px visual line (`cursor:
col-resize`, `touchAction: 'none'`), with **Pointer Events + pointer capture**
— `onPointerDown` → `setPointerCapture(e.pointerId)`, then `onPointerMove`/
`onPointerUp` on the handle itself (capture routes events to it; **no window
listeners, nothing to leak**). During drag, set `document.body.style.userSelect
= 'none'` and restore on up. State: `const [wlPct, setWlPct] = useState(56)` in
`Dashboard`, applied as `width: `${wlPct}%``; clamp **25%–75%**.

**The RTL-correct math, explicitly:** never use `movementX` deltas — sign
conventions are the classic inverted-drag bug. Compute the width **absolutely**
from viewport geometry, which is direction-agnostic:

```js
const rect = containerRef.current.getBoundingClientRect();
const pct = ((rect.right - e.clientX) / rect.width) * 100;  // watchlist = RIGHT panel:
setWlPct(clamp(pct, 25, 75));                               // its width IS the distance
                                                            // pointer → right edge
```

`clientX`/`getBoundingClientRect` are pure viewport coordinates — `dir="rtl"`
does not affect them, so this formula cannot invert. Nice-to-have: double-click
resets to 56%. Re-render cost during drag is ~10 row components per move —
fine at this scale (rAF throttling available if a device proves otherwise).

**Persistence (NOT implemented, per rules):** state only — resets on reload.
Persisting would require a server-side per-user pref (a `users` column or a
`user_prefs` table + RLS + a write path); localStorage is banned.

### B2. Touch/mobile degradation — **desktop-only, hidden on mobile**
Recommendation accepted as proposed: below the mobile breakpoint the divider is
not rendered at all — mobile gets the stacked/cards layout (§D), where a
resizable split is meaningless and the drag would steal swipe gestures. Pointer
capture means it *would* technically work on touch; hiding it is a product
choice, not a limitation.

## C. Sticky security column

### C1. Approach (follows from A2): `position: sticky` on the name cell
No table rework — sticky works on grid items. Structure change:

1. Wrap **header + rows together** in ONE `overflow-x: auto` container (this
   also fixes the A2 misalignment bug); the rows' `overflow-y: auto` container
   nests inside it.
2. Name cell (in header and in every row): `position: 'sticky'`,
   **`insetInlineStart: 0`** — the logical property resolves to **`right: 0`
   in RTL** (the classic mistake is `left: 0`, which pins the wrong edge) —
   plus an **opaque background** and `zIndex: 1`, or scrolled content shows
   through it.
3. The row hover background currently lives on the row div; the sticky cell
   must paint the SAME hover color or it will visibly mismatch while scrolled —
   `Row` already holds hover state, so the cell can read it (MEASURED: hover is
   a `useState` in `Row`).
4. No JS, no breakpoints: when the panel is wider than ~622px there is no
   overflow and sticky is inert. We never touch `scrollLeft`, so RTL's
   browser-specific negative-scrollLeft quirks are irrelevant.

### C2. What the other columns do when narrow → **horizontal scroll** (recommended)
Options were scroll vs priority-hiding. **Scroll wins:** it loses nothing
(every column stays reachable), needs zero breakpoint/priority logic, matches
the GOLD spec wording ("other columns scroll or truncate"), and pairs naturally
with the divider — a narrow panel is now a *user choice*, and scroll is the
predictable response. Priority-hiding discards data with no affordance to
recover it and adds re-templating complexity in RTL. The × (remove) column
scrolls away when narrow — acceptable: the spec protects the NAME only; a
second sticky edge (× at `insetInlineEnd`) is possible but recommended against
(clutter, shrinks the scrollable viewport).

## D. Mobile audit (from code only — see D3 for what needs a device)

### D1. Per component at ~390px (MEASURED structure, INFERRED rendering)
| Component | Below 760px today |
|---|---|
| `App` Dashboard | Same side-by-side split: watchlist ≈218px vs its **622px floor** → horizontal scroll + misaligned header (A2); news ≈172px. Technically loads, practically unusable. |
| `Watchlist` rows | Crushed/scrolling per A2; ידני badge + name ellipsize (ellipsis is set). |
| `SearchBox` | Dropdown is `right:24/left:24` inside the panel → ~170px wide; candidates ellipsize; cramped but functional. |
| `News` | Header has `flexWrap:'wrap'` (MEASURED) → tabs wrap under the title; FeedItem badge row wraps; titles wrap. Cramped, functional. |
| `Detail` | Numbers/returns rows have `flexWrap:'wrap'` (MEASURED); chart SVG is `width:100%` with `preserveAspectRatio="none"` → reflows fine. Likely the best page on mobile already. |
| `Login` | `maxWidth: 380` + page padding → fits 390px. Fine. |
| Topbar | No wrap; email (mono) + logout at 390px is tight, may overflow (INFERRED). |

### D2. Will-break flags at ~390px
- **The split itself** — the one structural blocker; everything else is polish.
- **`height: 100vh`** (3 places) — on iOS Safari the URL bar makes 100vh taller
  than the visible viewport → bottom of the page hides behind chrome. Fix is
  `100dvh` with a `vh` fallback.
- **Tap targets under 40px** (MEASURED paddings): remove × ≈24px, tabs/period
  pills ≈28–30px, logout ≈31px.
- **iOS zoom-on-focus**: inputs are 14–15px font; iOS auto-zooms any input
  <16px (INFERRED, device-confirm) — search box needs 16px on mobile.
- The **new divider** must be hidden on mobile (B2).

### D3. What only a real device can confirm
dvh/URL-bar behavior, zoom-on-focus, sticky-in-RTL rendering on iOS Safari
(desktop Chrome passing is not proof), nested-scroller touch feel (page scroll
vs table x-scroll), and real tap ergonomics. My audit is code-only.

## E. Plan

**Order (each shippable alone):**
1. **Divider** — smallest, isolated to `App.jsx` (state + handle component).
2. **Sticky column** — `Watchlist.jsx` restructure (shared x-scroll wrapper +
   sticky name cells + hover-bg move). Do it right after the divider, since the
   divider is what makes narrow panels reachable. Also fixes the A2 header bug.
3. **Mobile** — the big one: `useIsMobile()` (matchMedia **760px**, the
   design's own breakpoint), stacked layout for Dashboard, card list in
   Watchlist (mirroring the mockup's card markup: name+sub+badge / price+day /
   4-col returns grid), 100dvh, divider hidden, tap targets ≥40px, 16px inputs.
   Verified on a real device via the Vercel deploy afterward.

**Files:** `App.jsx` (1, 3), `Watchlist.jsx` (2, 3), `index.css` (dvh
fallback), possibly `News.jsx` (minor mobile spacing). No new deps anywhere.

**Risk to the existing desktop layout:** step 2 is the only risky one — it
changes the DOM around the exact table desktop users look at all day (new
scroll wrapper, header moves inside it). Steps 1 and 3 are additive/gated
(divider is new; mobile is behind matchMedia and cannot affect ≥760px).

**Phase 5 conflict check (same files):** the auto-refresh timer + `refreshTick`
live in `App.jsx` `Dashboard` — the divider adds *state* to the same component
but touches neither the timer effect nor the hooks' deps; no functional
conflict, just merge locality. `News.jsx`'s "הפריט האחרון" header already wraps
(`flexWrap`), so mobile won't fight it.

**Decision needed before building step 3 (only one):** the mobile navigation
pattern. The mockup has card markup but I could not conclusively identify its
panel-switching mechanism (no tab-switcher markup found in the export). Options:
**(a) vertical stack** — watchlist cards then news, one page scroll (simplest,
no navigation state); **(b) bottom/top tab switcher** — one panel at a time
(more app-like, denser). Steps 1–2 need no decisions.
