import React, { useEffect, useState } from 'react';
import apiService from '../../services/api';

// ── Performance + market-regime hero strip ────────────────────────────────────

const REGIME_STYLE: Record<string, { color: string; label: string; icon: string }> = {
  bullish: { color: '#22c55e', label: 'Risk-On · Bullish',  icon: 'trending_up' },
  bearish: { color: '#ef4444', label: 'Risk-Off · Bearish', icon: 'trending_down' },
  neutral: { color: '#f59e0b', label: 'Neutral',            icon: 'trending_flat' },
};

// ── AI forecast bits ──────────────────────────────────────────────────────────

// Accuracy sparkline — single series (rolling-20 out-of-sample accuracy) with a
// dashed 50% reference line. Neutral accent (not a bullish/bearish status color)
// so the line reads as "model quality", not market direction.
const ACC_COLOR = '#3b82f6';

const AccuracySparkline: React.FC<{ series: { d: string; a: number }[]; w?: number; h?: number }> = ({ series, w = 110, h = 30 }) => {
  if (!series || series.length < 2) return null;
  const PAD = 2;
  const vals = series.map(p => p.a);
  const lo = Math.min(0.45, ...vals) - 0.03;
  const hi = Math.max(0.55, ...vals) + 0.03;
  const range = hi - lo || 1;
  const xs = vals.map((_, i) => PAD + (i / (vals.length - 1)) * (w - PAD * 2));
  const ys = vals.map(v => h - PAD - ((v - lo) / range) * (h - PAD * 2));
  const line = xs.map((x, i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${ys[i].toFixed(1)}`).join(' ');
  const halfY = h - PAD - ((0.5 - lo) / range) * (h - PAD * 2);
  return (
    <svg width={w} height={h} style={{ display: 'block' }}>
      <line x1={PAD} y1={halfY.toFixed(1)} x2={w - PAD} y2={halfY.toFixed(1)}
        stroke="var(--nd-border)" strokeWidth="1" strokeDasharray="3 3" />
      <path d={line} fill="none" stroke={ACC_COLOR} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={xs[xs.length - 1].toFixed(1)} cy={ys[ys.length - 1].toFixed(1)} r="2.5" fill={ACC_COLOR} />
    </svg>
  );
};

// ── Regime detail modal ───────────────────────────────────────────────────────

const REGIME_IMPLICATIONS: Record<string, { headline: string; why: string; trading: string[]; watch: string[] }> = {
  bullish: {
    headline: 'Broad market is in a confirmed uptrend.',
    why: 'NIFTY 50\'s 20-day SMA is above the 50-day SMA (golden-cross alignment) AND the index gained ground over the last 5 sessions. Both momentum and trend filters are green.',
    trading: [
      'Long-biased setups have the macro wind at their back.',
      'BUY signals from individual stock agents carry higher conviction.',
      'Tighter stops are viable — pullbacks in uptrends tend to be shallow.',
    ],
    watch: [
      'A reversal below SMA20 would weaken the thesis.',
      'Momentum divergence (price rises but 5-day return fades) is an early warning.',
    ],
  },
  bearish: {
    headline: 'Broad market is in a confirmed downtrend.',
    why: 'NIFTY 50\'s 20-day SMA has crossed below the 50-day SMA (death-cross alignment) AND the index fell over the last 5 sessions. Both momentum and trend filters are red.',
    trading: [
      'BUY signals face a headwind — treat them with extra skepticism.',
      'SELL/HOLD signals are more likely to play out.',
      'Reduce position sizes or widen stops to allow for bear-market volatility.',
    ],
    watch: [
      'A reclaim of SMA20 above SMA50 flips the regime to neutral or bullish.',
      'Positive 5-day momentum first is often the leading signal of a reversal.',
    ],
  },
  neutral: {
    headline: 'Broad market is sending mixed signals.',
    why: 'Either SMA20 and SMA50 are aligned but momentum is counter-trend, or momentum is positive but the MAs are still bearish-aligned. One filter is green and one is red.',
    trading: [
      'No structural edge in either direction from the macro filter.',
      'Stock-level signals (RSI, momentum, sentiment) carry more weight than usual.',
      'Use tighter risk limits until the regime resolves.',
    ],
    watch: [
      'Watch for both conditions to align in the same direction to confirm a new regime.',
    ],
  },
};

const RegimeModal: React.FC<{ onClose: () => void }> = ({ onClose }) => {
  const [detail, setDetail] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    apiService.getRegimeDetail()
      .then((r: any) => setDetail(r?.data ?? {}))
      .catch(() => setDetail({}))
      .finally(() => setLoading(false));
  }, []);

  const regime: string = detail?.regime ?? 'neutral';
  const rg = REGIME_STYLE[regime] ?? REGIME_STYLE.neutral;
  const impl = REGIME_IMPLICATIONS[regime] ?? REGIME_IMPLICATIONS.neutral;
  const conditions: any[] = detail?.conditions ?? [];
  const fmtNum = (v: number | undefined) => v != null ? v.toLocaleString('en-IN', { minimumFractionDigits: 0, maximumFractionDigits: 2 }) : '—';

  return (
    <div onClick={e => { if (e.target === e.currentTarget) onClose(); }}
      style={{ position: 'fixed', inset: 0, background: '#00000088', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 12 }}>
      <div style={{ background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderRadius: 16, width: '100%', maxWidth: 500, maxHeight: '92vh', overflow: 'auto', boxShadow: '0 24px 64px #00000060' }}>

        {/* Header */}
        <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--nd-border)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span className="material-icons" style={{ fontSize: 22, color: rg.color }}>{rg.icon}</span>
            <div>
              <div style={{ fontSize: 16, fontWeight: 700, color: rg.color }}>{rg.label}</div>
              <div style={{ fontSize: 11, color: 'var(--nd-text-3)', textTransform: 'uppercase', letterSpacing: 0.5 }}>Market Regime · {detail?.index ?? 'NIFTY 50'}</div>
            </div>
          </div>
          <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer' }}>
            <span className="material-icons" style={{ color: 'var(--nd-text-3)', fontSize: 20 }}>close</span>
          </button>
        </div>

        <div style={{ padding: '16px 20px' }}>
          {loading ? (
            <div style={{ textAlign: 'center', padding: 32, color: 'var(--nd-text-3)' }}>
              <span className="material-icons nd-spin" style={{ fontSize: 22, verticalAlign: 'middle' }}>autorenew</span>
            </div>
          ) : (
            <>
              {/* Headline */}
              <p style={{ margin: '0 0 16px', fontSize: 13, fontWeight: 600, color: 'var(--nd-text-1)', lineHeight: 1.5 }}>{impl.headline}</p>

              {/* Raw indicators */}
              {detail?.niftyPrice != null && (
                <div style={{ background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 10, padding: '12px 14px', marginBottom: 16 }}>
                  <div style={{ fontSize: 11, color: 'var(--nd-text-3)', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 10 }}>Live Indicators</div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px 0' }}>
                    {[
                      { label: 'NIFTY 50 Price', value: `₹${fmtNum(detail.niftyPrice)}`, color: 'var(--nd-text-1)' },
                      { label: '20-day SMA', value: `₹${fmtNum(detail.sma20)}`, color: detail.sma20 > detail.sma50 ? 'var(--nd-green)' : 'var(--nd-red)' },
                      { label: '50-day SMA', value: `₹${fmtNum(detail.sma50)}`, color: 'var(--nd-text-2)' },
                      { label: '5-day Momentum', value: `${detail.mom5dPct >= 0 ? '+' : ''}${fmtNum(detail.mom5dPct)}%`, color: detail.mom5dPct >= 0 ? 'var(--nd-green)' : 'var(--nd-red)' },
                    ].map(row => (
                      <div key={row.label}>
                        <div style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>{row.label}</div>
                        <div style={{ fontSize: 14, fontWeight: 700, color: row.color }}>{row.value}</div>
                      </div>
                    ))}
                  </div>
                  {detail.candlesUsed && (
                    <div style={{ fontSize: 10, color: 'var(--nd-text-3)', marginTop: 8 }}>
                      Based on {detail.candlesUsed} daily candles
                      {detail.updatedAt ? ` · updated ${new Date(detail.updatedAt).toLocaleString('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: true })}` : ''}
                    </div>
                  )}
                </div>
              )}

              {/* Condition checklist */}
              {conditions.length > 0 && (
                <div style={{ marginBottom: 16 }}>
                  <div style={{ fontSize: 11, color: 'var(--nd-text-3)', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 8 }}>Classification Conditions</div>
                  {conditions.map((c: any) => (
                    <div key={c.id} style={{ display: 'flex', alignItems: 'flex-start', gap: 10, padding: '8px 0', borderBottom: '1px solid var(--nd-border)' }}>
                      <span className="material-icons" style={{ fontSize: 16, color: c.met ? 'var(--nd-green)' : 'var(--nd-red)', marginTop: 1, flexShrink: 0 }}>
                        {c.met ? 'check_circle' : 'cancel'}
                      </span>
                      <div>
                        <div style={{ fontSize: 12, fontWeight: 600, color: c.met ? 'var(--nd-text-1)' : 'var(--nd-text-3)' }}>{c.label}</div>
                        <div style={{ fontSize: 11, color: 'var(--nd-text-3)', marginTop: 1 }}>{c.detail}</div>
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {/* AI next-session forecast + its out-of-sample track record */}
              {detail?.ai?.prediction && (() => {
                const ai = detail.ai;
                const pred: string = ai.prediction.regime ?? 'neutral';
                const pRg = REGIME_STYLE[pred] ?? REGIME_STYLE.neutral;
                const probs: Record<string, number> = ai.prediction.probs ?? {};
                const acc = ai.accuracy ?? {};
                const beats = acc.overall != null && acc.persistence != null && acc.overall >= acc.persistence;
                return (
                  <div style={{ background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 10, padding: '12px 14px', marginBottom: 16 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 10 }}>
                      <span className="material-icons" style={{ fontSize: 14, color: ACC_COLOR }}>psychology</span>
                      <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--nd-text-3)', textTransform: 'uppercase', letterSpacing: 0.5 }}>
                        AI Forecast · Next Session
                      </span>
                      <span style={{ marginLeft: 'auto', fontSize: 14, fontWeight: 700, color: pRg.color }}>
                        {pred.charAt(0).toUpperCase() + pred.slice(1)} {probs[pred] != null ? `${Math.round(probs[pred] * 100)}%` : ''}
                      </span>
                    </div>
                    {/* class probability bars — label + bar + value, color follows the class */}
                    {['bullish', 'neutral', 'bearish'].map(cls => {
                      const p = probs[cls] ?? 0;
                      const c = (REGIME_STYLE[cls] ?? REGIME_STYLE.neutral).color;
                      return (
                        <div key={cls} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 5 }}>
                          <span style={{ fontSize: 10.5, color: 'var(--nd-text-2)', width: 48, textTransform: 'capitalize' }}>{cls}</span>
                          <div style={{ flex: 1, height: 6, background: 'var(--nd-border)', borderRadius: 3, overflow: 'hidden' }}>
                            <div style={{ width: `${Math.round(p * 100)}%`, height: '100%', background: c, borderRadius: 3 }} />
                          </div>
                          <span style={{ fontSize: 10.5, fontWeight: 600, color: 'var(--nd-text-2)', width: 30, textAlign: 'right' }}>{Math.round(p * 100)}%</span>
                        </div>
                      );
                    })}
                    {/* accuracy record — the honest bit */}
                    {acc.n > 0 && (
                      <div style={{ marginTop: 10, paddingTop: 10, borderTop: '1px solid var(--nd-border)' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                          <div>
                            <div style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>Prediction accuracy (rolling 20 sessions)</div>
                            <AccuracySparkline series={acc.series ?? []} w={200} h={44} />
                          </div>
                          <div style={{ fontSize: 11, color: 'var(--nd-text-2)', lineHeight: 1.7 }}>
                            <div>Out-of-sample: <b style={{ color: 'var(--nd-text-1)' }}>{Math.round((acc.overall ?? 0) * 100)}%</b> over {acc.n} sessions</div>
                            <div>Last 20: <b style={{ color: 'var(--nd-text-1)' }}>{Math.round((acc.recent20 ?? 0) * 100)}%</b></div>
                            <div>Naive "same as today": {Math.round((acc.persistence ?? 0) * 100)}%</div>
                          </div>
                        </div>
                        {!beats && (
                          <div style={{ marginTop: 8, fontSize: 10.5, color: '#f59e0b' }}>
                            ⚠ Model is not currently beating the persistence baseline — treat the forecast as low-signal.
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })()}

              {/* Why section */}
              <div style={{ background: 'var(--nd-surface)', border: `1px solid ${rg.color}30`, borderRadius: 10, padding: '12px 14px', marginBottom: 16 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
                  <span className="material-icons" style={{ fontSize: 14, color: rg.color }}>info</span>
                  <span style={{ fontSize: 11, fontWeight: 700, color: rg.color, textTransform: 'uppercase', letterSpacing: 0.4 }}>Why this regime</span>
                </div>
                <p style={{ margin: 0, fontSize: 12, color: 'var(--nd-text-2)', lineHeight: 1.6 }}>{impl.why}</p>
              </div>

              {/* Trading implications */}
              <div style={{ marginBottom: 16 }}>
                <div style={{ fontSize: 11, color: 'var(--nd-text-3)', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 8 }}>Trading Implications</div>
                {impl.trading.map((t, i) => (
                  <div key={i} style={{ display: 'flex', gap: 8, marginBottom: 6 }}>
                    <span style={{ fontSize: 12, color: rg.color, flexShrink: 0, marginTop: 1 }}>›</span>
                    <span style={{ fontSize: 12, color: 'var(--nd-text-2)', lineHeight: 1.5 }}>{t}</span>
                  </div>
                ))}
              </div>

              {/* Watch for */}
              <div style={{ background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.25)', borderRadius: 10, padding: '12px 14px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
                  <span className="material-icons" style={{ fontSize: 14, color: '#f59e0b' }}>warning_amber</span>
                  <span style={{ fontSize: 11, fontWeight: 700, color: '#f59e0b', textTransform: 'uppercase', letterSpacing: 0.4 }}>Watch For</span>
                </div>
                {impl.watch.map((w, i) => (
                  <div key={i} style={{ display: 'flex', gap: 8, marginBottom: i < impl.watch.length - 1 ? 4 : 0 }}>
                    <span style={{ fontSize: 12, color: '#f59e0b', flexShrink: 0 }}>›</span>
                    <span style={{ fontSize: 12, color: 'var(--nd-text-2)', lineHeight: 1.5 }}>{w}</span>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
};

// ── Total-return sparkline ────────────────────────────────────────────────────
const ReturnSparkline: React.FC<{ points: number[]; good: boolean }> = ({ points, good }) => {
  if (points.length < 2) return null;
  const W = 130, H = 38, PAD = 2;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const range = max - min || 1;
  const xs = points.map((_, i) => PAD + (i / (points.length - 1)) * (W - PAD * 2));
  const ys = points.map(v => H - PAD - ((v - min) / range) * (H - PAD * 2));
  const line = xs.map((x, i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${ys[i].toFixed(1)}`).join(' ');
  const area = `${line} L${xs[xs.length - 1].toFixed(1)},${H} L${xs[0].toFixed(1)},${H} Z`;
  const color = good ? '#22c55e' : '#ef4444';
  const zeroY = H - PAD - ((0 - min) / range) * (H - PAD * 2);
  return (
    <svg width={W} height={H} style={{ display: 'block', marginTop: 4 }}>
      <defs>
        <linearGradient id="sp-fill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.25" />
          <stop offset="100%" stopColor={color} stopOpacity="0.02" />
        </linearGradient>
      </defs>
      {/* zero baseline */}
      {zeroY >= PAD && zeroY <= H && (
        <line x1={PAD} y1={zeroY.toFixed(1)} x2={W - PAD} y2={zeroY.toFixed(1)}
          stroke="rgba(255,255,255,0.12)" strokeWidth="1" strokeDasharray="3 3" />
      )}
      <path d={area} fill="url(#sp-fill)" />
      <path d={line} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
      {/* endpoint dot */}
      <circle cx={xs[xs.length - 1].toFixed(1)} cy={ys[ys.length - 1].toFixed(1)} r="2.5" fill={color} />
    </svg>
  );
};

const PerformanceRegimeStrip: React.FC = () => {
  const [pm, setPm]               = useState<any>(null);
  const [regime, setRegime]       = useState<string>('neutral');
  const [equity, setEquity]       = useState<number[]>([]);
  const [showRegime, setShowRegime] = useState(false);
  const [ai, setAi]               = useState<any>(null);

  useEffect(() => {
    apiService.getPortfolioMetrics().then((r: any) => { if (r && !r.error) setPm(r); }).catch(() => {});
    apiService.aiWatchlist().then((r: any) => { const d = r?.data; if (d?.marketRegime) setRegime(d.marketRegime); }).catch(() => {});
    apiService.getRegimeDetail().then((r: any) => { const a = r?.data?.ai; if (a?.prediction) setAi(a); }).catch(() => {});
    // Fetch cumulative equity curve for the sparkline (all sources, coarse sample)
    apiService.getLearningCurve('PAPER,LIVE,REPLAY,BACKTEST', 50).then((r: any) => {
      const pts: any[] = r?.data?.points ?? [];
      if (pts.length >= 2) setEquity(pts.map((p: any) => p.cumEquity ?? 0));
    }).catch(() => {});
  }, []);

  const rg = REGIME_STYLE[regime] ?? REGIME_STYLE.neutral;
  const returnGood = (pm?.totalReturnPct ?? 0) >= 0;
  const statTiles = pm && pm.totalTrades > 0 ? [
    { label: 'Win Rate', value: `${(pm.winRate * 100).toFixed(0)}%`,       good: pm.winRate >= 0.5 },
    { label: 'Sharpe',   value: pm.sharpeRatio?.toFixed(2),                good: pm.sharpeRatio >= 1 },
    { label: 'Max DD',   value: `${pm.maxDrawdownPct?.toFixed(1)}%`,       good: pm.maxDrawdownPct < 15 },
  ] : [];

  return (
    <>
    {showRegime && <RegimeModal onClose={() => setShowRegime(false)} />}
    <div className="nd-card" style={{ padding: '12px 16px', marginBottom: 20, display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
      {/* Market regime chip — clickable */}
      <div
        onClick={() => setShowRegime(true)}
        title="Click for full regime breakdown"
        style={{
          display: 'flex', alignItems: 'center', gap: 8,
          paddingRight: 16, borderRight: (statTiles.length || pm) ? '1px solid var(--nd-border)' : 'none',
          cursor: 'pointer', borderRadius: 8, padding: '4px 8px',
          transition: 'background 0.15s',
        }}
        onMouseEnter={e => (e.currentTarget.style.background = `${rg.color}12`)}
        onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
      >
        <span className="material-icons" style={{ fontSize: 20, color: rg.color }}>{rg.icon}</span>
        <div>
          <div style={{ fontSize: 10, color: 'var(--nd-text-3)', textTransform: 'uppercase', letterSpacing: 0.6 }}>Market Regime</div>
          <div style={{ fontSize: 13, fontWeight: 700, color: rg.color }}>{rg.label}</div>
        </div>
        <span className="material-icons" style={{ fontSize: 13, color: 'var(--nd-text-3)', marginLeft: 2 }}>open_in_new</span>
      </div>

      {/* AI next-session forecast — click opens the same modal for the full record */}
      {ai?.prediction && (() => {
        const pred: string = ai.prediction.regime ?? 'neutral';
        const pRg = REGIME_STYLE[pred] ?? REGIME_STYLE.neutral;
        const prob = ai.prediction.probs?.[pred];
        return (
          <div
            onClick={() => setShowRegime(true)}
            title="AI forecast for the next session — click for accuracy record"
            style={{
              display: 'flex', alignItems: 'center', gap: 10,
              paddingRight: 16, borderRight: (pm && pm.totalTrades > 0) ? '1px solid var(--nd-border)' : 'none',
              cursor: 'pointer', borderRadius: 8, padding: '4px 8px',
            }}
            onMouseEnter={e => (e.currentTarget.style.background = `${ACC_COLOR}12`)}
            onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
          >
            <div>
              <div style={{ fontSize: 10, color: 'var(--nd-text-3)', textTransform: 'uppercase', letterSpacing: 0.6, display: 'flex', alignItems: 'center', gap: 4 }}>
                <span className="material-icons" style={{ fontSize: 12, color: ACC_COLOR }}>psychology</span>
                AI Forecast · Next
              </div>
              <div style={{ fontSize: 13, fontWeight: 700, color: pRg.color }}>
                {pred.charAt(0).toUpperCase() + pred.slice(1)}{prob != null ? ` · ${Math.round(prob * 100)}%` : ''}
              </div>
            </div>
            {(ai.accuracy?.series?.length ?? 0) >= 2 && (
              <div>
                <div style={{ fontSize: 9.5, color: 'var(--nd-text-3)', textAlign: 'right' }}>
                  acc {ai.accuracy?.recent20 != null ? `${Math.round(ai.accuracy.recent20 * 100)}%` : '—'}
                </div>
                <AccuracySparkline series={ai.accuracy.series} />
              </div>
            )}
          </div>
        );
      })()}

      {pm && pm.totalTrades > 0 ? (
        <>
          {/* Stat tiles — Win Rate, Sharpe, Max DD */}
          {statTiles.map(t => (
            <div key={t.label}>
              <div style={{ fontSize: 10, color: 'var(--nd-text-3)', textTransform: 'uppercase', letterSpacing: 0.6 }}>{t.label}</div>
              <div style={{ fontSize: 16, fontWeight: 700, color: t.good ? 'var(--nd-green)' : 'var(--nd-red)' }}>{t.value}</div>
            </div>
          ))}

          {/* Total Return — number + sparkline */}
          <div style={{ marginLeft: 4, paddingLeft: 16, borderLeft: '1px solid var(--nd-border)' }}>
            <div style={{ fontSize: 10, color: 'var(--nd-text-3)', textTransform: 'uppercase', letterSpacing: 0.6 }}>Total Return</div>
            <div style={{ fontSize: 16, fontWeight: 700, color: returnGood ? 'var(--nd-green)' : 'var(--nd-red)' }}>
              {pm.totalReturnPct >= 0 ? '+' : ''}{pm.totalReturnPct?.toFixed(1)}%
            </div>
            <ReturnSparkline points={equity} good={returnGood} />
          </div>

          <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--nd-text-3)' }}>{pm.totalTrades} closed trades</span>
        </>
      ) : (
        <div style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>Live performance appears here once trades are recorded.</div>
      )}
    </div>
    </>
  );
};

export default PerformanceRegimeStrip;
