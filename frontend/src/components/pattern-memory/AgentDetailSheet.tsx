import React, { useState } from 'react';
import ActionTrendChart from './ActionTrendChart';
import { AGENT_META, accuracyColor, accuracyBg, LearningAgent, AgentActionStat } from './shared';

// ── Agent detail bottom-sheet (proper component so it can hold state) ─────────

const AgentDetailSheet: React.FC<{ agent: LearningAgent; rank: number; onClose: () => void }> = ({ agent: a, rank, onClose }) => {
  const [expandedAction, setExpandedAction] = useState<string | null>(null);

  const acc   = a.accuracy;
  const pct   = Math.round(acc * 100);
  const col   = accuracyColor(acc);
  const bgCol = accuracyBg(acc);
  const wrong = (a.total ?? 0) - (a.correct ?? 0);
  const meta  = AGENT_META[a.agent?.toLowerCase()] ?? {
    icon: 'smart_toy', label: a.agent, sources: [], signals: [],
    definition: 'AI agent contributing to the ensemble consensus.',
    edge: '',
  };

  const ACTION_COLOR: Record<string, string> = { BUY: '#22c55e', SELL: '#ef4444', HOLD: '#f59e0b' };
  const ACTION_BG:    Record<string, string> = { BUY: 'rgba(34,197,94,0.08)', SELL: 'rgba(239,68,68,0.08)', HOLD: 'rgba(245,158,11,0.08)' };

  const byAction: AgentActionStat[] = a.byAction ?? a.by_action ?? [];
  const sortedActions   = [...byAction].sort((x, y) => ['BUY','SELL','HOLD'].indexOf(x.action) - ['BUY','SELL','HOLD'].indexOf(y.action));
  const totalVotes      = sortedActions.reduce((s: number, x) => s + (x.total || 0), 0);

  return (
    <>
      {/* Backdrop */}
      <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(0,0,0,0.75)', touchAction: 'none' }} />

      {/* Sheet */}
      <div
        onClick={e => e.stopPropagation()}
        style={{
          position: 'fixed', bottom: 0, left: '50%', transform: 'translateX(-50%)',
          zIndex: 1001, width: '100%', maxWidth: 540,
          background: 'var(--nd-surface)', borderRadius: '20px 20px 0 0',
          border: '1px solid var(--nd-border)', borderBottom: 'none',
          paddingBottom: 'calc(20px + env(safe-area-inset-bottom, 0px))',
          maxHeight: '88vh', overflowY: 'auto',
          WebkitOverflowScrolling: 'touch' as any, touchAction: 'pan-y',
        }}
      >
        {/* Drag handle */}
        <div style={{ position: 'sticky', top: 0, zIndex: 2, background: 'var(--nd-surface)', display: 'flex', justifyContent: 'center', padding: '10px 0 6px' }}>
          <div style={{ width: 36, height: 4, borderRadius: 2, background: 'var(--nd-border)' }} />
        </div>

        {/* Header */}
        <div style={{ position: 'sticky', top: 30, zIndex: 2, background: 'var(--nd-surface)', display: 'flex', alignItems: 'center', gap: 12, padding: '10px 16px 14px', borderBottom: '1px solid var(--nd-border)' }}>
          <div style={{ width: 44, height: 44, borderRadius: 12, flexShrink: 0, background: bgCol, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <span className="material-icons" style={{ color: col, fontSize: 22 }}>{meta.icon}</span>
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 3 }}>
              <span style={{ fontSize: 15, fontWeight: 700, color: 'var(--nd-text-1)', textTransform: 'capitalize', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{a.agent}</span>
              <span style={{ fontSize: 10, fontWeight: 700, color: 'var(--nd-text-3)', background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderRadius: 5, padding: '1px 6px', flexShrink: 0 }}>#{rank}</span>
            </div>
            <span style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>{meta.label}</span>
          </div>
          <button onClick={onClose} style={{ background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderRadius: 8, width: 32, height: 32, cursor: 'pointer', flexShrink: 0, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <span className="material-icons" style={{ fontSize: 16, color: 'var(--nd-text-2)' }}>close</span>
          </button>
        </div>

        {/* Scrollable body */}
        <div style={{ padding: '16px 16px 0', display: 'flex', flexDirection: 'column', gap: 12 }}>

          {/* Accuracy hero */}
          <div style={{ background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderRadius: 14, padding: '14px 16px' }}>
            <div style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.5px', color: 'var(--nd-text-3)', marginBottom: 6 }}>Live Accuracy</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 10 }}>
              <span style={{ fontSize: 38, fontWeight: 800, color: col, lineHeight: 1 }}>{pct}%</span>
              <span style={{ fontSize: 12, color: 'var(--nd-text-3)', lineHeight: 1.6 }}>{a.correct} correct<br />{a.total} predictions</span>
            </div>
            <div style={{ height: 8, background: 'var(--nd-border)', borderRadius: 4, overflow: 'hidden' }}>
              <div style={{ width: `${pct}%`, height: '100%', background: col, borderRadius: 4 }} />
            </div>
          </div>

          {/* 2×2 stats */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
            {[
              { label: 'Weight',  value: String(a.weight),       icon: 'balance',      iconCol: 'var(--nd-text-2)' },
              { label: 'Rank',    value: `#${rank}`,              icon: 'leaderboard',  iconCol: 'var(--nd-text-2)' },
              { label: 'Correct', value: String(a.correct ?? 0), icon: 'check_circle', iconCol: 'var(--nd-green)'  },
              { label: 'Wrong',   value: String(wrong),           icon: 'cancel',       iconCol: '#e74c3c'          },
            ].map(s => (
              <div key={s.label} style={{ background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderRadius: 10, padding: '12px 14px', display: 'flex', alignItems: 'center', gap: 10 }}>
                <span className="material-icons" style={{ fontSize: 22, color: s.iconCol, flexShrink: 0 }}>{s.icon}</span>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--nd-text-1)', lineHeight: 1, marginBottom: 4 }}>{s.value}</div>
                  <div style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.4px', color: 'var(--nd-text-3)' }}>{s.label}</div>
                </div>
              </div>
            ))}
          </div>

          {/* ── BUY / SELL contribution ── */}
          {sortedActions.length > 0 && (
            <div style={{ background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderRadius: 12, padding: '14px 16px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 12 }}>
                <span className="material-icons" style={{ fontSize: 14, color: '#a855f7' }}>pie_chart</span>
                <span style={{ fontSize: 11, fontWeight: 700, color: '#a855f7', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                  Vote Contribution to Training
                </span>
              </div>

              {/* Stacked proportion bar */}
              {totalVotes > 0 && (
                <div style={{ display: 'flex', height: 8, borderRadius: 4, overflow: 'hidden', marginBottom: 12, gap: 1 }}>
                  {sortedActions.map((x) => (
                    <div key={x.action} title={`${x.action}: ${x.total} (${((x.total / totalVotes) * 100).toFixed(0)}%)`}
                      style={{ flex: x.total, background: ACTION_COLOR[x.action] ?? '#64748b', minWidth: x.total > 0 ? 2 : 0 }} />
                  ))}
                </div>
              )}

              {/* Per-action cards — clickable for BUY/SELL */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {sortedActions.map((x) => {
                  const sharePct  = totalVotes > 0 ? ((x.total / totalVotes) * 100).toFixed(0) : '0';
                  const correct   = x.correct ?? 0;
                  const wrongCnt  = x.total - correct;
                  const ratePct   = x.total > 0 && x.rate != null ? Math.round(x.rate * 100) : null;
                  const avgPnl    = x.avgPnl ?? x.avg_pnl;
                  const acCol     = ACTION_COLOR[x.action] ?? '#94a3b8';
                  const acBg      = ACTION_BG[x.action]    ?? 'var(--nd-surface)';
                  const isExpanded = expandedAction === x.action;
                  const canExpand  = x.action !== 'HOLD';
                  return (
                    <div key={x.action}
                      onClick={() => canExpand && setExpandedAction(isExpanded ? null : x.action)}
                      style={{ background: acBg, border: `1px solid ${acCol}${isExpanded ? '60' : '30'}`, borderRadius: 10, padding: '10px 14px', cursor: canExpand ? 'pointer' : 'default', transition: 'border-color 0.15s' }}
                    >
                      {/* Row 1: badge + total + share + avg pnl + chevron */}
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                        <span style={{ fontSize: 11, fontWeight: 800, padding: '2px 8px', borderRadius: 5, background: `${acCol}20`, color: acCol, border: `1px solid ${acCol}40` }}>{x.action}</span>
                        <span style={{ fontSize: 14, fontWeight: 700, color: 'var(--nd-text-1)' }}>{x.total.toLocaleString()}</span>
                        <span style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>decisions ({sharePct}%)</span>
                        {avgPnl != null && x.action !== 'HOLD' && (
                          <span style={{ marginLeft: 'auto', fontSize: 12, fontWeight: 600, color: avgPnl >= 0 ? '#22c55e' : '#ef4444' }}>
                            {avgPnl >= 0 ? '+' : ''}{avgPnl}% avg P&L
                          </span>
                        )}
                        {canExpand && (
                          <span className="material-icons" style={{ fontSize: 16, color: acCol, flexShrink: 0, marginLeft: avgPnl != null ? 4 : 'auto' }}>
                            {isExpanded ? 'expand_less' : 'show_chart'}
                          </span>
                        )}
                      </div>

                      {/* Row 2: correct / wrong / accuracy */}
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                          <span className="material-icons" style={{ fontSize: 13, color: '#22c55e' }}>check_circle</span>
                          <span style={{ fontSize: 13, fontWeight: 700, color: '#22c55e' }}>{correct.toLocaleString()}</span>
                          <span style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>correct</span>
                        </div>
                        <span style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>·</span>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                          <span className="material-icons" style={{ fontSize: 13, color: '#ef4444' }}>cancel</span>
                          <span style={{ fontSize: 13, fontWeight: 700, color: '#ef4444' }}>{wrongCnt.toLocaleString()}</span>
                          <span style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>wrong</span>
                        </div>
                        {ratePct != null && (
                          <div style={{ marginLeft: 'auto', display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 3 }}>
                            <span style={{ fontSize: 16, fontWeight: 800, color: ratePct >= 50 ? '#22c55e' : '#ef4444', lineHeight: 1 }}>{ratePct}%</span>
                            <div style={{ width: 72, height: 4, background: 'var(--nd-border)', borderRadius: 2 }}>
                              <div style={{ height: '100%', width: `${ratePct}%`, background: ratePct >= 50 ? '#22c55e' : '#ef4444', borderRadius: 2 }} />
                            </div>
                          </div>
                        )}
                      </div>

                      {/* Expanded trend chart */}
                      {isExpanded && (
                        <div style={{ marginTop: 14, paddingTop: 14, borderTop: `1px solid ${acCol}30` }}
                          onClick={e => e.stopPropagation()}>
                          <div style={{ fontSize: 11, fontWeight: 700, color: acCol, marginBottom: 10 }}>
                            Accuracy over time — {x.action} decisions
                          </div>
                          <ActionTrendChart agentName={a.agent} action={x.action} color={acCol} />
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
              <div style={{ fontSize: 10, color: 'var(--nd-text-3)', marginTop: 8, lineHeight: 1.5 }}>
                Tap BUY or SELL to see the accuracy trend over time. "Correct" = vote direction matched the actual trade result.
              </div>
            </div>
          )}

          {/* ── Definition ── */}
                <div style={{
                  background: 'rgba(0,179,134,0.05)',
                  border: '1px solid rgba(0,179,134,0.18)',
                  borderRadius: 12, padding: '14px 16px',
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 10 }}>
                    <span className="material-icons" style={{ fontSize: 14, color: 'var(--nd-green)' }}>auto_stories</span>
                    <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--nd-green)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                      What it is
                    </span>
                  </div>
                  <p style={{ margin: 0, fontSize: 13, color: 'var(--nd-text-1)', lineHeight: 1.7 }}>{meta.definition}</p>
                </div>

                {/* ── Data Sources ── */}
                <div style={{
                  background: 'var(--nd-bg)', border: '1px solid var(--nd-border)',
                  borderRadius: 12, padding: '14px 16px',
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 10 }}>
                    <span className="material-icons" style={{ fontSize: 14, color: 'var(--nd-text-2)' }}>database</span>
                    <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--nd-text-2)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                      Data Sources
                    </span>
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {meta.sources.map((src, i) => (
                      <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
                        <span className="material-icons" style={{ fontSize: 13, color: 'var(--nd-text-3)', marginTop: 2, flexShrink: 0 }}>fiber_manual_record</span>
                        <span style={{ fontSize: 12, color: 'var(--nd-text-2)', lineHeight: 1.5 }}>{src}</span>
                      </div>
                    ))}
                  </div>
                </div>

                {/* ── Signals it reads ── */}
                <div style={{
                  background: 'var(--nd-bg)', border: '1px solid var(--nd-border)',
                  borderRadius: 12, padding: '14px 16px',
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 10 }}>
                    <span className="material-icons" style={{ fontSize: 14, color: 'var(--nd-text-2)' }}>sensors</span>
                    <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--nd-text-2)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                      Signals &amp; Indicators
                    </span>
                  </div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                    {meta.signals.map((sig, i) => (
                      <span key={i} style={{
                        fontSize: 11, color: 'var(--nd-text-1)',
                        background: 'var(--nd-surface)', border: '1px solid var(--nd-border)',
                        borderRadius: 6, padding: '4px 10px', lineHeight: 1.4,
                      }}>{sig}</span>
                    ))}
                  </div>
                </div>

                {/* ── Best used for ── */}
                <div style={{
                  background: 'var(--nd-bg)', border: '1px solid var(--nd-border)',
                  borderRadius: 12, padding: '14px 16px',
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
                    <span className="material-icons" style={{ fontSize: 14, color: '#f59e0b' }}>lightbulb</span>
                    <span style={{ fontSize: 11, fontWeight: 700, color: '#f59e0b', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                      When it shines
                    </span>
                  </div>
                  <p style={{ margin: 0, fontSize: 13, color: 'var(--nd-text-2)', lineHeight: 1.65 }}>{meta.edge}</p>
                </div>

                {/* ── Ensemble influence ── */}
                <div style={{
                  background: 'var(--nd-bg)', border: '1px solid var(--nd-border)',
                  borderRadius: 12, padding: '14px 16px',
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <span className="material-icons" style={{ fontSize: 14, color: 'var(--nd-text-3)' }}>workspaces</span>
                      <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--nd-text-3)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                        Ensemble influence
                      </span>
                    </div>
                    <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--nd-text-1)' }}>w{a.weight}</span>
                  </div>
                  <div style={{ height: 8, background: 'var(--nd-border)', borderRadius: 4, overflow: 'hidden', marginBottom: 8 }}>
                    <div style={{
                      width: `${Math.min(100, (a.weight / 5) * 100)}%`,
                      height: '100%', background: 'var(--nd-green)', borderRadius: 4,
                    }} />
                  </div>
                  <p style={{ margin: 0, fontSize: 11, color: 'var(--nd-text-3)', lineHeight: 1.6 }}>
                    Weight is updated after every completed session. Higher accuracy → higher weight → more influence on the final trade decision.
                  </p>
                </div>

              </div>
            </div>
    </>
  );
};

export default AgentDetailSheet;
