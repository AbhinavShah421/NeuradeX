import React, { useEffect, useState, useCallback } from 'react';
import apiService from '../services/api';
import ScanControl from '../components/ScanControl';
import { useScanStore } from '../stores/scanStore';

const inr = (v: number) =>
  `₹${(v ?? 0).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const ACTION_BG: Record<string, string> = { BUY: '#22c55e', SELL: '#ef4444', HOLD: '#f59e0b' };
const GRADE_COLOR: Record<string, string> = { A: '#22c55e', B: '#3b82f6', C: '#f59e0b', D: '#94a3b8' };
const FILTERS: (number | 'All')[] = [10, 20, 50, 100, 'All'];

const fmtTime = (s?: string) => {
  if (!s) return '';
  try { return new Date(s).toLocaleString('en-IN'); } catch { return ''; }
};

// ── Per-stock "why this rank" detail ──────────────────────────────────────────
const RankDetail: React.FC<{ s: any; onClose: () => void }> = ({ s, onClose }) => {
  const m = s.metrics || {};
  const cf = s.confirmations || {};
  const actionColor = ACTION_BG[s.action] ?? 'var(--nd-text-2)';
  const FACTORS: [string, string][] = [
    ['trend', 'Trend (price vs SMA20/50)'],
    ['momentum', 'Momentum (10-day)'],
    ['macd', 'MACD histogram'],
    ['volume', 'Relative volume'],
    ['regime', 'Market regime alignment'],
    ['rsi', 'RSI (not overbought)'],
  ];
  const Row: React.FC<{ k: string; v: React.ReactNode }> = ({ k, v }) => (
    <div style={{ display: 'flex', justifyContent: 'space-between', padding: '7px 0', borderBottom: '1px solid var(--nd-border)', fontSize: 12.5 }}>
      <span style={{ color: 'var(--nd-text-3)' }}>{k}</span>
      <span style={{ color: 'var(--nd-text-1)', fontWeight: 600 }}>{v}</span>
    </div>
  );

  return (
    <div onClick={e => { if (e.target === e.currentTarget) onClose(); }}
      style={{ position: 'fixed', inset: 0, background: '#00000080', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 12 }}>
      <div style={{ background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderRadius: 16, width: '100%', maxWidth: 520, maxHeight: '90vh', overflow: 'auto', boxShadow: '0 24px 64px #00000060' }}>
        {/* Header */}
        <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--nd-border)', display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--nd-text-3)' }}>#{s.rank}</span>
          <span style={{ fontSize: 17, fontWeight: 700, color: 'var(--nd-text-1)' }}>{s.symbol}</span>
          <span style={{ fontSize: 11, fontWeight: 700, padding: '2px 9px', borderRadius: 5, background: `${actionColor}1a`, color: actionColor }}>{s.action}</span>
          {s.grade && (
            <span style={{ fontSize: 11, fontWeight: 800, padding: '2px 8px', borderRadius: 5, background: `${GRADE_COLOR[s.grade]}1f`, color: GRADE_COLOR[s.grade], border: `1px solid ${GRADE_COLOR[s.grade]}55` }}>
              Grade {s.grade}{s.winProbability != null ? ` · ${(s.winProbability * 100).toFixed(0)}% win` : ''}
            </span>
          )}
          <button onClick={onClose} style={{ marginLeft: 'auto', background: 'none', border: 'none', cursor: 'pointer' }}>
            <span className="material-icons" style={{ color: 'var(--nd-text-3)', fontSize: 20 }}>close</span>
          </button>
        </div>

        <div style={{ padding: '14px 20px' }}>
          <div style={{ fontSize: 12.5, color: 'var(--nd-text-2)', lineHeight: 1.6, marginBottom: 8 }}>{s.name}</div>

          {/* Why this rank */}
          <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--nd-text-3)', letterSpacing: 0.3, margin: '6px 0 8px' }}>WHY THIS RANK</div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
            {[
              { k: 'Signal score', v: s.signalScore },
              { k: 'Rank score', v: s.rankScore },
              { k: 'Win prob', v: s.winProbability != null ? `${(s.winProbability * 100).toFixed(0)}%` : '—' },
              { k: 'Factors aligned', v: `${s.confirmedFactors ?? 0}/6` },
              ...(s.catalystBoost ? [{ k: 'News boost', v: `${s.catalystBoost > 0 ? '+' : ''}${(s.catalystBoost * 100).toFixed(0)}%` }] : []),
            ].map(x => (
              <div key={x.k} style={{ background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 8, padding: '6px 10px', minWidth: 90 }}>
                <div style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>{x.k}</div>
                <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--nd-text-1)' }}>{x.v}</div>
              </div>
            ))}
          </div>

          {/* Factor confirmation breakdown */}
          <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--nd-text-3)', letterSpacing: 0.3, margin: '4px 0 8px' }}>AGENT FACTOR VOTE</div>
          <div style={{ marginBottom: 12 }}>
            {FACTORS.map(([key, label]) => {
              const on = (cf[key] ?? 0) >= 1.0;
              return (
                <div key={key} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 0', borderBottom: '1px solid var(--nd-border)', fontSize: 12.5 }}>
                  <span className="material-icons" style={{ fontSize: 16, color: on ? 'var(--nd-green)' : 'var(--nd-text-3)' }}>{on ? 'check_circle' : 'remove_circle_outline'}</span>
                  <span style={{ flex: 1, color: 'var(--nd-text-2)' }}>{label}</span>
                  <span style={{ fontWeight: 700, color: on ? 'var(--nd-green)' : 'var(--nd-text-3)' }}>{on ? 'aligned' : 'no'}</span>
                </div>
              );
            })}
          </div>

          {/* News sentiment */}
          {s.news && (
            <div style={{ border: '1px solid var(--nd-border)', borderRadius: 10, padding: '10px 12px', marginBottom: 12, background: 'var(--nd-surface)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                <span className="material-icons" style={{ fontSize: 15, color: ACTION_BG[s.news.action] ?? 'var(--nd-text-3)' }}>article</span>
                <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--nd-text-3)', letterSpacing: 0.3 }}>NEWS SENTIMENT · LLM</span>
                <span style={{ flex: 1 }} />
                {s.news.action && <span style={{ fontSize: 10, fontWeight: 700, padding: '1px 7px', borderRadius: 4, background: `${ACTION_BG[s.news.action]}1a`, color: ACTION_BG[s.news.action] }}>{s.news.action}</span>}
              </div>
              {s.news.summary && <div style={{ fontSize: 12, color: 'var(--nd-text-2)', lineHeight: 1.5 }}>{s.news.summary}</div>}
              {s.news.catalyst && s.news.catalyst !== 'none' && (
                <div style={{ fontSize: 11.5, marginTop: 6 }}><span style={{ color: 'var(--nd-text-3)' }}>Catalyst: </span><strong>{s.news.catalyst}</strong></div>
              )}
            </div>
          )}

          {/* Market indicators */}
          <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--nd-text-3)', letterSpacing: 0.3, margin: '4px 0 6px' }}>MARKET-INDICATOR EVIDENCE</div>
          {[
            ['Price', inr(s.price)],
            ['Avg daily volume', m.avgVolume != null ? `${(m.avgVolume / 1e6).toFixed(2)}M` : '—'],
            ['Relative volume', m.relVolume != null ? `${m.relVolume}×` : '—'],
            ['Volatility (ATR)', m.atrPct != null ? `${m.atrPct}%` : '—'],
            ['RSI (14)', m.rsi ?? '—'],
            ['Momentum (10d)', m.momentumPct != null ? `${m.momentumPct >= 0 ? '+' : ''}${m.momentumPct}%` : '—'],
            ['Trend', m.smaTrend ? `${m.smaTrend}${m.sma20 ? ` · SMA20 ${m.sma20}` : ''}` : '—'],
            ['MACD histogram', m.macdHist != null ? `${m.macdHist >= 0 ? '+' : ''}${m.macdHist}` : '—'],
            ['Opening gap', m.gapPct != null ? `${m.gapPct >= 0 ? '+' : ''}${m.gapPct}%` : '—'],
            ['Room to 20d high', m.distFromHighPct != null ? `${m.distFromHighPct}%` : '—'],
            ['Market regime', m.marketRegime ?? '—'],
            ['Liquidity score', m.liquidityScore != null ? `${(m.liquidityScore * 100).toFixed(0)}%` : '—'],
            ['Volatility score', m.volatilityScore != null ? `${(m.volatilityScore * 100).toFixed(0)}%` : '—'],
          ].map(([k, v]) => <Row key={k as string} k={k as string} v={v as React.ReactNode} />)}

          {/* Reasoning */}
          {s.reasoning && (
            <div style={{ marginTop: 12, fontSize: 12.5, color: 'var(--nd-text-2)', lineHeight: 1.6, background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 8, padding: '10px 12px' }}>
              {s.reasoning}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

const Predictions: React.FC = () => {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [limit, setLimit] = useState<number | 'All'>(20);
  const [sel, setSel] = useState<any>(null);

  const fetchRanked = useCallback(async (lim: number | 'All') => {
    setLoading(true);
    try {
      const res = await apiService.getRanked(lim === 'All' ? 250 : lim);
      setData(res.data);
    } catch { /* ignore */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { fetchRanked(limit); }, [limit, fetchRanked]);

  // When a centralized scan finishes, auto re-pull the ranked board.
  const scanning = useScanStore(s => s.scanning);
  useEffect(() => {
    if (!scanning) fetchRanked(limit);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scanning]);

  const items: any[] = data?.items ?? [];

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <h1 className="nd-page-title">AI Stock Rankings</h1>
        <p className="nd-page-sub">Every AI-scanned stock ranked by the agentic engine — click any row to see why it earned its rank.</p>
      </div>

      {/* Controls */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 14 }}>
        <div style={{ display: 'flex', borderRadius: 8, overflow: 'hidden', border: '1px solid var(--nd-border)' }}>
          {FILTERS.map(f => (
            <button key={String(f)} onClick={() => setLimit(f)}
              style={{ padding: '7px 14px', fontSize: 12.5, fontWeight: 600, cursor: 'pointer', border: 'none',
                background: limit === f ? 'var(--nd-accent)' : 'var(--nd-surface)',
                color: limit === f ? '#fff' : 'var(--nd-text-2)' }}>
              {f === 'All' ? 'All' : `Top ${f}`}
            </button>
          ))}
        </div>
        <span style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>
          {data ? `${data.candidates ?? items.length} ranked · ${fmtTime(data.updatedAt)}` : ''}
        </span>
        <ScanControl align="right" />
      </div>

      {/* Ranked table */}
      {loading && !items.length ? (
        <div className="nd-loading"><span className="material-icons nd-spin">autorenew</span><span>Loading AI rankings…</span></div>
      ) : !items.length ? (
        <div className="nd-card" style={{ textAlign: 'center', padding: 48, color: 'var(--nd-text-3)' }}>
          No ranked stocks yet — the AI scanner is warming up. Check back after the next scan.
        </div>
      ) : (
        <div className="nd-card" style={{ padding: 0 }}>
          <div style={{ overflowX: 'auto' }}>
            <table className="nd-table">
              <thead><tr>
                <th style={{ width: 50, textAlign: 'center' }}>#</th>
                <th>Stock</th>
                <th style={{ textAlign: 'center' }}>Action</th>
                <th style={{ textAlign: 'center' }}>Grade</th>
                <th style={{ textAlign: 'right' }}>Win%</th>
                <th style={{ textAlign: 'right' }}>Signal</th>
                <th style={{ textAlign: 'right' }}>Momentum</th>
                <th style={{ textAlign: 'right' }}>Price</th>
                <th style={{ width: 40 }}></th>
              </tr></thead>
              <tbody>
                {items.map(s => {
                  const ac = ACTION_BG[s.action] ?? 'var(--nd-text-2)';
                  const mom = s.metrics?.momentumPct;
                  return (
                    <tr key={s.symbol} onClick={() => setSel(s)} style={{ cursor: 'pointer' }}>
                      <td style={{ textAlign: 'center', fontWeight: 700, color: 'var(--nd-text-3)' }}>{s.rank}</td>
                      <td>
                        <div style={{ fontWeight: 700, fontFamily: 'monospace', color: 'var(--nd-accent)' }}>{s.symbol}</div>
                        <div style={{ fontSize: 10.5, color: 'var(--nd-text-3)', maxWidth: 240, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.name}</div>
                      </td>
                      <td style={{ textAlign: 'center' }}>
                        <span style={{ fontSize: 10.5, fontWeight: 700, padding: '2px 8px', borderRadius: 5, background: `${ac}1a`, color: ac }}>{s.action}</span>
                      </td>
                      <td style={{ textAlign: 'center', fontWeight: 800, color: GRADE_COLOR[s.grade] ?? 'var(--nd-text-3)' }}>{s.grade ?? '—'}</td>
                      <td style={{ textAlign: 'right', fontSize: 12.5, color: 'var(--nd-green)' }}>{s.winProbability != null ? `${(s.winProbability * 100).toFixed(0)}%` : '—'}</td>
                      <td style={{ textAlign: 'right', fontSize: 12.5, fontWeight: 600 }}>{s.signalScore ?? '—'}</td>
                      <td style={{ textAlign: 'right', fontSize: 12.5, color: (mom ?? 0) >= 0 ? 'var(--nd-green)' : 'var(--nd-red)' }}>
                        {mom != null ? `${mom >= 0 ? '+' : ''}${mom}%` : '—'}
                      </td>
                      <td style={{ textAlign: 'right', fontSize: 12.5, fontWeight: 600 }}>{inr(s.price)}</td>
                      <td style={{ textAlign: 'center' }}><span className="material-icons" style={{ fontSize: 16, color: 'var(--nd-text-3)' }}>chevron_right</span></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {sel && <RankDetail s={sel} onClose={() => setSel(null)} />}
    </div>
  );
};

export default Predictions;
