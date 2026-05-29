import React, { useEffect, useState } from 'react';

const FEEDBACK_BASE = 'http://localhost:8012';

interface AgentSignals {
  technical?: string;
  sentiment?: string;
  macro?: string;
  pattern?: string;
  rl?: string;
  [key: string]: string | undefined;
}

interface MarketContext {
  atr?: number;
  regime?: string;
  vix?: number;
  [key: string]: any;
}

interface TradeRecord {
  trade_id: string;
  symbol: string;
  action: string;
  entry_price: number;
  exit_price?: number;
  pnl_abs?: number;
  pnl_pct?: number;
  ensemble_confidence: number;
  paper_trade?: boolean;
  trade_source?: string;   // LIVE | PAPER | BACKTEST
  outcome?: string;
  agent_signals: AgentSignals;
  market_context: MarketContext;
  timestamp_open: string;
  timestamp_close?: string;
  duration_minutes?: number;
}

interface Stats {
  trade_stats: Array<{ outcome: string; count: number; avg_pnl: number }>;
  trades_since_retrain: number;
  agent_weights?: Record<string, number>;
}

// ── Execution Step Types ──────────────────────────────────────────────────────

interface ExecStep {
  step: number;
  name: string;
  icon: string;
  color: string;
  data: Record<string, any>;
}

function buildExecutionSteps(trade: TradeRecord): ExecStep[] {
  const steps: ExecStep[] = [];

  steps.push({
    step: 1,
    name: 'Market Signal',
    icon: 'wifi',
    color: '#3b82f6',
    data: {
      symbol: trade.symbol,
      price: `₹${trade.entry_price?.toFixed(2)}`,
      time: trade.timestamp_open ? new Date(trade.timestamp_open).toLocaleString() : '—',
      regime: trade.market_context?.regime ?? '—',
      vix: trade.market_context?.vix ?? '—',
    },
  });

  const agentSignals = trade.agent_signals ?? {};
  steps.push({
    step: 2,
    name: 'Agent Decisions',
    icon: 'smart_toy',
    color: '#8b5cf6',
    data: Object.fromEntries(
      ['technical', 'sentiment', 'macro', 'pattern', 'rl'].map(agent => [
        agent,
        agentSignals[agent] ?? '—',
      ])
    ),
  });

  steps.push({
    step: 3,
    name: 'Ensemble Vote',
    icon: 'how_to_vote',
    color: '#f59e0b',
    data: {
      decision: trade.action,
      confidence: `${((trade.ensemble_confidence ?? 0) * 100).toFixed(1)}%`,
      gate: (trade.ensemble_confidence ?? 0) >= 0.60 ? 'PASSED (≥ 60%)' : 'FAILED (< 60%)',
    },
  });

  const atr = trade.market_context?.atr ?? 0;
  const stopLoss   = trade.action === 'BUY' ? trade.entry_price - atr * 2 : trade.entry_price + atr * 2;
  const takeProfit = trade.action === 'BUY' ? trade.entry_price + atr * 3 : trade.entry_price - atr * 3;
  steps.push({
    step: 4,
    name: 'Risk Gate',
    icon: 'security',
    color: '#10b981',
    data: {
      'ATR': atr ? `₹${atr.toFixed(2)}` : '—',
      'Stop Loss':   atr ? `₹${stopLoss.toFixed(2)}` : '—',
      'Take Profit': atr ? `₹${takeProfit.toFixed(2)}` : '—',
      'Max Risk': '2% of portfolio',
      'Max Position': '5% of portfolio',
    },
  });

  steps.push({
    step: 5,
    name: 'Order Fill',
    icon: 'receipt_long',
    color: '#06b6d4',
    data: {
      'Fill Price': `₹${trade.entry_price?.toFixed(2)}`,
      'Mode': trade.trade_source ?? (trade.paper_trade ? 'PAPER' : 'LIVE'),
      'Status': 'FILLED',
      'Duration': trade.duration_minutes ? `${trade.duration_minutes} min` : '—',
    },
  });

  steps.push({
    step: 6,
    name: 'Trade Outcome',
    icon: trade.pnl_pct != null && trade.pnl_pct >= 0 ? 'trending_up' : 'trending_down',
    color: trade.pnl_pct != null && trade.pnl_pct >= 0 ? '#22c55e' : '#ef4444',
    data: {
      'Exit Price': trade.exit_price ? `₹${trade.exit_price.toFixed(2)}` : '—',
      'P&L': trade.pnl_abs != null ? `₹${trade.pnl_abs.toFixed(2)}` : '—',
      'P&L %': trade.pnl_pct != null ? `${(trade.pnl_pct * 100).toFixed(2)}%` : '—',
      'Outcome': trade.outcome ?? 'OPEN',
      'Model Update': trade.outcome ? 'Weights updated' : 'Pending close',
    },
  });

  return steps;
}

