import React, { useEffect, useState } from 'react';
import apiService from '../services/api';
import { Prediction } from '../types';

const inr = (v: number) =>
  `₹${v.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const Predictions: React.FC = () => {
  const [predictions, setPredictions] = useState<Record<string, Prediction>>({});
  const [loading, setLoading] = useState(true);
  const [selectedSymbol, setSelectedSymbol] = useState('SBIN');
  const [analysis, setAnalysis] = useState<any>(null);

  const stocks = ['SBIN', 'IDBI', 'SUZLON', 'RELIANCE', 'TCS', 'INFY', 'HDFCBANK', 'ICICIBANK'];

  useEffect(() => { fetchPredictions(); }, []);

  const fetchPredictions = async () => {
    try {
      setLoading(true);
      const all: Record<string, Prediction> = {};
      for (const symbol of stocks) {
        try {
          const r = await apiService.getPrediction(symbol);
          if (r.data) all[symbol] = r.data;
        } catch { /* skip */ }
      }
      setPredictions(all);
    } finally {
      setLoading(false);
    }
  };

  const fetchAnalysis = async (symbol: string) => {
    try {
      const r = await apiService.getCustomAnalysis(symbol);
      if (r.data) setAnalysis(r.data);
    } catch { /* skip */ }
  };

  const handleSelect = (symbol: string) => {
    setSelectedSymbol(symbol);
    fetchAnalysis(symbol);
  };

  if (loading) {
    return (
      <div className="nd-loading">
        <span className="material-icons nd-spin">autorenew</span>
        <span>Loading predictions...</span>
      </div>
    );
  }

  const selected = predictions[selectedSymbol];

  return (
    <div>
      <div style={{ marginBottom: 20 }}>
        <h1 className="nd-page-title">AI Predictions</h1>
        <p className="nd-page-sub">Machine-learning powered directional predictions with confidence scores</p>
      </div>

      {/* Prediction cards grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 12, marginBottom: 24 }}>
        {Object.entries(predictions).map(([symbol, pred]) => {
          const isUp   = pred.prediction === 'UP';
          const isDn   = pred.prediction === 'DOWN';
          const isSelected = selectedSymbol === symbol;
          return (
            <div
              key={symbol}
              onClick={() => handleSelect(symbol)}
              style={{
                padding: '16px',
                borderRadius: 10,
                border: `2px solid ${isSelected ? 'var(--nd-green)' : 'var(--nd-border)'}`,
                background: isSelected ? 'var(--nd-green-50)' : 'var(--nd-bg)',
                cursor: 'pointer',
                transition: 'border-color 0.15s, background 0.15s',
                boxShadow: 'var(--nd-shadow)',
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 10 }}>
                <p style={{ fontWeight: 700, fontSize: 15 }}>{symbol}</p>
                <span className={`nd-badge ${isUp ? 'nd-badge-green' : isDn ? 'nd-badge-red' : 'nd-badge-gray'}`}>
                  {isUp ? '▲' : isDn ? '▼' : '—'} {pred.prediction}
                </span>
              </div>
              <p style={{ fontSize: 18, fontWeight: 700, marginBottom: 4 }}>{inr(pred.currentPrice)}</p>
              <p style={{ fontSize: 12, color: 'var(--nd-text-2)' }}>
                Confidence: <strong>{(pred.confidence * 100).toFixed(0)}%</strong>
              </p>
              <p style={{ fontSize: 12, color: 'var(--nd-text-2)' }}>
                Target: <strong style={{ color: isUp ? 'var(--nd-green)' : 'var(--nd-red)' }}>{inr(pred.targetPrice)}</strong>
              </p>
            </div>
          );
        })}
      </div>

      {/* Detailed analysis */}
      {analysis && selected && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16 }}>
          {/* AI Recommendation */}
          <div className="nd-card">
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
              <span className="material-icons" style={{ color: 'var(--nd-purple)' }}>smart_toy</span>
              <h3 className="nd-section-title" style={{ margin: 0 }}>AI Recommendation</h3>
            </div>
            <div style={{
              padding: '14px 16px',
              borderRadius: 8,
              background: analysis.ai_recommendation.action === 'BUY' ? 'var(--nd-green-50)' : analysis.ai_recommendation.action === 'SELL' ? 'var(--nd-red-50)' : 'var(--nd-surface)',
              marginBottom: 12,
            }}>
              <p style={{ fontSize: 28, fontWeight: 700, color: analysis.ai_recommendation.action === 'BUY' ? 'var(--nd-green)' : analysis.ai_recommendation.action === 'SELL' ? 'var(--nd-red)' : 'var(--nd-text-2)' }}>
                {analysis.ai_recommendation.action}
              </p>
              <p style={{ fontSize: 13, color: 'var(--nd-text-2)', marginTop: 4 }}>
                Confidence: {(analysis.ai_recommendation.confidence * 100).toFixed(0)}%
              </p>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13 }}>
                <span style={{ color: 'var(--nd-text-2)' }}>Stop Loss</span>
                <strong style={{ color: 'var(--nd-red)' }}>{inr(selected.stopLoss)}</strong>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13 }}>
                <span style={{ color: 'var(--nd-text-2)' }}>Risk/Reward</span>
                <strong>{selected.riskRewardRatio.toFixed(2)}</strong>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13 }}>
                <span style={{ color: 'var(--nd-text-2)' }}>Upside</span>
                <strong style={{ color: 'var(--nd-green)' }}>{selected.upsidePotential.toFixed(2)}%</strong>
              </div>
            </div>
          </div>

          {/* Technical Analysis */}
          <div className="nd-card">
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
              <span className="material-icons" style={{ color: 'var(--nd-blue)' }}>candlestick_chart</span>
              <h3 className="nd-section-title" style={{ margin: 0 }}>Technical</h3>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {[
                { label: 'Trend',      value: analysis.technical_analysis.trend },
                { label: 'Strength',   value: `${(analysis.technical_analysis.strength * 100).toFixed(0)}%` },
                { label: 'Support',    value: `₹${analysis.technical_analysis.key_support.toFixed(2)}` },
                { label: 'Resistance', value: `₹${analysis.technical_analysis.key_resistance.toFixed(2)}` },
              ].map(r => (
                <div key={r.label} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, paddingBottom: 8, borderBottom: '1px solid var(--nd-border)' }}>
                  <span style={{ color: 'var(--nd-text-2)' }}>{r.label}</span>
                  <strong>{r.value}</strong>
                </div>
              ))}
            </div>
          </div>

          {/* Sentiment */}
          <div className="nd-card">
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
              <span className="material-icons" style={{ color: 'var(--nd-orange)' }}>sentiment_satisfied</span>
              <h3 className="nd-section-title" style={{ margin: 0 }}>Sentiment</h3>
            </div>
            {[
              { label: 'News Score',   val: analysis.sentiment_analysis.news_score,   color: 'var(--nd-blue)' },
              { label: 'Social Score', val: analysis.sentiment_analysis.social_score, color: 'var(--nd-green)' },
            ].map(s => (
              <div key={s.label} style={{ marginBottom: 14 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, marginBottom: 6 }}>
                  <span style={{ color: 'var(--nd-text-2)' }}>{s.label}</span>
                  <strong>{((s.val + 1) * 50).toFixed(0)}%</strong>
                </div>
                <div style={{ height: 6, background: 'var(--nd-surface-2)', borderRadius: 3, overflow: 'hidden' }}>
                  <div style={{
                    height: '100%',
                    width: `${Math.max(0, (s.val + 1) * 50)}%`,
                    background: s.color,
                    borderRadius: 3,
                  }} />
                </div>
              </div>
            ))}
            {analysis.fundamental_analysis && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 10, paddingTop: 10, borderTop: '1px solid var(--nd-border)' }}>
                {[
                  { label: 'P/E Ratio',   value: analysis.fundamental_analysis.pe_ratio.toFixed(2) },
                  { label: 'ROE',         value: `${analysis.fundamental_analysis.roe.toFixed(2)}%` },
                  { label: 'Debt/Equity', value: analysis.fundamental_analysis.debt_to_equity.toFixed(2) },
                ].map(f => (
                  <div key={f.label} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }}>
                    <span style={{ color: 'var(--nd-text-2)' }}>{f.label}</span>
                    <strong>{f.value}</strong>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Entry / Exit */}
          {analysis.ai_recommendation.entry_points && (
            <div className="nd-card">
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
                <span className="material-icons" style={{ color: 'var(--nd-green)', fontSize: 18 }}>login</span>
                <h3 className="nd-section-title" style={{ margin: 0 }}>Entry Points</h3>
              </div>
              {analysis.ai_recommendation.entry_points.map((price: number, idx: number) => (
                <div key={idx} style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 12px', background: 'var(--nd-green-50)', borderRadius: 6, marginBottom: 6, fontSize: 13 }}>
                  <span style={{ color: 'var(--nd-text-2)' }}>Entry {idx + 1}</span>
                  <strong>₹{price.toFixed(2)}</strong>
                </div>
              ))}
            </div>
          )}

          {analysis.ai_recommendation.exit_points && (
            <div className="nd-card">
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
                <span className="material-icons" style={{ color: 'var(--nd-orange)', fontSize: 18 }}>logout</span>
                <h3 className="nd-section-title" style={{ margin: 0 }}>Target Exits</h3>
              </div>
              {analysis.ai_recommendation.exit_points.map((price: number, idx: number) => (
                <div key={idx} style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 12px', background: 'var(--nd-surface)', borderRadius: 6, marginBottom: 6, fontSize: 13, border: '1px solid var(--nd-border)' }}>
                  <span style={{ color: 'var(--nd-text-2)' }}>Target {idx + 1}</span>
                  <strong style={{ color: 'var(--nd-green)' }}>₹{price.toFixed(2)}</strong>
                </div>
              ))}
            </div>
          )}

          {/* Reasoning */}
          {selected.reasoning && (
            <div className="nd-card">
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
                <span className="material-icons" style={{ color: 'var(--nd-purple)', fontSize: 18 }}>psychology</span>
                <h3 className="nd-section-title" style={{ margin: 0 }}>Reasoning</h3>
              </div>
              <p style={{ fontSize: 13, color: 'var(--nd-text-2)', lineHeight: 1.6 }}>{selected.reasoning}</p>
              {selected.factors && (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 12 }}>
                  {selected.factors.map((f: string, i: number) => (
                    <span key={i} className="nd-badge nd-badge-purple">{f}</span>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default Predictions;
