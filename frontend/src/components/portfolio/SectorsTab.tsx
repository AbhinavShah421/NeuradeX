import React from 'react';

interface SectorsTabProps {
  sectorData: any;
}

const SectorsTab: React.FC<SectorsTabProps> = ({ sectorData }) => {
  return (
    <div style={{ padding: '18px 20px' }}>
      {!sectorData ? (
        <div className="nd-card" style={{ textAlign: 'center', padding: 40, color: 'var(--nd-text-3)', fontSize: 13 }}>Scanning sector exposure…</div>
      ) : (
        <>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 14 }}>
            {[
              { label: 'Top sector', value: `${sectorData.topSector} · ${sectorData.topSectorPct}%`, color: sectorData.topSectorPct > 40 ? 'var(--nd-red)' : 'var(--nd-text-1)' },
              { label: 'Effective sectors', value: sectorData.effectiveSectors, color: 'var(--nd-text-1)' },
              { label: 'AI-favoured', value: (sectorData.aiFavoured ?? []).slice(0, 3).map((a: any) => a.sector).join(', ') || '—', color: 'var(--nd-green)' },
            ].map((c, i) => (
              <div key={i} className="nd-card" style={{ flex: '1 1 200px', padding: '12px 16px' }}>
                <div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>{c.label}</div>
                <div style={{ fontSize: 15, fontWeight: 700, color: c.color }}>{c.value}</div>
              </div>
            ))}
          </div>

          {(sectorData.warnings ?? []).map((w: string, i: number) => (
            <div key={i} style={{ fontSize: 12, color: '#fca5a5', background: '#ef444415', border: '1px solid #ef444433', borderRadius: 8, padding: '8px 11px', marginBottom: 10 }}>⚠ {w}</div>
          ))}

          {/* Donut of current sector allocation */}
          {(() => {
            const PALETTE = ['#22c55e', '#3b82f6', '#a855f7', '#f59e0b', '#ef4444', '#06b6d4', '#ec4899', '#84cc16', '#94a3b8'];
            const entries = Object.entries(sectorData.current || {}) as [string, number][];
            if (!entries.length) return null;
            const top = entries.slice(0, 8);
            const restPct = entries.slice(8).reduce((s, [, v]) => s + (v as number), 0);
            const segs = restPct > 0.1 ? [...top, ['Other', restPct] as [string, number]] : top;
            const R = 52, C = 2 * Math.PI * R;
            let off = 0;
            return (
              <div className="nd-card" style={{ padding: '14px 18px', marginBottom: 14, display: 'flex', gap: 22, alignItems: 'center', flexWrap: 'wrap' }}>
                <svg width={140} height={140} viewBox="0 0 140 140" style={{ flexShrink: 0 }}>
                  <g transform="rotate(-90 70 70)">
                    {segs.map(([sec, pct], i) => {
                      const len = (pct / 100) * C;
                      const el = (
                        <circle key={sec} cx={70} cy={70} r={R} fill="none"
                          stroke={PALETTE[i % PALETTE.length]} strokeWidth={16}
                          strokeDasharray={`${len} ${C - len}`} strokeDashoffset={-off} />
                      );
                      off += len; return el;
                    })}
                  </g>
                  <text x={70} y={66} textAnchor="middle" fontSize="11" fill="var(--nd-text-3)">sectors</text>
                  <text x={70} y={82} textAnchor="middle" fontSize="15" fontWeight="700" fill="var(--nd-text-1)">{entries.length}</text>
                </svg>
                <div style={{ flex: 1, minWidth: 180, display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: '4px 14px' }}>
                  {segs.map(([sec, pct], i) => (
                    <div key={sec} style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 12 }}>
                      <span style={{ width: 10, height: 10, borderRadius: 2, background: PALETTE[i % PALETTE.length], flexShrink: 0 }} />
                      <span style={{ color: 'var(--nd-text-2)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{sec}</span>
                      <span style={{ color: 'var(--nd-text-1)', fontWeight: 600 }}>{(pct as number).toFixed(0)}%</span>
                    </div>
                  ))}
                </div>
              </div>
            );
          })()}

          <div className="nd-card" style={{ padding: '14px 18px', marginBottom: 14 }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--nd-text-1)', marginBottom: 10 }}>Your sectors vs the AI-favoured target</div>
            {(sectorData.sectors ?? []).map((r: any) => {
              const col = r.status === 'overweight' ? '#ef4444' : r.status === 'underweight' ? '#f59e0b' : '#22c55e';
              return (
                <div key={r.sector} style={{ marginBottom: 11 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12.5, marginBottom: 3 }}>
                    <span style={{ fontWeight: 600, color: 'var(--nd-text-1)' }}>{r.sector} <span style={{ color: 'var(--nd-text-3)', fontWeight: 400 }}>({r.holdingCount})</span></span>
                    <span style={{ color: 'var(--nd-text-2)' }}>now {r.currentPct}% · target {r.targetPct}% <span style={{ color: col, fontWeight: 700 }}>{r.status}</span></span>
                  </div>
                  <div style={{ position: 'relative', height: 8, background: 'var(--nd-border)', borderRadius: 4 }}>
                    <div style={{ position: 'absolute', height: 8, borderRadius: 4, width: `${Math.min(100, r.currentPct)}%`, background: col, opacity: 0.85 }} />
                    <div style={{ position: 'absolute', height: 8, width: 2, background: 'var(--nd-text-1)', left: `${Math.min(100, r.targetPct)}%` }} title={`AI target ${r.targetPct}%`} />
                  </div>
                </div>
              );
            })}
          </div>

          {(sectorData.suggestions ?? []).length > 0 && (
            <div className="nd-card" style={{ padding: '14px 18px' }}>
              <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--nd-text-1)', marginBottom: 10 }}>AI rebalance moves</div>
              {sectorData.suggestions.map((s: any, i: number) => (
                <div key={i} style={{ display: 'flex', alignItems: 'baseline', gap: 8, padding: '7px 0', borderBottom: '1px solid var(--nd-border)', fontSize: 12.5 }}>
                  <span style={{ fontSize: 10, fontWeight: 700, color: s.action === 'ADD' ? '#22c55e' : '#ef4444', minWidth: 38 }}>{s.action}</span>
                  <span style={{ fontWeight: 700, color: 'var(--nd-text-1)', minWidth: 120 }}>{s.sector}</span>
                  <span style={{ color: 'var(--nd-text-2)' }}>{s.reason}{s.stock ? ` → ${s.action === 'ADD' ? 'buy' : 'trim'} ${s.stock}` : ''}</span>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
};

export default SectorsTab;
