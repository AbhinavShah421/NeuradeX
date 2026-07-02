import React from 'react';
import { ModelRow, accuracyColor, accuracyBg } from './shared';

interface AgentLearningCardProps {
  learning: any;
  models: ModelRow[];
  modelBusy: string | null;
  modelError: string | null;
  setModelError: (err: string | null) => void;
  toggleModel: (m: ModelRow) => void;
  setWeightOverride: (m: ModelRow, weight: number | null) => void;
  sortedAgents: any[];
  onSelectAgent: (agent: any, rank: number) => void;
}

const AgentLearningCard: React.FC<AgentLearningCardProps> = ({
  learning, models, modelBusy, modelError, setModelError,
  toggleModel, setWeightOverride, sortedAgents, onSelectAgent,
}) => {
  if (!learning) return null;

  return (
    <div className="nd-pm-card" style={{ borderLeft: '3px solid var(--nd-green)' }}>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16, flexWrap: 'wrap' }}>
        <span className="material-icons" style={{ fontSize: 18, color: 'var(--nd-green)' }}>school</span>
        <h3 style={{ margin: 0, fontSize: 14, fontWeight: 700, color: 'var(--nd-text-1)' }}>Agent Learning</h3>
        {(learning.totals?.recentOutcomes24h ?? 0) > 0 && (
          <span style={{
            fontSize: 10, fontWeight: 600,
            color: 'var(--nd-green)',
            background: 'rgba(0,179,134,0.1)',
            border: '1px solid rgba(0,179,134,0.25)',
            borderRadius: 20, padding: '2px 10px', whiteSpace: 'nowrap',
          }}>
            ● {learning.totals.recentOutcomes24h} trained in last 24h
          </span>
        )}
      </div>

      {/* 4 counters — 2×2 on mobile, 4×1 on desktop */}
      <div className="nd-pm-stats-grid" style={{ marginBottom: 20 }}>
        {[
          { label: 'Predictions', value: (learning.totals?.predictions ?? 0).toLocaleString(), color: 'var(--nd-text-1)' },
          { label: 'Outcomes learned', value: (learning.totals?.outcomes ?? 0).toLocaleString(), color: 'var(--nd-text-1)' },
          { label: 'Overall accuracy',
            value: `${((learning.overallAccuracy ?? 0) * 100).toFixed(1)}%`,
            color: (learning.overallAccuracy ?? 0) >= 0.5 ? 'var(--nd-green)' : '#e74c3c' },
          { label: 'Memory cases', value: (learning.memoryCases ?? 0).toLocaleString(), color: 'var(--nd-text-1)' },
        ].map(s => (
          <div key={s.label} style={{
            background: 'var(--nd-bg)', border: '1px solid var(--nd-border)',
            borderRadius: 10, padding: '12px 14px',
          }}>
            <div style={{
              fontSize: 10, fontWeight: 600, textTransform: 'uppercase',
              letterSpacing: '0.4px', color: 'var(--nd-text-3)', marginBottom: 6,
            }}>{s.label}</div>
            <div style={{ fontSize: 20, fontWeight: 700, color: s.color }}>{s.value}</div>
          </div>
        ))}
      </div>

      {/* Per-agent ranked list — 2-row compact cards, no fixed widths */}
      {sortedAgents.length > 0 && (
        <>
          <div style={{
            fontSize: 10, fontWeight: 600, textTransform: 'uppercase',
            letterSpacing: '0.5px', color: 'var(--nd-text-3)', marginBottom: 10,
          }}>
            Agent Rankings — sorted by weight
          </div>
          {modelError && (
            <div style={{
              marginBottom: 8, padding: '8px 12px',
              background: 'rgba(231,76,60,0.1)', border: '1px solid rgba(231,76,60,0.3)',
              borderRadius: 8, fontSize: 12, color: '#e74c3c',
              display: 'flex', alignItems: 'center', gap: 8,
            }}>
              <span className="material-icons" style={{ fontSize: 14, flexShrink: 0 }}>error_outline</span>
              {modelError}
              <button onClick={() => setModelError(null)} style={{ marginLeft: 'auto', background: 'none', border: 'none', cursor: 'pointer', color: '#e74c3c', padding: 0 }}>
                <span className="material-icons" style={{ fontSize: 14 }}>close</span>
              </button>
            </div>
          )}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {sortedAgents.map((a: any, i: number) => {
              const acc = a.accuracy as number;
              const pct = Math.round(acc * 100);
              const col = accuracyColor(acc);
              const bgCol = accuracyBg(acc);
              const mdl = models.find(m => m.name === a.agent);
              const busy = modelBusy === a.agent;
              const effectiveWeight = mdl?.weight ?? a.weight;
              const hasPinnedWeight = mdl != null && mdl.weight != null;
              return (
                <div key={a.agent} style={{
                  background: mdl && !mdl.enabled ? 'rgba(100,116,139,0.06)' : 'var(--nd-bg)',
                  border: `1px solid ${mdl && !mdl.enabled ? '#475569' : 'var(--nd-border)'}`,
                  borderRadius: 10, overflow: 'hidden',
                  opacity: mdl && !mdl.enabled ? 0.7 : 1,
                  transition: 'opacity 0.2s',
                }}>
                  {/* ── Clickable top area: opens detail popup ── */}
                  <div onClick={() => onSelectAgent(a, i + 1)}
                    style={{ padding: '10px 12px', cursor: 'pointer' }}>
                    {/* Top row: rank + name + accuracy badge + weight badge + chevron */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                      <span style={{
                        fontSize: 10, fontWeight: 700, color: 'var(--nd-text-3)',
                        flexShrink: 0, width: 18, textAlign: 'center',
                      }}>#{i + 1}</span>
                      <span style={{
                        fontSize: 13, fontWeight: 600, color: 'var(--nd-text-1)',
                        flex: 1, minWidth: 0, textTransform: 'capitalize',
                        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                      }}>{a.agent.replace(/_/g, ' ')}</span>
                      <span style={{
                        fontSize: 12, fontWeight: 700, color: col,
                        background: bgCol, borderRadius: 6, padding: '2px 8px',
                        flexShrink: 0, minWidth: 42, textAlign: 'center',
                      }}>{pct}%</span>
                      <span style={{
                        fontSize: 10, fontWeight: 600,
                        color: hasPinnedWeight ? 'var(--nd-orange, #f59e0b)' : 'var(--nd-text-2)',
                        background: 'var(--nd-surface)',
                        border: `1px solid ${hasPinnedWeight ? 'var(--nd-orange, #f59e0b)' : 'var(--nd-border)'}`,
                        borderRadius: 6, padding: '2px 8px',
                        flexShrink: 0, minWidth: 48, textAlign: 'center',
                      }} title={hasPinnedWeight ? `Manual override — auto would be w${a.weightLearned ?? '—'}` : 'Learned weight'}>
                        w{effectiveWeight}
                      </span>
                      <span className="material-icons" style={{ fontSize: 14, color: 'var(--nd-text-3)', flexShrink: 0 }}>chevron_right</span>
                    </div>
                    {/* Accuracy bar */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <div style={{ flex: 1, height: 5, background: 'var(--nd-border)', borderRadius: 3, overflow: 'hidden' }}>
                        <div style={{ width: `${pct}%`, height: '100%', background: col, borderRadius: 3, transition: 'width 0.4s ease' }} />
                      </div>
                      <span style={{ fontSize: 10, color: 'var(--nd-text-3)', flexShrink: 0, whiteSpace: 'nowrap' }}>
                        {a.correct}/{a.total}
                      </span>
                    </div>
                  </div>

                  {/* ── Controls row: isolated from card click ── */}
                  {mdl && (
                    <div style={{
                      display: 'flex', alignItems: 'center', gap: 8,
                      padding: '8px 12px', borderTop: '1px solid var(--nd-border)',
                      background: 'var(--nd-surface)',
                    }}>
                      {/* Enable / disable toggle */}
                      <button
                        onClick={() => toggleModel(mdl)}
                        disabled={busy}
                        style={{
                          background: mdl.enabled ? 'rgba(0,179,134,0.15)' : 'rgba(100,116,139,0.15)',
                          border: `1px solid ${mdl.enabled ? 'rgba(0,179,134,0.5)' : '#475569'}`,
                          borderRadius: 7, padding: '5px 12px',
                          fontSize: 11, fontWeight: 700,
                          color: mdl.enabled ? 'var(--nd-green)' : '#94a3b8',
                          cursor: busy ? 'not-allowed' : 'pointer',
                          display: 'flex', alignItems: 'center', gap: 4, flexShrink: 0,
                          transition: 'all 0.15s',
                        }}
                      >
                        <span className="material-icons" style={{ fontSize: 14 }}>
                          {mdl.enabled ? 'toggle_on' : 'toggle_off'}
                        </span>
                        {mdl.enabled ? 'On' : 'Off'}
                      </button>
                      {/* Weight number input — no max ceiling */}
                      <span style={{ fontSize: 11, color: 'var(--nd-text-3)', flexShrink: 0 }}>Weight</span>
                      <input
                        type="number"
                        min={0} step={0.1}
                        value={mdl.weight ?? a.weight}
                        onChange={e => {
                          const v = parseFloat(e.target.value);
                          if (!isNaN(v) && v >= 0) setWeightOverride(mdl, v);
                        }}
                        style={{
                          width: 64, padding: '4px 8px',
                          background: 'var(--nd-bg)', border: '1px solid var(--nd-border)',
                          borderRadius: 7, fontSize: 12, fontWeight: 600,
                          color: 'var(--nd-text-1)', outline: 'none',
                        }}
                      />
                      {mdl.weight !== null && (
                        <button
                          onClick={() => setWeightOverride(mdl, null)}
                          disabled={busy}
                          title="Clear override — revert to learned weight"
                          style={{
                            background: 'none', border: '1px solid var(--nd-border)',
                            borderRadius: 7, padding: '4px 8px',
                            fontSize: 11, color: 'var(--nd-text-3)',
                            cursor: busy ? 'not-allowed' : 'pointer',
                          }}
                        >
                          Auto
                        </button>
                      )}
                      {busy && (
                        <span className="material-icons nd-spin" style={{ fontSize: 14, color: 'var(--nd-text-3)', marginLeft: 'auto' }}>
                          autorenew
                        </span>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </>
      )}

      <p style={{ margin: '14px 0 0', fontSize: 12, color: 'var(--nd-text-3)', lineHeight: 1.65 }}>
        Every{' '}
        <strong style={{ color: 'var(--nd-text-2)' }}>backtest</strong>,{' '}
        <strong style={{ color: 'var(--nd-text-2)' }}>paper trade</strong> and{' '}
        <strong style={{ color: 'var(--nd-text-2)' }}>live session</strong>{' '}
        updates these weights, the RL policy, and the memory bank.
      </p>
    </div>
  );
};

export default AgentLearningCard;
