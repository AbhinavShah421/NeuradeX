import React, { useEffect, useState } from 'react';
import apiService from '../services/api';
import { useAppStore } from '../stores/appStore';
import TradingChart from '../components/TradingChart';

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

// NOTE: the axios response interceptor converts all snake_case API fields to
// camelCase, so these interfaces (and every access below) use camelCase.
interface TradeRecord {
  tradeId: string;
  symbol: string;
  action: string;
  entryPrice: number;
  exitPrice?: number;
  pnlAbs?: number;
  pnlPct?: number;
  ensembleConfidence: number;
  paperTrade?: boolean;
  tradeSource?: string;   // LIVE | PAPER | BACKTEST
  outcome?: string;
  agentSignals: AgentSignals;
  marketContext: MarketContext;
  timestampOpen: string;
  timestampClose?: string;
  durationMinutes?: number;
  createdAt?: string;   // when the backtest/session was actually run
}

interface Stats {
  tradeStats: Array<{ outcome: string; count: number; avgPnl: number }>;
  tradesSinceRetrain: number;
  agentWeights?: Record<string, number>;
}

// Response keys are camelCased by the axios interceptor (snake_case → camelCase).
interface PortfolioMetrics {
  totalTrades: number;
  winRate: number;
  meanPnlPct: number;
  totalReturnPct: number;
  sharpeRatio: number;
  sortinoRatio: number;
  maxDrawdownPct: number;
  calmarRatio: number;
  error?: string;
}

