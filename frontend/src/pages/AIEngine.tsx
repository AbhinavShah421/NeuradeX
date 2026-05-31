import React, { useState, useEffect, useCallback } from 'react';
import { useAppStore } from '../stores/appStore';
import apiService from '../services/api';

// ── Types ──────────────────────────────────────────────────────────────────────

interface AgentResult {
  agentName: string;
  action: 'BUY' | 'SELL' | 'HOLD';
  confidence: number;
  weight: number;
  reasoning: string;
  indicators: Record<string, any>;
}

interface AnalysisResult {
  predictionId: string;
  action: 'BUY' | 'SELL' | 'HOLD';
  confidence: number;
  agentAgreement: number;
  riskScore: number;
  reasoning: string;
  timestamp: string;
  agents: AgentResult[];
  rlState: number | null;
}

interface AgentPerf {
  agent: string;
  weight: number;
  total: number;
  correct: number;
  accuracy: number;
  totalReward: number;
}

interface PredictionHistory {
  predictionId: string;
  symbol: string;
  candleTime: string;
  finalAction: string;
  finalConfidence: number;
  agentAgreement: number;
  riskScore: number;
  createdAt: string;
  pnl: number | null;
  pnlPct: number | null;
  outcome: string | null;
  reward: number | null;
}

// ── Constants ─────────────────────────────────────────────────────────────────

const STOCKS = [
  'SBIN', 'RELIANCE', 'TCS', 'INFY', 'HDFCBANK', 'ICICIBANK',
  'BAJFINANCE', 'WIPRO', 'TATAMOTORS', 'KOTAKBANK', 'MARUTI',
  'SUNPHARMA', 'INDUSINDBK', 'PNB', 'FEDERALBNK', 'SUZLON', 'IDBI',
];

const AGENT_ICONS: Record<string, string> = {
  technical:  'show_chart',
  pattern:    'candlestick_chart',
  momentum:   'speed',
  volatility: 'waves',
  sentiment:  'psychology',
  rl:         'smart_toy',
};

const AGENT_COLORS: Record<string, string> = {
  technical:  '#3b82f6',
  pattern:    '#8b5cf6',
  momentum:   '#f59e0b',
  volatility: '#ef4444',
  sentiment:  '#06b6d4',
  rl:         '#10b981',
};

const ACTION_COLOR: Record<string, string> = {
  BUY:  '#22c55e',
  SELL: '#ef4444',
  HOLD: '#f59e0b',
};

// ── Helpers ───────────────────────────────────────────────────────────────────

const pct  = (v: number) => `${(v * 100).toFixed(1)}%`;
const fmt2 = (v: number) => v.toFixed(2);

