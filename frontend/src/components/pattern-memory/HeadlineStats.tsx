import React from 'react';

interface HeadlineStatsProps {
  loading: boolean;
  total: number;
  overallWin: number;
  symbolsCount: number;
}

const HeadlineStats: React.FC<HeadlineStatsProps> = ({ loading, total, overallWin, symbolsCount }) => {
  return (
    <div className="nd-pm-headline" style={{ gap: 16 }}>
      {[
        {
          label: 'Total Cases', value: loading ? '—' : total.toLocaleString(),
          color: 'var(--nd-text-1)', icon: 'dataset',
          iconColor: 'var(--nd-green)', iconBg: 'rgba(0,179,134,0.12)',
        },
        {
          label: 'Historical Win-Rate',
          value: loading || !total ? '—' : `${(overallWin * 100).toFixed(1)}%`,
          color: overallWin >= 0.5 ? 'var(--nd-green)' : '#e74c3c',
          icon: 'emoji_events',
          iconColor: overallWin >= 0.5 ? 'var(--nd-green)' : '#e74c3c',
          iconBg: overallWin >= 0.5 ? 'rgba(0,179,134,0.12)' : 'rgba(231,76,60,0.12)',
        },
        {
          label: 'Symbols', value: loading ? '—' : String(symbolsCount ?? 0),
          color: 'var(--nd-text-1)', icon: 'candlestick_chart',
          iconColor: '#7c3aed', iconBg: 'rgba(124,58,237,0.12)',
        },
      ].map(s => (
        <div key={s.label} className="nd-pm-card" style={{
          display: 'flex', alignItems: 'center', gap: 12,
        }}>
          <div style={{
            width: 40, height: 40, borderRadius: 10, flexShrink: 0,
            background: s.iconBg,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            <span className="material-icons" style={{ color: s.iconColor, fontSize: 21 }}>{s.icon}</span>
          </div>
          <div style={{ minWidth: 0 }}>
            <div style={{
              fontSize: 10, fontWeight: 600, textTransform: 'uppercase',
              letterSpacing: '0.5px', color: 'var(--nd-text-3)', marginBottom: 4,
            }}>{s.label}</div>
            <div style={{ fontSize: 26, fontWeight: 700, color: s.color, lineHeight: 1 }}>{s.value}</div>
          </div>
        </div>
      ))}
    </div>
  );
};

export default HeadlineStats;
