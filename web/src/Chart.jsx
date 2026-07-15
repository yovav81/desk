import { useMemo } from 'react';
import { theme as t } from './theme';
import { fmtPrice } from './format';

// Hand-rolled SVG line chart — no charting library, so the bundle stays small
// and there's no new dependency to install.
//
// Time runs left→right, so the NEWEST point is on the right. SVG coordinates
// are absolute and are NOT mirrored by the surrounding RTL layout, so this
// holds regardless of direction. (The container sets dir="ltr" to keep the
// axis labels from re-ordering.)
//
// Colour: the line is the gold ACCENT — a chart is decorative, not a return.
// grn/red stay reserved for gains/losses, per the theme rules.

const H = 240; // viewBox height
const W = 800; // viewBox width — scales to the container via preserveAspectRatio
const PAD = { top: 14, right: 8, bottom: 22, left: 52 };

export default function Chart({ points, currency }) {
  const geom = useMemo(() => {
    if (points.length < 2) return null;
    const closes = points.map((p) => p.close);
    let min = Math.min(...closes);
    let max = Math.max(...closes);
    if (min === max) {
      // A dead-flat series would divide by zero; give it a nominal band so the
      // line renders through the middle instead of vanishing.
      const pad = Math.abs(min) * 0.01 || 1;
      min -= pad;
      max += pad;
    }
    const innerW = W - PAD.left - PAD.right;
    const innerH = H - PAD.top - PAD.bottom;
    const x = (i) => PAD.left + (i / (points.length - 1)) * innerW;
    const y = (v) => PAD.top + (1 - (v - min) / (max - min)) * innerH;

    const line = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(2)},${y(p.close).toFixed(2)}`).join(' ');
    const area = `${line} L${x(points.length - 1).toFixed(2)},${(H - PAD.bottom).toFixed(2)} L${x(0).toFixed(2)},${(H - PAD.bottom).toFixed(2)} Z`;
    return { min, max, x, y, line, area };
  }, [points]);

  if (!geom) return null;

  const { min, max, x, y, line, area } = geom;
  // 3 gridlines: bottom, middle, top of the value range.
  const ticks = [min, (min + max) / 2, max];
  const first = points[0].date;
  const last = points[points.length - 1].date;

  return (
    <div dir="ltr" style={{ width: '100%' }}>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ width: '100%', height: 240, display: 'block' }}>
        <defs>
          <linearGradient id="chartFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={t.acc} stopOpacity="0.18" />
            <stop offset="100%" stopColor={t.acc} stopOpacity="0" />
          </linearGradient>
        </defs>

        {ticks.map((v, i) => (
          <g key={i}>
            <line x1={PAD.left} x2={W - PAD.right} y1={y(v)} y2={y(v)} stroke={t.bd} strokeWidth="1" />
            <text
              x={PAD.left - 8}
              y={y(v)}
              textAnchor="end"
              dominantBaseline="middle"
              fill={t.mut}
              fontSize="11"
              fontFamily="'IBM Plex Mono', monospace"
            >
              {fmtPrice(v)}
            </text>
          </g>
        ))}

        <path d={area} fill="url(#chartFill)" />
        <path d={line} fill="none" stroke={t.acc} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
        {/* Marker on the newest point — the number the watchlist shows. */}
        <circle cx={x(points.length - 1)} cy={y(points[points.length - 1].close)} r="3.5" fill={t.acc} />

        <text x={PAD.left} y={H - 6} fill={t.mut} fontSize="11" fontFamily="'IBM Plex Mono', monospace">
          {fmtDate(first)}
        </text>
        <text x={W - PAD.right} y={H - 6} textAnchor="end" fill={t.mut} fontSize="11" fontFamily="'IBM Plex Mono', monospace">
          {fmtDate(last)}
        </text>
      </svg>
      <div dir="rtl" style={{ fontSize: 11, color: t.mut, textAlign: 'left', paddingTop: 2 }}>
        {points.length} נקודות · {currency || ''}
      </div>
    </div>
  );
}

function fmtDate(d) {
  const dd = String(d.getDate()).padStart(2, '0');
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  return `${dd}.${mm}.${String(d.getFullYear()).slice(2)}`;
}
