import React, { useEffect, useState, useCallback } from 'react';
import apiService from '../../services/api';
import TradingChart from '../TradingChart';
import { useAppStore } from '../../stores/appStore';
import { inr } from '../../utils/format';

// ── Live auto-trading sessions (open positions + intraday P&L) ─────────────────

// ── Live session detail modal (what's happening inside a running session) ──────

const SESS_ACTION_COLOR: Record<string, string> = { BUY: '#22c55e', SELL: '#ef4444', HOLD: '#f59e0b' };
const SESS_AGENT_COLOR: Record<string, string> = {
  technical: '#3b82f6', sentiment: '#06b6d4', macro: '#f59e0b', pattern: '#8b5cf6', rl: '#10b981',
  gbm: '#14b8a6', regime: '#a855f7', anomaly: '#ec4899', momentum: '#eab308', memory: '#64748b',
  meanrev: '#f97316', volatility: '#ef4444', day_structure: '#0ea5e9',
};

// ── Agent detail popup ────────────────────────────────────────────────────────

const AGENT_DESCRIPTIONS: Record<string, string> = {
  technical:  'RSI, VWAP, SMA, ATR — classic price/momentum indicators.',
  sentiment:  'News LLM + FinBERT — real-time catalyst and sentiment scoring.',
  macro:      'Market-regime, sector strength and macro-level breadth filters.',
  pattern:    'Trained neural-network pattern recognition across the NSE universe.',
  rl:         'Reinforcement-learning agent trained on live trade outcomes.',
  gbm:        'Gradient-boosted tree ensemble (tabular features, probabilistic output).',
  regime:     'HMM market-regime classifier — trend / chop / range / high-vol.',
  anomaly:    'Statistical outlier detector — flags unusual price or volume behaviour.',
  momentum:   'Short-window price momentum (5-min, 10-min slopes).',
  memory:     'Pattern-memory bank — surfaces exact historical precedents.',
  meanrev:       'Mean-reversion signal — Z-score distance from VWAP/SMA.',
  volatility:    'ATR-based volatility filter — widens stops in high-vol regimes.',
  day_structure: 'Intraday S/R levels, day-range position and R/R ratio — blocks entries near day highs.',
};

