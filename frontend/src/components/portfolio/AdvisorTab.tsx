import React from 'react';
import { pct, pctColor } from './shared';

interface AdvisorTabProps {
  bench: any;
  advisor: any;
}

const AdvisorTab: React.FC<AdvisorTabProps> = ({ bench, advisor }) => {
  return (
    <div style={{ padding: '18px 20px' }}>
      {/* Benchmark vs NIFTY */}
      <div className="nd-card" style={{ padding: '14px 18px', marginBottom: 14 }}>
        <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 10 }}>Portfolio vs NIFTY 50</div>
        {!bench ? <div style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>Comparing to benchmark…</div>
          : bench.note ? <div style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>{bench.note}</div>
          : (
            <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap' }}>
              {(bench.periods ?? []).map((p: any) => (
                <div key={p.key} style={{ minWidth: 120 }}>
                  <div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>{p.label}</div>
                  <div style={{ fontSize: 14 }}>You <strong style={{ color: pctColor(p.portfolio) }}>{pct(p.portfolio)}</strong></div>
                  <div style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>NIFTY {pct(p.benchmark)}</div>
                  <div style={{ fontSize: 12, fontWeight: 700, color: pctColor(p.alpha) }}>{p.alpha >= 0 ? 'α +' : 'α '}{p.alpha != null ? `${p.alpha}%` : '—'}</div>
                </div>
              ))}
            </div>
          )}
      </div>
      {/* AI insights feed */}
      <div className="nd-card" style={{ padding: '14px 18px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 8 }}>
          <div style={{ fontSize: 13, fontWeight: 700 }}>AI advisor insights</div>
          {advisor?.source && <span style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>{advisor.source === 'llm' ? 'AI-generated' : 'rule-based'}{advisor.score != null ? ` · health ${advisor.score}/100` : ''}</span>}
        </div>
        {!advisor ? <div style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>Analysing your portfolio…</div>
          : (advisor.insights ?? []).map((ins: string, i: number) => (
            <div key={i} style={{ display: 'flex', gap: 8, padding: '7px 0', borderBottom: '1px solid var(--nd-border)', fontSize: 12.5 }}>
              <span style={{ color: 'var(--nd-green)' }}>▸</span><span style={{ color: 'var(--nd-text-2)' }}>{ins}</span>
            </div>
          ))}
      </div>
    </div>
  );
};

export default AdvisorTab;
