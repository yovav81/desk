import { useMemo, useState } from 'react';
import { theme as t } from './theme';
import Chart from './Chart';
import { FeedItem, Notice } from './FeedItem';
import { usePriceHistory } from './usePriceHistory';
import { useSecurityFeed } from './useNews';
import { ccySymbol, displayName, fmtPct, fmtPrice, retColor, subLine, tsValue } from './format';

// Full-screen security detail page: chart + numbers on top, everything we
// collected about THIS security below. Replaces the dashboard view (simple
// client-side state in App — no router needed for one page).
//
// The security row + its quote arrive as a prop from the watchlist fetch, so
// only the history and the per-security feed are fetched here.

const PERIODS = [
  { key: 'month', label: 'חודש', days: 30 },
  { key: 'quarter', label: 'רבעון', days: 91 },
  { key: 'year', label: 'שנה', days: 365 },
];

const RET_KEYS = [
  { key: 'mtd_pct', label: 'חודש' },
  { key: 'qtd_pct', label: 'רבעון' },
  { key: 'ytd_pct', label: 'שנה' },
  { key: 'y12_pct', label: "12ח׳" },
];

// Below this, a "line" would be two dots and a stroke — visual noise implying a
// trend we don't have. Bagira's ~23 real points chart fine.
const MIN_CHART_POINTS = 5;

const mono = "'IBM Plex Mono', monospace";