// ── Execution Modal ───────────────────────────────────────────────────────────

const AGENT_COLORS: Record<string, string> = {
  technical: '#3b82f6', sentiment: '#06b6d4', macro: '#f59e0b',
  pattern: '#8b5cf6', rl: '#10b981',
};
const ACTION_COLOR: Record<string, string> = {
  BUY: '#22c55e', SELL: '#ef4444', HOLD: '#f59e0b',
};

function ExecutionModal({ trade, onClose }: { trade: TradeRecord; onClose: () => void }) {
  const steps = buildExecutionSteps(trade);

  return (
    <div
      style={{ position: 'fixed', inset: 0, background: '#00000080', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24 }}
      onClick={e => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div style={{ background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderRadius: 16, width: '100%', maxWidth: 680, maxHeight: '85vh', overflow: 'hidden', display: 'flex', flexDirection: 'column', boxShadow: '0 24px 64px #00000060' }}>

        {/* Header */}
        <div style={{ padding: '18px 24px', borderBottom: '1px solid var(--nd-border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <span style={{ fontSize: 18, fontWeight: 700, color: 'var(--nd-text-1)' }}>{trade.symbol}</span>
              <span style={{ padding: '2px 10px', borderRadius: 6, fontSize: 12, fontWeight: 700, background: `${ACTION_COLOR[trade.action] ?? '#888'}20`, color: ACTION_COLOR[trade.action] ?? 'var(--nd-text-1)' }}>{trade.action}</span>
              <span style={{ padding: '2px 8px', borderRadius: 4, fontSize: 10, fontWeight: 600, background: 'var(--nd-surface)', color: 'var(--nd-text-3)', border: '1px solid var(--nd-border)' }}>
                {trade.trade_source ?? (trade.paper_trade ? 'PAPER' : 'LIVE')}
              </span>
            </div>
            <div style={{ fontSize: 12, color: 'var(--nd-text-3)', marginTop: 3 }}>
              Execution trace · {trade.timestamp_open ? new Date(trade.timestamp_open).toLocaleString() : '—'}
            </div>
          </div>
          <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 4 }}>
            <span className="material-icons" style={{ color: 'var(--nd-text-3)', fontSize: 20 }}>close</span>
          </button>
        </div>

        {/* Steps timeline */}
        <div style={{ overflowY: 'auto', padding: '20px 24px', display: 'flex', flexDirection: 'column', gap: 0 }}>
          {steps.map((step, idx) => (
            <div key={step.step} style={{ display: 'flex', gap: 16, paddingBottom: idx < steps.length - 1 ? 0 : 0 }}>

              {/* Timeline line + dot */}
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', flexShrink: 0, width: 36 }}>
                <div style={{ width: 36, height: 36, borderRadius: '50%', background: `${step.color}20`, border: `2px solid ${step.color}`, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                  <span className="material-icons" style={{ fontSize: 16, color: step.color }}>{step.icon}</span>
                </div>
                {idx < steps.length - 1 && (
                  <div style={{ width: 2, flex: 1, minHeight: 24, background: 'var(--nd-border)', margin: '4px 0' }} />
                )}
              </div>

              {/* Step content */}
              <div style={{ flex: 1, paddingBottom: idx < steps.length - 1 ? 16 : 0 }}>
                <div style={{ fontSize: 12, fontWeight: 700, color: step.color, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 8 }}>
                  Step {step.step} — {step.name}
                </div>

                {step.step === 2 ? (
                  // Special rendering for agent decisions grid
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                    {Object.entries(step.data).map(([agent, action]) => (
                      <div key={agent} style={{ background: 'var(--nd-surface)', border: `1px solid ${AGENT_COLORS[agent] ?? 'var(--nd-border)'}40`, borderRadius: 8, padding: '6px 12px', display: 'flex', flexDirection: 'column', alignItems: 'center', minWidth: 90 }}>
                        <div style={{ fontSize: 10, color: 'var(--nd-text-3)', textTransform: 'capitalize', marginBottom: 2 }}>{agent}</div>
                        <div style={{ fontSize: 13, fontWeight: 700, color: ACTION_COLOR[action as string] ?? 'var(--nd-text-3)' }}>
                          {action as string}
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div style={{ background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 10, padding: '10px 14px', display: 'flex', flexWrap: 'wrap', gap: '8px 24px' }}>
                    {Object.entries(step.data).map(([k, v]) => (
                      <div key={k}>
                        <div style={{ fontSize: 10, color: 'var(--nd-text-3)', marginBottom: 2 }}>{k}</div>
                        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--nd-text-1)' }}>{String(v)}</div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ── Main Orders Page ──────────────────────────────────────────────────────────

const Orders: React.FC = () => {
  const [trades,  setTrades]  = useState<TradeRecord[]>([]);
  const [stats,   setStats]   = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState<string | null>(null);
  const [selected, setSelected] = useState<TradeRecord | null>(null);
  const [filter,   setFilter]   = useState<'ALL' | 'LIVE' | 'PAPER' | 'BACKTEST'>('ALL');

  useEffect(() => {
    const load = async () => {
      try {
        const [statsRes, tradesRes] = await Promise.all([
          fetch(`${FEEDBACK_BASE}/stats`),
          fetch(`${FEEDBACK_BASE}/trades`).catch(() => null),
        ]);
        if (statsRes.ok) setStats(await statsRes.json());
        if (tradesRes?.ok) {
          const data = await tradesRes.json();
          setTrades(Array.isArray(data) ? data : data.trades ?? []);
        }
      } catch (e: any) {
        setError(e.message);
      } finally {
        setLoading(false);
      }
    };
    load();
  }, []);

  const filteredTrades = trades.filter(t => {
    if (filter === 'ALL') return true;
    const src = t.trade_source ?? (t.paper_trade ? 'PAPER' : 'LIVE');
    return src === filter;
  });

  const totalTrades  = stats?.trade_stats?.reduce((s, r) => s + Number(r.count), 0) ?? 0;
  const winningTrades = stats?.trade_stats?.find(r => r.outcome === 'WIN')?.count ?? 0;
  const losingTrades  = stats?.trade_stats?.find(r => r.outcome === 'LOSS')?.count ?? 0;
  const winRate = totalTrades > 0 ? winningTrades / totalTrades : 0;

  const pnlColor = (v: number) => v >= 0 ? 'var(--nd-green)' : 'var(--nd-red)';

  const modeColor: Record<string, string> = {
    LIVE: '#22c55e', PAPER: '#f59e0b', BACKTEST: '#3b82f6',
  };

  if (loading) return <div style={{ padding: 40, textAlign: 'center', color: 'var(--nd-text-3)' }}>Loading trade history...</div>;
  if (error)   return <div style={{ padding: 40, textAlign: 'center', color: 'var(--nd-red)' }}>Failed to connect: {error}</div>;

  return (
    <div style={{ padding: '24px 32px', maxWidth: 1200, margin: '0 auto' }}>
      <h2 style={{ margin: '0 0 6px', fontSize: 22, fontWeight: 700, color: 'var(--nd-text-1)' }}>Orders</h2>
      <p style={{ margin: '0 0 24px', color: 'var(--nd-text-3)', fontSize: 13 }}>
        Click any row to see the full execution trace. All modes (Live · Paper · Backtest) train the AI.
      </p>

      {/* Stats row */}
      {stats && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 12, marginBottom: 20 }}>
          {[
            { label: 'Total Trades',      value: totalTrades },
            { label: 'Win Rate',          value: `${(winRate * 100).toFixed(1)}%` },
            { label: 'Wins',              value: winningTrades },
            { label: 'Losses',            value: losingTrades },
            { label: 'Until Retrain',     value: `${stats.trades_since_retrain} / 500` },
          ].map(s => (
            <div key={s.label} style={{ background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 10, padding: '14px 16px' }}>
              <div style={{ fontSize: 11, color: 'var(--nd-text-3)', marginBottom: 4 }}>{s.label}</div>
              <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--nd-text-1)' }}>{s.value}</div>
            </div>
          ))}
        </div>
      )}

      {/* Agent weights */}
      {stats?.agent_weights && (
        <div style={{ background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 12, padding: '14px 18px', marginBottom: 20 }}>
          <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--nd-text-3)', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 10 }}>
            Adaptive Agent Weights (live)
          </div>
          <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap' }}>
            {Object.entries(stats.agent_weights).map(([agent, weight]) => (
              <div key={agent} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4, minWidth: 72 }}>
                <div style={{ fontSize: 11, color: 'var(--nd-text-3)', textTransform: 'capitalize' }}>{agent}</div>
                <div style={{ fontSize: 18, fontWeight: 700, color: AGENT_COLORS[agent] ?? 'var(--nd-accent)' }}>
                  {(weight * 100).toFixed(1)}%
                </div>
                <div style={{ width: 48, height: 3, background: 'var(--nd-border)', borderRadius: 2, overflow: 'hidden' }}>
                  <div style={{ height: '100%', width: `${weight * 100 * 1.67}%`, background: AGENT_COLORS[agent] ?? 'var(--nd-accent)', borderRadius: 2 }} />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Filter pills */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 14 }}>
        {(['ALL', 'LIVE', 'PAPER', 'BACKTEST'] as const).map(f => (
          <button key={f} onClick={() => setFilter(f)}
            style={{ padding: '5px 14px', borderRadius: 20, border: '1px solid var(--nd-border)', cursor: 'pointer', fontSize: 12, fontWeight: 500, transition: 'all 0.15s',
              background: filter === f ? (modeColor[f] ?? 'var(--nd-accent)') : 'var(--nd-surface)',
              color: filter === f ? '#fff' : 'var(--nd-text-2)',
              borderColor: filter === f ? (modeColor[f] ?? 'var(--nd-accent)') : 'var(--nd-border)',
            }}>
            {f}
          </button>
        ))}
        <span style={{ marginLeft: 4, fontSize: 12, color: 'var(--nd-text-3)', alignSelf: 'center' }}>
          {filteredTrades.length} trade{filteredTrades.length !== 1 ? 's' : ''}
        </span>
      </div>

      {/* Trade list */}
      {filteredTrades.length === 0 ? (
        <div style={{ background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 12, padding: 48, textAlign: 'center', color: 'var(--nd-text-3)' }}>
          No trades yet. Trades from Live, Paper, and Backtest modes all appear here.
        </div>
      ) : (
        <div style={{ background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 12, overflow: 'hidden' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--nd-border)', background: 'var(--nd-bg)' }}>
                {['Symbol', 'Action', 'Mode', 'Entry', 'Exit', 'P&L', 'P&L %', 'Confidence', 'Outcome', 'Opened'].map(h => (
                  <th key={h} style={{ padding: '10px 14px', textAlign: 'left', color: 'var(--nd-text-3)', fontWeight: 500, fontSize: 11 }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filteredTrades.map((t, i) => {
                const src = t.trade_source ?? (t.paper_trade ? 'PAPER' : 'LIVE');
                return (
                  <tr
                    key={t.trade_id}
                    onClick={() => setSelected(t)}
                    style={{ borderBottom: i < filteredTrades.length - 1 ? '1px solid var(--nd-border)' : 'none', cursor: 'pointer', transition: 'background 0.1s' }}
                    onMouseEnter={e => (e.currentTarget.style.background = 'var(--nd-bg)')}
                    onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                  >
                    <td style={{ padding: '10px 14px', fontWeight: 700, color: 'var(--nd-text-1)' }}>{t.symbol}</td>
                    <td style={{ padding: '10px 14px' }}>
                      <span style={{ padding: '2px 8px', borderRadius: 4, fontSize: 11, fontWeight: 700, background: `${ACTION_COLOR[t.action] ?? '#888'}18`, color: ACTION_COLOR[t.action] ?? 'var(--nd-text-1)' }}>
                        {t.action}
                      </span>
                    </td>
                    <td style={{ padding: '10px 14px' }}>
                      <span style={{ fontSize: 10, padding: '2px 7px', borderRadius: 4, fontWeight: 600, background: `${modeColor[src] ?? '#888'}18`, color: modeColor[src] ?? 'var(--nd-text-3)' }}>
                        {src}
                      </span>
                    </td>
                    <td style={{ padding: '10px 14px', color: 'var(--nd-text-1)' }}>₹{t.entry_price?.toFixed(2)}</td>
                    <td style={{ padding: '10px 14px', color: 'var(--nd-text-2)' }}>{t.exit_price ? `₹${t.exit_price.toFixed(2)}` : '—'}</td>
                    <td style={{ padding: '10px 14px', fontWeight: 600, color: pnlColor(t.pnl_abs ?? 0) }}>
                      {t.pnl_abs != null ? `₹${t.pnl_abs.toFixed(2)}` : '—'}
                    </td>
                    <td style={{ padding: '10px 14px', fontWeight: 600, color: pnlColor(t.pnl_pct ?? 0) }}>
                      {t.pnl_pct != null ? `${(t.pnl_pct * 100).toFixed(2)}%` : '—'}
                    </td>
                    <td style={{ padding: '10px 14px', color: 'var(--nd-text-2)' }}>
                      {t.ensemble_confidence ? `${(t.ensemble_confidence * 100).toFixed(0)}%` : '—'}
                    </td>
                    <td style={{ padding: '10px 14px' }}>
                      <span style={{ fontSize: 11, fontWeight: 600, color: t.outcome === 'WIN' ? 'var(--nd-green)' : t.outcome === 'LOSS' ? 'var(--nd-red)' : 'var(--nd-text-3)' }}>
                        {t.outcome ?? 'OPEN'}
                      </span>
                    </td>
                    <td style={{ padding: '10px 14px', color: 'var(--nd-text-3)', fontSize: 11 }}>
                      {t.timestamp_open ? new Date(t.timestamp_open).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—'}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Execution preview modal */}
      {selected && <ExecutionModal trade={selected} onClose={() => setSelected(null)} />}
    </div>
  );
};

export default Orders;
