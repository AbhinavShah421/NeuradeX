import React, { useEffect, useState } from 'react';
import apiService from '../services/api';
import { RiskMetrics, StressTestResult, FactorAnalysis, OptimizationResult, StressScenario } from '../types';

type Tab = 'risk' | 'stress' | 'factors' | 'optimization';

const RiskAnalytics: React.FC = () => {
  const [activeTab, setActiveTab] = useState<Tab>('risk');
  const [loading, setLoading] = useState(true);
  const [riskMetrics, setRiskMetrics]   = useState<RiskMetrics | null>(null);
  const [stressTest, setStressTest]     = useState<StressTestResult | null>(null);
  const [factors, setFactors]           = useState<FactorAnalysis | null>(null);
  const [optimization, setOptimization] = useState<OptimizationResult | null>(null);
  const [expandedScenario, setExpandedScenario] = useState<string | null>(null);
  const [llmAnalysis, setLlmAnalysis]     = useState<string | null>(null);
  const [llmLoading, setLlmLoading]       = useState(false);
  const [llmError, setLlmError]           = useState<string | null>(null);
  const [llmModel, setLlmModel]           = useState<string>('');
  const [llmGeneratedAt, setLlmGeneratedAt] = useState<string | null>(null);

  useEffect(() => {
    const fetchAll = async () => {
      setLoading(true);
      try {
        const [r1, r2, r3, r4] = await Promise.all([
          apiService.getRiskVar(), apiService.getStressTest(),
          apiService.getFactorAnalysis(), apiService.getOptimization(),
        ]);
        if (r1.data) setRiskMetrics(r1.data);
        if (r2.data) setStressTest(r2.data);
        if (r3.data) setFactors(r3.data);
        if (r4.data) setOptimization(r4.data);
      } catch (err) { console.error('Risk data error:', err); }
      finally { setLoading(false); }
    };
    fetchAll();
  }, []);

  const runLlmAnalysis = async () => {
    setLlmLoading(true);
    setLlmError(null);
    setLlmAnalysis(null);
    try {
      const res = await apiService.getOptimizationAnalysis();
      if (res.status === 'success' && res.data) {
        setLlmAnalysis(res.data.analysis);
        setLlmModel(res.data.modelUsed);
        setLlmGeneratedAt(res.data.generatedAt);
      } else {
        setLlmError('Failed to get AI analysis');
      }
    } catch (err: any) {
      setLlmError(err?.response?.data?.detail || 'Ollama LLM unavailable. Make sure Ollama is running.');
    } finally {
      setLlmLoading(false);
    }
  };

  const pct   = (v: number) => `${(v * 100).toFixed(2)}%`;
  const money = (v: number) => `₹${Math.abs(v).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
  const sign  = (v: number) => (v >= 0 ? '+' : '−');

  const severityBadge: Record<string, string> = {
    moderate: 'nd-badge nd-badge-orange',
    severe:   'nd-badge' ,
    extreme:  'nd-badge nd-badge-red',
  };
  const severityBorder: Record<string, string> = {
    moderate: '#f5a623',
    severe:   '#ef4444',
    extreme:  '#dc2626',
  };

  const factorBar = (val: number) => {
    const clamped = Math.max(-2, Math.min(2, val));
    const pctWidth = Math.abs(clamped) / 2 * 50;
    const isPos = clamped >= 0;
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, width: '100%' }}>
        <div style={{ flex: 1, display: 'flex', justifyContent: 'flex-end' }}>
          {!isPos && <div style={{ background: '#f97316', height: 14, borderRadius: '3px 0 0 3px', width: `${pctWidth}%` }} />}
        </div>
        <div style={{ width: 1, height: 14, background: 'var(--nd-border)' }} />
        <div style={{ flex: 1 }}>
          {isPos && <div style={{ background: '#3b82f6', height: 14, borderRadius: '0 3px 3px 0', width: `${pctWidth}%` }} />}
        </div>
        <span style={{ fontSize: 12, width: 40, textAlign: 'right', color: 'var(--nd-text-2)' }}>{val.toFixed(2)}</span>
      </div>
    );
  };

  if (loading) {
    return (
      <div className="nd-loading">
        <span className="material-icons nd-spin">autorenew</span>
        <span>Loading risk analytics...</span>
      </div>
    );
  }

  const TABS: { id: Tab; label: string; icon: string }[] = [
    { id: 'risk',         label: 'Risk Overview',    icon: 'shield' },
    { id: 'stress',       label: 'Stress Testing',   icon: 'bolt' },
    { id: 'factors',      label: 'Factor Analysis',  icon: 'science' },
    { id: 'optimization', label: 'Optimization',     icon: 'balance' },
  ];

  return (
    <div>
      <div style={{ marginBottom: 20 }}>
        <h1 className="nd-page-title">Risk Analytics</h1>
        <p className="nd-page-sub">Aladdin-inspired portfolio risk management — VaR, stress testing, factor analysis, optimization</p>
      </div>

      <div className="nd-tabs">
        {TABS.map(t => (
          <button key={t.id} className={`nd-tab${activeTab === t.id ? ' active' : ''}`} onClick={() => setActiveTab(t.id)}>
            <span className="material-icons" style={{ fontSize: 16, verticalAlign: 'middle', marginRight: 5 }}>{t.icon}</span>
            {t.label}
          </button>
        ))}
      </div>

      {/* ── TAB 1: RISK OVERVIEW ─────────────────────────────────────────── */}
      {activeTab === 'risk' && riskMetrics && (
        <div>
          <div className="nd-card" style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 20 }}>
            <div style={{ flex: 1 }}>
              <p className="nd-label">Portfolio Value</p>
              <p className="nd-value-xl">₹{riskMetrics.portfolioValue.toLocaleString('en-IN')}</p>
            </div>
            <p style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>As of {new Date(riskMetrics.asOf).toLocaleString('en-IN')}</p>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 12 }}>
            {[
              { label: 'VaR 95% (1-day)',  value: money(riskMetrics.var951Day),              sub: 'Max expected loss',  color: 'var(--nd-red)' },
              { label: 'VaR 99% (1-day)',  value: money(riskMetrics.var991Day),              sub: 'Tail risk',          color: 'var(--nd-red)' },
              { label: 'Portfolio Beta',   value: riskMetrics.portfolioBeta.toFixed(3),      sub: 'vs NIFTY 50',        color: '#f97316' },
              { label: 'Ann. Volatility',  value: pct(riskMetrics.annualizedVolatility),     sub: 'Std dev of returns', color: '#f97316' },
            ].map(m => (
              <div key={m.label} className="nd-metric">
                <p className="nd-metric-label">{m.label}</p>
                <p className="nd-metric-value" style={{ color: m.color }}>{m.value}</p>
                <p className="nd-metric-sub">{m.sub}</p>
              </div>
            ))}
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 20 }}>
            {[
              { label: 'Sharpe Ratio',      value: riskMetrics.sharpeRatio.toFixed(3),       color: 'var(--nd-green)' },
              { label: 'Sortino Ratio',     value: riskMetrics.sortinoRatio.toFixed(3),      color: 'var(--nd-green)' },
              { label: 'CVaR 95%',          value: money(riskMetrics.cvar95),                color: 'var(--nd-red)' },
              { label: 'Max Drawdown',      value: pct(Math.abs(riskMetrics.maxDrawdown)),   color: 'var(--nd-red)' },
              { label: 'VaR 95% (10-day)', value: money(riskMetrics.var9510Day),             color: 'var(--nd-red)' },
              { label: 'VaR 99% (10-day)', value: money(riskMetrics.var9910Day),             color: 'var(--nd-red)' },
              { label: 'Tracking Error',    value: pct(riskMetrics.trackingError),           color: '#f97316' },
              { label: 'Info. Ratio',       value: riskMetrics.informationRatio.toFixed(3),  color: 'var(--nd-blue)' },
            ].map(m => (
              <div key={m.label} className="nd-metric">
                <p className="nd-metric-label">{m.label}</p>
                <p style={{ fontSize: 17, fontWeight: 700, color: m.color }}>{m.value}</p>
              </div>
            ))}
          </div>

          <div className="nd-card" style={{ padding: 0 }}>
            <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--nd-border)' }}>
              <h2 className="nd-section-title" style={{ margin: 0 }}>Per-Holding Risk Contribution</h2>
            </div>
            <div style={{ overflowX: 'auto' }}>
              <table className="nd-table">
                <thead>
                  <tr>
                    <th>Symbol</th><th>Name</th>
                    <th className="text-right">Weight</th>
                    <th className="text-right">Beta</th>
                    <th className="text-right">VaR Contribution</th>
                  </tr>
                </thead>
                <tbody>
                  {riskMetrics.holdingsVar.map(h => (
                    <tr key={h.symbol}>
                      <td style={{ fontWeight: 700, color: 'var(--nd-green)' }}>{h.symbol}</td>
                      <td style={{ color: 'var(--nd-text-2)' }}>{h.name}</td>
                      <td className="text-right">{pct(h.weight)}</td>
                      <td className="text-right">{h.beta.toFixed(2)}</td>
                      <td className="text-right" style={{ color: 'var(--nd-red)', fontWeight: 600 }}>−{money(h.varContribution)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* ── TAB 2: STRESS TESTING ────────────────────────────────────────── */}
      {activeTab === 'stress' && stressTest && (
        <div>
          <div className="nd-card" style={{ marginBottom: 16, padding: '14px 20px' }}>
            <p className="nd-label">Portfolio Value at Risk</p>
            <p className="nd-value-xl">₹{stressTest.portfolioValue.toLocaleString('en-IN')}</p>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
            {stressTest.scenarios.map((s: StressScenario) => (
              <div key={s.name} className="nd-card" style={{ borderTop: `3px solid ${severityBorder[s.severity]}`, padding: 0 }}>
                <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--nd-border)' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 6 }}>
                    <div>
                      <p style={{ fontWeight: 700, fontSize: 14.5 }}>{s.name}</p>
                      <p style={{ fontSize: 12, color: 'var(--nd-text-2)', marginTop: 2 }}>{s.period} · {s.durationDays} days</p>
                    </div>
                    <span className={severityBadge[s.severity]} style={{ background: `${severityBorder[s.severity]}20`, color: severityBorder[s.severity] }}>
                      {s.severity}
                    </span>
                  </div>
                  <p style={{ fontSize: 12.5, color: 'var(--nd-text-2)' }}>{s.description}</p>
                </div>
                <div style={{ padding: '14px 20px', display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
                  <div>
                    <p style={{ fontSize: 11, color: 'var(--nd-text-3)', textTransform: 'uppercase', letterSpacing: '0.3px' }}>Market</p>
                    <p style={{ fontWeight: 700, color: 'var(--nd-red)' }}>{pct(s.marketReturn)}</p>
                  </div>
                  <div>
                    <p style={{ fontSize: 11, color: 'var(--nd-text-3)', textTransform: 'uppercase', letterSpacing: '0.3px' }}>Portfolio</p>
                    <p style={{ fontWeight: 700, color: 'var(--nd-red)' }}>{pct(s.portfolioReturn)}</p>
                  </div>
                  <div>
                    <p style={{ fontSize: 11, color: 'var(--nd-text-3)', textTransform: 'uppercase', letterSpacing: '0.3px' }}>Est. P&L</p>
                    <p style={{ fontWeight: 700, color: 'var(--nd-red)' }}>−{money(s.portfolioPnl)}</p>
                  </div>
                </div>
                <div style={{ padding: '0 20px 14px' }}>
                  <button onClick={() => setExpandedScenario(expandedScenario === s.name ? null : s.name)}
                    style={{ fontSize: 12, color: 'var(--nd-green)', background: 'none', border: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4 }}>
                    <span className="material-icons" style={{ fontSize: 14 }}>{expandedScenario === s.name ? 'expand_less' : 'expand_more'}</span>
                    {expandedScenario === s.name ? 'Hide' : 'Show'} per-stock impact
                  </button>
                  {expandedScenario === s.name && (
                    <table className="nd-table" style={{ marginTop: 10, fontSize: 12 }}>
                      <thead><tr><th>Symbol</th><th className="text-right">Return</th><th className="text-right">P&L</th></tr></thead>
                      <tbody>
                        {s.holdingsImpact.map(h => (
                          <tr key={h.symbol}>
                            <td style={{ fontWeight: 600 }}>{h.symbol}</td>
                            <td className="text-right" style={{ color: 'var(--nd-red)' }}>{pct(h.return)}</td>
                            <td className="text-right" style={{ color: 'var(--nd-red)' }}>−{money(h.pnl)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── TAB 3: FACTOR ANALYSIS ───────────────────────────────────────── */}
      {activeTab === 'factors' && factors && (
        <div>
          <div className="nd-card" style={{ marginBottom: 16 }}>
            <h2 className="nd-section-title">Portfolio Factor Exposures</h2>
            <p style={{ fontSize: 12, color: 'var(--nd-text-2)', marginBottom: 20 }}>Fama-French 5-Factor · Positive = overweight, Negative = underweight vs market · Scale: −2.0 to +2.0</p>
            {[
              { label: 'Market (Beta)', val: factors.factorExposures.marketBeta,    desc: 'Broad market sensitivity' },
              { label: 'Size (SMB)',    val: factors.factorExposures.sizeSmb,        desc: 'Small-minus-Big premium' },
              { label: 'Value (HML)',   val: factors.factorExposures.valueHml,       desc: 'High-minus-Low premium' },
              { label: 'Momentum',     val: factors.factorExposures.momentumMom,    desc: 'Momentum factor' },
              { label: 'Quality',      val: factors.factorExposures.quality,        desc: 'Profitability & quality' },
            ].map(f => (
              <div key={f.label} style={{ marginBottom: 16 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                  <span style={{ fontWeight: 600, fontSize: 13.5 }}>{f.label}</span>
                  <span style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>{f.desc}</span>
                </div>
                {factorBar(f.val)}
              </div>
            ))}
            <div style={{ display: 'flex', gap: 20, marginTop: 10, fontSize: 12, color: 'var(--nd-text-2)' }}>
              <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                <span style={{ display: 'inline-block', width: 12, height: 12, background: '#f97316', borderRadius: 2 }} /> Negative
              </span>
              <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                <span style={{ display: 'inline-block', width: 12, height: 12, background: '#3b82f6', borderRadius: 2 }} /> Positive
              </span>
            </div>
          </div>

          <div className="nd-card" style={{ marginBottom: 16 }}>
            <h2 className="nd-section-title">Risk Variance Decomposition</h2>
            <div style={{ display: 'flex', borderRadius: 6, overflow: 'hidden', height: 22, marginBottom: 12 }}>
              {[
                { key: 'market',       label: 'Market',       color: '#3b82f6' },
                { key: 'size',         label: 'Size',         color: '#7c3aed' },
                { key: 'value',        label: 'Value',        color: '#22c55e' },
                { key: 'momentum',     label: 'Momentum',     color: '#f97316' },
                { key: 'idiosyncratic',label: 'Idiosyncratic',color: '#94a3b8' },
              ].map(seg => (
                <div key={seg.key} style={{ width: `${factors.factorContributions[seg.key] * 100}%`, background: seg.color }}
                  title={`${seg.label}: ${(factors.factorContributions[seg.key] * 100).toFixed(1)}%`} />
              ))}
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16, fontSize: 12 }}>
              {[
                { key: 'market', label: 'Market', color: '#3b82f6' },
                { key: 'size', label: 'Size', color: '#7c3aed' },
                { key: 'value', label: 'Value', color: '#22c55e' },
                { key: 'momentum', label: 'Momentum', color: '#f97316' },
                { key: 'idiosyncratic', label: 'Idiosyncratic', color: '#94a3b8' },
              ].map(seg => (
                <span key={seg.key} style={{ display: 'flex', alignItems: 'center', gap: 5, color: 'var(--nd-text-2)' }}>
                  <span style={{ display: 'inline-block', width: 10, height: 10, background: seg.color, borderRadius: 2 }} />
                  {seg.label}: {(factors.factorContributions[seg.key] * 100).toFixed(1)}%
                </span>
              ))}
            </div>
          </div>

          <div className="nd-card" style={{ padding: 0 }}>
            <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--nd-border)' }}>
              <h2 className="nd-section-title" style={{ margin: 0 }}>Per-Holding Factor Loadings</h2>
            </div>
            <div style={{ overflowX: 'auto' }}>
              <table className="nd-table">
                <thead><tr>
                  <th>Symbol</th><th className="text-right">Weight</th>
                  <th className="text-right">Beta</th><th className="text-right">Size</th>
                  <th className="text-right">Value</th><th className="text-right">Momentum</th>
                  <th className="text-right">Quality</th>
                </tr></thead>
                <tbody>
                  {factors.holdingsFactors.map(h => (
                    <tr key={h.symbol}>
                      <td style={{ fontWeight: 700, color: 'var(--nd-green)' }}>{h.symbol}</td>
                      <td className="text-right">{pct(h.weight)}</td>
                      <td className="text-right" style={{ fontWeight: 600, color: h.beta > 1 ? '#f97316' : 'var(--nd-green)' }}>{h.beta.toFixed(2)}</td>
                      <td className="text-right" style={{ color: h.size >= 0 ? '#3b82f6' : '#f97316' }}>{h.size.toFixed(2)}</td>
                      <td className="text-right" style={{ color: h.value >= 0 ? '#3b82f6' : '#f97316' }}>{h.value.toFixed(2)}</td>
                      <td className="text-right" style={{ color: h.momentum >= 0 ? '#3b82f6' : '#f97316' }}>{h.momentum.toFixed(2)}</td>
                      <td className="text-right" style={{ color: h.quality >= 0.5 ? 'var(--nd-green)' : '#f97316' }}>{h.quality.toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* ── TAB 4: OPTIMIZATION ──────────────────────────────────────────── */}
      {activeTab === 'optimization' && optimization && (
        <div>

          {/* ── Trigger bar ── always visible ── */}
          <div className="nd-card" style={{ marginBottom: 20, display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <span className="material-icons" style={{ fontSize: 28, color: 'var(--nd-green)' }}>psychology</span>
              <div>
                <h2 className="nd-section-title" style={{ margin: 0 }}>AI Portfolio Optimizer</h2>
                <p style={{ fontSize: 12, color: 'var(--nd-text-2)', marginTop: 3 }}>
                  Runs your portfolio data through the local LLM — returns analysis + all optimization data as a unified report
                </p>
              </div>
            </div>
            <button
              onClick={runLlmAnalysis}
              disabled={llmLoading}
              style={{
                display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0,
                padding: '10px 22px', borderRadius: 10, border: 'none',
                cursor: llmLoading ? 'not-allowed' : 'pointer',
                background: llmLoading ? 'var(--nd-text-3)' : 'var(--nd-green)',
                color: '#fff', fontWeight: 600, fontSize: 14, transition: 'background 0.15s',
              }}
            >
              {llmLoading
                ? <><span style={{ width: 16, height: 16, border: '2px solid rgba(255,255,255,0.3)', borderTopColor: '#fff', borderRadius: '50%', display: 'inline-block', animation: 'nd-spin 0.7s linear infinite' }} />Analyzing…</>
                : <><span className="material-icons" style={{ fontSize: 18 }}>psychology</span>{llmAnalysis ? 'Re-run Analysis' : 'Run AI Analysis'}</>
              }
            </button>
          </div>

          {/* ── Error ── */}
          {llmError && (
            <div style={{ background: 'var(--nd-red-50)', border: '1px solid #fca5a5', borderRadius: 10, padding: '12px 16px', marginBottom: 20, display: 'flex', alignItems: 'flex-start', gap: 10 }}>
              <span className="material-icons" style={{ fontSize: 18, color: 'var(--nd-red)', flexShrink: 0, marginTop: 1 }}>error_outline</span>
              <span style={{ fontSize: 13, color: 'var(--nd-red)' }}>{llmError}</span>
            </div>
          )}

          {/* ── Loading skeleton ── */}
          {llmLoading && (
            <div className="nd-card" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '48px 24px', gap: 16, borderTop: '3px solid var(--nd-green)' }}>
              <span style={{ width: 40, height: 40, border: '3px solid var(--nd-border)', borderTopColor: 'var(--nd-green)', borderRadius: '50%', display: 'inline-block', animation: 'nd-spin 0.7s linear infinite' }} />
              <p style={{ fontWeight: 600, fontSize: 15 }}>Analyzing your portfolio…</p>
              <p style={{ fontSize: 13, color: 'var(--nd-text-2)', textAlign: 'center', maxWidth: 420 }}>
                The AI is reviewing your holdings, beta exposure, factor loadings, and optimization opportunities. This may take 20–60 seconds.
              </p>
            </div>
          )}

          {/* ── UNIFIED AI REPORT — shown after analysis ── */}
          {llmAnalysis && !llmLoading && (
            <div className="nd-card" style={{ borderTop: '3px solid var(--nd-green)', padding: 0 }}>

              {/* Report header */}
              <div style={{ padding: '18px 24px', borderBottom: '1px solid var(--nd-border)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <span className="material-icons" style={{ fontSize: 22, color: 'var(--nd-green)' }}>psychology</span>
                  <div>
                    <h2 className="nd-section-title" style={{ margin: 0 }}>AI Optimization Report</h2>
                    <p style={{ fontSize: 11, color: 'var(--nd-text-3)', marginTop: 2 }}>
                      Portfolio value ₹{optimization.portfolioValue.toLocaleString('en-IN')} · {optimization.rebalancingActions.length} holdings analyzed
                    </p>
                  </div>
                </div>
                <div style={{ textAlign: 'right' }}>
                  <p style={{ fontSize: 11, color: 'var(--nd-text-3)', fontFamily: 'monospace' }}>{llmModel}</p>
                  {llmGeneratedAt && <p style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>{new Date(llmGeneratedAt).toLocaleTimeString('en-IN')}</p>}
                </div>
              </div>

              {/* ── Section 1: AI narrative ── */}
              <div style={{ padding: '24px 24px 0' }}>
                <div style={{ fontSize: 13.5, lineHeight: 1.8, color: 'var(--nd-text-1)' }}>
                  {llmAnalysis.split('\n').map((line, i) => {
                    if (line.startsWith('## ')) {
                      return (
                        <div key={i} style={{ fontWeight: 700, fontSize: 13.5, color: 'var(--nd-green)', marginTop: i === 0 ? 0 : 22, marginBottom: 8, display: 'flex', alignItems: 'center', gap: 6, textTransform: 'uppercase', letterSpacing: '0.4px' }}>
                          <span className="material-icons" style={{ fontSize: 14 }}>chevron_right</span>
                          {line.replace('## ', '')}
                        </div>
                      );
                    }
                    if (line.startsWith('- ') || line.match(/^\d+\. /)) {
                      return <div key={i} style={{ paddingLeft: 18, marginBottom: 5, color: 'var(--nd-text-1)' }}>{line}</div>;
                    }
                    if (line.trim() === '') return <div key={i} style={{ height: 8 }} />;
                    return <div key={i} style={{ marginBottom: 4 }}>{line}</div>;
                  })}
                </div>
              </div>

              {/* ── Divider ── */}
              <div style={{ borderTop: '1px solid var(--nd-border)', margin: '28px 0 0' }} />

              {/* ── Section 2: Portfolio scenario cards ── */}
              <div style={{ padding: '20px 24px' }}>
                <p style={{ fontWeight: 700, fontSize: 13, textTransform: 'uppercase', letterSpacing: '0.5px', color: 'var(--nd-text-2)', marginBottom: 14 }}>Portfolio Scenarios</p>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 14 }}>
                  {[
                    { label: 'Current Portfolio', p: optimization.currentPortfolio,     accent: 'var(--nd-border)', icon: 'account_balance_wallet' },
                    { label: 'Min Variance',      p: optimization.minVariancePortfolio, accent: '#3b82f6',          icon: 'compress' },
                    { label: 'Max Sharpe',        p: optimization.maxSharpePortfolio,   accent: 'var(--nd-green)', icon: 'stars' },
                  ].map(({ label, p, accent, icon }) => (
                    <div key={label} style={{ background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderTop: `3px solid ${accent}`, borderRadius: 10, padding: '14px 16px' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 12 }}>
                        <span className="material-icons" style={{ fontSize: 16, color: accent }}>{icon}</span>
                        <span style={{ fontWeight: 600, fontSize: 13.5 }}>{label}</span>
                      </div>
                      {[
                        { l: 'Expected Return', v: pct(p.expectedReturn), c: 'var(--nd-green)' },
                        { l: 'Volatility',      v: pct(p.volatility),     c: '#f97316' },
                        { l: 'Sharpe Ratio',    v: p.sharpeRatio.toFixed(3), c: '#3b82f6' },
                      ].map(r => (
                        <div key={r.l} style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 0', borderBottom: '1px solid var(--nd-border)', fontSize: 12.5 }}>
                          <span style={{ color: 'var(--nd-text-2)' }}>{r.l}</span>
                          <strong style={{ color: r.c }}>{r.v}</strong>
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
              </div>

              {/* ── Section 3: Efficient Frontier ── */}
              <div style={{ padding: '0 24px 20px' }}>
                <p style={{ fontWeight: 700, fontSize: 13, textTransform: 'uppercase', letterSpacing: '0.5px', color: 'var(--nd-text-2)', marginBottom: 14 }}>Efficient Frontier</p>
                <div style={{ overflowX: 'auto' }}>
                  <svg viewBox="0 0 500 240" style={{ width: '100%', maxWidth: 680, height: 220 }}>
                    <line x1="50" y1="10" x2="50" y2="210" stroke="var(--nd-border)" strokeWidth="1" />
                    <line x1="50" y1="210" x2="490" y2="210" stroke="var(--nd-border)" strokeWidth="1" />
                    <text x="270" y="235" textAnchor="middle" fontSize="11" fill="var(--nd-text-2)">Volatility (σ)</text>
                    <text x="15" y="110" textAnchor="middle" fontSize="11" fill="var(--nd-text-2)" transform="rotate(-90 15 110)">Return (μ)</text>
                    {(() => {
                      const pts = optimization.efficientFrontier;
                      const minV = Math.min(...pts.map(p => p.volatility));
                      const maxV = Math.max(...pts.map(p => p.volatility));
                      const minR = Math.min(...pts.map(p => p.return));
                      const maxR = Math.max(...pts.map(p => p.return));
                      const sx = (v: number) => 50 + ((v - minV) / (maxV - minV)) * 420;
                      const sy = (r: number) => 210 - ((r - minR) / (maxR - minR)) * 185;
                      const polyline = pts.map(p => `${sx(p.volatility).toFixed(1)},${sy(p.return).toFixed(1)}`).join(' ');
                      const cur = optimization.currentPortfolio;
                      const ms  = optimization.maxSharpePortfolio;
                      const mv  = optimization.minVariancePortfolio;
                      return (
                        <>
                          <polyline points={polyline} fill="none" stroke="#3b82f6" strokeWidth="2.5" strokeLinecap="round" />
                          {pts.map((p, i) => <circle key={i} cx={sx(p.volatility)} cy={sy(p.return)} r="3" fill="#3b82f6" fillOpacity="0.45" />)}
                          <circle cx={sx(cur.volatility)} cy={sy(cur.expectedReturn)} r="7" fill="#94a3b8" stroke="white" strokeWidth="2" />
                          <text x={sx(cur.volatility) + 10} y={sy(cur.expectedReturn) - 5} fontSize="10" fill="var(--nd-text-2)">Current</text>
                          <circle cx={sx(ms.volatility)} cy={sy(ms.expectedReturn)} r="7" fill="var(--nd-green)" stroke="white" strokeWidth="2" />
                          <text x={sx(ms.volatility) + 10} y={sy(ms.expectedReturn) - 5} fontSize="10" fill="var(--nd-text-2)">Max Sharpe</text>
                          <circle cx={sx(mv.volatility)} cy={sy(mv.expectedReturn)} r="7" fill="#3b82f6" stroke="white" strokeWidth="2" />
                          <text x={sx(mv.volatility) + 10} y={sy(mv.expectedReturn) + 14} fontSize="10" fill="var(--nd-text-2)">Min Var</text>
                        </>
                      );
                    })()}
                  </svg>
                </div>
              </div>

              {/* ── Section 4: Rebalancing table ── */}
              <div style={{ borderTop: '1px solid var(--nd-border)' }}>
                <div style={{ padding: '14px 24px', borderBottom: '1px solid var(--nd-border)', display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span className="material-icons" style={{ fontSize: 16, color: 'var(--nd-green)' }}>swap_horiz</span>
                  <h2 className="nd-section-title" style={{ margin: 0 }}>Rebalancing Actions → Max Sharpe</h2>
                </div>
                <div style={{ overflowX: 'auto' }}>
                  <table className="nd-table">
                    <thead><tr>
                      <th>Symbol</th><th className="text-right">Current</th><th className="text-right">Target</th>
                      <th className="text-right">Delta</th><th style={{ textAlign: 'center' }}>Action</th>
                      <th className="text-right">Shares</th><th className="text-right">Est. Value</th>
                    </tr></thead>
                    <tbody>
                      {optimization.rebalancingActions.map(a => (
                        <tr key={a.symbol}>
                          <td style={{ fontWeight: 700, color: 'var(--nd-green)' }}>{a.symbol}</td>
                          <td className="text-right">{pct(a.currentWeight)}</td>
                          <td className="text-right">{pct(a.targetWeight)}</td>
                          <td className="text-right" style={{ fontWeight: 600, color: a.weightDelta >= 0 ? 'var(--nd-green)' : 'var(--nd-red)' }}>
                            {sign(a.weightDelta)}{pct(Math.abs(a.weightDelta))}
                          </td>
                          <td style={{ textAlign: 'center' }}>
                            <span className={`nd-badge ${a.action === 'BUY' ? 'nd-badge-green' : a.action === 'SELL' ? 'nd-badge-red' : 'nd-badge-gray'}`}>
                              {a.action}
                            </span>
                          </td>
                          <td className="text-right">{a.sharesDelta > 0 ? a.sharesDelta : '—'}</td>
                          <td className="text-right">{a.estimatedValue > 0 ? `₹${a.estimatedValue.toLocaleString('en-IN')}` : '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

            </div>
          )}

          {/* ── Default view before AI analysis is run ── */}
          {!llmAnalysis && !llmLoading && (
            <>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16, marginBottom: 20 }}>
                {[
                  { label: 'Current Portfolio', p: optimization.currentPortfolio,     accent: 'var(--nd-border)', icon: 'account_balance_wallet' },
                  { label: 'Min Variance',      p: optimization.minVariancePortfolio, accent: '#3b82f6',          icon: 'compress' },
                  { label: 'Max Sharpe',        p: optimization.maxSharpePortfolio,   accent: 'var(--nd-green)', icon: 'stars' },
                ].map(({ label, p, accent, icon }) => (
                  <div key={label} className="nd-card" style={{ borderTop: `3px solid ${accent}` }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
                      <span className="material-icons" style={{ fontSize: 18, color: accent }}>{icon}</span>
                      <h3 style={{ fontWeight: 600, fontSize: 14.5 }}>{label}</h3>
                    </div>
                    {[
                      { l: 'Expected Return', v: pct(p.expectedReturn), c: 'var(--nd-green)' },
                      { l: 'Volatility',      v: pct(p.volatility),     c: '#f97316' },
                      { l: 'Sharpe Ratio',    v: p.sharpeRatio.toFixed(3), c: '#3b82f6' },
                    ].map(r => (
                      <div key={r.l} style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 0', borderBottom: '1px solid var(--nd-border)', fontSize: 13 }}>
                        <span style={{ color: 'var(--nd-text-2)' }}>{r.l}</span>
                        <strong style={{ color: r.c }}>{r.v}</strong>
                      </div>
                    ))}
                  </div>
                ))}
              </div>

              <div className="nd-card" style={{ marginBottom: 20 }}>
                <h2 className="nd-section-title">Efficient Frontier</h2>
                <div style={{ overflowX: 'auto' }}>
                  <svg viewBox="0 0 500 240" style={{ width: '100%', maxWidth: 680, height: 240 }}>
                    <line x1="50" y1="10" x2="50" y2="210" stroke="var(--nd-border)" strokeWidth="1" />
                    <line x1="50" y1="210" x2="490" y2="210" stroke="var(--nd-border)" strokeWidth="1" />
                    <text x="270" y="235" textAnchor="middle" fontSize="11" fill="var(--nd-text-2)">Volatility (σ)</text>
                    <text x="15" y="110" textAnchor="middle" fontSize="11" fill="var(--nd-text-2)" transform="rotate(-90 15 110)">Return (μ)</text>
                    {(() => {
                      const pts = optimization.efficientFrontier;
                      const minV = Math.min(...pts.map(p => p.volatility));
                      const maxV = Math.max(...pts.map(p => p.volatility));
                      const minR = Math.min(...pts.map(p => p.return));
                      const maxR = Math.max(...pts.map(p => p.return));
                      const sx = (v: number) => 50 + ((v - minV) / (maxV - minV)) * 420;
                      const sy = (r: number) => 210 - ((r - minR) / (maxR - minR)) * 185;
                      const polyline = pts.map(p => `${sx(p.volatility).toFixed(1)},${sy(p.return).toFixed(1)}`).join(' ');
                      const cur = optimization.currentPortfolio;
                      const ms  = optimization.maxSharpePortfolio;
                      const mv  = optimization.minVariancePortfolio;
                      return (
                        <>
                          <polyline points={polyline} fill="none" stroke="#3b82f6" strokeWidth="2.5" strokeLinecap="round" />
                          {pts.map((p, i) => <circle key={i} cx={sx(p.volatility)} cy={sy(p.return)} r="3" fill="#3b82f6" fillOpacity="0.45" />)}
                          <circle cx={sx(cur.volatility)} cy={sy(cur.expectedReturn)} r="7" fill="#94a3b8" stroke="white" strokeWidth="2" />
                          <text x={sx(cur.volatility) + 10} y={sy(cur.expectedReturn) - 5} fontSize="10" fill="var(--nd-text-2)">Current</text>
                          <circle cx={sx(ms.volatility)} cy={sy(ms.expectedReturn)} r="7" fill="var(--nd-green)" stroke="white" strokeWidth="2" />
                          <text x={sx(ms.volatility) + 10} y={sy(ms.expectedReturn) - 5} fontSize="10" fill="var(--nd-text-2)">Max Sharpe</text>
                          <circle cx={sx(mv.volatility)} cy={sy(mv.expectedReturn)} r="7" fill="#3b82f6" stroke="white" strokeWidth="2" />
                          <text x={sx(mv.volatility) + 10} y={sy(mv.expectedReturn) + 14} fontSize="10" fill="var(--nd-text-2)">Min Var</text>
                        </>
                      );
                    })()}
                  </svg>
                </div>
              </div>

              <div className="nd-card" style={{ padding: 0 }}>
                <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--nd-border)' }}>
                  <h2 className="nd-section-title" style={{ margin: 0 }}>Rebalancing Actions → Max Sharpe</h2>
                </div>
                <div style={{ overflowX: 'auto' }}>
                  <table className="nd-table">
                    <thead><tr>
                      <th>Symbol</th><th className="text-right">Current</th><th className="text-right">Target</th>
                      <th className="text-right">Delta</th><th style={{ textAlign: 'center' }}>Action</th>
                      <th className="text-right">Shares</th><th className="text-right">Est. Value</th>
                    </tr></thead>
                    <tbody>
                      {optimization.rebalancingActions.map(a => (
                        <tr key={a.symbol}>
                          <td style={{ fontWeight: 700, color: 'var(--nd-green)' }}>{a.symbol}</td>
                          <td className="text-right">{pct(a.currentWeight)}</td>
                          <td className="text-right">{pct(a.targetWeight)}</td>
                          <td className="text-right" style={{ fontWeight: 600, color: a.weightDelta >= 0 ? 'var(--nd-green)' : 'var(--nd-red)' }}>
                            {sign(a.weightDelta)}{pct(Math.abs(a.weightDelta))}
                          </td>
                          <td style={{ textAlign: 'center' }}>
                            <span className={`nd-badge ${a.action === 'BUY' ? 'nd-badge-green' : a.action === 'SELL' ? 'nd-badge-red' : 'nd-badge-gray'}`}>
                              {a.action}
                            </span>
                          </td>
                          <td className="text-right">{a.sharesDelta > 0 ? a.sharesDelta : '—'}</td>
                          <td className="text-right">{a.estimatedValue > 0 ? `₹${a.estimatedValue.toLocaleString('en-IN')}` : '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </>
          )}

        </div>
      )}
    </div>
  );
};

export default RiskAnalytics;
