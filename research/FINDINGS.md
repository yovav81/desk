# DESK — Phase 0: Data Source Investigation Findings

Date: 2026-07-08. Environment: Windows 11, Python 3.12.9, yfinance 1.2.0.
Test scripts in `research\` (`test_yf_quotes.py`, `test_yf_bonds_news.py`, `test_rss.py`, `test_rss2.py`).

---

## A. Israeli stocks

### 1. TASE downloadable securities list — FAILED (for automation) / manual path exists
- No publicly downloadable CSV/XLSX reachable by script. The list pages exist at
  `https://market.tase.co.il/he/market_data/securities/data/all` (stocks: `/stocks`, bonds: `/bonds`)
  with an in-browser Excel export button, but every underlying JSON/export call goes through
  **Imperva/Incapsula WAF → HTTP 403** for non-browser clients (tested `api.tase.co.il`,
  `mayaapi.tase.co.il` with browser UA, Referer, `X-Maya-With: allow` — all rejected).
- Official programmatic source: TASE DataHub **"Securities (Basic)"** API product — listed **FREE**
  in the official 2025 price list — but requires portal signup + API key (out of scope this phase).
- No copy saved to `research\` — blocked without a browser session.
- **Recommendation:** one-time manual browser export from market.tase.co.il to seed the mapping
  (security number ↔ symbol ↔ name ↔ type), then move to DataHub Securities (Basic) in Phase 1.

### 2. yfinance .TA stocks — VERIFIED
| Ticker | Last close (2026-07-08) | MTD | YTD | Currency |
|---|---|---|---|---|
| TEVA.TA | 10,210 | +1.29% | +2.58% | **ILA** |
| LUMI.TA | 6,978 | +4.30% | −3.86% | **ILA** |
| POLI.TA | 7,131 | +3.81% | −4.19% | **ILA** |

- `history(period="ytd")` returns ~129 daily rows — plenty for MTD/YTD.
- **Currency pitfall confirmed:** `fast_info.currency == "ILA"` (agorot). TEVA 10,210 ILA = ₪102.10.
  Divide by 100 when currency is ILA; always read the currency field, don't assume.
- **Recommendation:** yfinance is the primary quote source for TASE **stocks**.

## B. Israeli bonds

### 3. yfinance for TASE bonds — FAILED (as expected)
- Tested numeric security numbers as tickers (incl. real corporate bond 1234673 "אקסל אגח א"):
  Yahoo returns `Quote not found` / empty history for all. `yf.Search` finds no TASE bonds.
- **Yahoo Finance does not carry TASE bonds.** A different source is required.

### 4. market.tase.co.il public JSON endpoints — FAILED / fragile
- All `api.tase.co.il` / `mayaapi.tase.co.il` endpoints return **403 (Imperva WAF)** without a
  browser-executed JS challenge. Workable only via headless browser (Playwright) — fragile,
  breaks on WAF updates, and TASE site terms prohibit automated scraping. **Not recommended.**

### 5. TASE DataHub — DOCS-ONLY
- Portal: `datahub.tase.co.il` / developer portal `openapi.tase.co.il`; access = signup + API key.
- Official 2025 API price list (saved: `research\tase_api_pricelist_2025.pdf`, from
  content.tase.co.il), monthly USD, relevant items:
  - **FREE:** Securities (Basic), Indices (Basic), TASE indices online, Mutual Funds (Basic),
    Trading & Vacation Schedules, Lending Pool (online), OTC Transactions Online.
  - **Paid:** Securities data EoD current $100 (internal use) / Quotes End Of Day $747 /
    Securities Prices Online (15-min delayed, refresh 30s) $500 / 5-yr history $500, 10-yr $1,000.
- So: free tier exists for **reference data**, but **bond prices (EOD/delayed) are paid** (~$100/mo
  minimum for internal-use EOD).
- **Recommendation:** Phase 1 decision — either pay for Securities data EoD, or accept
  browser-assisted/manual bond quotes. Bonds are the hard part of this project.

## C. US quotes

### 6. yfinance US — VERIFIED
AAPL: last 314.30 (2026-07-08), MTD +6.77%, YTD +16.19%. MSFT: 383.21, MTD −0.28%, YTD −18.61%.
Currency USD, ~128 daily rows YTD. No issues; yfinance is sufficient for US watchlist quotes.

## D. News sources

### 7. Israeli finance RSS — PARTIAL
| Source | Status | URL | Notes |
|---|---|---|---|
| Globes (capital-markets, He) | **VERIFIED** | `globes.co.il/webservice/rss/rssfeeder.asmx/FeederNode?iID=2` | 15 items; fields: title, link, description, pubDate, category, author, media:content. Hebrew UTF-8 OK. Other sections via other `iID`s. |
| Globes (iID=1725, En) | **VERIFIED** | same base, `iID=1725` | 93 items, English-language feed. |
| TheMarker | **VERIFIED** (flag) | `themarker.com/cmlink/1.144` | 100 items; Hebrew OK; first item's pubDate was ~6 weeks old — check ordering/staleness before relying on it. |
| Calcalist | **FAILED** | `calcalist.co.il/GeneralRSS/...` | HTTP 403 even with browser UA (WAF/geo). Covered indirectly via Google News. |
| Bizportal | **FAILED** | `bizportal.co.il/shukhahon/rss.*` | Cloudflare 403 challenge page. Covered indirectly via Google News. |

Earlier "garbled Hebrew" was Windows console codepage only — feeds themselves are clean UTF-8.

### 8. yfinance news (AAPL) — VERIFIED
Returns 10 items, each `{id, content}`; `content` fields: `title, summary, description, pubDate,
displayTime, contentType, canonicalUrl, clickThroughUrl, provider, thumbnail, storyline, metadata,
isHosted, previewUrl, bypassModal, finance`. Good enough for a per-US-ticker news panel.

### 9. FMP stock-news — DOCS-ONLY
Endpoint: legacy `GET /api/v3/stock_news?tickers=...` (new "stable" API: `/stable/news/stock`).
Docs/pricing pages indicate financial-market news is included from the **Starter (paid)** plan;
free key (250 req/day) does not reliably include news endpoints. **Skip — yfinance + RSS suffice.**

### 10. Google News RSS per-company query — VERIFIED
`https://news.google.com/rss/search?q="טבע תעשיות"&hl=he&gl=IL&ceid=IL:he` → 41 items;
fields: title, link, description, pubDate, source. Hebrew OK.
**Pros:** per-company queries in Hebrew or English; aggregates Calcalist/Bizportal despite their
blocking; zero setup. **Cons:** links are Google redirect URLs; titles suffixed with " - source";
no full text; duplicates across sources; unofficial (may throttle aggressive polling).
**Recommendation:** primary per-company Israeli news source, dedup by title similarity.

