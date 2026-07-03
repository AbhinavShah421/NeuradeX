import React from 'react';
import { MemStats, SOURCE_META } from './shared';

interface BreakdownPanelProps {
  stats: MemStats | null;
}

const BreakdownPanel: React.FC<BreakdownPanelProps> = ({ stats }) => {
  return (
    <div className="nd-pm-breakdown" style={{ gap: 20 }}>

      {/* By Action */}
      <div className="nd-pm-card">
        <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 14 }}>
          <span className="material-icons" style={{ fontSize: 15, color: 'var(--nd-text-3)' }}>swap_vert</span>
          <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: 'var(--nd-text-1)' }}>By Action</h3>
        </div>
        {!stats || !stats.byAction.length ? (
          <p style={{ margin: 0, fontSize: 13, color: 'var(--nd-text-3)' }}>No cases yet — seed the bank to begin.</p>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {stats.byAction.map(a => {
              const wr = Math.round(a.winRate * 100);
              const col = a.action === 'BUY' ? 'var(--nd-green)'
                         : a.action === 'SELL' ? '#e74c3c'
                         : 'var(--nd-text-3)';
              const colAlpha = a.action === 'BUY' ? 'rgba(0,179,134,0.12)' : 'rgba(231,76,60,0.12)';
              return (
                <div key={a.action} style={{
                  background: 'var(--nd-bg)', border: '1px solid var(--nd-border)',
                  borderRadius: 10, padding: '12px 14px',
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span style={{
                        fontSize: 11, fontWeight: 700, color: col,
                        background: colAlpha, borderRadius: 5,
                        padding: '2px 8px', letterSpacing: '0.5px',
                      }}>{a.action}</span>
                      <span style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>{a.count.toLocaleString()} cases</span>
                    </div>
                    <span style={{ fontSize: 18, fontWeight: 700, color: wr >= 50 ? 'var(--nd-green)' : 'var(--nd-text-2)' }}>
                      {wr}%
                    </span>
                  </div>
                  <div style={{ height: 6, background: 'var(--nd-border)', borderRadius: 4, overflow: 'hidden', marginBottom: 6 }}>
                    <div style={{ width: `${wr}%`, height: '100%', borderRadius: 4, background: wr >= 50 ? 'var(--nd-green)' : 'var(--nd-text-3)' }} />
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <span style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>Win rate</span>
                    <span style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>{100 - wr}% loss</span>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* By Source + Top Symbols stacked */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>

        {/* By Source */}
        <div className="nd-pm-card">
          <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 14 }}>
            <span className="material-icons" style={{ fontSize: 15, color: 'var(--nd-text-3)' }}>account_tree</span>
            <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: 'var(--nd-text-1)' }}>By Source</h3>
          </div>
          {!stats || !stats.bySource.length ? (
            <p style={{ margin: 0, fontSize: 13, color: 'var(--nd-text-3)' }}>No cases yet.</p>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {stats.bySource.map(s => {
                const meta = SOURCE_META[s.source] ?? { icon: 'storage' };
                const wr = Math.round(s.winRate * 100);
                return (
                  <div key={s.source} style={{
                    display: 'flex', alignItems: 'center', gap: 10,
                    background: 'var(--nd-bg)', border: '1px solid var(--nd-border)',
                    borderRadius: 10, padding: '10px 12px',
                  }}>
                    <div style={{
                      width: 32, height: 32, borderRadius: 8, flexShrink: 0,
                      background: 'var(--nd-surface)',
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                    }}>
                      <span className="material-icons" style={{ fontSize: 15, color: 'var(--nd-text-2)' }}>{meta.icon}</span>
                    </div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 5 }}>
                        <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--nd-text-1)', textTransform: 'capitalize' }}>
                          {s.source.charAt(0) + s.source.slice(1).toLowerCase()}
                        </span>
                        <span style={{ fontSize: 12, fontWeight: 700, color: wr >= 50 ? 'var(--nd-green)' : 'var(--nd-text-2)' }}>
                          {wr}% win
                        </span>
                      </div>
                      <div style={{ height: 4, background: 'var(--nd-border)', borderRadius: 3, overflow: 'hidden', marginBottom: 4 }}>
                        <div style={{ width: `${wr}%`, height: '100%', borderRadius: 3, background: wr >= 50 ? 'var(--nd-green)' : 'var(--nd-text-3)' }} />
                      </div>
                      <span style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>{s.count.toLocaleString()} cases</span>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Top Symbols */}
        <div className="nd-pm-card">
          <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 14 }}>
            <span className="material-icons" style={{ fontSize: 15, color: 'var(--nd-text-3)' }}>bar_chart</span>
            <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: 'var(--nd-text-1)' }}>Top Symbols</h3>
          </div>
          {!stats || !stats.topSymbols.length ? (
            <p style={{ margin: 0, fontSize: 13, color: 'var(--nd-text-3)' }}>—</p>
          ) : (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {stats.topSymbols.map((s, i) => (
                <div key={s.symbol} style={{
                  display: 'flex', alignItems: 'center', gap: 5,
                  background: i < 3 ? 'rgba(0,179,134,0.08)' : 'var(--nd-bg)',
                  border: `1px solid ${i < 3 ? 'rgba(0,179,134,0.22)' : 'var(--nd-border)'}`,
                  borderRadius: 7, padding: '4px 9px',
                }}>
                  <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--nd-text-1)' }}>{s.symbol}</span>
                  <span style={{
                    fontSize: 10, fontWeight: 700,
                    color: i < 3 ? 'var(--nd-green)' : 'var(--nd-text-3)',
                    background: i < 3 ? 'rgba(0,179,134,0.15)' : 'var(--nd-surface)',
                    borderRadius: 4, padding: '1px 5px',
                  }}>{s.count}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default BreakdownPanel;
