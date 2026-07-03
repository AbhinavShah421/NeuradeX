import React from 'react';
import { Performance } from '../../types';

interface PerformanceTabProps {
  performance: Performance;
}

const PerformanceTab: React.FC<PerformanceTabProps> = ({ performance }) => {
  return (
    <div className="nd-card">
      <h2 className="nd-section-title">Performance Metrics</h2>
      <div className="nd-grid-4" style={{ gap: 12 }}>
        {[
          { label: 'Daily Return',   value: `${performance.dailyReturn > 0 ? '+' : ''}${performance.dailyReturn.toFixed(2)}%`,   color: performance.dailyReturn >= 0 ? 'var(--nd-green)' : 'var(--nd-red)' },
          { label: 'Monthly Return', value: `${performance.monthlyReturn > 0 ? '+' : ''}${performance.monthlyReturn.toFixed(2)}%`, color: performance.monthlyReturn >= 0 ? 'var(--nd-green)' : 'var(--nd-red)' },
          { label: 'Sharpe Ratio',   value: performance.sharpeRatio.toFixed(2),                                                   color: 'var(--nd-text-1)' },
          { label: 'Win Rate',       value: `${(performance.winRate * 100).toFixed(1)}%`,                                         color: 'var(--nd-text-1)' },
        ].map(m => (
          <div key={m.label} className="nd-metric">
            <p className="nd-metric-label">{m.label}</p>
            <p className="nd-metric-value" style={{ color: m.color }}>{m.value}</p>
          </div>
        ))}
      </div>
    </div>
  );
};

export default PerformanceTab;
