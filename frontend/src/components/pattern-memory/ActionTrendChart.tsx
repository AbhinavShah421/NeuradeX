import React, { useEffect, useState } from 'react';
import apiService from '../../services/api';

// ── Action trend mini-chart (used inside AgentDetailSheet) ───────────────────

const ActionTrendChart: React.FC<{ agentName: string; action: string; color: string }> = ({ agentName, action, color }) => {
  const [points, setPoints] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    apiService.agentActionTrend(agentName, action)
      .then((r: any) => setPoints(r?.data?.points ?? []))
      .catch(() => setPoints([]))
      .finally(() => setLoading(false));
  }, [agentName, action]);

  if (loading) return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 100, color: 'var(--nd-text-3)', fontSize: 12 }}>
      <span className="material-icons nd-spin" style={{ fontSize: 18, marginRight: 6 }}>autorenew</span>Loading…
    </div>
  );

  if (points.length < 2) return (
    <div style={{ fontSize: 11, color: 'var(--nd-text-3)', fontStyle: 'italic', padding: '10px 0' }}>
      Not enough dated data yet — accuracy trend will appear once more training sessions complete.
    </div>
  );

  const W = 340, H = 110, PL = 34, PR = 10, PT = 10, PB = 22;
  const accs   = points.map((p: any) => p.accuracy * 100);
  const minA   = Math.max(0,   Math.min(...accs) - 8);
  const maxA   = Math.min(100, Math.max(...accs) + 8);
  const rangeA = maxA - minA || 1;
  const sx = (i: number) => PL + (i / (points.length - 1)) * (W - PL - PR);
  const sy = (v: number) => PT + (1 - (v - minA) / rangeA) * (H - PT - PB);
  const linePath = points.map((p: any, i: number) => `${i === 0 ? 'M' : 'L'}${sx(i).toFixed(1)},${sy(p.accuracy * 100).toFixed(1)}`).join(' ');
  const gradId = `atg_${agentName}_${action}`;

  // 7-day rolling average
  const rolling: number[] = points.map((_: any, i: number) => {
    const slice = points.slice(Math.max(0, i - 6), i + 1);
    return slice.reduce((s: number, p: any) => s + p.accuracy * 100, 0) / slice.length;
  });
  const rollingPath = rolling.map((v, i) => `${i === 0 ? 'M' : 'L'}${sx(i).toFixed(1)},${sy(v).toFixed(1)}`).join(' ');

  const refY = sy(50);
  const dates = points.map((p: any) => p.date as string);

  return (
    <div>
      {/* Legend */}
      <div style={{ display: 'flex', gap: 14, marginBottom: 6, fontSize: 10, color: 'var(--nd-text-3)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <div style={{ width: 10, height: 2, background: color, opacity: 0.4, borderRadius: 1 }} />
          Daily accuracy
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <div style={{ width: 10, height: 2, background: color, borderRadius: 1 }} />
          7-day rolling
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <div style={{ width: 10, height: 2, background: '#64748b', borderRadius: 1, borderTop: '1px dashed #64748b' }} />
          50% baseline
        </div>
      </div>

      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} style={{ display: 'block', overflow: 'visible' }}>
        <defs>
          <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.18} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>

        {/* Y-axis labels */}
        {[minA, 50, maxA].filter((v, i, a) => a.indexOf(v) === i).map((v, i) => (
          <text key={i} x={PL - 3} y={sy(v) + 4} textAnchor="end" fontSize={8} fill="var(--nd-text-3)">{v.toFixed(0)}%</text>
        ))}

        {/* 50% reference line */}
        {refY >= PT && refY <= H - PB && (
          <line x1={PL} y1={refY} x2={W - PR} y2={refY} stroke="#64748b" strokeWidth={1} strokeDasharray="3 3" />
        )}

        {/* Area fill under daily line */}
        <path d={`${linePath} L${sx(points.length - 1).toFixed(1)},${H - PB} L${sx(0).toFixed(1)},${H - PB} Z`}
          fill={`url(#${gradId})`} />

        {/* Daily accuracy line (faint) */}
        <path d={linePath} fill="none" stroke={color} strokeWidth={1.2} strokeOpacity={0.4} strokeLinejoin="round" />

        {/* Dots on daily points */}
        {points.map((p: any, i: number) => (
          <circle key={i} cx={sx(i)} cy={sy(p.accuracy * 100)} r={2.5} fill={color} opacity={0.5} />
        ))}

        {/* 7-day rolling average (bold) */}
        <path d={rollingPath} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        <circle cx={sx(points.length - 1)} cy={sy(rolling[rolling.length - 1])} r={3.5} fill={color} />

        {/* X-axis: first and last date */}
        <text x={PL} y={H} textAnchor="start" fontSize={8} fill="var(--nd-text-3)">{dates[0]}</text>
        <text x={W - PR} y={H} textAnchor="end" fontSize={8} fill="var(--nd-text-3)">{dates[dates.length - 1]}</text>
      </svg>

      {/* Latest stats row */}
      <div style={{ display: 'flex', gap: 16, marginTop: 6, fontSize: 11, color: 'var(--nd-text-3)' }}>
        <span>{points.length} trading days</span>
        <span>·</span>
        <span>{points.reduce((s: number, p: any) => s + p.total, 0).toLocaleString()} total decisions</span>
        <span>·</span>
        <span style={{ color, fontWeight: 600 }}>
          {(rolling[rolling.length - 1]).toFixed(0)}% recent accuracy
        </span>
      </div>
    </div>
  );
};

export default ActionTrendChart;