function ConfBar({ value, color }: { value: number; color: string }) {
  return (
    <div style={{ height: 4, borderRadius: 2, background: 'var(--nd-border)', overflow: 'hidden', marginTop: 4 }}>
      <div style={{ height: '100%', width: `${value * 100}%`, background: color, borderRadius: 2, transition: 'width 0.4s' }} />
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────

const AIEngine: React.FC = () => {
  const { theme: _theme } = useAppStore();
  void _theme; // consumed by nd-card/css var, no JS usage needed

  // Input state
  const [symbol, setSymbol]     = useState('SBIN');
  const [capital, setCapital]   = useState('50000');
  const [position, setPosition] = useState<'NONE' | 'LONG'>('NONE');
  const [candles, setCandles]   = useState<any[]>([]);
  const [loadingCandles, setLoadingCandles] = useState(false);

  // Results state
  const [analysis, setAnalysis]     = useState<AnalysisResult | null>(null);
  const [analyzing, setAnalyzing]   = useState(false);
  const [performance, setPerformance] = useState<AgentPerf[]>([]);
  const [history, setHistory]       = useState<PredictionHistory[]>([]);
  const [activeTab, setActiveTab]   = useState<'analyze' | 'performance' | 'history'>('analyze');

  // Outcome form
  const [outcomeOpen, setOutcomeOpen] = useState(false);
  const [entryPrice, setEntryPrice]   = useState('');
  const [exitPrice, setExitPrice]     = useState('');
  const [outcomeLoading, setOutcomeLoading] = useState(false);
  const [outcomeResult, setOutcomeResult]   = useState<{ reward: number } | null>(null);

  // ── Fetch historical intraday candles (works weekends — uses last trading day) ─

  const fetchCandles = useCallback(async (): Promise<any[]> => {
    setLoadingCandles(true);
    try {
      // Find the most recent weekday
      const d = new Date();
      while (d.getDay() === 0 || d.getDay() === 6) d.setDate(d.getDate() - 1);
      const date = d.toISOString().slice(0, 10);

      const r = await apiService.getIntradayCandles(symbol, date);
      const loaded: any[] = (r as any)?.data?.candles ?? (r as any)?.candles ?? [];
      if (loaded.length) setCandles(loaded);
      return loaded;
    } catch (err) {
      console.error('fetchCandles failed:', err);
      return [];
    } finally {
      setLoadingCandles(false);
    }
  }, [symbol]);

  // ── Run analysis ──────────────────────────────────────────────────────────────

  const runAnalysis = async () => {
    // Ensure candles are loaded — fetch if missing, then proceed immediately
    let activeCandles = candles;
    if (!activeCandles.length) {
      activeCandles = await fetchCandles();
      if (!activeCandles.length) {
        console.error('No candle data available for analysis');
        return;
      }
    }

    setAnalyzing(true);
    setOutcomeResult(null);
    try {
      const r = await apiService.aiEngineAnalyze({
        symbol, candles: activeCandles,
        capital: parseFloat(capital) || 50000,
        position,
        context: { symbol, position },
      });
      setAnalysis(r as unknown as AnalysisResult);
    } catch (err) {
      console.error('analyze failed:', err);
    } finally {
      setAnalyzing(false);
    }
  };

  // ── Load performance + history ────────────────────────────────────────────────

  const loadPerformance = useCallback(async () => {
    try {
      const r = await apiService.aiEnginePerformance();
      if (Array.isArray(r)) setPerformance(r as unknown as AgentPerf[]);
    } catch { /* ignore */ }
  }, []);

  const loadHistory = useCallback(async () => {
    try {
      const r = await apiService.aiEngineHistory(undefined, 30);
      if (Array.isArray(r)) setHistory(r as unknown as PredictionHistory[]);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    if (activeTab === 'performance') loadPerformance();
    if (activeTab === 'history')     loadHistory();
  }, [activeTab, loadPerformance, loadHistory]);

  // Auto-fetch candles on symbol change
  useEffect(() => { fetchCandles(); }, [symbol]);

  // ── Record outcome ────────────────────────────────────────────────────────────

  const submitOutcome = async () => {
    if (!analysis || !entryPrice || !exitPrice) return;
    setOutcomeLoading(true);
    try {
      const ep  = parseFloat(entryPrice);
      const xp  = parseFloat(exitPrice);
      const pnl = xp - ep;
      const pct = (pnl / ep) * 100;
      const r = await apiService.aiEngineOutcome({
        predictionId: analysis.predictionId,
        symbol,
        entryPrice: ep,
        exitPrice:  xp,
        pnl, pnlPct: pct,
      });
      setOutcomeResult(r as unknown as { reward: number });
      setOutcomeOpen(false);
      await loadPerformance();
    } catch { /* ignore */ } finally {
      setOutcomeLoading(false);
    }
  };

  // ── Card style helper ─────────────────────────────────────────────────────────

  const card = (extra?: React.CSSProperties): React.CSSProperties => ({
    background: 'var(--nd-surface)',
    border: '1px solid var(--nd-border)',
    borderRadius: 12,
    padding: 20,
    ...extra,
  });

  // ── Tab labels ────────────────────────────────────────────────────────────────

  const tabs: { id: 'analyze' | 'performance' | 'history'; label: string; icon: string }[] = [
    { id: 'analyze',     label: 'Live Analysis',   icon: 'psychology' },
    { id: 'performance', label: 'Agent Performance', icon: 'leaderboard' },
    { id: 'history',     label: 'Prediction History', icon: 'history' },
  ];

  return (
    <div>

      {/* ── Page Header ──────────────────────────────────────────────────────── */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, marginBottom: 20 }}>
        <div style={{ minWidth: 0 }}>
          <h1 style={{ fontSize: 19, fontWeight: 700, margin: 0, color: 'var(--nd-text-1)', display: 'flex', alignItems: 'center', gap: 8 }}>
            <span className="material-icons" style={{ color: 'var(--nd-green)', fontSize: 24, flexShrink: 0 }}>smart_toy</span>
            Multi-Agent AI Engine
          </h1>
          <p style={{ fontSize: 13, color: 'var(--nd-text-3)', margin: '4px 0 0' }}>
            6 parallel agents · Weighted ensemble voting · Self-learning Q-table
          </p>
        </div>
        {candles.length > 0 && (
          <div style={{ fontSize: 12, color: 'var(--nd-text-3)', background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 8, padding: '4px 10px', whiteSpace: 'nowrap', flexShrink: 0 }}>
            {candles.length} candles
          </div>
        )}
      </div>

      {/* ── Tabs ────────────────────────────────────────────────────────────────── */}
      <div className="nd-pill-tabs" style={{ marginBottom: 20 }}>
        {tabs.map(t => (
          <button key={t.id} onClick={() => setActiveTab(t.id)} className="nd-pill-tab"
            style={{
              background: activeTab === t.id ? 'var(--nd-green)' : 'transparent',
              color:      activeTab === t.id ? '#fff' : 'var(--nd-text-2)',
            }}>
            <span className="material-icons" style={{ fontSize: 15 }}>{t.icon}</span>
            {t.label}
          </button>
        ))}
      </div>

      {/* ════════════════════════════════════════════════════════════════════════
          TAB: Live Analysis
      ════════════════════════════════════════════════════════════════════════ */}
      {activeTab === 'analyze' && (
        <div className="nd-grid-sidebar">

          {/* ── Left column: controls ─────────────────────────────────────────── */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

            {/* Symbol + capital */}
            <div style={card()}>
              <h3 style={{ fontSize: 13, fontWeight: 600, color: 'var(--nd-text-2)', margin: '0 0 14px', textTransform: 'uppercase', letterSpacing: 1 }}>Setup</h3>

              <label style={{ fontSize: 12, color: 'var(--nd-text-3)', display: 'block', marginBottom: 4 }}>Symbol</label>
              <select value={symbol} onChange={e => setSymbol(e.target.value)}
                style={{ width: '100%', padding: '8px 10px', borderRadius: 8, border: '1px solid var(--nd-border)', background: 'var(--nd-bg)', color: 'var(--nd-text-1)', fontSize: 13, marginBottom: 12 }}>
                {STOCKS.map(s => <option key={s} value={s}>{s}</option>)}
              </select>

              <label style={{ fontSize: 12, color: 'var(--nd-text-3)', display: 'block', marginBottom: 4 }}>Capital (₹)</label>
              <input type="number" value={capital} onChange={e => setCapital(e.target.value)}
                style={{ width: '100%', padding: '8px 10px', borderRadius: 8, border: '1px solid var(--nd-border)', background: 'var(--nd-bg)', color: 'var(--nd-text-1)', fontSize: 13, marginBottom: 12, boxSizing: 'border-box' }} />

              <label style={{ fontSize: 12, color: 'var(--nd-text-3)', display: 'block', marginBottom: 4 }}>Current Position</label>
              <div style={{ display: 'flex', gap: 8 }}>
                {(['NONE', 'LONG'] as const).map(p => (
                  <button key={p} onClick={() => setPosition(p)}
                    style={{ flex: 1, padding: '7px 0', borderRadius: 8, border: '1px solid var(--nd-border)', cursor: 'pointer', fontSize: 12, fontWeight: 600, transition: 'all 0.15s',
                      background: position === p ? (p === 'LONG' ? '#22c55e22' : 'var(--nd-surface)') : 'transparent',
                      color: position === p ? (p === 'LONG' ? '#22c55e' : 'var(--nd-text-1)') : 'var(--nd-text-3)',
                      borderColor: position === p ? (p === 'LONG' ? '#22c55e' : 'var(--nd-border)') : 'var(--nd-border)' }}>
                    {p}
                  </button>
                ))}
              </div>
            </div>

            {/* Run button */}
            <button onClick={runAnalysis} disabled={analyzing || loadingCandles}
              style={{ width: '100%', padding: '13px 0', borderRadius: 10, border: 'none', cursor: 'pointer', fontSize: 14, fontWeight: 700, transition: 'all 0.2s',
                background: analyzing || loadingCandles ? 'var(--nd-border)' : 'var(--nd-green)',
                color: analyzing || loadingCandles ? 'var(--nd-text-3)' : '#fff',
                display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8 }}>
              <span className="material-icons" style={{ fontSize: 18 }}>
                {analyzing ? 'hourglass_empty' : loadingCandles ? 'download' : 'psychology'}
              </span>
              {analyzing ? 'Analysing...' : loadingCandles ? 'Loading candles...' : 'Run AI Analysis'}
            </button>

            {/* Record outcome */}
            {analysis && !outcomeOpen && (
              <button onClick={() => setOutcomeOpen(true)}
                style={{ width: '100%', padding: '10px 0', borderRadius: 10, border: '1px solid var(--nd-border)', cursor: 'pointer', fontSize: 13, fontWeight: 600, background: 'transparent', color: 'var(--nd-text-2)', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6 }}>
                <span className="material-icons" style={{ fontSize: 16 }}>rate_review</span>
                Record Trade Outcome
              </button>
            )}

            {outcomeOpen && (
              <div style={card()}>
                <h3 style={{ fontSize: 13, fontWeight: 600, color: 'var(--nd-text-2)', margin: '0 0 12px', textTransform: 'uppercase', letterSpacing: 1 }}>Trade Outcome</h3>
                <label style={{ fontSize: 12, color: 'var(--nd-text-3)', display: 'block', marginBottom: 4 }}>Entry Price</label>
                <input type="number" value={entryPrice} onChange={e => setEntryPrice(e.target.value)}
                  placeholder="e.g. 820.50"
                  style={{ width: '100%', padding: '8px 10px', borderRadius: 8, border: '1px solid var(--nd-border)', background: 'var(--nd-bg)', color: 'var(--nd-text-1)', fontSize: 13, marginBottom: 10, boxSizing: 'border-box' }} />
                <label style={{ fontSize: 12, color: 'var(--nd-text-3)', display: 'block', marginBottom: 4 }}>Exit Price</label>
                <input type="number" value={exitPrice} onChange={e => setExitPrice(e.target.value)}
                  placeholder="e.g. 835.00"
                  style={{ width: '100%', padding: '8px 10px', borderRadius: 8, border: '1px solid var(--nd-border)', background: 'var(--nd-bg)', color: 'var(--nd-text-1)', fontSize: 13, marginBottom: 12, boxSizing: 'border-box' }} />
                <div style={{ display: 'flex', gap: 8 }}>
                  <button onClick={submitOutcome} disabled={outcomeLoading}
                    style={{ flex: 1, padding: '9px 0', borderRadius: 8, border: 'none', cursor: 'pointer', background: '#22c55e', color: '#fff', fontSize: 13, fontWeight: 600 }}>
                    {outcomeLoading ? 'Saving...' : 'Submit'}
                  </button>
                  <button onClick={() => setOutcomeOpen(false)}
                    style={{ flex: 1, padding: '9px 0', borderRadius: 8, border: '1px solid var(--nd-border)', cursor: 'pointer', background: 'transparent', color: 'var(--nd-text-2)', fontSize: 13, fontWeight: 600 }}>
                    Cancel
                  </button>
                </div>
              </div>
            )}

            {outcomeResult && (
              <div style={{ ...card(), border: '1px solid #22c55e33', background: '#22c55e08', textAlign: 'center' }}>
                <div style={{ fontSize: 12, color: '#22c55e', fontWeight: 600 }}>Outcome recorded</div>
                <div style={{ fontSize: 20, fontWeight: 700, color: outcomeResult.reward >= 0 ? '#22c55e' : '#ef4444', marginTop: 4 }}>
                  Reward: {outcomeResult.reward > 0 ? '+' : ''}{outcomeResult.reward.toFixed(2)}
                </div>
                <div style={{ fontSize: 11, color: 'var(--nd-text-3)', marginTop: 2 }}>Agents updated</div>
              </div>
            )}
          </div>

          {/* ── Right column: results ────────────────────────────────────────────── */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

            {!analysis && !analyzing && (
              <div style={{ ...card({ textAlign: 'center', padding: 60 }) }}>
                <span className="material-icons" style={{ fontSize: 48, color: 'var(--nd-text-3)', display: 'block', marginBottom: 12 }}>smart_toy</span>
                <div style={{ fontSize: 14, color: 'var(--nd-text-3)' }}>Select a symbol and click "Run AI Analysis"</div>
                <div style={{ fontSize: 12, color: 'var(--nd-text-3)', marginTop: 4 }}>6 agents will analyse in parallel</div>
              </div>
            )}

            {analyzing && (
              <div style={{ ...card({ textAlign: 'center', padding: 60 }) }}>
                <div style={{ display: 'flex', justifyContent: 'center', gap: 8, marginBottom: 16 }}>
                  {Object.entries(AGENT_COLORS).map(([name, color]) => (
                    <div key={name} style={{ width: 10, height: 10, borderRadius: '50%', background: color, animation: 'pulse 1.2s ease-in-out infinite', animationDelay: `${Object.keys(AGENT_COLORS).indexOf(name) * 0.15}s` }} />
                  ))}
                </div>
                <div style={{ fontSize: 14, color: 'var(--nd-text-2)', fontWeight: 500 }}>Running 6 agents in parallel...</div>
              </div>
            )}

            {analysis && !analyzing && (
              <>
                {/* Ensemble Decision */}
                <div style={{ ...card(), background: `${ACTION_COLOR[analysis.action]}0d`, border: `1px solid ${ACTION_COLOR[analysis.action]}33` }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
                    <div>
                      <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--nd-text-3)', textTransform: 'uppercase', letterSpacing: 1 }}>Ensemble Decision</div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 6 }}>
                        <span style={{ fontSize: 30, fontWeight: 800, color: ACTION_COLOR[analysis.action] }}>{analysis.action}</span>
                        <span style={{ fontSize: 14, color: 'var(--nd-text-2)' }}>{pct(analysis.confidence)} confidence</span>
                      </div>
                    </div>
                    <div style={{ textAlign: 'right' }}>
                      <div style={{ fontSize: 11, color: 'var(--nd-text-3)', marginBottom: 4 }}>Risk Score</div>
                      <div style={{ fontSize: 22, fontWeight: 700, color: analysis.riskScore > 0.6 ? '#ef4444' : analysis.riskScore > 0.35 ? '#f59e0b' : '#22c55e' }}>
                        {fmt2(analysis.riskScore * 100)}
                      </div>
                    </div>
                  </div>

                  {/* Metric pills */}
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    {[
                      { label: 'Agreement', value: pct(analysis.agentAgreement) },
                      { label: 'Symbol', value: symbol },
                      { label: 'Candles', value: String(candles.length) },
                    ].map(m => (
                      <div key={m.label} style={{ background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 6, padding: '4px 10px', fontSize: 12 }}>
                        <span style={{ color: 'var(--nd-text-3)' }}>{m.label}: </span>
                        <span style={{ fontWeight: 600, color: 'var(--nd-text-1)' }}>{m.value}</span>
                      </div>
                    ))}
                  </div>

                  <div style={{ fontSize: 12, color: 'var(--nd-text-2)', marginTop: 12, fontStyle: 'italic' }}>{analysis.reasoning}</div>
                </div>

                {/* Agent Cards Grid */}
                <div className="nd-grid-3" style={{ gap: 12 }}>
                  {analysis.agents.map((ag) => {
                    const color = AGENT_COLORS[ag.agentName] || '#767676';
                    const icon  = AGENT_ICONS[ag.agentName]  || 'auto_awesome';
                    return (
                      <div key={ag.agentName} style={{ ...card({ padding: 14 }), borderLeft: `3px solid ${color}` }}>
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                            <span className="material-icons" style={{ fontSize: 16, color }}>{icon}</span>
                            <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--nd-text-1)', textTransform: 'capitalize' }}>{ag.agentName}</span>
                          </div>
                          <span style={{ fontSize: 11, fontWeight: 700, color: ACTION_COLOR[ag.action], background: `${ACTION_COLOR[ag.action]}18`, padding: '2px 6px', borderRadius: 4 }}>
                            {ag.action}
                          </span>
                        </div>

                        <div style={{ fontSize: 20, fontWeight: 800, color, marginBottom: 2 }}>{pct(ag.confidence)}</div>
                        <ConfBar value={ag.confidence} color={color} />

                        <div style={{ fontSize: 11, color: 'var(--nd-text-3)', marginTop: 8, lineHeight: 1.4 }}>{ag.reasoning}</div>

                        <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 8, fontSize: 11 }}>
                          <span style={{ color: 'var(--nd-text-3)' }}>Weight</span>
                          <span style={{ fontWeight: 600, color: ag.weight >= 1 ? '#22c55e' : '#f59e0b' }}>{ag.weight.toFixed(2)}×</span>
                        </div>

                        {/* Key indicator */}
                        {ag.agentName === 'technical' && ag.indicators.rsi !== undefined && (
                          <div style={{ marginTop: 6, fontSize: 11, color: 'var(--nd-text-3)' }}>
                            RSI <b style={{ color: 'var(--nd-text-1)' }}>{String(ag.indicators.rsi)}</b>
                            {' '} MACD <b style={{ color: 'var(--nd-text-1)' }}>{String(ag.indicators.macd)}</b>
                          </div>
                        )}
                        {ag.agentName === 'volatility' && ag.indicators.atrPct !== undefined && (
                          <div style={{ marginTop: 6, fontSize: 11, color: 'var(--nd-text-3)' }}>
                            ATR <b style={{ color: 'var(--nd-text-1)' }}>{fmt2(Number(ag.indicators.atrPct))}%</b>
                            {' '} <span style={{ color: ag.indicators.regime === 'high_volatility' ? '#ef4444' : ag.indicators.regime === 'moderate_volatility' ? '#f59e0b' : '#22c55e' }}>{ag.indicators.regime ?? ''}</span>
                          </div>
                        )}
                        {ag.agentName === 'rl' && ag.indicators.state !== undefined && (
                          <div style={{ marginTop: 6, fontSize: 11, color: 'var(--nd-text-3)' }}>
                            State <b style={{ color: 'var(--nd-text-1)' }}>{ag.indicators.state}</b>
                            {' '} Q[B/S/H] {[ag.indicators.qBuy, ag.indicators.qSell, ag.indicators.qHold].map(v => fmt2(Number(v ?? 0))).join('/')}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {/* ════════════════════════════════════════════════════════════════════════
          TAB: Agent Performance
      ════════════════════════════════════════════════════════════════════════ */}
      {activeTab === 'performance' && (
        <div>
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 12 }}>
            <button onClick={loadPerformance} style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '7px 14px', borderRadius: 8, border: '1px solid var(--nd-border)', background: 'var(--nd-surface)', cursor: 'pointer', fontSize: 12, color: 'var(--nd-text-2)' }}>
              <span className="material-icons" style={{ fontSize: 14 }}>refresh</span>
              Refresh
            </button>
          </div>

          {performance.length === 0 ? (
            <div style={{ ...card({ textAlign: 'center', padding: 48 }) }}>
              <span className="material-icons" style={{ fontSize: 40, color: 'var(--nd-text-3)', display: 'block' }}>leaderboard</span>
              <div style={{ color: 'var(--nd-text-3)', marginTop: 8, fontSize: 13 }}>No performance data yet — run some analyses and record outcomes</div>
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              {performance.map((p) => {
                const color   = AGENT_COLORS[p.agent] || '#767676';
                const icon    = AGENT_ICONS[p.agent]  || 'auto_awesome';
                const maxW    = Math.max(...performance.map(x => x.weight), 2.5);
                return (
                  <div key={p.agent} style={card({ padding: '14px 16px' })}>
                    {/* Top row: icon + name + stats */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
                      <div style={{ width: 38, height: 38, borderRadius: '50%', background: `${color}22`, border: `2px solid ${color}`, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                        <span className="material-icons" style={{ fontSize: 18, color }}>{icon}</span>
                      </div>
                      <div style={{ flex: '1 1 80px', minWidth: 0 }}>
                        <div style={{ fontWeight: 700, fontSize: 13, color: 'var(--nd-text-1)', textTransform: 'capitalize' }}>{p.agent}</div>
                        <div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>{p.total} predictions</div>
                      </div>
                      <div style={{ display: 'flex', gap: 16, marginLeft: 'auto', flexShrink: 0 }}>
                        <div style={{ textAlign: 'center' }}>
                          <div style={{ fontSize: 18, fontWeight: 800, color: p.accuracy >= 0.55 ? '#22c55e' : p.accuracy >= 0.45 ? '#f59e0b' : '#ef4444' }}>
                            {p.total > 0 ? `${(p.accuracy * 100).toFixed(0)}%` : '–'}
                          </div>
                          <div style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>Accuracy</div>
                        </div>
                        <div style={{ textAlign: 'center' }}>
                          <div style={{ fontSize: 15, fontWeight: 700, color: p.totalReward >= 0 ? '#22c55e' : '#ef4444' }}>
                            {p.totalReward > 0 ? '+' : ''}{p.totalReward.toFixed(2)}
                          </div>
                          <div style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>Reward</div>
                        </div>
                      </div>
                    </div>
                    {/* Weight bar */}
                    <div style={{ marginTop: 10 }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--nd-text-3)', marginBottom: 4 }}>
                        <span>Weight</span>
                        <span style={{ fontWeight: 700, color: p.weight >= 1.0 ? '#22c55e' : p.weight >= 0.7 ? '#f59e0b' : '#ef4444' }}>{p.weight.toFixed(3)}×</span>
                      </div>
                      <div style={{ height: 5, borderRadius: 3, background: 'var(--nd-border)', overflow: 'hidden' }}>
                        <div style={{ height: '100%', width: `${(p.weight / maxW) * 100}%`, background: color, borderRadius: 3 }} />
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* ════════════════════════════════════════════════════════════════════════
          TAB: Prediction History
      ════════════════════════════════════════════════════════════════════════ */}
      {activeTab === 'history' && (
        <div>
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 12 }}>
            <button onClick={loadHistory} style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '7px 14px', borderRadius: 8, border: '1px solid var(--nd-border)', background: 'var(--nd-surface)', cursor: 'pointer', fontSize: 12, color: 'var(--nd-text-2)' }}>
              <span className="material-icons" style={{ fontSize: 14 }}>refresh</span>
              Refresh
            </button>
          </div>

          {history.length === 0 ? (
            <div style={{ ...card({ textAlign: 'center', padding: 48 }) }}>
              <span className="material-icons" style={{ fontSize: 40, color: 'var(--nd-text-3)', display: 'block' }}>history</span>
              <div style={{ color: 'var(--nd-text-3)', marginTop: 8, fontSize: 13 }}>No predictions recorded yet</div>
            </div>
          ) : (
            <div style={{ ...card({ padding: 0, overflow: 'hidden' }) }}>
              <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' as any }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12, minWidth: 640 }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--nd-border)', background: 'var(--nd-bg)' }}>
                    {['Time', 'Symbol', 'Action', 'Confidence', 'Agreement', 'Risk', 'Outcome', 'P&L %', 'Reward'].map(h => (
                      <th key={h} style={{ padding: '10px 14px', textAlign: 'left', fontSize: 11, fontWeight: 600, color: 'var(--nd-text-3)', textTransform: 'uppercase', letterSpacing: 0.5 }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {history.map((row, i) => (
                    <tr key={row.predictionId} style={{ borderBottom: i < history.length - 1 ? '1px solid var(--nd-border)' : 'none', transition: 'background 0.1s' }}
                      onMouseEnter={e => (e.currentTarget.style.background = 'var(--nd-surface)')}
                      onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
                      <td style={{ padding: '10px 14px', color: 'var(--nd-text-3)' }}>{row.candleTime || row.createdAt?.slice(11, 16)}</td>
                      <td style={{ padding: '10px 14px', fontWeight: 700, color: 'var(--nd-text-1)' }}>{row.symbol}</td>
                      <td style={{ padding: '10px 14px' }}>
                        <span style={{ fontWeight: 700, color: ACTION_COLOR[row.finalAction] || 'var(--nd-text-1)', background: `${ACTION_COLOR[row.finalAction] || 'transparent'}15`, padding: '2px 7px', borderRadius: 4, fontSize: 11 }}>
                          {row.finalAction}
                        </span>
                      </td>
                      <td style={{ padding: '10px 14px', color: 'var(--nd-text-1)' }}>{pct(row.finalConfidence)}</td>
                      <td style={{ padding: '10px 14px', color: 'var(--nd-text-2)' }}>{pct(row.agentAgreement)}</td>
                      <td style={{ padding: '10px 14px', color: row.riskScore > 0.6 ? '#ef4444' : row.riskScore > 0.35 ? '#f59e0b' : '#22c55e' }}>{fmt2(row.riskScore * 100)}</td>
                      <td style={{ padding: '10px 14px' }}>
                        {row.outcome ? (
                          <span style={{ color: row.outcome === 'correct' ? '#22c55e' : '#ef4444', fontWeight: 600, fontSize: 11 }}>
                            {row.outcome === 'correct' ? '✓ Correct' : '✗ Wrong'}
                          </span>
                        ) : <span style={{ color: 'var(--nd-text-3)' }}>–</span>}
                      </td>
                      <td style={{ padding: '10px 14px', color: row.pnlPct != null ? (row.pnlPct >= 0 ? '#22c55e' : '#ef4444') : 'var(--nd-text-3)' }}>
                        {row.pnlPct != null ? `${row.pnlPct > 0 ? '+' : ''}${row.pnlPct.toFixed(2)}%` : '–'}
                      </td>
                      <td style={{ padding: '10px 14px', color: row.reward != null ? (row.reward >= 0 ? '#22c55e' : '#ef4444') : 'var(--nd-text-3)' }}>
                        {row.reward != null ? `${row.reward > 0 ? '+' : ''}${row.reward.toFixed(2)}` : '–'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
            </div>
          )}
        </div>
      )}

      {/* pulse animation */}
      <style>{`
        @keyframes pulse {
          0%, 100% { transform: scale(1); opacity: 1; }
          50% { transform: scale(1.4); opacity: 0.5; }
        }
      `}</style>
    </div>
  );
};

export default AIEngine;