const AgentDetailModal: React.FC<{ agent: any; onClose: () => void }> = ({ agent, onClose }) => {
  const name       = agent.agent || agent.agent_name || '';
  const action     = agent.action ?? '—';
  const conf       = agent.confidence != null ? (agent.confidence <= 1 ? agent.confidence * 100 : agent.confidence) : null;
  const weight     = agent.weight != null ? Math.round(agent.weight * 100) : null;
  const reasoning  = agent.reasoning ?? null;
  const accentColor = SESS_AGENT_COLOR[name] ?? 'var(--nd-border)';
  const actionColor = SESS_ACTION_COLOR[action] ?? 'var(--nd-text-1)';
  const desc = AGENT_DESCRIPTIONS[name] ?? '';

  return (
    <div
      onClick={e => { if (e.target === e.currentTarget) onClose(); }}
      style={{ position: 'fixed', inset: 0, background: '#00000090', zIndex: 1100, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}
    >
      <div style={{ background: 'var(--nd-bg)', border: `1px solid ${accentColor}60`, borderRadius: 14, width: '100%', maxWidth: 420, boxShadow: `0 16px 48px #00000070, 0 0 0 1px ${accentColor}20` }}>
        {/* Header */}
        <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--nd-border)', display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{ width: 10, height: 10, borderRadius: '50%', background: accentColor, flexShrink: 0 }} />
          <span style={{ fontSize: 15, fontWeight: 700, color: 'var(--nd-text-1)', textTransform: 'capitalize', flex: 1 }}>{name}</span>
          <span style={{ fontSize: 9, fontWeight: 700, padding: '2px 7px', borderRadius: 4, background: `${actionColor}18`, color: actionColor, border: `1px solid ${actionColor}40`, letterSpacing: 0.5 }}>
            {action}
          </span>
          <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', marginLeft: 4 }}>
            <span className="material-icons" style={{ color: 'var(--nd-text-3)', fontSize: 18 }}>close</span>
          </button>
        </div>

        <div style={{ padding: '14px 18px', display: 'flex', flexDirection: 'column', gap: 12 }}>
          {/* Description */}
          {desc && (
            <div style={{ fontSize: 11.5, color: 'var(--nd-text-3)', lineHeight: 1.5 }}>{desc}</div>
          )}

          {/* Metrics row */}
          <div style={{ display: 'flex', gap: 10 }}>
            {conf != null && (
              <div style={{ flex: 1, background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 9, padding: '10px 12px' }}>
                <div style={{ fontSize: 10, color: 'var(--nd-text-3)', marginBottom: 4 }}>CONFIDENCE</div>
                <div style={{ fontSize: 22, fontWeight: 700, color: actionColor }}>{conf.toFixed(0)}%</div>
                {/* confidence bar */}
                <div style={{ marginTop: 5, height: 4, background: 'var(--nd-border)', borderRadius: 2 }}>
                  <div style={{ height: '100%', width: `${conf}%`, background: actionColor, borderRadius: 2 }} />
                </div>
              </div>
            )}
            {weight != null && (
              <div style={{ flex: 1, background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 9, padding: '10px 12px' }}>
                <div style={{ fontSize: 10, color: 'var(--nd-text-3)', marginBottom: 4 }}>ENSEMBLE WEIGHT</div>
                <div style={{ fontSize: 22, fontWeight: 700, color: accentColor }}>{weight}%</div>
                <div style={{ marginTop: 5, height: 4, background: 'var(--nd-border)', borderRadius: 2 }}>
                  <div style={{ height: '100%', width: `${Math.min(weight, 100)}%`, background: accentColor, borderRadius: 2 }} />
                </div>
              </div>
            )}
          </div>

          {/* Reasoning */}
          {reasoning ? (
            <div style={{ background: 'var(--nd-surface)', border: `1px solid ${accentColor}30`, borderRadius: 9, padding: '10px 12px' }}>
              <div style={{ fontSize: 10, color: 'var(--nd-text-3)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 6 }}>Reasoning</div>
              <div style={{ fontSize: 12.5, color: 'var(--nd-text-1)', lineHeight: 1.6 }}>{reasoning}</div>
            </div>
          ) : (
            <div style={{ fontSize: 11, color: 'var(--nd-text-3)', fontStyle: 'italic' }}>
              Reasoning not captured — available for new sessions going forward.
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

const SessionModal: React.FC<{ id: string; onClose: () => void }> = ({ id, onClose }) => {
  const { theme } = useAppStore();
  const [d, setD] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [selectedAgent, setSelectedAgent] = useState<any>(null);

  const load = useCallback(async () => {
    try { const r = await apiService.sessionGet(id); setD((r as any).data ?? null); }
    catch { /* keep last */ } finally { setLoading(false); }
  }, [id]);
  useEffect(() => { load(); const t = setInterval(load, 4000); return () => clearInterval(t); }, [load]);

  const ld     = d?.lastDecision || {};
  const agents = ld.agents || d?.agents || [];
  const ind    = ld.indicators || {};
  const pos    = d?.positionDetail || {};
  const running = d?.status === 'running';
  const markers = (d?.tradesList || [])
    .filter((t: any) => t.timestamp)
    .map((t: any) => ({ timestamp: t.timestamp, action: t.action, price: t.price }));
  const modeColor: Record<string, string> = { paper: '#f59e0b', backtest: '#3b82f6', replay: '#a855f7' };

  const Section: React.FC<{ icon: string; color: string; title: string; children: React.ReactNode }> = ({ icon, color, title, children }) => (
    <div style={{ marginBottom: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <span className="material-icons" style={{ fontSize: 16, color }}>{icon}</span>
        <span style={{ fontSize: 12, fontWeight: 700, color, textTransform: 'uppercase', letterSpacing: 0.5 }}>{title}</span>
      </div>
      {children}
    </div>
  );
  const Kv: React.FC<{ k: string; v: React.ReactNode; c?: string }> = ({ k, v, c }) => (
    <div><div style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>{k}</div><div style={{ fontSize: 13, fontWeight: 600, color: c || 'var(--nd-text-1)' }}>{v}</div></div>
  );

  return (
    <>
    {selectedAgent && <AgentDetailModal agent={selectedAgent} onClose={() => setSelectedAgent(null)} />}
    <div onClick={e => { if (e.target === e.currentTarget) onClose(); }}
      style={{ position: 'fixed', inset: 0, background: '#000000aa', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 12 }}>
      <div style={{ background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderRadius: 16, width: '100%', maxWidth: 840, maxHeight: '92vh', overflow: 'auto', boxShadow: '0 24px 64px #00000060' }}>
        {/* Header — two explicit rows (not one flex row with marginLeft:auto).
            The single-row version squeezed the date span into whatever px was
            left after the P&L block claimed the right edge, word-wrapping
            "@ 15:13 · 2026-07-02" into 4 stacked lines on a phone. */}
        <div style={{ position: 'sticky', top: 0, background: 'var(--nd-bg)', zIndex: 2, padding: '14px 20px', borderBottom: '1px solid var(--nd-border)', display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ fontSize: 18, fontWeight: 700, color: 'var(--nd-text-1)', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d?.symbol ?? '…'}</span>
            {d?.mode && <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 4, background: `${modeColor[d.mode] ?? '#888'}22`, color: modeColor[d.mode] ?? 'var(--nd-text-3)', flexShrink: 0 }}>{d.mode.toUpperCase()}</span>}
            <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 10, background: running ? 'rgba(16,185,129,0.15)' : 'var(--nd-surface)', color: running ? 'var(--nd-green)' : 'var(--nd-text-3)', fontWeight: 600, flexShrink: 0 }}>
              {running ? 'LIVE' : (d?.status ?? '—')}
            </span>
            <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', marginLeft: 'auto', flexShrink: 0, display: 'flex' }}>
              <span className="material-icons" style={{ color: 'var(--nd-text-3)', fontSize: 20 }}>close</span>
            </button>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 12, color: 'var(--nd-text-3)', whiteSpace: 'nowrap' }}>{d?.currentTime ? `@ ${d.currentTime}` : ''}{d?.date ? ` · ${d.date}` : ''}</span>
            <span style={{ fontSize: 14, fontWeight: 700, color: (d?.pnl ?? 0) >= 0 ? 'var(--nd-green)' : 'var(--nd-red)', whiteSpace: 'nowrap' }}>
              {(d?.pnl ?? 0) >= 0 ? '+' : ''}{inr(d?.pnl ?? 0)} <span style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>({(d?.pnlPct ?? 0).toFixed(2)}%)</span>
            </span>
          </div>
        </div>

        <div style={{ padding: '16px 20px' }}>
          {loading && !d ? (
            <div style={{ textAlign: 'center', padding: 40, color: 'var(--nd-text-3)' }}>
              <span className="material-icons nd-spin" style={{ fontSize: 22 }}>autorenew</span>
            </div>
          ) : (
            <>
              {/* Live chart (same component as the order trace) */}
              <div style={{ border: '1px solid var(--nd-border)', borderRadius: 10, overflow: 'hidden', marginBottom: 16 }}>
                <TradingChart
                  candles={d?.candles}
                  prevDayCandles={d?.prevDayCandles}
                  symbol={d?.symbol}
                  date={d?.date}
                  markers={markers}
                  height={280}
                  isDark={theme === 'dark'}
                />
              </div>

              {/* Current position */}
              <Section icon="account_balance_wallet" color="#10b981" title="Position">
                <div style={{ background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 10, padding: '10px 14px', display: 'flex', flexWrap: 'wrap', gap: '10px 28px' }}>
                  <Kv k="Status" v={pos.status ?? 'NONE'} c={pos.status === 'LONG' ? 'var(--nd-green)' : 'var(--nd-text-3)'} />
                  <Kv k="Entry" v={pos.entry_price ? `₹${pos.entry_price}` : '—'} />
                  <Kv k="Qty" v={pos.quantity ?? 0} />
                  <Kv k="Unrealised P&L" v={`₹${(pos.current_pnl ?? 0).toFixed(2)}`} c={(pos.current_pnl ?? 0) >= 0 ? 'var(--nd-green)' : 'var(--nd-red)'} />
                  <Kv k="Trades" v={d?.trades ?? 0} />
                  <Kv k="Cash" v={inr(d?.cash ?? 0)} />
                </div>
              </Section>

              {/* Latest ensemble decision */}
              <Section icon="how_to_vote" color="#f59e0b" title="Latest Decision">
                <div style={{ background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 10, padding: '10px 14px' }}>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '10px 28px', marginBottom: ld.reason ? 8 : 0 }}>
                    <Kv k="Action" v={ld.action ?? '—'} c={SESS_ACTION_COLOR[ld.action] ?? 'var(--nd-text-1)'} />
                    <Kv k="Confidence" v={ld.confidence != null ? `${(ld.confidence * 100).toFixed(0)}%` : '—'} />
                    <Kv k="Candle" v={ld.time ?? d?.currentTime ?? '—'} />
                  </div>
                  {ld.reason && <div style={{ fontSize: 11.5, color: 'var(--nd-text-2)', lineHeight: 1.5 }}>{ld.reason}</div>}
                </div>
              </Section>

              {/* Agent votes */}
              {agents.length > 0 && (
                <Section icon="smart_toy" color="#8b5cf6" title={`Agent Decisions (${agents.length})`}>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                    {agents.map((a: any, i: number) => {
                      const name = a.agent || a.agent_name;
                      return (
                        <div key={i} onClick={() => setSelectedAgent(a)}
                          style={{ background: 'var(--nd-surface)', border: `1px solid ${(SESS_AGENT_COLOR[name] ?? 'var(--nd-border)')}40`, borderRadius: 8, padding: '6px 12px', display: 'flex', flexDirection: 'column', alignItems: 'center', minWidth: 88, cursor: 'pointer', transition: 'border-color 0.15s, background 0.15s' }}
                          onMouseEnter={e => { (e.currentTarget as HTMLDivElement).style.borderColor = (SESS_AGENT_COLOR[name] ?? '#888') + 'aa'; (e.currentTarget as HTMLDivElement).style.background = 'var(--nd-bg)'; }}
                          onMouseLeave={e => { (e.currentTarget as HTMLDivElement).style.borderColor = (SESS_AGENT_COLOR[name] ?? 'var(--nd-border)') + '40'; (e.currentTarget as HTMLDivElement).style.background = 'var(--nd-surface)'; }}
                        >
                          <div style={{ fontSize: 10, color: 'var(--nd-text-3)', textTransform: 'capitalize' }}>{name}</div>
                          <div style={{ fontSize: 13, fontWeight: 700, color: SESS_ACTION_COLOR[a.action] ?? 'var(--nd-text-3)' }}>{a.action}</div>
                          {a.confidence != null && <div style={{ fontSize: 9.5, color: 'var(--nd-text-3)' }}>{(a.confidence * 100).toFixed(0)}%</div>}
                          <span className="material-icons" style={{ fontSize: 10, color: 'var(--nd-text-3)', marginTop: 2 }}>info</span>
                        </div>
                      );
                    })}
                  </div>
                </Section>
              )}

              {/* Indicators */}
              {(ind.rsi != null || ind.vwap != null) && (
                <Section icon="insights" color="#3b82f6" title="Indicators (latest candle)">
                  <div style={{ background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 10, padding: '10px 14px', display: 'flex', flexWrap: 'wrap', gap: '10px 28px' }}>
                    <Kv k="RSI" v={ind.rsi ?? '—'} />
                    <Kv k="VWAP" v={ind.vwap ? `₹${ind.vwap}` : '—'} />
                    <Kv k="SMA5" v={ind.sma5 ? `₹${ind.sma5}` : '—'} />
                    <Kv k="SMA20" v={ind.sma20 ? `₹${ind.sma20}` : '—'} />
                    <Kv k="ATR" v={ind.atr ?? '—'} />
                  </div>
                </Section>
              )}

              {/* Trade Decisions — ensemble + full agent breakdown at every BUY / SELL */}
              {Array.isArray(d?.tradesList) && d.tradesList.length > 0 && (
                <Section icon="swap_vert" color="#22c55e" title={`Trade Decisions (${d.tradesList.length})`}>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 10, maxHeight: 480, overflowY: 'auto' }}>
                    {[...d.tradesList].reverse().map((t: any, i: number) => {
                      const isBuy = t.action === 'BUY';
                      const accentColor = isBuy ? '#22c55e' : '#ef4444';
                      // agents stored directly on trade (new sessions) or fallback from decisionLog
                      const logEntry = (d.decisionLog || []).find((x: any) => x.time === t.time && x.executed);
                      const agentVotes: any[] = (t.agents?.length ? t.agents : logEntry?.agents) || [];
                      return (
                        <div key={i} style={{
                          background: 'var(--nd-surface)',
                          border: `1px solid ${accentColor}30`,
                          borderLeft: `3px solid ${accentColor}`,
                          borderRadius: 10, padding: '12px 14px',
                        }}>
                          {/* ── Header: action · time · price · qty · pnl ── */}
                          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
                            <span style={{
                              fontSize: 12, fontWeight: 700, padding: '3px 10px', borderRadius: 6,
                              background: `${accentColor}20`, color: accentColor, letterSpacing: 0.5,
                            }}>{t.action}</span>
                            <span style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>{t.time}</span>
                            <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--nd-text-1)' }}>
                              ₹{typeof t.price === 'number' ? t.price.toFixed(2) : t.price}
                            </span>
                            {t.quantity != null && (
                              <span style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>× {t.quantity}</span>
                            )}
                            {t.pnl != null && (
                              <span style={{ marginLeft: 'auto', fontSize: 13, fontWeight: 700, color: t.pnl >= 0 ? '#22c55e' : '#ef4444' }}>
                                {t.pnl >= 0 ? '+' : ''}₹{t.pnl.toFixed(2)}
                                {t.pnlPct != null && (
                                  <span style={{ fontSize: 10, fontWeight: 400, marginLeft: 4, color: 'var(--nd-text-3)' }}>
                                    ({t.pnlPct > 0 ? '+' : ''}{t.pnlPct}%)
                                  </span>
                                )}
                              </span>
                            )}
                          </div>

                          {/* ── Ensemble decision row ── */}
                          <div style={{
                            display: 'flex', alignItems: 'center', gap: 10,
                            background: 'var(--nd-bg)', borderRadius: 7, padding: '7px 10px', marginBottom: 8,
                          }}>
                            <span className="material-icons" style={{ fontSize: 14, color: 'var(--nd-text-3)' }}>how_to_vote</span>
                            <span style={{ fontSize: 11, color: 'var(--nd-text-3)', fontWeight: 600 }}>Ensemble</span>
                            <span style={{ fontSize: 13, fontWeight: 700, color: SESS_ACTION_COLOR[t.action] ?? 'var(--nd-text-1)' }}>
                              {t.action}
                            </span>
                            {t.confidence != null && (
                              <span style={{
                                fontSize: 11, fontWeight: 600, padding: '1px 7px', borderRadius: 4,
                                background: `${accentColor}15`, color: accentColor,
                              }}>{t.confidence}%</span>
                            )}
                            {t.reason && (
                              <span style={{ fontSize: 10.5, color: 'var(--nd-text-2)', lineHeight: 1.4, flex: 1 }}>
                                {t.reason}
                              </span>
                            )}
                          </div>

                          {/* ── Agent votes grid ── */}
                          {agentVotes.length > 0 ? (
                            <>
                              <div style={{ fontSize: 10, color: 'var(--nd-text-3)', fontWeight: 600, marginBottom: 5, textTransform: 'uppercase', letterSpacing: 0.5 }}>
                                Agent Votes
                              </div>
                              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
                                {agentVotes.map((a: any, j: number) => {
                                  const name = a.agent || a.agent_name;
                                  const borderColor = SESS_AGENT_COLOR[name] ?? 'var(--nd-border)';
                                  return (
                                    <div key={j} onClick={() => setSelectedAgent(a)}
                                      style={{
                                        background: 'var(--nd-bg)', border: `1px solid ${borderColor}50`,
                                        borderRadius: 7, padding: '4px 10px',
                                        display: 'flex', flexDirection: 'column', alignItems: 'center', minWidth: 72,
                                        cursor: 'pointer', transition: 'border-color 0.15s, background 0.15s',
                                      }}
                                      onMouseEnter={e => { (e.currentTarget as HTMLDivElement).style.borderColor = borderColor + 'aa'; (e.currentTarget as HTMLDivElement).style.background = 'var(--nd-surface)'; }}
                                      onMouseLeave={e => { (e.currentTarget as HTMLDivElement).style.borderColor = borderColor + '50'; (e.currentTarget as HTMLDivElement).style.background = 'var(--nd-bg)'; }}
                                    >
                                      <div style={{ fontSize: 9, color: 'var(--nd-text-3)', textTransform: 'capitalize', whiteSpace: 'nowrap' }}>{name}</div>
                                      <div style={{ fontSize: 12, fontWeight: 700, color: SESS_ACTION_COLOR[a.action] ?? 'var(--nd-text-3)' }}>{a.action}</div>
                                      {a.confidence != null && (
                                        <div style={{ fontSize: 9, color: 'var(--nd-text-3)' }}>{(a.confidence * 100).toFixed(0)}%</div>
                                      )}
                                      <span className="material-icons" style={{ fontSize: 9, color: 'var(--nd-text-3)', marginTop: 1 }}>info</span>
                                    </div>
                                  );
                                })}
                              </div>
                            </>
                          ) : (
                            <div style={{ fontSize: 11, color: 'var(--nd-text-3)', fontStyle: 'italic' }}>
                              Agent votes not recorded — start a new session to capture them.
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </Section>
              )}
            </>
          )}
        </div>
      </div>
    </div>
    </>
  );
};

const LiveSessionsPanel: React.FC = () => {
  const [sessions, setSessions] = useState<any[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [openSession, setOpenSession] = useState<string | null>(null);
  const [batchSize, setBatchSize]   = useState<number | null>(null);
  const [batchInput, setBatchInput] = useState('');
  const [savingBatch, setSavingBatch] = useState(false);
  const [speed, setSpeed] = useState<number | null>(null);
  const [savingSpeed, setSavingSpeed] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await apiService.sessionList('running');
      setSessions((r as any).data ?? []);
    } catch { /* keep last */ }
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t); }, [load]);

  // Current autopilot batch size + replay speed.
  useEffect(() => {
    apiService.getAutopilot().then((a: any) => {
      const bs = a?.data?.backtest?.batchSize;
      if (bs != null) { setBatchSize(bs); setBatchInput(String(bs)); }
      const sp = a?.data?.backtest?.speed;
      if (sp != null) setSpeed(sp);
    }).catch(() => {});
  }, []);

  const applySpeed = async (n: number) => {
    if (n === speed) return;
    setSavingSpeed(true);
    try {
      const r = await apiService.setAutopilotSpeed(n);
      setSpeed((r as any).data?.backtest?.speed ?? n);
    } catch { /* keep last */ }
    finally { setSavingSpeed(false); }
  };

  const applyBatch = async () => {
    const n = Math.max(1, Math.min(50, parseInt(batchInput || '0', 10) || 0));
    if (!n || n === batchSize) { setBatchInput(batchSize != null ? String(batchSize) : ''); return; }
    setSavingBatch(true);
    try {
      const r = await apiService.setAutopilotBatchSize(n);
      const bs = (r as any).data?.backtest?.batchSize ?? n;
      setBatchSize(bs); setBatchInput(String(bs));
    } catch { setBatchInput(batchSize != null ? String(batchSize) : ''); }
    finally { setSavingBatch(false); }
  };

  const stop = async (id: string) => {
    setBusy(id);
    try { await apiService.sessionStop(id); await load(); } catch { /* ignore */ } finally { setBusy(null); }
  };

  if (!sessions.length) return null;
  const totalPnl = sessions.reduce((s, x) => s + (x.pnl ?? 0), 0);

  return (
    <div className="nd-card" style={{ padding: '14px 18px', marginBottom: 20 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10, flexWrap: 'wrap' }}>
        <span className="material-icons" style={{ fontSize: 18, color: 'var(--nd-green)' }}>monitoring</span>
        <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--nd-text-1)' }}>Live Auto-Trading</div>
        <span style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>{sessions.length} running</span>
        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
          {/* Replay speed — candles advanced per step in new backtest sessions */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }} title="Backtest replay speed — candles advanced per step (applies to newly started sessions).">
            <span style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>Speed</span>
            <select
              value={speed ?? ''} disabled={savingSpeed || speed == null}
              onChange={e => applySpeed(Number(e.target.value))}
              style={{ padding: '4px 6px', borderRadius: 6, border: '1px solid var(--nd-border)',
                background: 'var(--nd-surface)', color: 'var(--nd-text-1)', fontSize: 12, fontWeight: 600,
                cursor: 'pointer', outline: 'none', opacity: savingSpeed ? 0.6 : 1 }}>
              {speed != null && ![1, 2, 5, 10, 30, 60].includes(speed) && <option value={speed}>{speed}×</option>}
              {[1, 2, 5, 10, 30, 60].map(n => <option key={n} value={n}>{n}×</option>)}
            </select>
            {savingSpeed && <span className="material-icons nd-spin" style={{ fontSize: 14, color: 'var(--nd-text-3)' }}>autorenew</span>}
          </div>
          {/* Concurrent-sessions-per-batch — editable; applies to the next batch */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }} title="How many stocks the autopilot trades at once per batch (1–50). Takes effect on the next batch.">
            <span style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>Batch size</span>
            <input
              type="number" min={1} max={50} value={batchInput} disabled={savingBatch}
              onChange={e => setBatchInput(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); }}
              onBlur={applyBatch}
              style={{ width: 50, padding: '4px 6px', borderRadius: 6, border: '1px solid var(--nd-border)',
                background: 'var(--nd-surface)', color: 'var(--nd-text-1)', fontSize: 12, fontWeight: 600,
                textAlign: 'center', outline: 'none', opacity: savingBatch ? 0.6 : 1 }}
            />
            {savingBatch && <span className="material-icons nd-spin" style={{ fontSize: 14, color: 'var(--nd-text-3)' }}>autorenew</span>}
          </div>
          <span style={{ fontSize: 13, fontWeight: 700, color: totalPnl >= 0 ? 'var(--nd-green)' : 'var(--nd-red)' }}>
            {totalPnl >= 0 ? '+' : ''}{inr(totalPnl)}
          </span>
        </div>
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', minWidth: 540, borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr style={{ color: 'var(--nd-text-3)', textAlign: 'left' }}>
              {['Symbol', 'Mode', 'Position', 'Hold cap', 'P&L', 'Trades', ''].map(h => (
                <th key={h} style={{ padding: '6px 10px', fontWeight: 500 }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sessions.map(s => (
              <tr key={s.id} onClick={() => setOpenSession(s.id)} title="Click for live session details"
                style={{ borderTop: '1px solid var(--nd-border)', cursor: 'pointer', transition: 'background 0.1s' }}
                onMouseEnter={e => (e.currentTarget.style.background = 'var(--nd-bg)')}
                onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
                <td style={{ padding: '7px 10px', fontWeight: 700, color: 'var(--nd-text-1)' }}>{s.symbol}</td>
                <td style={{ padding: '7px 10px' }}>
                  <span style={{ fontSize: 10, fontWeight: 600, padding: '2px 6px', borderRadius: 4, background: s.mode === 'paper' ? 'rgba(245,158,11,0.15)' : s.mode === 'backtest' ? 'rgba(59,130,246,0.15)' : 'rgba(168,85,247,0.15)', color: s.mode === 'paper' ? '#f59e0b' : s.mode === 'backtest' ? '#3b82f6' : '#a855f7' }}>{(s.mode || '').toUpperCase()}</span>
                </td>
                <td style={{ padding: '7px 10px' }}>
                  <span style={{ fontWeight: 600, color: s.position === 'LONG' ? 'var(--nd-green)' : 'var(--nd-text-3)' }}>{s.position ?? 'NONE'}</span>
                </td>
                <td style={{ padding: '7px 10px', color: 'var(--nd-text-2)' }}>{s.maxHoldMinutes ? `${s.maxHoldMinutes}m` : '—'}</td>
                <td style={{ padding: '7px 10px', fontWeight: 600, color: (s.pnl ?? 0) >= 0 ? 'var(--nd-green)' : 'var(--nd-red)' }}>
                  {(s.pnl ?? 0) >= 0 ? '+' : ''}{inr(s.pnl ?? 0)} <span style={{ color: 'var(--nd-text-3)', fontWeight: 400 }}>({(s.pnlPct ?? 0).toFixed(2)}%)</span>
                </td>
                <td style={{ padding: '7px 10px', color: 'var(--nd-text-3)' }}>{s.trades ?? 0}</td>
                <td style={{ padding: '7px 10px', textAlign: 'right' }}>
                  <button onClick={e => { e.stopPropagation(); stop(s.id); }} disabled={busy === s.id}
                    // Kills a live auto-trading session — minHeight keeps the
                    // tap target real-sized even though the row is dense.
                    style={{ padding: '6px 12px', minHeight: 34, borderRadius: 6, border: '1px solid var(--nd-border)', background: 'var(--nd-surface)', color: 'var(--nd-red)', cursor: 'pointer', fontSize: 11.5, fontWeight: 600 }}>
                    {busy === s.id ? '…' : 'Stop'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {openSession && <SessionModal id={openSession} onClose={() => setOpenSession(null)} />}
    </div>
  );
};

export default LiveSessionsPanel;
