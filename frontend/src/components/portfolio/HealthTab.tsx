import React from 'react';

interface HealthTabProps {
  health: any;
}

const HealthTab: React.FC<HealthTabProps> = ({ health }) => {
  return (
    <div style={{ padding: '18px 20px' }}>
      {!health ? (
        <div style={{ textAlign: 'center', padding: 40, color: 'var(--nd-text-3)', fontSize: 13 }}>Analysing portfolio health…</div>
      ) : health.score == null ? (
        <div style={{ textAlign: 'center', padding: 40, color: 'var(--nd-text-3)', fontSize: 13 }}>{health.note}</div>
      ) : (() => {
        const sc = health.score; const col = sc >= 70 ? '#22c55e' : sc >= 55 ? '#f59e0b' : '#ef4444';
        return (
          <div style={{ display: 'flex', gap: 22, flexWrap: 'wrap' }}>
            <div className="nd-card" style={{ padding: '18px 22px', textAlign: 'center', minWidth: 180 }}>
              <svg width={150} height={150} viewBox="0 0 150 150">
                <circle cx={75} cy={75} r={62} fill="none" stroke="var(--nd-border)" strokeWidth={12} />
                <circle cx={75} cy={75} r={62} fill="none" stroke={col} strokeWidth={12} strokeLinecap="round"
                  strokeDasharray={`${2 * Math.PI * 62 * sc / 100} ${2 * Math.PI * 62}`} transform="rotate(-90 75 75)" />
                <text x={75} y={70} textAnchor="middle" fontSize="34" fontWeight="700" fill={col}>{sc.toFixed(0)}</text>
                <text x={75} y={94} textAnchor="middle" fontSize="12" fill="var(--nd-text-3)">/ 100 · {health.grade}</text>
              </svg>
              <div style={{ fontSize: 12, color: 'var(--nd-text-3)', marginTop: 4 }}>{health.metrics.holdings} holdings · ~{health.metrics.effectiveHoldings} effective</div>
            </div>
            <div style={{ flex: 1, minWidth: 280 }}>
              <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>Health factors</div>
              {health.factors.map((f: any) => {
                const fc = f.score >= 70 ? '#22c55e' : f.score >= 50 ? '#f59e0b' : '#ef4444';
                return (
                  <div key={f.key} style={{ marginBottom: 8 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, marginBottom: 2 }}>
                      <span style={{ color: 'var(--nd-text-2)' }}>{f.label} <span style={{ color: 'var(--nd-text-3)' }}>({f.weight}%)</span></span>
                      <span style={{ color: fc, fontWeight: 700 }}>{f.score.toFixed(0)}</span>
                    </div>
                    <div style={{ height: 7, background: 'var(--nd-border)', borderRadius: 4 }}><div style={{ height: 7, width: `${f.score}%`, background: fc, borderRadius: 4 }} /></div>
                  </div>
                );
              })}
              <div style={{ marginTop: 14, fontSize: 13, fontWeight: 700 }}>Issues &amp; fixes</div>
              {health.issues.map((s: string, i: number) => <div key={i} style={{ fontSize: 12, color: 'var(--nd-text-2)', padding: '3px 0' }}>• {s}</div>)}
              {health.actions.map((s: string, i: number) => <div key={i} style={{ fontSize: 12, color: 'var(--nd-green)', padding: '3px 0' }}>→ {s}</div>)}
            </div>
          </div>
        );
      })()}
    </div>
  );
};

export default HealthTab;
