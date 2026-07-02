import React, { useEffect, useState } from 'react';
import apiService from '../../services/api';

// ── Learning curve ────────────────────────────────────────────────────────────

type LcMetric = 'equity' | 'rolling' | 'cumulative';

const SOURCE_PRESETS: Record<string, string> = {
  All:      'PAPER,LIVE,REPLAY,BACKTEST',
  Paper:    'PAPER,LIVE',
  Replay:   'REPLAY',
  Backtest: 'BACKTEST',
};

const EVENT_COLOR: Record<string, string> = {
  scanner: '#3b82f6', trading: '#f59e0b', learning: '#a855f7', update: '#94a3b8',
};

const LearningCurveCard: React.FC<{ embedded?: boolean }> = ({ embedded }) => {
  const [data, setData]       = useState<any>(null);
  const [wideData, setWideData] = useState<any>(null); // window=200 for Trend WR tab
  const [metric, setMetric]   = useState<LcMetric>('equity');
  const [srcKey, setSrcKey]   = useState<string>('All');
  const [hovEv, setHovEv]     = useState<{ x: number; ev: any } | null>(null);
  const rootCls = embedded ? undefined : 'nd-card';
  const rootStyle: React.CSSProperties = embedded
    ? { padding: '16px 18px', position: 'relative' }
    : { padding: '16px 18px', marginBottom: 20, position: 'relative' };

  useEffect(() => {
    const src = SOURCE_PRESETS[srcKey];
    apiService.learningCurve(src, 50)
      .then(r => setData((r as any).data)).catch(() => {});
    // Fetch wider window in parallel — used for the Trend WR view so the line
    // actually moves instead of being frozen like a cumulative over 8000+ trades.
    apiService.learningCurve(src, 200)
      .then(r => setWideData((r as any).data)).catch(() => {});
  }, [srcKey]);

  // Trend WR uses wideData (200-trade rolling) so it shows actual movement.
  // For equity + rolling, use the standard 50-window data.
  const activeData = metric === 'cumulative' ? (wideData ?? data) : data;
  const pts: any[] = activeData?.points ?? [];
  const events: any[] = data?.events ?? [];
  const bySource: any[] = data?.bySource ?? [];
  if ((data?.points?.length ?? 0) < 2) {
    return (
      <div className={rootCls} style={{ padding: '16px 18px' }}>
        <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--nd-text-1)' }}>System Learning Curve</div>
        <div style={{ fontSize: 12, color: 'var(--nd-text-3)', marginTop: 6 }}>
          Not enough {srcKey.toLowerCase()} trades yet to plot.
        </div>
      </div>
    );
  }

  // 'cumulative' tab now shows 200-trade rolling WR (rollWinRate from wideData)
  // — still smooth enough to show long-run trend but actually responsive to changes.
  const val = (p: any): number =>
    metric === 'equity' ? p.cumEquity
      : p.rollWinRate * 100;   // rolling-50 OR rolling-200 depending on activeData
  const isPct = metric !== 'equity';
  const color = metric === 'equity' ? 'var(--nd-green)' : metric === 'rolling' ? '#3b82f6' : '#a855f7';

  const visiblePts = pts;  // no tail-zoom needed — Trend WR (200-window) always moves

  const W = 600, H = 170, PL = 42, PR = 12, PT = 16, PB = 26;
  const ys = visiblePts.map(val);
  const pad = (Math.max(...ys) - Math.min(...ys)) * 0.08 || 1;
  let yMin = Math.min(...ys) - pad, yMax = Math.max(...ys) + pad;
  if (isPct) { yMin = Math.max(0, yMin); yMax = Math.min(100, yMax); }
  // x = trade SEQUENCE (evenly spaced), not wall-clock — backtest trades cluster
  // by backfill date, so a time axis crushes thousands of trades into a flat line
  // then a cliff. Sequence spacing shows the real trade-by-trade progression.
  const vp = visiblePts;
  const sx = (i: number) => PL + (vp.length <= 1 ? 0.5 : i / (vp.length - 1)) * (W - PL - PR);
  const sy = (v: number) => PT + (1 - (v - yMin) / (yMax - yMin || 1)) * (H - PT - PB);
  const eventX = (ts: string) => {
    const t = Date.parse(ts); if (isNaN(t)) return null;
    let best = 0, bd = Infinity;
    vp.forEach((p, i) => { const d = Math.abs((Date.parse(p.ts) || 0) - t); if (d < bd) { bd = d; best = i; } });
    return sx(best);
  };
  const line = vp.map((p, i) => `${sx(i).toFixed(1)},${sy(val(p)).toFixed(1)}`).join(' ');
  const last = pts[pts.length - 1];
  const fmt = (v: number) => isPct ? `${v.toFixed(1)}%` : `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`;
  const headline = isPct ? `${val(last).toFixed(1)}%`
    : `${val(last) >= 0 ? '+' : ''}${val(last).toFixed(1)}%`;
  const trend = val(last) - val(pts[Math.max(0, pts.length - 6)]);

  const tabBtn = (label: string, active: boolean, onClick: () => void) => (
    <button key={label} onClick={onClick} style={{
      padding: '3px 10px', fontSize: 11, fontWeight: 600, borderRadius: 6, cursor: 'pointer',
      border: `1px solid ${active ? color : 'var(--nd-border)'}`,
      background: active ? color : 'transparent',
      color: active ? '#fff' : 'var(--nd-text-2)',
    }}>{label}</button>
  );

  return (
    <div className={rootCls} style={rootStyle}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8, marginBottom: 8 }}>
        <div>
          <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--nd-text-1)' }}>System Learning Curve</div>
          <div style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>
            {metric === 'equity' ? 'Cumulative return (equity) — rises even when win-rate is below 50%'
              : metric === 'rolling' ? 'Trailing-50-trade win-rate — recent skill, not dragged by old trades'
                : 'Trailing-200-trade win-rate — long-run trend that actually moves'}
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={{ fontSize: 18, fontWeight: 700, color }}>{headline}</div>
          <div style={{ fontSize: 11, color: trend >= 0 ? 'var(--nd-green)' : 'var(--nd-red)' }}>
            {trend >= 0 ? '▲' : '▼'} {fmt(Math.abs(trend))} recent · {data.totalTrades} trades
          </div>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 8 }}>
        {tabBtn('Equity', metric === 'equity', () => setMetric('equity'))}
        {tabBtn('Rolling WR', metric === 'rolling', () => setMetric('rolling'))}
        {tabBtn('Trend WR', metric === 'cumulative', () => setMetric('cumulative'))}
        <span style={{ width: 1, background: 'var(--nd-border)', margin: '0 4px' }} />
        {Object.keys(SOURCE_PRESETS).map(k => tabBtn(k, srcKey === k, () => setSrcKey(k)))}
      </div>

      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 160 }} preserveAspectRatio="none"
        onMouseLeave={() => setHovEv(null)}>
        {[yMin, (yMin + yMax) / 2, yMax].map((v, i) => (
          <g key={i}>
            <line x1={PL} y1={sy(v)} x2={W - PR} y2={sy(v)} stroke="var(--nd-border)" strokeWidth="0.5" />
            <text x={4} y={sy(v) + 3} fontSize="9" fill="var(--nd-text-3)">{fmt(v)}</text>
          </g>
        ))}
        {metric === 'equity' && yMin < 0 && yMax > 0 && (
          <line x1={PL} y1={sy(0)} x2={W - PR} y2={sy(0)} stroke="var(--nd-text-3)" strokeWidth="0.6" strokeDasharray="3 3" />
        )}
        {/* System-update event markers */}
        {events.map((ev, i) => {
          const x = eventX(ev.occurredAt);
          if (x == null) return null;
          const c = EVENT_COLOR[ev.category] || EVENT_COLOR.update;
          return (
            <g key={i} style={{ cursor: 'pointer' }}
              onMouseEnter={() => setHovEv({ x, ev })} onMouseLeave={() => setHovEv(null)}>
              <line x1={x} y1={PT} x2={x} y2={H - PB} stroke={c} strokeWidth="1" strokeDasharray="3 3" opacity={0.8} />
              <polygon points={`${x - 3},${PT} ${x + 3},${PT} ${x},${PT + 5}`} fill={c} />
              <rect x={x - 7} y={PT} width={14} height={H - PB - PT} fill="transparent" />
            </g>
          );
        })}
        <polyline points={line} fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx={sx(vp.length - 1)} cy={sy(val(last))} r="3.5" fill={color} />
        {/* x-axis date labels — only the endpoints (time-clustered data makes a
            middle label collide with the end one). */}
        <text x={PL} y={H - 8} fontSize="9" fill="var(--nd-text-3)" textAnchor="start">{vp[0]?.date ?? pts[0]?.date}</text>
        {(vp[vp.length - 1]?.date ?? pts[pts.length - 1]?.date) !== (vp[0]?.date ?? pts[0]?.date) && (
          <text x={W - PR} y={H - 8} fontSize="9" fill="var(--nd-text-3)" textAnchor="end">{vp[vp.length - 1]?.date ?? pts[pts.length - 1]?.date}</text>
        )}
      </svg>

      {hovEv && (
        <div style={{
          position: 'absolute', left: `${(hovEv.x / W) * 100}%`, top: 96, transform: 'translateX(-50%)',
          background: 'var(--nd-bg-2, #1b2330)', border: '1px solid var(--nd-border)', borderRadius: 8,
          padding: '8px 10px', maxWidth: 260, zIndex: 10, boxShadow: '0 6px 20px rgba(0,0,0,.35)',
          pointerEvents: 'none',
        }}>
          <div style={{ fontSize: 10, color: EVENT_COLOR[hovEv.ev.category] || '#94a3b8', fontWeight: 700, textTransform: 'uppercase' }}>
            {hovEv.ev.category}
          </div>
          <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--nd-text-1)' }}>{hovEv.ev.title}</div>
          <div style={{ fontSize: 10, color: 'var(--nd-text-3)', marginBottom: 4 }}>
            {new Date(hovEv.ev.occurredAt).toLocaleString()}
          </div>
          <div style={{ fontSize: 11, color: 'var(--nd-text-2)', lineHeight: 1.35 }}>{hovEv.ev.detail}</div>
        </div>
      )}

      {/* Per-source breakdown — exposes that win-rate ≠ profitability */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 10 }}>
        {bySource.map((s) => (
          <div key={s.source} style={{
            border: '1px solid var(--nd-border)', borderRadius: 8, padding: '5px 9px', fontSize: 11,
          }}>
            <span style={{ fontWeight: 700, color: 'var(--nd-text-1)' }}>{s.source}</span>
            <span style={{ color: 'var(--nd-text-3)' }}> · {s.trades} trades</span>
            <span style={{ color: 'var(--nd-text-2)' }}> · WR {(s.winRate * 100).toFixed(0)}%</span>
            <span style={{ color: s.expectancy >= 0 ? 'var(--nd-green)' : 'var(--nd-red)', fontWeight: 600 }}>
              {' · exp '}{s.expectancy >= 0 ? '+' : ''}{s.expectancy.toFixed(2)}%/trade
            </span>
          </div>
        ))}
      </div>
    </div>
  );
};

export default LearningCurveCard;