export default function Detail({ sec, onBack }) {
  const { points, status: histStatus, error: histError } = usePriceHistory(sec.sec_id);
  const { items, status: feedStatus, error: feedError } = useSecurityFeed(sec.sec_id);
  const [period, setPeriod] = useState('year');

  // One fetch, sliced client-side — switching periods never refetches.
  const shownPoints = useMemo(() => {
    const days = PERIODS.find((p) => p.key === period)?.days ?? 365;
    const cutoff = new Date();
    cutoff.setDate(cutoff.getDate() - days);
    return points.filter((p) => p.date >= cutoff);
  }, [points, period]);

  const feed = useMemo(() => [...items].sort((a, b) => tsValue(b.ts) - tsValue(a.ts)), [items]);

  const q = sec.quote;
  const manual = sec.price_source === 'manual';

  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column', background: t.bg, color: t.txt, overflow: 'hidden' }}>
      {/* header: back + identity */}
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
        <BackButton onClick={onBack} />
        <div style={{ minWidth: 0 }}>
          <div dir="auto" style={{ fontSize: 17, fontWeight: 700, textAlign: 'right' }}>
            {displayName(sec)}
          </div>
          <div dir="auto" style={{ fontSize: 12, color: t.mut, textAlign: 'right' }}>
            {subLine(sec)}
          </div>
        </div>
      </div>

      <div style={{ flex: 1, overflowY: 'auto', minHeight: 0, padding: '20px 24px 32px' }}>
        {/* numbers */}
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 18, flexWrap: 'wrap', marginBottom: 6 }}>
          <div style={{ whiteSpace: 'nowrap' }}>
            <span dir="ltr" style={{ fontFamily: mono, fontSize: 30, fontWeight: 600 }}>
              {fmtPrice(q?.last_price)}
            </span>
            {q?.last_price != null && (
              <span style={{ fontSize: 14, color: t.mut }}> {ccySymbol(q?.currency, sec.market)}</span>
            )}
          </div>
          <div dir="ltr" style={{ fontFamily: mono, fontSize: 16, fontWeight: 500, color: manual ? t.mut : retColor(q?.day_change_pct) }}>
            {manual ? '—' : fmtPct(q?.day_change_pct)}
          </div>
        </div>

        {/* manual securities have no daily change and sparse points — say so
            rather than implying a live price. */}
        {manual && (
          <div style={{ fontSize: 12.5, color: t.acc, marginBottom: 14 }}>
            מחיר ידני{q?.as_of ? `, נכון ל-${fmtDay(q.as_of)}` : ''}
          </div>
        )}

        <div style={{ display: 'flex', gap: 26, flexWrap: 'wrap', margin: '14px 0 22px' }}>
          {RET_KEYS.map((r) => (
            <div key={r.key} style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
              <div style={{ fontSize: 11, color: t.mut }}>{r.label}</div>
              <div dir="ltr" style={{ fontFamily: mono, fontSize: 15, fontWeight: 500, color: retColor(q?.[r.key]) }}>
                {fmtPct(q?.[r.key])}
              </div>
            </div>
          ))}
        </div>

        {/* chart */}
        <div style={{ background: t.surf, border: `1px solid ${t.bd}`, borderRadius: 14, padding: '14px 16px 10px', marginBottom: 26 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, marginBottom: 10, flexWrap: 'wrap' }}>
            <div style={{ fontSize: 14, fontWeight: 600 }}>גרף מחיר</div>
            {/* Manual securities never get a selector — there's no series to slice. */}
            {!manual && (
              <div style={{ display: 'flex', gap: 6 }}>
                {PERIODS.map((p) => {
                  const active = period === p.key;
                  return (
                    <button
                      key={p.key}
                      onClick={() => setPeriod(p.key)}
                      style={{
                        padding: '5px 12px',
                        borderRadius: 999,
                        fontSize: 12.5,
                        fontFamily: 'Heebo, sans-serif',
                        cursor: 'pointer',
                        border: `1px solid ${active ? t.acc : t.bd}`,
                        background: active ? t.accSoft : 'transparent',
                        color: active ? t.acc : t.mut,
                      }}
                    >
                      {p.label}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
          <ChartArea
            manual={manual}
            status={histStatus}
            error={histError}
            total={points.length}
            points={shownPoints}
            currency={q?.currency}
          />
        </div>

        {/* feed — this security only */}
        <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 4 }}>מידע ועדכונים</div>
        {feedStatus === 'loading' && <Notice title="טוען…" />}
        {feedStatus === 'error' && <Notice title="שגיאה בטעינת המידע" sub={feedError} />}
        {feedStatus === 'ready' && feed.length === 0 && <Notice title="אין מידע להצגה" />}
        {feedStatus === 'ready' &&
          feed.map((item) => (
            // No security tag: we're already inside this security.
            <FeedItem key={item.key} item={item} secLabel={null} />
          ))}
      </div>
    </div>
  );
}

// Every not-a-chart case is explicit — a blank box would be indistinguishable
// from a broken one, and a 2-point line would be a lie.
function ChartArea({ manual, status, error, total, points, currency }) {
  if (manual) {
    return <ChartNote text="מחיר ידני — אין סדרת מחירים רציפה להצגה" />;
  }
  if (status === 'loading') return <ChartNote text="טוען…" />;
  if (status === 'error') return <ChartNote text={`שגיאה בטעינת ההיסטוריה: ${error}`} tone="err" />;
  if (total < MIN_CHART_POINTS) return <ChartNote text="אין מספיק היסטוריה" />;
  if (points.length < 2) return <ChartNote text="אין מספיק היסטוריה בתקופה זו" />;
  return <Chart points={points} currency={currency} />;
}

function ChartNote({ text, tone }) {
  return (
    <div style={{ height: 240, display: 'grid', placeItems: 'center', fontSize: 13, color: tone === 'err' ? t.red : t.mut, textAlign: 'center', padding: '0 16px' }}>
      {text}
    </div>
  );
}

function BackButton({ onClick }) {
  const [hover, setHover] = useState(false);
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        background: 'none',
        border: `1px solid ${hover ? t.acc : t.bd}`,
        borderRadius: 8,
        padding: '6px 14px',
        fontSize: 13,
        color: hover ? t.acc : t.mut,
        fontFamily: 'Heebo, sans-serif',
        cursor: 'pointer',
        flexShrink: 0,
      }}
    >
      חזרה →
    </button>
  );
}

function fmtDay(ts) {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return '';
  const dd = String(d.getDate()).padStart(2, '0');
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  return `${dd}.${mm}.${d.getFullYear()}`;
}