interface AgentMetric {
  precision?: number;
  recall?: number;
  f1?: number;
  accuracy?: number;
  totalTrades?: number;
  confusionMatrix?: { tp: number; fp: number; tn: number; fn: number };
  status?: string;
  total?: number;
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
      price: `₹${trade.entryPrice?.toFixed(2)}`,
      time: trade.timestampOpen ? new Date(trade.timestampOpen).toLocaleString() : '—',
      regime: trade.marketContext?.regime ?? '—',
      vix: trade.marketContext?.vix ?? '—',
    },
  });

  const agentSignals = trade.agentSignals ?? {};
  // Show every agent the ensemble actually recorded for this trade. Older trades
  // only stored a synthetic 5-agent set, so fall back to that when nothing richer
  // was persisted.
  const agentEntries = Object.entries(agentSignals).filter(([, v]) => v != null && v !== '');
  steps.push({
    step: 2,
    name: 'Agent Decisions',
    icon: 'smart_toy',
    color: '#8b5cf6',
    data: agentEntries.length > 0
      ? Object.fromEntries(agentEntries)
      : Object.fromEntries(['technical', 'sentiment', 'macro', 'pattern', 'rl'].map(a => [a, '—'])),
  });

  const mc = trade.marketContext ?? {};
  const ensembleData: Record<string, any> = {
    decision: trade.action,
    confidence: `${((trade.ensembleConfidence ?? 0) * 100).toFixed(1)}%`,
    gate: (trade.ensembleConfidence ?? 0) >= 0.60 ? 'PASSED (≥ 60%)' : 'FAILED (< 60%)',
  };
  // New ensemble fields — shown only when the pipeline persisted them.
  if (mc.regime) ensembleData.regime = String(mc.regime);
  if (mc.rawConfidence != null) ensembleData['raw → calibrated'] = `${(mc.rawConfidence * 100).toFixed(0)}% → ${((trade.ensembleConfidence ?? 0) * 100).toFixed(0)}%`;
  if (mc.metaWinProbability != null) ensembleData['meta win prob'] = `${(mc.metaWinProbability * 100).toFixed(0)}%`;
  steps.push({
    step: 3,
    name: 'Ensemble Vote',
    icon: 'how_to_vote',
    color: '#f59e0b',
    data: ensembleData,
  });

  const atr = trade.marketContext?.atr ?? 0;
  const stopLoss   = trade.action === 'BUY' ? trade.entryPrice - atr * 2 : trade.entryPrice + atr * 2;
  const takeProfit = trade.action === 'BUY' ? trade.entryPrice + atr * 3 : trade.entryPrice - atr * 3;
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
      'Fill Price': `₹${trade.entryPrice?.toFixed(2)}`,
      'Mode': trade.tradeSource ?? (trade.paperTrade ? 'PAPER' : 'LIVE'),
      'Status': 'FILLED',
      'Duration': trade.durationMinutes ? `${trade.durationMinutes} min` : '—',
    },
  });

  steps.push({
    step: 6,
    name: 'Trade Outcome',
    icon: trade.pnlPct != null && trade.pnlPct >= 0 ? 'trending_up' : 'trending_down',
    color: trade.pnlPct != null && trade.pnlPct >= 0 ? '#22c55e' : '#ef4444',
    data: {
      'Exit Price': trade.exitPrice ? `₹${trade.exitPrice.toFixed(2)}` : '—',
      'P&L': trade.pnlAbs != null ? `₹${trade.pnlAbs.toFixed(2)}` : '—',
      'P&L %': trade.pnlPct != null ? `${(trade.pnlPct * 100).toFixed(2)}%` : '—',
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
  // Full ensemble roster
  gbm: '#14b8a6', regime: '#a855f7', anomaly: '#ec4899', momentum: '#eab308',
  memory: '#64748b', meanrev: '#f97316', volatility: '#ef4444',
};
const ACTION_COLOR: Record<string, string> = {
  BUY: '#22c55e', SELL: '#ef4444', HOLD: '#f59e0b',
};

// ── Trade chart block: thin wrapper over the shared TradingChart ──────────────
// Shows the trade's day; if the trade belongs to a session, all the session's
// trades are passed so the whole session's entry/exit markers are drawn.
function TradeChartBlock({ trade, allTrades = [] }: { trade: TradeRecord; allTrades?: TradeRecord[] }) {
  const { theme } = useAppStore();
  const date = trade.timestampOpen ? trade.timestampOpen.slice(0, 10) : '';
  const sessionId = trade.marketContext?.sessionId;
  const sessionTrades = sessionId
    ? allTrades.filter(t => t.marketContext?.sessionId === sessionId && (t.timestampOpen || '').slice(0, 10) === date)
    : [trade];

  return (
    <div style={{ border: '1px solid var(--nd-border)', borderRadius: 10, overflow: 'hidden' }}>
      <TradingChart symbol={trade.symbol} date={date} trades={sessionTrades} height={300} isDark={theme === 'dark'} />
    </div>
  );
}

const FField: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <div style={{ display: 'flex', flexDirection: 'column', gap: 5, minWidth: 0 }}>
    <label style={{ fontSize: 11, fontWeight: 500, color: 'var(--nd-text-3)' }}>{label}</label>
    {children}
  </div>
);

function ExecutionModal({ trade, allTrades = [], onClose }: { trade: TradeRecord; allTrades?: TradeRecord[]; onClose: () => void }) {
  const steps = buildExecutionSteps(trade);
  const sessionId = trade.marketContext?.sessionId;
  const sessionCount = sessionId
    ? allTrades.filter(t => t.marketContext?.sessionId === sessionId).length
    : 1;

  return (
    <div
      style={{ position: 'fixed', inset: 0, background: '#00000080', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 12 }}
      onClick={e => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div style={{ background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderRadius: 16, width: '100%', maxWidth: 680, maxHeight: '90vh', overflow: 'hidden', display: 'flex', flexDirection: 'column', boxShadow: '0 24px 64px #00000060' }}>

        {/* Header */}
        <div style={{ padding: '18px 24px', borderBottom: '1px solid var(--nd-border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <span style={{ fontSize: 18, fontWeight: 700, color: 'var(--nd-text-1)' }}>{trade.symbol}</span>
              <span style={{ padding: '2px 10px', borderRadius: 6, fontSize: 12, fontWeight: 700, background: `${ACTION_COLOR[trade.action] ?? '#888'}20`, color: ACTION_COLOR[trade.action] ?? 'var(--nd-text-1)' }}>{trade.action}</span>
              <span style={{ padding: '2px 8px', borderRadius: 4, fontSize: 10, fontWeight: 600, background: 'var(--nd-surface)', color: 'var(--nd-text-3)', border: '1px solid var(--nd-border)' }}>
                {trade.tradeSource ?? (trade.paperTrade ? 'PAPER' : 'LIVE')}
              </span>
            </div>
            <div style={{ fontSize: 12, color: 'var(--nd-text-3)', marginTop: 3 }}>
              Execution trace · {trade.timestampOpen ? new Date(trade.timestampOpen).toLocaleString() : '—'}
            </div>
          </div>
          <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 4 }}>
            <span className="material-icons" style={{ color: 'var(--nd-text-3)', fontSize: 20 }}>close</span>
          </button>
        </div>

        {/* Steps timeline */}
        <div style={{ overflowY: 'auto', padding: '20px 24px', display: 'flex', flexDirection: 'column', gap: 0 }}>

          {/* Price chart with entry/exit markers */}
          <div style={{ marginBottom: 20 }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--nd-text-2)', marginBottom: 8 }}>
              {sessionCount > 1 ? `Session execution · ${sessionCount} trades` : 'Price action · entry & exit'}
            </div>
            <TradeChartBlock trade={trade} allTrades={allTrades} />
          </div>

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
  const [portfolio, setPortfolio] = useState<PortfolioMetrics | null>(null);
  const [agentAcc,  setAgentAcc]  = useState<Record<string, AgentMetric> | null>(null);
  const [selected, setSelected] = useState<TradeRecord | null>(null);
  const [filter,   setFilter]   = useState<'ALL' | 'LIVE' | 'PAPER' | 'BACKTEST' | 'REPLAY'>('ALL');
  const [sortKey,  setSortKey]  = useState<string>('created');
  const [sortDir,  setSortDir]  = useState<'asc' | 'desc'>('desc');
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [lessons,     setLessons]     = useState<any[]>([]);
  const [postmortems, setPostmortems] = useState<any[]>([]);
  const [lossBusy,    setLossBusy]    = useState(false);

  const runLossLearning = async () => {
    setLossBusy(true);
    try {
      await apiService.runLossLearning();
      const [l, p] = await Promise.all([apiService.getLossLessons(), apiService.getLossPostmortems(50)]);
      setLessons(l.data?.lessons ?? []);
      setPostmortems(p.data?.items ?? []);
    } catch (e) { console.error('loss-learning failed', e); }
    finally { setLossBusy(false); }
  };

  // Column filters
  const [showFilters,  setShowFilters]  = useState(false);
  const [fSymbol,      setFSymbol]      = useState('');
  const [fPnl,         setFPnl]         = useState<'ALL' | 'PROFIT' | 'LOSS'>('ALL');
  const [fMinTrades,   setFMinTrades]   = useState('');
  const [fMinWin,      setFMinWin]      = useState('');
  const [fTradeFrom,   setFTradeFrom]   = useState('');
  const [fTradeTo,     setFTradeTo]     = useState('');
  const [fCreatedFrom, setFCreatedFrom] = useState('');
  const [fCreatedTo,   setFCreatedTo]   = useState('');

  // Load stats/portfolio/agents once; reload trades whenever the source filter changes.
  useEffect(() => {
    const loadOnce = async () => {
      const [statsRes, pmRes, aaRes, lessRes, pmortemRes] = await Promise.allSettled([
        apiService.getFeedbackStats(),
        apiService.getPortfolioMetrics(),
        apiService.getAgentAccuracy(20),
        apiService.getLossLessons(),
        apiService.getLossPostmortems(50),
      ]);
      if (statsRes.status === 'fulfilled') setStats(statsRes.value);
      if (pmRes.status === 'fulfilled' && pmRes.value && !pmRes.value.error) setPortfolio(pmRes.value as PortfolioMetrics);
      if (aaRes.status === 'fulfilled' && aaRes.value?.agentAccuracy) setAgentAcc(aaRes.value.agentAccuracy as Record<string, AgentMetric>);
      if (lessRes.status === 'fulfilled') setLessons(lessRes.value?.data?.lessons ?? []);
      if (pmortemRes.status === 'fulfilled') setPostmortems(pmortemRes.value?.data?.items ?? []);
      setLoading(false);
    };
    loadOnce().catch(e => { setError(e.message); setLoading(false); });
  }, []);

  useEffect(() => {
    setLoading(true);
    apiService.getFeedbackTrades(filter === 'ALL' ? undefined : filter).then(data => {
      setTrades(Array.isArray(data) ? data : data.trades ?? []);
    }).catch(() => {}).finally(() => setLoading(false));
  }, [filter]);

  // Server already filters by source — trades array is already the right subset.
  const filteredTrades = trades;

  // ── Group trades into sessions ────────────────────────────────────────────
  // Trades sharing a session id collapse into one row; trades without a session
  // (e.g. an individual live order) stand alone as a single-trade session.
  const groups = new Map<string, TradeRecord[]>();
  for (const t of filteredTrades) {
    const sid = t.marketContext?.sessionId;
    const key = sid ? `s:${sid}` : `t:${t.tradeId}`;
    (groups.get(key) ?? groups.set(key, []).get(key)!).push(t);
  }
  const sessions = Array.from(groups.entries()).map(([key, ts]) => {
    const trades = [...ts].sort((a, b) => new Date(a.timestampOpen).getTime() - new Date(b.timestampOpen).getTime());
    const totalPnl = trades.reduce((s, t) => s + (t.pnlAbs ?? 0), 0);
    const closed = trades.filter(t => t.outcome === 'WIN' || t.outcome === 'LOSS').length;
    const wins = trades.filter(t => (t.pnlAbs ?? 0) > 0).length;
    return {
      key, isSession: key.startsWith('s:'),
      symbol: trades[0].symbol,
      source: trades[0].tradeSource ?? (trades[0].paperTrade ? 'PAPER' : 'LIVE'),
      mode: (trades[0].marketContext?.sessionMode as string) || '',
      tradeDate: trades[0].timestampOpen ? new Date(trades[0].timestampOpen).getTime() : 0,
      createdAt: trades[0].createdAt ? new Date(trades[0].createdAt).getTime() : 0,
      count: trades.length, totalPnl, wins, closed, trades,
    };
  });

  type SGroup = typeof sessions[number];
  const SCOLS: { label: string; key: string; get: (s: SGroup) => string | number }[] = [
    { label: 'Symbol',     key: 'symbol',  get: s => s.symbol },
    { label: 'Mode',       key: 'mode',    get: s => s.source },
    { label: 'Trades',     key: 'trades',  get: s => s.count },
    { label: 'P&L',        key: 'pnl',     get: s => s.totalPnl },
    { label: 'Win %',      key: 'win',     get: s => (s.closed ? s.wins / s.closed : 0) },
    { label: 'Trade Date', key: 'opened',  get: s => s.tradeDate },
    { label: 'Created On', key: 'created', get: s => s.createdAt },
  ];
  const scol = SCOLS.find(c => c.key === sortKey) ?? SCOLS.find(c => c.key === 'created')!;
  const sortedSessions = [...sessions].sort((a, b) => {
    const va = scol.get(a), vb = scol.get(b);
    const cmp = typeof va === 'number' && typeof vb === 'number' ? va - vb : String(va).localeCompare(String(vb));
    return sortDir === 'asc' ? cmp : -cmp;
  });

  const onSort = (key: string) => {
    if (key === sortKey) setSortDir(d => (d === 'asc' ? 'desc' : 'asc'));
    else { setSortKey(key); setSortDir(key === 'symbol' || key === 'mode' ? 'asc' : 'desc'); }
  };
  const toggle = (key: string) => setExpanded(prev => {
    const n = new Set(prev); n.has(key) ? n.delete(key) : n.add(key); return n;
  });

  // Apply the per-column filters to the (already mode-filtered, sorted) sessions
  const DAY = 86_400_000;
  const visibleSessions = sortedSessions.filter(s => {
    if (fSymbol && !s.symbol.toLowerCase().includes(fSymbol.toLowerCase())) return false;
    if (fPnl === 'PROFIT' && s.totalPnl <= 0) return false;
    if (fPnl === 'LOSS'   && s.totalPnl >= 0) return false;
    if (fMinTrades && s.count < Number(fMinTrades)) return false;
    if (fMinWin && (!s.closed || (s.wins / s.closed * 100) < Number(fMinWin))) return false;
    if (fTradeFrom   && s.tradeDate && s.tradeDate < Date.parse(fTradeFrom)) return false;
    if (fTradeTo     && s.tradeDate && s.tradeDate > Date.parse(fTradeTo) + DAY) return false;
    if (fCreatedFrom && s.createdAt && s.createdAt < Date.parse(fCreatedFrom)) return false;
    if (fCreatedTo   && s.createdAt && s.createdAt > Date.parse(fCreatedTo) + DAY) return false;
    return true;
  });
  const filtersActive = !!(fSymbol || fPnl !== 'ALL' || fMinTrades || fMinWin || fTradeFrom || fTradeTo || fCreatedFrom || fCreatedTo);
  const clearFilters = () => { setFSymbol(''); setFPnl('ALL'); setFMinTrades(''); setFMinWin(''); setFTradeFrom(''); setFTradeTo(''); setFCreatedFrom(''); setFCreatedTo(''); };

  const totalTrades  = stats?.tradeStats?.reduce((s, r) => s + Number(r.count), 0) ?? 0;
  const winningTrades = stats?.tradeStats?.find(r => r.outcome === 'WIN')?.count ?? 0;
  const losingTrades  = stats?.tradeStats?.find(r => r.outcome === 'LOSS')?.count ?? 0;
  const winRate = totalTrades > 0 ? winningTrades / totalTrades : 0;

  const pnlColor = (v: number) => v >= 0 ? 'var(--nd-green)' : 'var(--nd-red)';

  const modeColor: Record<string, string> = {
    LIVE: '#22c55e', PAPER: '#f59e0b', BACKTEST: '#3b82f6', REPLAY: '#a855f7',
  };

  if (loading) return <div style={{ padding: 40, textAlign: 'center', color: 'var(--nd-text-3)' }}>Loading trade history...</div>;
  if (error)   return <div style={{ padding: 40, textAlign: 'center', color: 'var(--nd-red)' }}>Failed to connect: {error}</div>;

  return (
    <div className="nd-orders-page">
      <h2 style={{ margin: '0 0 6px', fontSize: 22, fontWeight: 700, color: 'var(--nd-text-1)' }}>Orders</h2>
      <p style={{ margin: '0 0 24px', color: 'var(--nd-text-3)', fontSize: 13 }}>
        Click any row to see the full execution trace. All modes (Live · Paper · Backtest) train the AI.
      </p>

      {/* Stats row */}
      {stats && (
        <div className="nd-orders-stats" style={{ gap: 12, marginBottom: 20 }}>
          {[
            { label: 'Total Trades',      value: totalTrades },
            { label: 'Win Rate',          value: `${(winRate * 100).toFixed(1)}%` },
            { label: 'Wins',              value: winningTrades },
            { label: 'Losses',            value: losingTrades },
            { label: 'Until Retrain',     value: `${stats.tradesSinceRetrain} / 500` },
          ].map(s => (
            <div key={s.label} style={{ background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 10, padding: '14px 16px' }}>
              <div style={{ fontSize: 11, color: 'var(--nd-text-3)', marginBottom: 4 }}>{s.label}</div>
              <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--nd-text-1)' }}>{s.value}</div>
            </div>
          ))}
        </div>
      )}

      {/* ── AI Loss Learning — why trades lost + lessons applied to future decisions ── */}
      <div style={{ background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 12, padding: '16px 18px', marginBottom: 20 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: lessons.length || postmortems.length ? 12 : 0 }}>
          <span className="material-icons" style={{ color: 'var(--nd-purple, #a78bfa)' }}>psychology_alt</span>
          <div style={{ flex: 1, minWidth: 220 }}>
            <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--nd-text-1)' }}>AI Loss Learning</div>
            <div style={{ fontSize: 12, color: 'var(--nd-text-3)', marginTop: 2 }}>
              The AI explains why losing trades lost, then applies those lessons to future decisions.
            </div>
          </div>
          <button onClick={runLossLearning} disabled={lossBusy}
            style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '8px 14px', borderRadius: 8, border: 'none',
              background: 'var(--nd-purple, #7c3aed)', color: '#fff', fontWeight: 600, fontSize: 13, cursor: lossBusy ? 'wait' : 'pointer' }}>
            <span className={`material-icons${lossBusy ? ' nd-spin' : ''}`} style={{ fontSize: 17 }}>{lossBusy ? 'autorenew' : 'insights'}</span>
            {lossBusy ? 'Analysing losses…' : 'Analyse recent losses'}
          </button>
        </div>

        {/* Lessons learned */}
        {lessons.length > 0 && (
          <div style={{ marginBottom: postmortems.length ? 14 : 0 }}>
            <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--nd-text-3)', letterSpacing: 0.5, marginBottom: 8 }}>LESSONS APPLIED TO FUTURE DECISIONS</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {lessons.map((l: any, i: number) => (
                <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: 10, padding: '8px 10px', background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderRadius: 8 }}>
                  <span className="material-icons" style={{ fontSize: 15, color: '#f59e0b' }}>error_outline</span>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--nd-text-1)' }}>{l.failureMode}</div>
                    <div style={{ fontSize: 11.5, color: 'var(--nd-text-2)', marginTop: 1 }}>{l.avoidWhen || l.lesson}</div>
                  </div>
                  <span style={{ fontSize: 11, color: 'var(--nd-text-3)', whiteSpace: 'nowrap' }}>{l.occurrences}× · avg {l.avgLossPct}%</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Recent per-trade post-mortems */}
        {postmortems.length > 0 && (
          <div>
            <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--nd-text-3)', letterSpacing: 0.5, marginBottom: 8 }}>RECENT LOSS POST-MORTEMS</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6, maxHeight: 280, overflow: 'auto' }}>
              {postmortems.slice(0, 25).map((p: any, i: number) => (
                <div key={i} style={{ padding: '8px 10px', background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderRadius: 8 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 3 }}>
                    <span style={{ fontWeight: 700, fontFamily: 'monospace', color: 'var(--nd-text-1)' }}>{p.symbol}</span>
                    <span style={{ fontSize: 10.5, color: ACTION_COLOR[p.action] ?? 'var(--nd-text-3)' }}>{p.action}</span>
                    <span style={{ fontSize: 11, color: 'var(--nd-red)' }}>{p.pnlPct != null ? `${p.pnlPct}%` : ''}</span>
                    <span style={{ marginLeft: 'auto', fontSize: 10.5, fontWeight: 700, color: '#f59e0b' }}>{p.failureMode}</span>
                  </div>
                  <div style={{ fontSize: 11.5, color: 'var(--nd-text-2)', lineHeight: 1.5 }}>{p.rootCause}</div>
                  {p.lesson && <div style={{ fontSize: 11, color: 'var(--nd-text-3)', marginTop: 2 }}><strong>Lesson:</strong> {p.lesson}</div>}
                </div>
              ))}
            </div>
          </div>
        )}

        {!lessons.length && !postmortems.length && !lossBusy && (
          <div style={{ fontSize: 12.5, color: 'var(--nd-text-3)', marginTop: 10 }}>
            No post-mortems yet. Click <strong>Analyse recent losses</strong> to have the AI explain why recent losing trades lost and extract lessons.
          </div>
        )}
      </div>

      {/* Agent weights */}
      {stats?.agentWeights && (
        <div style={{ background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 12, padding: '14px 18px', marginBottom: 20 }}>
          <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--nd-text-3)', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 10 }}>
            Adaptive Agent Weights (live)
          </div>
          <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap' }}>
            {Object.entries(stats.agentWeights).map(([agent, weight]) => (
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

      {/* Portfolio performance metrics */}
      {portfolio && portfolio.totalTrades > 0 && (
        <div style={{ background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 12, padding: '14px 18px', marginBottom: 20 }}>
          <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--nd-text-3)', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 12 }}>
            Portfolio Performance · {portfolio.totalTrades} closed trades
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(110px, 1fr))', gap: 12 }}>
            {[
              { label: 'Sharpe',       value: portfolio.sharpeRatio.toFixed(2),       good: portfolio.sharpeRatio >= 1 },
              { label: 'Sortino',      value: portfolio.sortinoRatio.toFixed(2),      good: portfolio.sortinoRatio >= 1 },
              { label: 'Calmar',       value: portfolio.calmarRatio.toFixed(2),       good: portfolio.calmarRatio >= 1 },
              { label: 'Max Drawdown', value: `${portfolio.maxDrawdownPct.toFixed(1)}%`, good: portfolio.maxDrawdownPct < 15 },
              { label: 'Total Return', value: `${portfolio.totalReturnPct >= 0 ? '+' : ''}${portfolio.totalReturnPct.toFixed(1)}%`, good: portfolio.totalReturnPct >= 0 },
              { label: 'Avg P&L/trade', value: `${portfolio.meanPnlPct >= 0 ? '+' : ''}${portfolio.meanPnlPct.toFixed(2)}%`, good: portfolio.meanPnlPct >= 0 },
            ].map(m => (
              <div key={m.label} style={{ textAlign: 'center' }}>
                <div style={{ fontSize: 11, color: 'var(--nd-text-3)', marginBottom: 4 }}>{m.label}</div>
                <div style={{ fontSize: 18, fontWeight: 700, color: m.good ? 'var(--nd-green)' : 'var(--nd-red)' }}>{m.value}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Per-agent accuracy (precision / recall / F1) */}
      {agentAcc && Object.values(agentAcc).some(a => a.f1 !== undefined) && (
        <div style={{ background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 12, padding: '14px 18px', marginBottom: 20 }}>
          <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--nd-text-3)', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 12 }}>
            Per-Agent Accuracy (closed trades)
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', minWidth: 520, borderCollapse: 'collapse', fontSize: 12 }}>
              <thead>
                <tr style={{ color: 'var(--nd-text-3)', textAlign: 'left' }}>
                  {['Agent', 'Accuracy', 'Precision', 'Recall', 'F1', 'Trades'].map(h => (
                    <th key={h} style={{ padding: '6px 10px', fontWeight: 500 }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {Object.entries(agentAcc).map(([agent, m]) => (
                  <tr key={agent} style={{ borderTop: '1px solid var(--nd-border)' }}>
                    <td style={{ padding: '7px 10px', fontWeight: 600, color: AGENT_COLORS[agent] ?? 'var(--nd-text-1)', textTransform: 'capitalize' }}>{agent}</td>
                    {m.f1 === undefined ? (
                      <td colSpan={5} style={{ padding: '7px 10px', color: 'var(--nd-text-3)', fontStyle: 'italic' }}>
                        insufficient data ({m.total ?? 0} trades)
                      </td>
                    ) : (
                      <>
                        <td style={{ padding: '7px 10px', fontWeight: 600, color: (m.accuracy ?? 0) >= 0.55 ? 'var(--nd-green)' : (m.accuracy ?? 0) >= 0.45 ? '#f59e0b' : 'var(--nd-red)' }}>{((m.accuracy ?? 0) * 100).toFixed(0)}%</td>
                        <td style={{ padding: '7px 10px', color: 'var(--nd-text-2)' }}>{((m.precision ?? 0) * 100).toFixed(0)}%</td>
                        <td style={{ padding: '7px 10px', color: 'var(--nd-text-2)' }}>{((m.recall ?? 0) * 100).toFixed(0)}%</td>
                        <td style={{ padding: '7px 10px', fontWeight: 600, color: 'var(--nd-text-1)' }}>{(m.f1 ?? 0).toFixed(2)}</td>
                        <td style={{ padding: '7px 10px', color: 'var(--nd-text-3)' }}>{m.totalTrades ?? 0}</td>
                      </>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Filter pills */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 14, flexWrap: 'wrap', alignItems: 'center' }}>
        {(['ALL', 'LIVE', 'PAPER', 'BACKTEST', 'REPLAY'] as const).map(f => (
          <button key={f} onClick={() => setFilter(f)}
            style={{ padding: '5px 14px', borderRadius: 20, border: '1px solid var(--nd-border)', cursor: 'pointer', fontSize: 12, fontWeight: 500, transition: 'all 0.15s',
              background: filter === f ? (modeColor[f] ?? 'var(--nd-accent)') : 'var(--nd-surface)',
              color: filter === f ? '#fff' : 'var(--nd-text-2)',
              borderColor: filter === f ? (modeColor[f] ?? 'var(--nd-accent)') : 'var(--nd-border)',
            }}>
            {f}
          </button>
        ))}
        <button onClick={() => setShowFilters(v => !v)}
          style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '5px 12px', borderRadius: 20, border: `1px solid ${filtersActive ? 'var(--nd-green)' : 'var(--nd-border)'}`, cursor: 'pointer', fontSize: 12, fontWeight: 500, background: 'var(--nd-surface)', color: filtersActive ? 'var(--nd-green)' : 'var(--nd-text-2)' }}>
          <span className="material-icons" style={{ fontSize: 15 }}>tune</span>
          Filters{filtersActive ? ' •' : ''}
        </button>
        {filtersActive && (
          <button onClick={clearFilters} style={{ padding: '5px 10px', borderRadius: 20, border: '1px solid var(--nd-border)', cursor: 'pointer', fontSize: 12, background: 'var(--nd-surface)', color: 'var(--nd-text-3)' }}>Clear</button>
        )}
        <span style={{ marginLeft: 4, fontSize: 12, color: 'var(--nd-text-3)', alignSelf: 'center' }}>
          {visibleSessions.length} session{visibleSessions.length !== 1 ? 's' : ''}
        </span>
      </div>

      {/* Column filters panel */}
      {showFilters && (
        <div style={{ background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 12, padding: 16, marginBottom: 14 }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12 }}>
            <FField label="Symbol">
              <input className="nd-input" placeholder="e.g. SBIN" value={fSymbol} onChange={e => setFSymbol(e.target.value)} style={{ width: '100%', boxSizing: 'border-box' }} />
            </FField>
            <FField label="P&L">
              <select className="nd-select" value={fPnl} onChange={e => setFPnl(e.target.value as any)} style={{ width: '100%', boxSizing: 'border-box' }}>
                <option value="ALL">All</option><option value="PROFIT">Profit only</option><option value="LOSS">Loss only</option>
              </select>
            </FField>
            <FField label="Min trades">
              <input className="nd-input" type="number" inputMode="numeric" placeholder="0" value={fMinTrades} onChange={e => setFMinTrades(e.target.value)} style={{ width: '100%', boxSizing: 'border-box' }} />
            </FField>
            <FField label="Min win %">
              <input className="nd-input" type="number" inputMode="numeric" placeholder="0" value={fMinWin} onChange={e => setFMinWin(e.target.value)} style={{ width: '100%', boxSizing: 'border-box' }} />
            </FField>
            <FField label="Trade date from">
              <input className="nd-input" type="date" value={fTradeFrom} onChange={e => setFTradeFrom(e.target.value)} style={{ width: '100%', boxSizing: 'border-box' }} />
            </FField>
            <FField label="Trade date to">
              <input className="nd-input" type="date" value={fTradeTo} onChange={e => setFTradeTo(e.target.value)} style={{ width: '100%', boxSizing: 'border-box' }} />
            </FField>
            <FField label="Created from">
              <input className="nd-input" type="date" value={fCreatedFrom} onChange={e => setFCreatedFrom(e.target.value)} style={{ width: '100%', boxSizing: 'border-box' }} />
            </FField>
            <FField label="Created to">
              <input className="nd-input" type="date" value={fCreatedTo} onChange={e => setFCreatedTo(e.target.value)} style={{ width: '100%', boxSizing: 'border-box' }} />
            </FField>
          </div>
        </div>
      )}

      {/* Trade list */}
      {filteredTrades.length === 0 ? (
        <div style={{ background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 12, padding: 48, textAlign: 'center', color: 'var(--nd-text-3)' }}>
          No trades yet. Trades from Live, Paper, and Backtest modes all appear here.
        </div>
      ) : (
        <div style={{ background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 12, overflow: 'hidden' }}>
          <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' as any }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13, minWidth: 820 }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--nd-border)', background: 'var(--nd-bg)' }}>
                <th style={{ width: 34 }} />
                {SCOLS.map(c => (
                  <th key={c.key} onClick={() => onSort(c.key)}
                    style={{ padding: '10px 14px', textAlign: 'left', color: sortKey === c.key ? 'var(--nd-text-1)' : 'var(--nd-text-3)', fontWeight: 600, fontSize: 11, cursor: 'pointer', whiteSpace: 'nowrap', userSelect: 'none' }}>
                    {c.label}
                    <span style={{ marginLeft: 4, opacity: sortKey === c.key ? 1 : 0.25 }}>
                      {sortKey === c.key ? (sortDir === 'asc' ? '▲' : '▼') : '↕'}
                    </span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {visibleSessions.map(s => {
                const open = expanded.has(s.key);
                const fmt = (ms: number) => ms ? new Date(ms).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—';
                return (
                  <React.Fragment key={s.key}>
                    {/* Session header row — row click opens the modal; only the arrow toggles expand */}
                    <tr onClick={() => setSelected(s.trades[0])}
                      style={{ borderBottom: '1px solid var(--nd-border)', cursor: 'pointer', background: open ? 'var(--nd-bg)' : 'transparent', transition: 'background 0.1s' }}
                      onMouseEnter={e => (e.currentTarget.style.background = 'var(--nd-bg)')}
                      onMouseLeave={e => (e.currentTarget.style.background = open ? 'var(--nd-bg)' : 'transparent')}>
                      <td onClick={e => { e.stopPropagation(); toggle(s.key); }}
                        title={open ? 'Collapse' : 'Expand trades'}
                        style={{ padding: '10px 0 10px 14px', color: 'var(--nd-text-3)', cursor: 'pointer' }}>
                        <span className="material-icons" style={{ fontSize: 18, transition: 'transform 0.15s', transform: open ? 'rotate(90deg)' : 'none' }}>chevron_right</span>
                      </td>
                      <td style={{ padding: '10px 14px', fontWeight: 700, color: 'var(--nd-text-1)' }}>{s.symbol}</td>
                      <td style={{ padding: '10px 14px' }}>
                        <span style={{ fontSize: 10, padding: '2px 7px', borderRadius: 4, fontWeight: 600, background: `${modeColor[s.source] ?? '#888'}18`, color: modeColor[s.source] ?? 'var(--nd-text-3)' }}>{s.source}</span>
                      </td>
                      <td style={{ padding: '10px 14px', color: 'var(--nd-text-2)' }}>{s.count}{s.isSession ? ' · session' : ''}</td>
                      <td style={{ padding: '10px 14px', fontWeight: 600, color: pnlColor(s.totalPnl) }}>₹{s.totalPnl.toFixed(2)}</td>
                      <td style={{ padding: '10px 14px', color: 'var(--nd-text-2)' }}>{s.closed ? `${Math.round(s.wins / s.closed * 100)}% (${s.wins}/${s.closed})` : '—'}</td>
                      <td style={{ padding: '10px 14px', color: 'var(--nd-text-3)', fontSize: 11 }}>{fmt(s.tradeDate)}</td>
                      <td style={{ padding: '10px 14px', color: 'var(--nd-text-3)', fontSize: 11 }}>{fmt(s.createdAt)}</td>
                    </tr>

                    {/* Expanded: the session's trade rows only (chart lives in the modal) */}
                    {open && (
                      <tr style={{ background: 'var(--nd-bg)' }}>
                        <td colSpan={SCOLS.length + 1} style={{ padding: '8px 16px 16px' }}>
                          <div style={{ overflowX: 'auto' }}>
                            <table style={{ width: '100%', minWidth: 560, borderCollapse: 'collapse', fontSize: 12 }}>
                              <thead>
                                <tr style={{ color: 'var(--nd-text-3)', textAlign: 'left' }}>
                                  {['Time', 'Action', 'Entry', 'Exit', 'P&L', 'P&L %', 'Conf', 'Outcome'].map(h => (
                                    <th key={h} style={{ padding: '6px 10px', fontWeight: 500 }}>{h}</th>
                                  ))}
                                </tr>
                              </thead>
                              <tbody>
                                {s.trades.map(t => (
                                  <tr key={t.tradeId} style={{ borderTop: '1px solid var(--nd-border)' }}>
                                    <td style={{ padding: '7px 10px', color: 'var(--nd-text-3)' }}>{t.timestampOpen ? new Date(t.timestampOpen).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }) : '—'}</td>
                                    <td style={{ padding: '7px 10px' }}><span style={{ fontWeight: 700, color: ACTION_COLOR[t.action] ?? 'var(--nd-text-1)' }}>{t.action}</span></td>
                                    <td style={{ padding: '7px 10px', color: 'var(--nd-text-1)' }}>₹{t.entryPrice?.toFixed(2)}</td>
                                    <td style={{ padding: '7px 10px', color: 'var(--nd-text-2)' }}>{t.exitPrice ? `₹${t.exitPrice.toFixed(2)}` : '—'}</td>
                                    <td style={{ padding: '7px 10px', fontWeight: 600, color: pnlColor(t.pnlAbs ?? 0) }}>{t.pnlAbs != null ? `₹${t.pnlAbs.toFixed(2)}` : '—'}</td>
                                    <td style={{ padding: '7px 10px', fontWeight: 600, color: pnlColor(t.pnlPct ?? 0) }}>{t.pnlPct != null ? `${(t.pnlPct * 100).toFixed(2)}%` : '—'}</td>
                                    <td style={{ padding: '7px 10px', color: 'var(--nd-text-2)' }}>{t.ensembleConfidence ? `${(t.ensembleConfidence * 100).toFixed(0)}%` : '—'}</td>
                                    <td style={{ padding: '7px 10px' }}><span style={{ fontWeight: 600, color: t.outcome === 'WIN' ? 'var(--nd-green)' : t.outcome === 'LOSS' ? 'var(--nd-red)' : 'var(--nd-text-3)' }}>{t.outcome ?? 'OPEN'}</span></td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
          </div>
        </div>
      )}

      {/* Execution preview modal */}
      {selected && <ExecutionModal trade={selected} allTrades={trades} onClose={() => setSelected(null)} />}
    </div>
  );
};

export default Orders;
