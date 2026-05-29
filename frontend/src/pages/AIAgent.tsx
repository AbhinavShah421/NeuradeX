import React, { useEffect, useState, useCallback } from 'react';
import { useAppStore } from '../stores/appStore';
import apiService from '../services/api';
import { AgentStock, AIAnalysis, TechnicalIndicators } from '../types';

const inr = (v: number | null | undefined) =>
  v == null ? 'N/A' : `₹${v.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const pct = (v: number | null | undefined, decimals = 1) =>
  v == null ? 'N/A' : `${v >= 0 ? '+' : ''}${v.toFixed(decimals)}%`;

const num = (v: number | null | undefined, d = 2) =>
  v == null ? 'N/A' : v.toFixed(d);

// ── Analysis text renderer: parses ## headers into styled blocks ───────────────
const AnalysisRenderer: React.FC<{ text: string; theme: string }> = ({ text, theme }) => {
  const sectionColors: Record<string, string> = {
    'OVERALL TREND':             'border-blue-500',
    'TECHNICAL SIGNALS':         'border-purple-500',
    'KEY PRICE LEVELS':          'border-yellow-500',
    'SHORT-TERM OUTLOOK':        'border-green-500',
    'MEDIUM-TERM OUTLOOK':       'border-teal-500',
    'RISK ASSESSMENT':           'border-red-500',
    'TRADING RECOMMENDATION':    'border-orange-500',
  };

  const sections = text.split(/^## /m).filter(Boolean);

  if (sections.length <= 1) {
    return (
      <pre className={`whitespace-pre-wrap text-sm leading-relaxed ${theme === 'dark' ? 'text-gray-300' : 'text-gray-700'}`}>
        {text}
      </pre>
    );
  }

  return (
    <div className="space-y-4">
      {sections.map((section, idx) => {
        const newlineIdx = section.indexOf('\n');
        const heading = newlineIdx > -1 ? section.slice(0, newlineIdx).trim() : section.trim();
        const body = newlineIdx > -1 ? section.slice(newlineIdx + 1).trim() : '';

        const colorKey = Object.keys(sectionColors).find(k => heading.toUpperCase().includes(k));
        const borderColor = colorKey ? sectionColors[colorKey] : 'border-gray-400';
        const bg = theme === 'dark' ? 'bg-gray-750 bg-opacity-50' : 'bg-gray-50';

        return (
          <div key={idx} className={`rounded-lg border-l-4 ${borderColor} p-4 ${bg}`}>
            <h3 className="font-bold text-sm uppercase tracking-wide mb-2 opacity-80">{heading}</h3>
            <pre className={`whitespace-pre-wrap text-sm leading-relaxed font-sans ${theme === 'dark' ? 'text-gray-200' : 'text-gray-800'}`}>
              {body}
            </pre>
          </div>
        );
      })}
    </div>
  );
};

// ── Indicator card ─────────────────────────────────────────────────────────────
const IndCard: React.FC<{
  label: string;
  value: string;
  sub?: string;
  color?: string;
  theme: string;
}> = ({ label, value, sub, color = '' }) => (
  <div className="nd-metric">
    <p className="nd-metric-label">{label}</p>
    <p className="nd-metric-value" style={{ fontSize: 16, color: color.includes('green') ? 'var(--nd-green)' : color.includes('red') ? 'var(--nd-red)' : color.includes('yellow') ? '#ca8a04' : 'var(--nd-text-1)' }}>
      {value}
    </p>
    {sub && <p className="nd-metric-sub">{sub}</p>}
  </div>
);

// ── RSI color ──────────────────────────────────────────────────────────────────
const rsiColor = (v: number | null) => {
  if (v == null) return '';
  if (v > 70) return 'text-red-500';
  if (v < 30) return 'text-green-500';
  return 'text-yellow-500';
};

// ── Main page ──────────────────────────────────────────────────────────────────
const AIAgentPage: React.FC = () => {
  const { theme } = useAppStore();

  const [stocks, setStocks]       = useState<AgentStock[]>([]);
  const [models, setModels]       = useState<string[]>([]);
  const [selSymbol, setSelSymbol] = useState<string>('');
  const [selModel, setSelModel]   = useState<string>('');
  const [analysis, setAnalysis]   = useState<AIAnalysis | null>(null);
  const [loading, setLoading]     = useState(false);
  const [loadStep, setLoadStep]   = useState(0);
  const [error, setError]         = useState<string | null>(null);
  const [search, setSearch]       = useState('');

  useEffect(() => {
    apiService.getAgentStocks().then(r => {
      if (r.data) {
        setStocks(r.data);
        const first = r.data.find(s => s.inPortfolio) || r.data[0];
        if (first) setSelSymbol(first.symbol);
      }
    }).catch(() => {});

    apiService.getOllamaModels().then(r => {
      setModels(r.data.length ? r.data : [r.current]);
      setSelModel(r.current);
    }).catch(() => setSelModel('llama3.2'));
  }, []);

  const STEPS = [
    'Fetching 1 year of daily candles from Groww...',
    'Computing RSI, MACD, Bollinger Bands, ATR...',
    'Consulting AI model — this may take 30–90 seconds...',
  ];

  const handleAnalyze = useCallback(async () => {
    if (!selSymbol) return;
    setLoading(true);
    setError(null);
    setAnalysis(null);

    // Animate steps
    setLoadStep(0);
    const t1 = setTimeout(() => setLoadStep(1), 1500);
    const t2 = setTimeout(() => setLoadStep(2), 3000);

    try {
      const res = await apiService.analyzeStock(selSymbol, selModel || undefined);
      if (res.data) setAnalysis(res.data);
    } catch (err: any) {
      const msg = err?.response?.data?.detail || err?.message || 'Unknown error';
      setError(msg);
    } finally {
      clearTimeout(t1);
      clearTimeout(t2);
      setLoading(false);
    }
  }, [selSymbol, selModel]);

  const filteredStocks = stocks.filter(s =>
    s.symbol.includes(search.toUpperCase()) || s.name.toLowerCase().includes(search.toLowerCase())
  );

  const ind: TechnicalIndicators | undefined = analysis?.indicators;

  return (
    <div>
      {/* Header */}
      <div style={{ marginBottom: 20 }}>
        <h1 className="nd-page-title">AI Stock Analyst</h1>
        <p className="nd-page-sub">
          Select any NSE stock — fetches 1 year of daily candles from Groww,
          computes RSI / MACD / Bollinger / ATR, then queries your local Ollama LLM
          for a structured price prediction and trading recommendation.
        </p>
      </div>

      {/* Controls */}
      <div className="nd-card" style={{ marginBottom: 16 }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr auto', gap: 16, alignItems: 'end' }}>
          <div>
            <label className="nd-field-label">Stock Symbol</label>
            <input type="text" placeholder="Search by symbol or name..." value={search}
              onChange={e => setSearch(e.target.value)} className="nd-input" style={{ marginBottom: 8 }} />
            <div style={{ maxHeight: 160, overflowY: 'auto', border: '1px solid var(--nd-border)', borderRadius: 6, background: 'var(--nd-bg)' }}>
              {filteredStocks.map(s => (
                <button key={s.symbol} onClick={() => { setSelSymbol(s.symbol); setSearch(''); }}
                  style={{
                    width: '100%', textAlign: 'left', padding: '8px 12px', fontSize: 13,
                    display: 'flex', alignItems: 'center', gap: 8, border: 'none', cursor: 'pointer',
                    background: selSymbol === s.symbol ? 'var(--nd-green)' : 'transparent',
                    color: selSymbol === s.symbol ? '#fff' : 'var(--nd-text-1)',
                    transition: 'background 0.1s',
                  }}>
                  <span style={{ fontWeight: 700, fontFamily: 'monospace' }}>{s.symbol}</span>
                  <span style={{ opacity: 0.7, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.name}</span>
                  {s.inPortfolio && <span className="nd-badge nd-badge-green" style={{ marginLeft: 'auto', flexShrink: 0 }}>Portfolio</span>}
                </button>
              ))}
            </div>
            {selSymbol && (
              <p style={{ fontSize: 12, color: 'var(--nd-text-3)', marginTop: 4 }}>
                Selected: <strong style={{ color: 'var(--nd-text-1)' }}>{selSymbol}</strong> — {stocks.find(s => s.symbol === selSymbol)?.name}
              </p>
            )}
          </div>
          <div>
            <label className="nd-field-label">Ollama Model</label>
            <select value={selModel} onChange={e => setSelModel(e.target.value)} className="nd-select">
              {models.map(m => <option key={m} value={m}>{m}</option>)}
            </select>
          </div>
          <button onClick={handleAnalyze} disabled={loading || !selSymbol}
            className={`nd-btn nd-btn-primary`} style={{ height: 40, padding: '0 24px', alignSelf: 'end' }}>
            <span className="material-icons" style={{ fontSize: 17 }}>{loading ? 'autorenew' : 'search'}</span>
            {loading ? 'Analyzing...' : 'Analyze Stock'}
          </button>
        </div>
      </div>

      {/* Loading state */}
      {loading && (
        <div className="nd-card" style={{ marginBottom: 16, textAlign: 'center', padding: '40px 24px' }}>
          <span className="material-icons nd-spin" style={{ fontSize: 40, marginBottom: 16 }}>autorenew</span>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, maxWidth: 360, margin: '0 auto' }}>
            {STEPS.map((step, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 13.5, opacity: i <= loadStep ? 1 : 0.3 }}>
                <span style={{
                  flexShrink: 0, width: 22, height: 22, borderRadius: '50%',
                  display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 11, fontWeight: 700, color: '#fff',
                  background: i < loadStep ? 'var(--nd-green)' : i === loadStep ? '#3b82f6' : 'var(--nd-text-3)',
                }}>
                  {i < loadStep ? '✓' : i + 1}
                </span>
                <span style={{ color: 'var(--nd-text-2)' }}>{step}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="nd-alert nd-alert-error" style={{ flexDirection: 'column', alignItems: 'flex-start', marginBottom: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontWeight: 600, marginBottom: 4 }}>
            <span className="material-icons" style={{ fontSize: 18 }}>error_outline</span>
            Analysis Failed
          </div>
          <p style={{ fontSize: 13, marginBottom: 6 }}>{error}</p>
          <p style={{ fontSize: 12 }}>
            Make sure Ollama is running: <code style={{ background: 'rgba(0,0,0,0.08)', padding: '1px 6px', borderRadius: 3 }}>ollama serve</code> and model is pulled:
            <code style={{ background: 'rgba(0,0,0,0.08)', padding: '1px 6px', borderRadius: 3 }}> ollama pull {selModel}</code>
          </p>
        </div>
      )}

      {/* Results */}
      {analysis && ind && (
        <div className="space-y-6">
          {/* Meta banner */}
          <div className="nd-card" style={{ display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'center', justifyContent: 'space-between', padding: '14px 20px' }}>
            <div>
              <span style={{ fontSize: 22, fontWeight: 700, color: 'var(--nd-green)' }}>{analysis.symbol}</span>
              <span style={{ marginLeft: 8, color: 'var(--nd-text-2)', fontSize: 14 }}>{analysis.name}</span>
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <span className={`nd-badge ${analysis.dataSource === 'groww' ? 'nd-badge-green' : 'nd-badge-orange'}`}>
                {analysis.dataSource === 'groww' ? 'Live Groww Data' : 'Simulated Data'}
              </span>
              <span className="nd-badge nd-badge-purple">Model: {analysis.modelUsed}</span>
              <span className="nd-badge nd-badge-gray">{analysis.candleCount} candles</span>
              <span className="nd-badge nd-badge-gray">{new Date(analysis.generatedAt).toLocaleTimeString('en-IN')}</span>
            </div>
          </div>

          {/* Technical Indicators grid */}
          <div className="nd-card">
            <h2 className="nd-section-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span className="material-icons" style={{ fontSize: 18, color: 'var(--nd-blue)' }}>bar_chart</span>
              Technical Indicators
            </h2>
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
              <IndCard label="Current Price"  value={inr(ind.currentPrice)} theme={theme} />
              <IndCard
                label="52W High / Low"
                value={inr(ind.high52w)}
                sub={`Low: ${inr(ind.low52w)}`}
                theme={theme}
              />
              <IndCard
                label="RSI (14)"
                value={num(ind.rsi)}
                sub={ind.rsi == null ? '' : ind.rsi > 70 ? 'Overbought ⚠️' : ind.rsi < 30 ? 'Oversold 🟢' : 'Neutral'}
                color={rsiColor(ind.rsi)}
                theme={theme}
              />
              <IndCard
                label="MACD Histogram"
                value={num(ind.macdHistogram)}
                sub={ind.macdHistogram == null ? '' : ind.macdHistogram > 0 ? 'Bullish 🟢' : 'Bearish 🔴'}
                color={(ind.macdHistogram ?? 0) >= 0 ? 'text-green-600' : 'text-red-600'}
                theme={theme}
              />
              <IndCard
                label="SMA (20)"
                value={inr(ind.sma20)}
                sub={ind.priceVsSma20 != null ? `Price ${pct(ind.priceVsSma20)} vs SMA20` : ''}
                color={(ind.priceVsSma20 ?? 0) >= 0 ? 'text-green-600' : 'text-red-600'}
                theme={theme}
              />
              <IndCard
                label="SMA (50)"
                value={inr(ind.sma50)}
                sub={ind.priceVsSma50 != null ? `Price ${pct(ind.priceVsSma50)} vs SMA50` : ''}
                color={(ind.priceVsSma50 ?? 0) >= 0 ? 'text-green-600' : 'text-red-600'}
                theme={theme}
              />
              <IndCard
                label="Bollinger Bands"
                value={inr(ind.bbMiddle)}
                sub={`↑${inr(ind.bbUpper)} / ↓${inr(ind.bbLower)}`}
                theme={theme}
              />
              <IndCard
                label="ATR (14)"
                value={inr(ind.atr)}
                sub="Daily volatility range"
                theme={theme}
              />
              <IndCard
                label="Stochastic %K"
                value={num(ind.stochK)}
                sub={ind.stochK == null ? '' : ind.stochK > 80 ? 'Overbought' : ind.stochK < 20 ? 'Oversold' : 'Neutral'}
                color={ind.stochK != null && ind.stochK > 80 ? 'text-red-500' : ind.stochK != null && ind.stochK < 20 ? 'text-green-500' : ''}
                theme={theme}
              />
              <IndCard
                label="Volume Today"
                value={(ind.volCurrent ?? 0).toLocaleString('en-IN')}
                sub={`Avg 20D: ${(ind.volAvg20 ?? 0).toLocaleString('en-IN')}`}
                color={ind.volCurrent > ind.volAvg20 * 1.5 ? 'text-blue-600' : ''}
                theme={theme}
              />
              <IndCard
                label="EMA 12 / 26"
                value={inr(ind.ema12)}
                sub={`EMA26: ${inr(ind.ema26)}`}
                theme={theme}
              />
              <IndCard
                label="Bollinger %B"
                value={ind.bbPctB == null ? 'N/A' : ind.bbPctB.toFixed(2)}
                sub={ind.bbPctB == null ? '' : ind.bbPctB > 0.8 ? 'Near upper band' : ind.bbPctB < 0.2 ? 'Near lower band' : 'Mid-range'}
                theme={theme}
              />
            </div>
          </div>

          {/* MA Cross banner */}
          {ind.sma20 != null && ind.sma50 != null && (
            <div style={{
              padding: '14px 18px', borderRadius: 8,
              border: `2px solid ${ind.sma20 > ind.sma50 ? 'var(--nd-green)' : 'var(--nd-red)'}`,
              background: ind.sma20 > ind.sma50 ? 'var(--nd-green-50)' : 'var(--nd-red-50)',
            }}>
              <p style={{ fontWeight: 600, fontSize: 14, color: ind.sma20 > ind.sma50 ? '#065f46' : '#991b1b', display: 'flex', alignItems: 'center', gap: 8 }}>
                <span className="material-icons" style={{ fontSize: 18 }}>{ind.sma20 > ind.sma50 ? 'trending_up' : 'trending_down'}</span>
                {ind.sma20 > ind.sma50 ? 'Golden Cross — SMA20 above SMA50 (bullish structure)' : 'Death Cross — SMA20 below SMA50 (bearish structure)'}
              </p>
              <p style={{ fontSize: 12.5, color: 'var(--nd-text-2)', marginTop: 4 }}>
                SMA20: {inr(ind.sma20)} &nbsp;·&nbsp; SMA50: {inr(ind.sma50)}
              </p>
            </div>
          )}

          {/* Recent candles table */}
          <div className="nd-card" style={{ padding: 0 }}>
            <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--nd-border)', display: 'flex', alignItems: 'center', gap: 8 }}>
              <span className="material-icons" style={{ fontSize: 18, color: 'var(--nd-text-2)' }}>calendar_today</span>
              <h2 className="nd-section-title" style={{ margin: 0 }}>Recent Price Action — last 20 trading days</h2>
            </div>
            <div style={{ overflowX: 'auto' }}>
              <table className="nd-table">
                <thead>
                  <tr>
                    {['Date', 'Open', 'High', 'Low', 'Close', 'Change', 'Volume'].map(h => (
                      <th key={h} style={{ textAlign: h === 'Date' ? 'left' : 'right' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {[...analysis.recentCandles].reverse().map((c, i) => {
                    const chg = c.close - c.open;
                    const chgPct = ((chg / c.open) * 100).toFixed(2);
                    const isGreen = c.close >= c.open;
                    return (
                      <tr key={i}>
                        <td style={{ fontFamily: 'monospace', fontSize: 12 }}>{c.timestamp}</td>
                        <td className="text-right">{inr(c.open)}</td>
                        <td className="text-right" style={{ color: 'var(--nd-green)' }}>{inr(c.high)}</td>
                        <td className="text-right" style={{ color: 'var(--nd-red)' }}>{inr(c.low)}</td>
                        <td className="text-right" style={{ fontWeight: 700, color: isGreen ? 'var(--nd-green)' : 'var(--nd-red)' }}>{inr(c.close)}</td>
                        <td className="text-right" style={{ fontSize: 12, color: isGreen ? 'var(--nd-green)' : 'var(--nd-red)' }}>
                          {isGreen ? '+' : ''}{chgPct}%
                        </td>
                        <td className="text-right" style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>{c.volume.toLocaleString('en-IN')}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

          {/* AI Analysis */}
          <div className="nd-card">
            <h2 className="nd-section-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span className="material-icons" style={{ fontSize: 18, color: 'var(--nd-purple)' }}>psychology</span>
              AI Analysis — {analysis.symbol}
            </h2>
            <AnalysisRenderer text={analysis.analysis} theme={theme} />
          </div>

          {/* Disclaimer */}
          <p className="text-xs opacity-40 text-center pb-4">
            AI analysis is for educational purposes only. Not financial advice.
            Past technical patterns do not guarantee future performance.
          </p>
        </div>
      )}
    </div>
  );
};

export default AIAgentPage;
