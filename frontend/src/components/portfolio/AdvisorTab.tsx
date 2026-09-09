import React from 'react';
import { pct, pctColor } from './shared';

interface AdvisorTabProps {
  bench: any;
  advisor: any;
}

const AdvisorTab: React.FC<AdvisorTabProps> = ({ bench, advisor }) => {
  return (
    <div>
      {/* Benchmark vs NIFTY */}
      <div className="nd-card" style={{ padding: '14px 18px', marginBottom: 14 }}>
        <div className="nd-section-title" style={{ marginBottom: 10 }}>Portfolio vs NIFTY 50</div>
        {!bench ? <div style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>Comparing to benchmark…</div>
          : bench.note ? <div style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>{bench.note}</div>
          : (
            <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap' }}>
              {(bench.periods ?? []).map((p: any) => (
                <div key={p.key} className="nd-metric" style={{ minWidth: 140, flex: '1 1 140px', ['--tone' as any]: pctColor(p.alpha) }}>
                  <p className="nd-metric-label">{p.label}</p>
                  <p className="nd-metric-value" style={{ fontSize: 17, color: pctColor(p.portfolio) }}>{pct(p.portfolio)}</p>
                  {/* The benchmark and the alpha are the comparison this tile
                      exists for, so they stay attached to the value rather than
                      being pushed into a legend somewhere else. */}
                  <p className="nd-metric-sub">NIFTY {pct(p.benchmark)}</p>
                  <p className="nd-metric-sub" style={{ color: pctColor(p.alpha), fontWeight: 700 }}>
                    {p.alpha >= 0 ? 'α +' : 'α '}{p.alpha != null ? `${p.alpha}%` : '—'}
                  </p>
                </div>
              ))}
            </div>
          )}
      </div>
      {/* AI insights feed */}
      <div className="nd-card" style={{ padding: '14px 18px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 8 }}>
          <div className="nd-section-title" style={{ margin: 0 }}>AI advisor insights</div>
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
