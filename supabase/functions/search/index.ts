// GOLD — securities search proxy (Yahoo global + SEC US).
//
// A THIN PROXY, not a re-implementation of desk/onboarding.py. The browser
// cannot call these upstreams directly: Yahoo sends no CORS headers, and both
// Yahoo and SEC require User-Agent values a browser is not allowed to set
// (Yahoo 429s without one, SEC 403s without a descriptive contact UA). So this
// function sits in between: it sets the right headers, adds our own CORS, and
// returns merged candidates.
//
// ISRAEL IS DELIBERATELY NOT HERE. TASE search is served from the local
// `tase_securities` table (populated daily by desk/collect_tase_list.py), which
// the UI queries directly via PostgREST — instant, no live gate, no IP risk.
//
// NEVER auto-picks: always returns a list for the user to choose from, matching
// the resolve-assisted policy in research/GLOBAL_COVERAGE_FINDINGS.md (Yahoo
// same-ticker collisions return valid-but-wrong companies with clean prices).
//
// Fail-soft: if one upstream fails the other's results are still returned, with
// a note in `notes[]`. Never 500s the whole search over one bad upstream.

const SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json";
// SEC requires a descriptive User-Agent with contact info (blank/absent -> 403).
const SEC_UA = "DESK watchlist onboarding (contact: yovav81@gmail.com)";
const YAHOO_SEARCH_URL = "https://query1.finance.yahoo.com/v1/finance/search";
// Yahoo 429s without a browser-ish UA.
const YAHOO_UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
  "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36";

const HEBREW_RE = /[֐-׿]/;
const DEFAULT_LIMIT = 8;
const UPSTREAM_TIMEOUT_MS = 12_000;
const SEC_CACHE_TTL_MS = 24 * 60 * 60 * 1000;

type Candidate = {
  market: "US" | "GLOBAL";
  symbol: string;
  name: string;
  exchange?: string;
  hint: string;
};

// --------------------------------------------------------------------------- //
// CORS                                                                         //
// --------------------------------------------------------------------------- //
// Any localhost/127.0.0.1 port (Vite dev) plus deployed app origins. The app
// isn't hosted yet (Vercel is a later step) — add its origin here when it is.
const ALLOWED_ORIGIN_RE =
  /^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?$|^https:\/\/([a-z0-9-]+\.)*vercel\.app$/i;

function corsHeaders(origin: string | null): Record<string, string> {
  const allow = origin && ALLOWED_ORIGIN_RE.test(origin) ? origin : "null";
  return {
    "Access-Control-Allow-Origin": allow,
    "Access-Control-Allow-Headers": "authorization, apikey, content-type",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Vary": "Origin",
  };
}

// --------------------------------------------------------------------------- //
// SEC ticker map — fetched ONCE and cached in module scope                     //
// --------------------------------------------------------------------------- //
// ~800 KB static file; re-downloading it per keystroke would be absurd. Warm
// invocations reuse this; a cold start pays for it once. `inflight` collapses
// concurrent first-hits into a single upstream fetch.
type SecEntry = { ticker: string; title: string; cik: number };
let secCache: SecEntry[] | null = null;
let secCacheAt = 0;
let secInflight: Promise<SecEntry[]> | null = null;

async function loadSecTickers(): Promise<SecEntry[]> {
  if (secCache && Date.now() - secCacheAt < SEC_CACHE_TTL_MS) return secCache;
  if (secInflight) return secInflight;

  secInflight = (async () => {
    const res = await fetchWithTimeout(SEC_TICKERS_URL, {
      headers: { "User-Agent": SEC_UA, "Accept": "application/json" },
    });
    if (!res.ok) throw new Error(`SEC HTTP ${res.status}`);
    const raw = await res.json() as Record<string, {
      ticker: string;
      title: string;
      cik_str: number;
    }>;
    const entries = Object.values(raw).map((v) => ({
      ticker: String(v.ticker).toUpperCase(),
      title: v.title,
      cik: v.cik_str,
    }));
    secCache = entries;
    secCacheAt = Date.now();
    console.log(`SEC ticker map cached: ${entries.length} entries`);
    return entries;
  })();

  try {
    return await secInflight;
  } finally {
    secInflight = null;
  }
}

function escapeRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// Scored so a mid-word substring can't outrank a real match: searching "SAP"
// must not bury SAP SE under CHESAPEAKE UTILITIES (which contains "sap").
// Lower is better; entries scoring 4 are dropped.
function secScore(e: SecEntry, q: string, wordRe: RegExp): number {
  if (e.ticker === q) return 0;
  if (wordRe.test(e.title)) return 1; // query starts a word in the name
  if (e.ticker.startsWith(q)) return 2;
  if (e.title.toUpperCase().includes(q)) return 3; // loose mid-word substring
  return 4;
}

function secSuggest(entries: SecEntry[], query: string, limit: number): Candidate[] {
  const q = query.trim().toUpperCase();
  const wordRe = new RegExp(`\\b${escapeRe(q)}`, "i");
  const scored: Array<{ e: SecEntry; score: number }> = [];
  for (const e of entries) {
    const score = secScore(e, q, wordRe);
    if (score < 4) scored.push({ e, score });
  }
  // Stable sort keeps the SEC file's own order within a score band.
  scored.sort((a, b) => a.score - b.score);
  return scored.slice(0, limit).map(({ e }) => ({
    market: "US" as const,
    symbol: e.ticker,
    name: e.title,
    hint: `US · SEC CIK ${e.cik}`,
  }));
}

// --------------------------------------------------------------------------- //
// Yahoo search (GLOBAL)                                                        //
// --------------------------------------------------------------------------- //
async function yahooSuggest(query: string, limit: number): Promise<Candidate[]> {
  // Yahoo 400s on Hebrew — Hebrew queries belong to tase_securities anyway.
  if (!query.trim() || HEBREW_RE.test(query)) return [];
  const url = `${YAHOO_SEARCH_URL}?q=${encodeURIComponent(query)}&quotesCount=10&newsCount=0`;
  const res = await fetchWithTimeout(url, {
    headers: { "User-Agent": YAHOO_UA, "Accept": "application/json" },
  });
  if (!res.ok) throw new Error(`Yahoo HTTP ${res.status}`);
  const data = await res.json() as {
    quotes?: Array<Record<string, string | undefined>>;
  };
  const out: Candidate[] = [];
  for (const it of data.quotes ?? []) {
    // EQUITY only — drops ETFs, indices, options, currencies.
    if (it.quoteType !== "EQUITY" || !it.symbol) continue;
    const exchange = it.exchDisp || it.exchange || "";
    out.push({
      market: "GLOBAL",
      symbol: it.symbol,
      name: it.shortname || it.longname || it.symbol,
      exchange,
      hint: `GLOBAL · ${exchange}`,
    });
    if (out.length >= limit) break;
  }
  return out;
}

// --------------------------------------------------------------------------- //
// Shared fetch                                                                 //
// --------------------------------------------------------------------------- //
// NOTE: we build request headers from scratch and never forward the caller's —
// a browser Origin/Referer reaching these upstreams is exactly what we're here
// to strip (Imperva 403s a foreign Origin; see research/EDGE_SEARCH_FINDINGS.md).
async function fetchWithTimeout(url: string, init: RequestInit): Promise<Response> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), UPSTREAM_TIMEOUT_MS);
  try {
    return await fetch(url, { ...init, signal: ctrl.signal, redirect: "follow" });
  } finally {
    clearTimeout(timer);
  }
}

