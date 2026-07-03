import React, { useEffect, useState } from 'react';
import apiService from '../../services/api';

const ScanAccuracyCard: React.FC = () => {
  const [data, setData] = useState<any>(null);
  const [show, setShow] = useState<{ intraday: boolean; delivery: boolean; committed: boolean }>(
    { intraday: true, delivery: true, committed: true });
  const [hover, setHover] = useState<{ cx: number; cy: number; sLabel: string; sColor: string; p: any } | null>(null);
  useEffect(() => { apiService.scanEvaluation().then(r => setData((r as any).data)).catch(() => {}); }, []);

  const intraday: any[] = data?.trend ?? [];
  const delivery: any[] = data?.deliveryTrend ?? [];
  const committed: any[] = data?.committedTrend ?? [];
  const target: number = (data?.target ?? 0.9) * 100;
  const ov = data?.overall, ovd = data?.overallDelivery, ovc = data?.overallCommitted;

  const series = [
    { key: 'committed', label: 'High-conviction', color: '#a855f7', pts: committed, on: show.committed, overall: ovc },
    { key: 'intraday', label: 'Intraday', color: '#22c55e', pts: intraday, on: show.intraday, overall: ov },
    { key: 'delivery', label: 'Delivery', color: '#3b82f6', pts: delivery, on: show.delivery, overall: ovd },
  ];
  const allPts = series.filter(s => s.on).flatMap(s => s.pts);
  const hasData = allPts.length >= 1;

  const W = 600, H = 170, PL = 40, PR = 12, PT = 16, PB = 26;
  // x-axis = union of dates across both series, ordered
  const dates = Array.from(new Set([...intraday, ...delivery, ...committed].map(p => p.date))).filter(Boolean).sort();
  const xi = (d: string) => dates.length <= 1 ? PL + (W - PL - PR) / 2 : PL + (dates.indexOf(d) / (dates.length - 1)) * (W - PL - PR);
  const ys = [...allPts.map(p => p.accuracy * 100), target];
  const yMin = Math.max(0, Math.min(...ys) - 8), yMax = Math.min(100, Math.max(...ys) + 8);
  const sy = (v: number) => PT + (1 - (v - yMin) / (yMax - yMin || 1)) * (H - PT - PB);

  // Headline = the stable OVERALL accuracy (per-day is noisy at small committed
  // sample sizes). Falls back to the latest point if there's no overall yet.
  const latestAcc = (s: any) => s.overall?.accuracy != null ? s.overall.accuracy * 100
    : (s.pts.length ? s.pts[s.pts.length - 1].accuracy * 100 : null);
  const commAcc = ovc?.accuracy != null ? ovc.accuracy * 100 : null;
  const commBelow = commAcc != null && commAcc < target;

  return (
    <div className="nd-card" style={{ padding: '16px 18px', marginBottom: 0, height: '100%', display: 'flex', flexDirection: 'column', minWidth: 0, overflow: 'hidden' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8, marginBottom: 8 }}>
        <div>
          <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--nd-text-1)' }}>AI Scan Accuracy</div>
          <div style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>Graded vs the actual move · <span style={{ color: '#a855f7' }}>High-conviction</span> is the selective tier tuned to the {target.toFixed(0)}% target</div>
        </div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
          {series.map(s => {
            const la = latestAcc(s);
            const er = s.overall?.avgReturn;        // expectancy: avg return per graded pick
            return (
              <button key={s.key} onClick={() => setShow(p => ({ ...p, [s.key]: !(p as any)[s.key] }))} style={{
                padding: '4px 9px', borderRadius: 7, cursor: 'pointer', textAlign: 'right',
                border: `1px solid ${s.on ? s.color : 'var(--nd-border)'}`,
                background: s.on ? `${s.color}1a` : 'transparent', opacity: s.on ? 1 : 0.5,
              }}>
                <div style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>{s.label}</div>
                <div style={{ fontSize: 15, fontWeight: 700, color: s.color }}>{la != null ? `${la.toFixed(0)}%` : '—'}</div>
                {er != null && <div style={{ fontSize: 9.5, color: er >= 0 ? 'var(--nd-green)' : 'var(--nd-red)' }}>{er >= 0 ? '+' : ''}{er.toFixed(1)}%/pick</div>}
              </button>
            );
          })}
        </div>
      </div>

      {!hasData ? (
        <div style={{ fontSize: 12, color: 'var(--nd-text-3)', padding: '24px 0' }}>
          No graded scans yet — the morning watchlist is graded after each close (delivery picks after a {data?.latest?.horizon_days ?? 5}-day hold).
        </div>
      ) : (
        <div style={{ position: 'relative', width: '100%' }} onMouseLeave={() => setHover(null)}>
        <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 160, display: 'block' }} preserveAspectRatio="none">
          {[yMin, (yMin + yMax) / 2, yMax].map((v, i) => (
            <g key={i}>
              <line x1={PL} y1={sy(v)} x2={W - PR} y2={sy(v)} stroke="var(--nd-border)" strokeWidth="0.5" />
              <text x={4} y={sy(v) + 3} fontSize="9" fill="var(--nd-text-3)">{v.toFixed(0)}%</text>
            </g>
          ))}
          {/* target line */}
          <line x1={PL} y1={sy(target)} x2={W - PR} y2={sy(target)} stroke="#f59e0b" strokeWidth="1" strokeDasharray="4 3" opacity={0.8} />
          <text x={W - PR} y={sy(target) - 3} fontSize="9" fill="#f59e0b" textAnchor="end">target {target.toFixed(0)}%</text>
          {series.filter(s => s.on && s.pts.length).map(s => {
            const line = s.pts.map(p => `${xi(p.date).toFixed(1)},${sy(p.accuracy * 100).toFixed(1)}`).join(' ');
            return (
              <g key={s.key}>
                {s.pts.length > 1
                  ? <polyline points={line} fill="none" stroke={s.color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                  : null}
                {s.pts.map((p, i) => {
                  const cx = xi(p.date), cy = sy(p.accuracy * 100);
                  const active = hover && hover.p === p;
                  return (
                    <g key={i}>
                      <circle cx={cx} cy={cy} r={active ? 5 : 3}
                        fill={p.meetsTarget ? s.color : '#ef4444'} stroke={active ? '#fff' : s.color} strokeWidth={active ? 1.5 : 1}
                        style={{ transition: 'r 0.1s' }} />
                      {/* larger invisible hit target for easy hover/focus */}
                      <circle cx={cx} cy={cy} r="9" fill="transparent" style={{ cursor: 'pointer' }}
                        tabIndex={0}
                        onMouseEnter={() => setHover({ cx, cy, sLabel: s.label, sColor: s.color, p })}
                        onFocus={() => setHover({ cx, cy, sLabel: s.label, sColor: s.color, p })}
                        onBlur={() => setHover(null)} />
                    </g>
                  );
                })}
              </g>
            );
          })}
          {dates.length > 0 && [dates[0], dates[dates.length - 1]].map((d, k) => (
            <text key={k} x={xi(d)} y={H - 8} fontSize="9" fill="var(--nd-text-3)" textAnchor={k === 0 ? 'start' : 'end'}>{d}</text>
          ))}
        </svg>

        {/* Hover tooltip */}
        {hover && (() => {
          const leftPct = (hover.cx / W) * 100;
          const topPct = (hover.cy / H) * 100;
          const flipRight = leftPct > 62;       // keep tooltip on-screen near the right edge
          const er = hover.p.avgRealizedReturnPct;
          return (
            <div style={{
              position: 'absolute', left: `${leftPct}%`, top: `${topPct}%`,
              transform: `translate(${flipRight ? '-100%' : '0'}, calc(-100% - 10px))`,
              marginLeft: flipRight ? -8 : 8,
              background: 'var(--nd-bg-2, #1b2330)', border: `1px solid ${hover.sColor}55`,
              borderRadius: 8, padding: '8px 10px', minWidth: 150, zIndex: 20,
              boxShadow: '0 6px 20px rgba(0,0,0,.4)', pointerEvents: 'none',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                <span style={{ width: 8, height: 8, borderRadius: '50%', background: hover.sColor, flexShrink: 0 }} />
                <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--nd-text-1)' }}>{hover.sLabel}</span>
                <span style={{ marginLeft: 'auto', fontSize: 9.5, fontWeight: 700, padding: '1px 5px', borderRadius: 4,
                  background: hover.p.meetsTarget ? '#22c55e22' : '#ef444422',
                  color: hover.p.meetsTarget ? '#22c55e' : '#ef4444' }}>
                  {hover.p.meetsTarget ? 'HIT' : 'MISS'}
                </span>
              </div>
              <div style={{ fontSize: 10.5, color: 'var(--nd-text-3)', marginBottom: 6 }}>{hover.p.date}</div>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, fontSize: 11.5, marginBottom: 2 }}>
                <span style={{ color: 'var(--nd-text-3)' }}>Accuracy</span>
                <span style={{ fontWeight: 700, color: hover.sColor }}>{(hover.p.accuracy * 100).toFixed(0)}% vs {target.toFixed(0)}%</span>
              </div>
              {hover.p.picks != null && (
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, fontSize: 11.5, marginBottom: 2 }}>
                  <span style={{ color: 'var(--nd-text-3)' }}>Picks graded</span>
                  <span style={{ fontWeight: 600, color: 'var(--nd-text-1)' }}>{hover.p.picks}</span>
                </div>
              )}
              {er != null && (
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, fontSize: 11.5 }}>
                  <span style={{ color: 'var(--nd-text-3)' }}>Avg move/pick</span>
                  <span style={{ fontWeight: 600, color: er >= 0 ? 'var(--nd-green)' : 'var(--nd-red)' }}>{er >= 0 ? '+' : ''}{er.toFixed(2)}%</span>
                </div>
              )}
            </div>
          );
        })()}
        </div>
      )}

      {/* spacer pushes the status note to the bottom so the card fills the row
          height without stretching the chart */}
      <div style={{ flex: 1, minHeight: 10 }} />

      {commBelow ? (
        <div style={{ marginTop: 8, fontSize: 11, color: '#d8b4fe', background: '#a855f715', border: '1px solid #a855f733', borderRadius: 8, padding: '6px 9px' }}>
          The high-conviction tier is at {commAcc!.toFixed(0)}% vs the {target.toFixed(0)}% target — the selectivity bar auto-tightens each session (fewer, higher-confluence picks) to close the gap. Broad intraday/delivery accuracy stays ~50% by nature and isn't traded.
        </div>
      ) : commAcc != null ? (
        <div style={{ marginTop: 8, fontSize: 11, color: '#86efac', background: '#22c55e15', border: '1px solid #22c55e33', borderRadius: 8, padding: '6px 9px' }}>
          ✓ High-conviction tier at {commAcc.toFixed(0)}% — meeting the {target.toFixed(0)}% target. Only these committed picks are acted on.
        </div>
      ) : null}
    </div>
  );
};

export default ScanAccuracyCard;
