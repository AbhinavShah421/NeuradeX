import React, { useEffect, useState, useCallback, useRef } from 'react';
import { useAppStore } from '../stores/appStore';
import apiService from '../services/api';
import SessionManager from '../components/SessionManager';
import { BacktestResult, BacktestTrade, EquityPoint, LiveSignal, StrategyParam } from '../types';

const inr = (v: number) =>
  `₹${Math.abs(v).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const pct = (v: number, d = 2) => `${v >= 0 ? '+' : ''}${v.toFixed(d)}%`;

// ── Stocks ─────────────────────────────────────────────────────────────────────
const PORTFOLIO_STOCKS = [
  'IDBI','SUZLON','SHREEGANES','SBIN','INDUSINDBK','TMPV','PNB',
  'FEDERALBNK','TMCV','IREDA','ZEEL','SYNCOMF','IOB','JKTYRE','VIKASECO',
];
const ALL_STOCKS = [
  ...PORTFOLIO_STOCKS,
  'RELIANCE','TCS','INFY','HDFCBANK','ICICIBANK','HINDUNILVR','BAJFINANCE',
  'WIPRO','KOTAKBANK','TATAMOTORS','ADANIENT','MARUTI','SUNPHARMA','TITAN',
];


// ── Strategy defs ──────────────────────────────────────────────────────────────
const STRATEGY_DEFS: Record<string, { name: string; description: string; params: Record<string, StrategyParam> }> = {
  sma_crossover: {
    name: 'SMA Crossover',
    description: 'Buy on golden cross (fast SMA > slow SMA), sell on death cross.',
    params: {
      sma_fast: { label: 'Fast SMA period', default: 20, min: 5,  max: 50,  step: 1,   type: 'int' },
      sma_slow: { label: 'Slow SMA period', default: 50, min: 20, max: 200, step: 5,   type: 'int' },
    },
  },
  rsi_mean_reversion: {
    name: 'RSI Mean Reversion',
    description: 'Buy when RSI drops below oversold; sell when it rises above overbought.',
    params: {
      rsi_period:  { label: 'RSI period',           default: 14, min: 5,  max: 30, step: 1, type: 'int' },
      oversold:    { label: 'Oversold threshold',   default: 30, min: 15, max: 45, step: 1, type: 'int' },
      overbought:  { label: 'Overbought threshold', default: 70, min: 55, max: 85, step: 1, type: 'int' },
    },
  },
  macd_crossover: {
    name: 'MACD Crossover',
    description: 'Buy when MACD line crosses above signal; sell on cross below.',
    params: {
      fast:   { label: 'Fast EMA period',   default: 12, min: 5,  max: 20, step: 1, type: 'int' },
      slow:   { label: 'Slow EMA period',   default: 26, min: 15, max: 50, step: 1, type: 'int' },
      signal: { label: 'Signal EMA period', default: 9,  min: 3,  max: 15, step: 1, type: 'int' },
    },
  },
  bollinger_band: {
    name: 'Bollinger Band Reversion',
    description: 'Buy at lower band; sell at upper band.',
    params: {
      window:  { label: 'Window period',  default: 20,  min: 10, max: 50,  step: 5,   type: 'int' },
      std_dev: { label: 'Std deviations', default: 2.0, min: 1.0, max: 3.0, step: 0.5, type: 'float' },
    },
  },
};



// ═══════════════════════════════════════════════════════════════════════════════
//  EQUITY CURVE  (strategy backtest)
// ═══════════════════════════════════════════════════════════════════════════════
const EquityCurve: React.FC<{ curve: EquityPoint[]; initialCapital: number; trades: BacktestTrade[]; theme: string }> = ({ curve, initialCapital, trades, theme }) => {
  if (curve.length < 2) return null;
  const W = 800, H = 300, PL = 70, PR = 20, PT = 20, PB = 40;
  const plotW = W - PL - PR, plotH = H - PT - PB;
  const allV = curve.flatMap(p => [p.portfolio, p.benchmark]);
  const minV = Math.min(...allV) * 0.985, maxV = Math.max(...allV) * 1.015;
  const sx = (i: number) => PL + (i / (curve.length - 1)) * plotW;
  const sy = (v: number) => PT + plotH - ((v - minV) / (maxV - minV)) * plotH;
  const portPts  = curve.map((p, i) => `${sx(i).toFixed(1)},${sy(p.portfolio).toFixed(1)}`).join(' ');
  const benchPts = curve.map((p, i) => `${sx(i).toFixed(1)},${sy(p.benchmark).toFixed(1)}`).join(' ');
  const yLabels = Array.from({ length: 5 }, (_, i) => {
    const v = minV + (maxV - minV) * (i / 4);
    return { y: sy(v), label: v >= 1_000_000 ? `₹${(v/1e6).toFixed(2)}M` : `₹${(v/1000).toFixed(0)}K` };
  });
  const xStep  = Math.max(1, Math.floor(curve.length / 6));
  const xLabels = Array.from({ length: 7 }, (_, i) => {
    const idx = Math.min(i * xStep, curve.length - 1);
    return { x: sx(idx), label: curve[idx].date.slice(5) };
  });
  const textColor = theme === 'dark' ? '#9CA3AF' : '#6B7280';
  const gridColor = theme === 'dark' ? '#374151' : '#E5E7EB';
  const refY = sy(initialCapital);
  const dateIndex = new Map(curve.map((p, i) => [p.date, i]));
  const tradeMarkers = trades.flatMap(t => {
    const marks: {x:number;y:number;color:string;label:string}[] = [];
    const ei = dateIndex.get(t.entryDate); if (ei !== undefined) marks.push({ x: sx(ei), y: sy(curve[ei].portfolio), color: '#22C55E', label: 'B' });
    const xi = dateIndex.get(t.exitDate);  if (xi !== undefined) marks.push({ x: sx(xi), y: sy(curve[xi].portfolio), color: t.type === 'WIN' ? '#3B82F6' : '#EF4444', label: 'S' });
    return marks;
  });
  const portColor = curve[curve.length - 1].portfolio >= initialCapital ? '#3B82F6' : '#EF4444';
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: 300 }}>
      {yLabels.map((l, i) => <line key={i} x1={PL} x2={W-PR} y1={l.y} y2={l.y} stroke={gridColor} strokeDasharray="4" strokeWidth="1" />)}
      <line x1={PL} x2={W-PR} y1={refY} y2={refY} stroke="#6B7280" strokeDasharray="8,4" strokeWidth="1.5" opacity="0.5" />
      <polyline points={benchPts} fill="none" stroke="#9CA3AF" strokeWidth="1.5" strokeDasharray="6,3" />
      <polyline points={portPts}  fill="none" stroke={portColor} strokeWidth="2.5" />
      {tradeMarkers.map((m, i) => <circle key={i} cx={m.x} cy={m.y} r="4" fill={m.color} opacity="0.85" />)}
      {yLabels.map((l, i) => <text key={i} x={PL-5} y={l.y+4} textAnchor="end" fontSize="10" fill={textColor}>{l.label}</text>)}
      {xLabels.map((l, i) => <text key={i} x={l.x} y={H-8} textAnchor="middle" fontSize="10" fill={textColor}>{l.label}</text>)}
      <line x1={PL} x2={PL+24} y1={PT+12} y2={PT+12} stroke={portColor} strokeWidth="2.5" />
      <text x={PL+28} y={PT+16} fontSize="11" fill={textColor}>Strategy</text>
      <line x1={PL+90} x2={PL+114} y1={PT+12} y2={PT+12} stroke="#9CA3AF" strokeWidth="1.5" strokeDasharray="6,3" />
      <text x={PL+118} y={PT+16} fontSize="11" fill={textColor}>Buy &amp; Hold</text>
    </svg>
  );
};

// ── Helpers ────────────────────────────────────────────────────────────────────
const MCard: React.FC<{ label: string; value: string; sub?: string; color?: string }> = ({ label, value, sub, color = '' }) => (
  <div className="nd-metric">
    <p className="nd-metric-label">{label}</p>
    <p className="nd-metric-value" style={{ fontSize: 18, color: color.includes('green') ? 'var(--nd-green)' : color.includes('red') ? 'var(--nd-red)' : color.includes('yellow') ? '#ca8a04' : 'var(--nd-text-1)' }}>
      {value}
    </p>
    {sub && <p className="nd-metric-sub">{sub}</p>}
  </div>
);

const SignalBadge: React.FC<{ signal: string }> = ({ signal }) => {
  const bg   = signal === 'BUY' ? 'var(--nd-green)' : signal === 'SELL' ? 'var(--nd-red)' : '#ca8a04';
  const icon = signal === 'BUY' ? 'trending_up' : signal === 'SELL' ? 'trending_down' : 'pause_circle';
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '6px 18px', borderRadius: 100, background: bg, color: '#fff', fontWeight: 700, fontSize: 16 }}>
      <span className="material-icons" style={{ fontSize: 18 }}>{icon}</span>
      {signal}
    </span>
  );
};

// ═══════════════════════════════════════════════════════════════════════════════
//  MAIN PAGE
// ═══════════════════════════════════════════════════════════════════════════════
const BacktestPage: React.FC = () => {
  const { theme } = useAppStore();
  const isDark = theme === 'dark';

  const [pageTab, setPageTab] = useState<'autopilot'|'strategy'>('autopilot');

  // Restore the last strategy backtest (inputs + result) so a refresh doesn't wipe it
  const SAVED_BT: any = (() => {
    try { return JSON.parse(localStorage.getItem('nd_strategy_backtest') || 'null'); } catch { return null; }
  })();

  // ── Strategy backtest state ─────────────────────────────────────────────────
  const [symbol,     setSymbol]     = useState(SAVED_BT?.symbol ?? 'SBIN');
  const [strategy,   setStrategy]   = useState(SAVED_BT?.strategy ?? 'sma_crossover');
  const [startDate,  setStartDate]  = useState(SAVED_BT?.startDate ?? (() => { const d = new Date(); d.setFullYear(d.getFullYear()-1); return d.toISOString().slice(0,10); }));
  const [endDate,    setEndDate]    = useState(SAVED_BT?.endDate ?? (() => new Date().toISOString().slice(0,10)));
  const [capital,    setCapital]    = useState(SAVED_BT?.capital ?? '100000');
  const [commission, setCommission] = useState(SAVED_BT?.commission ?? '0.1');
  const [paramVals,  setParamVals]  = useState<Record<string, number>>({});
  const [result,     setResult]     = useState<BacktestResult | null>(SAVED_BT?.result ?? null);
  const [liveSignal, setLiveSignal] = useState<LiveSignal | null>(null);
  const [running,    setRunning]    = useState(false);
  const [loadSig,    setLoadSig]    = useState(false);
  const [error,      setError]      = useState<string | null>(null);
  const [tradeFilter, setTradeFilter] = useState<'ALL'|'WIN'|'LOSS'>('ALL');
  const liveTimer = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => {
    const defs = STRATEGY_DEFS[strategy]?.params ?? {};
    setParamVals(Object.fromEntries(Object.entries(defs).map(([k, v]) => [k, v.default])));
  }, [strategy]);

  // Persist the strategy backtest so it survives a page refresh
  useEffect(() => {
    if (!result) return;
    try {
      localStorage.setItem('nd_strategy_backtest', JSON.stringify({
        symbol, strategy, startDate, endDate, capital, commission, result,
      }));
    } catch { /* ignore quota errors */ }
  }, [result]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    clearTimeout(liveTimer.current);
    liveTimer.current = setTimeout(async () => {
      setLoadSig(true);
      try { const r = await apiService.getLiveSignal(symbol, strategy, paramVals); if (r.data) setLiveSignal(r.data); }
      catch {}
      finally { setLoadSig(false); }
    }, 800);
    return () => clearTimeout(liveTimer.current);
  }, [symbol, strategy, JSON.stringify(paramVals)]);

  const handleRun = useCallback(async () => {
    setRunning(true); setError(null); setResult(null);
    try {
      const r = await apiService.runBacktest({ symbol, strategy, start_date: startDate, end_date: endDate, initial_capital: parseFloat(capital)||100_000, commission: (parseFloat(commission)||0.1)/100, params: paramVals });
      if (r.data) setResult(r.data);
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || 'Backtest failed');
    } finally { setRunning(false); }
  }, [symbol, strategy, startDate, endDate, capital, commission, paramVals]);

  const strat = STRATEGY_DEFS[strategy];
  const m = result?.metrics;
  const filteredTrades = result?.trades.filter(t => tradeFilter === 'ALL' ? true : t.type === tradeFilter) ?? [];

  return (
    <div>
      <div style={{ marginBottom: 20 }}>
        <h1 className="nd-page-title">Backtesting & Day Trading</h1>
        <p className="nd-page-sub">AI-powered intraday simulation with live replay + historical strategy backtesting.</p>
      </div>

      {/* Tab switcher */}
      <div style={{ display: 'flex', gap: 4, marginBottom: 24, background: isDark ? 'rgba(255,255,255,0.05)' : 'rgba(0,0,0,0.04)', borderRadius: 12, padding: 4, width: 'fit-content', maxWidth: '100%', overflowX: 'auto' }}>
        {([['autopilot','AI Live Trading','auto_awesome'],['strategy','Strategy Backtest','timeline']] as const).map(([tab, label, icon]) => (
          <button key={tab} onClick={() => setPageTab(tab)} style={{
            display: 'flex', alignItems: 'center', gap: 6, padding: '9px 20px', minHeight: 36, borderRadius: 10, border: 'none',
            background: pageTab === tab ? 'var(--nd-primary)' : 'transparent',
            color: pageTab === tab ? '#fff' : 'var(--nd-text-2)',
            fontWeight: pageTab === tab ? 700 : 500, fontSize: 13, cursor: 'pointer', whiteSpace: 'nowrap', flexShrink: 0,
          }}>
            <span className="material-icons" style={{ fontSize: 16 }}>{icon}</span>
            {label}
          </button>
        ))}
      </div>

      {/* ══════════════════════════════════════════════════════════════════════ */}
      {/* TAB 1: AI LIVE TRADING                                               */}
      {/* ══════════════════════════════════════════════════════════════════════ */}
      {pageTab === 'autopilot' && (
        <div>
          {/* Multi-session manager — run several stocks at once, all server-side */}
          <SessionManager mode="replay" />
        </div>
      )}

      {/* ══════════════════════════════════════════════════════════════════════ */}
      {/* TAB 2: STRATEGY BACKTEST                                             */}
      {/* ══════════════════════════════════════════════════════════════════════ */}
      {pageTab === 'strategy' && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-6">
          <div className="nd-card lg:col-span-1 p-6" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <h2 className="nd-section-title" style={{ margin: 0 }}>Configuration</h2>
            <div>
              <label className="nd-field-label">Stock</label>
              <select value={symbol} onChange={e => setSymbol(e.target.value)} className="nd-input">
                <optgroup label="My Portfolio">{PORTFOLIO_STOCKS.map(s => <option key={s}>{s}</option>)}</optgroup>
                <optgroup label="NSE Stocks">{ALL_STOCKS.filter(s => !PORTFOLIO_STOCKS.includes(s)).map(s => <option key={s}>{s}</option>)}</optgroup>
              </select>
            </div>
            <div>
              <label className="nd-field-label">Strategy</label>
              <select value={strategy} onChange={e => setStrategy(e.target.value)} className="nd-input">
                {Object.entries(STRATEGY_DEFS).map(([k, v]) => <option key={k} value={k}>{v.name}</option>)}
              </select>
              <p style={{ fontSize: 11, color: 'var(--nd-text-3)', marginTop: 4 }}>{strat?.description}</p>
            </div>
            {Object.entries(strat?.params ?? {}).map(([k, p]) => (
              <div key={k}>
                <label className="nd-field-label">{p.label}: <strong>{paramVals[k] ?? p.default}</strong></label>
                <input type="range" min={p.min} max={p.max} step={p.step} value={paramVals[k] ?? p.default}
                  onChange={e => setParamVals(prev => ({ ...prev, [k]: p.type === 'int' ? parseInt(e.target.value) : parseFloat(e.target.value) }))}
                  className="nd-slider" />
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--nd-text-3)' }}>
                  <span>{p.min}</span><span>{p.max}</span>
                </div>
              </div>
            ))}
            <div style={{ display: 'flex', gap: 12 }}>
              <div style={{ flex: 1 }}><label className="nd-field-label">From</label><input type="date" value={startDate} onChange={e => setStartDate(e.target.value)} className="nd-input" /></div>
              <div style={{ flex: 1 }}><label className="nd-field-label">To</label><input type="date" value={endDate} onChange={e => setEndDate(e.target.value)} className="nd-input" /></div>
            </div>
            <div><label className="nd-field-label">Initial Capital (₹)</label><input type="number" value={capital} onChange={e => setCapital(e.target.value)} className="nd-input" min={10000} step={10000} /></div>
            <div><label className="nd-field-label">Commission (%)</label><input type="number" value={commission} onChange={e => setCommission(e.target.value)} className="nd-input" min={0} max={5} step={0.01} /></div>
            <button onClick={handleRun} disabled={running} className="nd-btn nd-btn-primary" style={{ width: '100%', justifyContent: 'center', padding: '11px 0', fontWeight: 700, fontSize: 14 }}>
              {running ? 'Running…' : 'Run Backtest'}
            </button>
            {error && <div style={{ padding: 12, borderRadius: 8, background: '#fef2f2', color: '#dc2626', fontSize: 12, border: '1px solid #fecaca' }}>{error}</div>}
          </div>

          <div className="lg:col-span-2" style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
            <div className="nd-card p-6">
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, flexWrap: 'wrap', gap: 8 }}>
                <h2 className="nd-section-title" style={{ margin: 0 }}>Live Signal — {symbol}</h2>
                {loadSig && <span style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>Refreshing…</span>}
              </div>
              {liveSignal ? (
                <>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 16, flexWrap: 'wrap' }}>
                    <SignalBadge signal={liveSignal.signal} />
                    <div>
                      <span style={{ fontSize: 28, fontWeight: 800 }}>{inr(liveSignal.lastPrice)}</span>
                      <span style={{ fontSize: 12, color: 'var(--nd-text-3)', marginLeft: 8 }}>{liveSignal.strategy?.replace(/_/g,' ')} · {liveSignal.candleCount} daily candles</span>
                    </div>
                  </div>
                  <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
                    {Object.entries(liveSignal.indicators ?? {}).map(([k, v]) => v !== null && (
                      <div key={k} className="nd-metric" style={{ minWidth: 100, padding: 12 }}>
                        <p className="nd-metric-label" style={{ textTransform: 'uppercase', fontSize: 10 }}>{k.replace(/_/g,' ')}</p>
                        <p className="nd-metric-value" style={{ fontSize: 16 }}>{typeof v === 'number' ? v.toFixed(2) : String(v)}</p>
                      </div>
                    ))}
                  </div>
                  {liveSignal.recentSignals && (
                    <>
                      <p style={{ fontSize: 11, fontWeight: 600, color: 'var(--nd-text-3)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 8 }}>Recent Signals</p>
                      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                        {liveSignal.recentSignals.map((s: any, i: number) => (
                          <div key={i} style={{
                            padding: '6px 12px', borderRadius: 8, textAlign: 'center', minWidth: 80,
                            background: s.signal === 'BUY' ? '#dcfce7' : s.signal === 'SELL' ? '#fee2e2' : isDark ? 'rgba(255,255,255,0.06)' : '#f8fafc',
                            border: `1px solid ${s.signal === 'BUY' ? '#86efac' : s.signal === 'SELL' ? '#fca5a5' : isDark ? 'rgba(255,255,255,0.1)' : '#e2e8f0'}`,
                          }}>
                            <div style={{ fontWeight: 700, fontSize: 12, color: s.signal === 'BUY' ? '#16a34a' : s.signal === 'SELL' ? '#dc2626' : 'var(--nd-text-2)' }}>{s.signal}</div>
                            <div style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>{s.date?.slice(5)}</div>
                            <div style={{ fontSize: 10, color: 'var(--nd-text-2)', fontWeight: 600 }}>{inr(s.close)}</div>
                          </div>
                        ))}
                      </div>
                    </>
                  )}
                  <p style={{ fontSize: 11, color: 'var(--nd-text-3)', marginTop: 12 }}>Paper trading only — no real orders are placed.</p>
                </>
              ) : (
                <div style={{ textAlign: 'center', padding: 24, color: 'var(--nd-text-3)' }}>Loading signal…</div>
              )}
            </div>

            {m && result && (
              <>
                <div className="nd-card p-6">
                  <h2 className="nd-section-title" style={{ margin: '0 0 16px' }}>Results — {result.strategyName} on {result.symbol}</h2>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 0 }}>
                    <MCard label="Total Return" value={pct(m.totalReturnPct)} color={m.totalReturnPct >= 0 ? 'green' : 'red'} sub={`vs B&H ${pct(m.buyHoldReturnPct)}`} />
                    <MCard label="CAGR" value={pct(m.cagr)} color={m.cagr >= 0 ? 'green' : 'red'} />
                    <MCard label="Sharpe Ratio" value={m.sharpeRatio.toFixed(3)} color={m.sharpeRatio >= 1 ? 'green' : m.sharpeRatio >= 0 ? 'yellow' : 'red'} />
                    <MCard label="Max Drawdown" value={`-${m.maxDrawdownPct.toFixed(2)}%`} color="red" />
                    <MCard label="Win Rate" value={`${m.winRate.toFixed(1)}%`} color={m.winRate >= 50 ? 'green' : 'red'} sub={`${m.winningTrades}W / ${m.losingTrades}L`} />
                    <MCard label="Profit Factor" value={m.profitFactor.toFixed(2)} color={m.profitFactor >= 1 ? 'green' : 'red'} />
                    <MCard label="Total Trades" value={String(m.totalTrades)} sub={`Avg hold ${m.avgHoldingDays}d`} />
                    <MCard label="Final Value" value={inr(m.finalValue)} sub={`Started: ${inr(m.initialCapital)}`} />
                  </div>
                </div>
                {result.equityCurve && (
                  <div className="nd-card p-6">
                    <h2 className="nd-section-title" style={{ margin: '0 0 16px' }}>Equity Curve</h2>
                    <EquityCurve curve={result.equityCurve} initialCapital={m.initialCapital} trades={result.trades} theme={theme} />
                  </div>
                )}
                {result.trades.length > 0 && (
                  <div className="nd-card p-6">
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16, flexWrap: 'wrap', gap: 8 }}>
                      <h2 className="nd-section-title" style={{ margin: 0 }}>Trade Log ({filteredTrades.length})</h2>
                      <div style={{ display: 'flex', gap: 4 }}>
                        {(['ALL','WIN','LOSS'] as const).map(f => (
                          <button key={f} onClick={() => setTradeFilter(f)} style={{
                            padding: '7px 14px', minHeight: 34, borderRadius: 20, border: 'none', fontSize: 12, fontWeight: 600, cursor: 'pointer',
                            background: tradeFilter === f ? 'var(--nd-primary)' : isDark ? 'rgba(255,255,255,0.06)' : '#f1f5f9',
                            color: tradeFilter === f ? '#fff' : 'var(--nd-text-2)',
                          }}>{f}</button>
                        ))}
                      </div>
                    </div>
                    <div style={{ overflowX: 'auto' }}>
                      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                        <thead>
                          <tr style={{ color: 'var(--nd-text-3)', borderBottom: '1px solid var(--nd-border)' }}>
                            {['Entry','Exit','Entry ₹','Exit ₹','Shares','P&L','P&L %','Hold','Result'].map(h => (
                              <th key={h} style={{ textAlign: 'left', padding: '6px 10px', fontWeight: 600, whiteSpace: 'nowrap' }}>{h}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {filteredTrades.map((t, i) => (
                            <tr key={i} style={{ borderBottom: '1px solid var(--nd-border)', color: 'var(--nd-text-2)' }}>
                              <td style={{ padding: '6px 10px', whiteSpace: 'nowrap' }}>{t.entryDate}</td>
                              <td style={{ padding: '6px 10px', whiteSpace: 'nowrap' }}>{t.exitDate}</td>
                              <td style={{ padding: '6px 10px' }}>{inr(t.entryPrice)}</td>
                              <td style={{ padding: '6px 10px' }}>{inr(t.exitPrice)}</td>
                              <td style={{ padding: '6px 10px' }}>{t.shares}</td>
                              <td style={{ padding: '6px 10px', fontWeight: 600, color: t.pnl >= 0 ? 'var(--nd-green)' : 'var(--nd-red)' }}>{t.pnl >= 0 ? '+' : ''}{inr(t.pnl)}</td>
                              <td style={{ padding: '6px 10px', color: t.pnlPct >= 0 ? 'var(--nd-green)' : 'var(--nd-red)' }}>{pct(t.pnlPct)}</td>
                              <td style={{ padding: '6px 10px' }}>{t.holdingDays}d</td>
                              <td style={{ padding: '6px 10px' }}>
                                <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 20, background: t.type === 'WIN' ? '#dcfce7' : '#fee2e2', color: t.type === 'WIN' ? '#16a34a' : '#dc2626', fontWeight: 600 }}>{t.type}</span>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

export default BacktestPage;