// --------------------------------------------------------------------------- //
// Merge + rank                                                                 //
// --------------------------------------------------------------------------- //
// Mirrors suggest() in desk/onboarding.py: exact symbol match first, de-duped by
// full symbol with US ahead of GLOBAL, so US 'SAP' wins over GLOBAL 'SAP' while
// the distinct 'SAP.DE'/'SAP.TO' collisions all survive for the user to pick.
//
// The tail INTERLEAVES the two markets rather than concatenating them. Straight
// concatenation let a flood of weak US name-matches consume every slot and drop
// the global candidates entirely (q=SAP returned no SAP.DE) — and the whole
// point of resolve-assisted global search is that the user sees the collisions.
function mergeRank(us: Candidate[], global: Candidate[], query: string, limit: number): Candidate[] {
  const q = query.trim().toUpperCase();
  const seen = new Set<string>();
  const out: Candidate[] = [];
  const take = (c: Candidate | undefined): void => {
    if (!c || out.length >= limit) return;
    const key = c.symbol.toUpperCase();
    if (seen.has(key)) return;
    seen.add(key);
    out.push(c);
  };

  const isExact = (c: Candidate) => c.symbol.toUpperCase() === q;
  // US before GLOBAL among exacts: a US listing wins its GLOBAL twin.
  us.filter(isExact).forEach(take);
  global.filter(isExact).forEach(take);

  const usRest = us.filter((c) => !isExact(c));
  const globalRest = global.filter((c) => !isExact(c));
  for (let i = 0; i < Math.max(usRest.length, globalRest.length); i++) {
    take(usRest[i]);
    take(globalRest[i]);
    if (out.length >= limit) break;
  }
  return out;
}

// --------------------------------------------------------------------------- //
// Handler                                                                      //
// --------------------------------------------------------------------------- //
async function readQuery(req: Request): Promise<{ q: string; limit: number }> {
  let q = "";
  let limit = DEFAULT_LIMIT;
  if (req.method === "POST") {
    try {
      const body = await req.json() as { q?: string; limit?: number };
      q = body?.q ?? "";
      if (Number.isFinite(body?.limit)) limit = Number(body!.limit);
    } catch {
      // Malformed body -> empty query -> clean 400 below.
    }
  } else {
    const url = new URL(req.url);
    q = url.searchParams.get("q") ?? "";
    const l = url.searchParams.get("limit");
    if (l && Number.isFinite(Number(l))) limit = Number(l);
  }
  return { q: q.trim(), limit: Math.min(Math.max(limit, 1), 25) };
}

Deno.serve(async (req: Request) => {
  const origin = req.headers.get("Origin");
  const cors = corsHeaders(origin);
  const json = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), {
      status,
      headers: { ...cors, "Content-Type": "application/json; charset=utf-8" },
    });

  if (req.method === "OPTIONS") return new Response("ok", { headers: cors });
  if (req.method !== "GET" && req.method !== "POST") {
    return json({ error: "method not allowed" }, 405);
  }

  const { q, limit } = await readQuery(req);
  if (!q) return json({ error: "missing query parameter 'q'" }, 400);

  const notes: string[] = [];

  // Hebrew never reaches the upstreams — the UI searches tase_securities for it.
  if (HEBREW_RE.test(q)) {
    return json({
      query: q,
      results: [],
      notes: ["Hebrew query — Israeli securities are searched locally in tase_securities, not here"],
    });
  }

  // Fail-soft: one upstream dying must not take the other down with it.
  const [usRes, globalRes] = await Promise.allSettled([
    loadSecTickers().then((entries) => secSuggest(entries, q, limit)),
    yahooSuggest(q, limit),
  ]);

  const us = usRes.status === "fulfilled" ? usRes.value : [];
  if (usRes.status === "rejected") {
    console.error("SEC lookup failed:", usRes.reason);
    notes.push(`US (SEC) search unavailable: ${usRes.reason}`);
  }

  const global = globalRes.status === "fulfilled" ? globalRes.value : [];
  if (globalRes.status === "rejected") {
    console.error("Yahoo search failed:", globalRes.reason);
    notes.push(`Global (Yahoo) search unavailable: ${globalRes.reason}`);
  }

  return json({ query: q, results: mergeRank(us, global, q, limit), notes });
});