## E. Email inbox ingestion (research only)

### 11. Inbox option comparison — DOCS-ONLY
| Option | Setup | Polling | Secrets on GitHub Actions | Reliability |
|---|---|---|---|---|
| **Gmail IMAP + app password** | Trivial (enable 2FA → generate app password) | `imaplib` every N min | 1 static secret string | High; app passwords stable for personal accounts |
| Gmail API (OAuth) | GCP project + consent screen + refresh-token dance | REST poll (or Pub/Sub push, overkill) | client id/secret + refresh token; token can be revoked/expire | High but more moving parts |
| Outlook.com IMAP | **Blocked** — Microsoft retired basic auth for consumer IMAP; OAuth2 (MSAL) required | — | — | — |

**Recommendation: Gmail IMAP + app password.** One secret, stdlib-only client, works unattended on
GitHub Actions with no OAuth refresh churn; a dedicated inbox limits blast radius if the app
password leaks. Revisit Gmail API only if Google restricts app passwords.

### 12. Parsing + company tagging — DOCS-ONLY
- Parse: `imaplib` fetch → `email.message_from_bytes()` → `msg.walk()`, take `text/html` part,
  `part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8")` →
  `BeautifulSoup(html, "html.parser").get_text(" ", strip=True)`.
- Tagging: per-company alias table (English name, Hebrew name variants, ticker, TASE security
  number). Match in priority order: (1) sender domain map (e.g. ir@company.com), (2) aliases in
  subject, (3) aliases/security number in body text. Unmatched → "general" bucket for manual triage.

```python
def tag(msg_from, subject, body, companies):
    for c in companies:
        if any(d in msg_from for d in c.domains):  return c.id
    text = f"{subject} {body}"
    for c in companies:
        if any(a in text for a in c.aliases):      return c.id
    return None
```

---

## Recommended data stack

| Need | Source | Status |
|---|---|---|
| US stock quotes + MTD/YTD | yfinance | VERIFIED |
| TASE stock quotes + MTD/YTD | yfinance `.TA` (**divide ILA by 100**) | VERIFIED |
| Security number ↔ symbol mapping | Manual TASE export now → DataHub "Securities (Basic)" (free, signup) later | PENDING signup |
| TASE bond quotes | **Open problem** — DataHub paid EOD (~$100/mo) or manual; yfinance/scraping ruled out | BLOCKED |
| Israeli market news | Globes RSS + Google News RSS per company (+ TheMarker RSS) | VERIFIED |
| US ticker news | yfinance `.news` (+ Google News RSS fallback) | VERIFIED |
| Email ingestion | Gmail dedicated inbox, IMAP + app password | DOCS-ONLY |

## Open questions
1. **Bonds pricing source** — pay for TASE DataHub EOD, or accept manual/degraded bond quotes?
   This is the main unresolved dependency for the watchlist.
2. DataHub free "Securities (Basic)": exact fields/coverage unverifiable without signup — verify
   it includes security type (stock/bond) and both active stocks and bonds.
3. TheMarker feed freshness (stale first item) — re-check before including.
4. yfinance ILA: spot-check a few more .TA names (incl. dual-listed) to confirm ILA is uniform.
5. Google News polling cadence limits — establish a polite interval (e.g. 15 min) empirically.
6. GitHub: `gh` CLI is **not installed** on this machine — private repo `yovav81/desk-dashboard`
   was NOT created/pushed. Install gh + `gh auth login`, then create the repo and push.
