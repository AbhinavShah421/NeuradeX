import React from 'react';
import { Portfolio } from '../../types';
import { inr, RiskMeter } from './shared';

interface StockWeight {
  symbol: string;
  pct: number;
  value: number;
  gain: number;
  isConcentrated: boolean;
}

interface RiskMetrics {
  hhi: number;
  varAmount: number;
  topHolding: Portfolio['stocks'][number] | undefined;
  topWeight: number;
  negCount: number;
  worstStock: Portfolio['stocks'][number] | undefined;
  concentrationRisk: 'LOW' | 'MEDIUM' | 'HIGH';
  diversificationRisk: 'LOW' | 'MEDIUM' | 'HIGH';
  drawdownRisk: 'LOW' | 'MEDIUM' | 'HIGH';
  stockWeights: StockWeight[];
}

interface RiskTabProps {
  portfolio: Portfolio;
  riskMetrics: RiskMetrics | null;
  riskLab: any;
}

const RiskTab: React.FC<RiskTabProps> = ({ portfolio, riskMetrics, riskLab }) => {
  return (
    <>
      {riskMetrics ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

          {/* Risk summary cards */}
          <div className="nd-grid-3" style={{ gap: 12 }}>
            {[
              {
                label: '95% 1-Day VaR',
                value: `₹${inr(riskMetrics.varAmount)}`,
                sub: 'Maximum expected daily loss',
                color: riskMetrics.varAmount > portfolio.totalValue * 0.03 ? 'var(--nd-red)' : 'var(--nd-text-1)',
                icon: 'trending_down',
              },
              {
                label: 'Diversification (HHI)',
                value: riskMetrics.hhi.toFixed(3),
                sub: riskMetrics.hhi > 0.25 ? 'High concentration' : riskMetrics.hhi > 0.12 ? 'Moderate' : 'Well diversified',
                color: riskMetrics.hhi > 0.25 ? 'var(--nd-red)' : riskMetrics.hhi > 0.12 ? '#f59e0b' : 'var(--nd-green)',
                icon: 'donut_large',
              },
              {
                label: 'Stocks in Loss',
                value: `${riskMetrics.negCount} / ${portfolio.stocks.length}`,
                sub: riskMetrics.worstStock ? `Worst: ${riskMetrics.worstStock.symbol} (${riskMetrics.worstStock.gainPercent.toFixed(1)}%)` : '',
                color: riskMetrics.negCount > portfolio.stocks.length / 2 ? 'var(--nd-red)' : 'var(--nd-text-1)',
                icon: 'warning_amber',
              },
            ].map(card => (
              <div key={card.label} className="nd-card" style={{ padding: '16px 20px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                  <span className="material-icons" style={{ fontSize: 18, color: 'var(--nd-text-3)' }}>{card.icon}</span>
                  <span style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>{card.label}</span>
                </div>
                <div style={{ fontSize: 24, fontWeight: 700, color: card.color, marginBottom: 4 }}>{card.value}</div>
                <div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>{card.sub}</div>
              </div>
            ))}
          </div>

          {/* Risk meters */}
          <div className="nd-card">
            <h3 style={{ margin: '0 0 16px', fontSize: 14, fontWeight: 600, color: 'var(--nd-text-2)' }}>Risk Indicators</h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              <RiskMeter score={riskMetrics.concentrationRisk}  label="Concentration Risk (HHI-based)" />
              <RiskMeter score={riskMetrics.diversificationRisk} label="Diversification Risk (stock count)" />
              <RiskMeter score={riskMetrics.drawdownRisk}        label="Drawdown Risk (portfolio returns)" />
            </div>
          </div>

          {/* Position weight breakdown */}
          <div className="nd-card" style={{ padding: 0 }}>
            <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--nd-border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: 'var(--nd-text-2)' }}>Position Weights</h3>
              <span style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>Positions &gt;10% flagged as HIGH concentration</span>
            </div>
            <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 10 }}>
              {riskMetrics.stockWeights.map(sw => (
                <div key={sw.symbol}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4, alignItems: 'center' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--nd-text-1)' }}>{sw.symbol}</span>
                      {sw.isConcentrated && (
                        <span style={{ fontSize: 10, background: '#ef444420', color: '#ef4444', padding: '1px 6px', borderRadius: 4, fontWeight: 600 }}>HIGH</span>
                      )}
                    </div>
                    <div style={{ display: 'flex', gap: 16, alignItems: 'center' }}>
                      <span style={{ fontSize: 12, color: sw.gain >= 0 ? 'var(--nd-green)' : 'var(--nd-red)', fontWeight: 500 }}>
                        {sw.gain >= 0 ? '+' : ''}{sw.gain.toFixed(1)}%
                      </span>
                      <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--nd-text-1)', minWidth: 40, textAlign: 'right' }}>
                        {sw.pct.toFixed(1)}%
                      </span>
                    </div>
                  </div>
                  <div style={{ height: 5, background: 'var(--nd-border)', borderRadius: 3, overflow: 'hidden' }}>
                    <div style={{
                      height: '100%',
                      width: `${Math.min(sw.pct, 100)}%`,
                      background: sw.isConcentrated ? 'var(--nd-red)' : sw.gain >= 0 ? 'var(--nd-green)' : '#f59e0b',
                      borderRadius: 3,
                      transition: 'width 0.4s',
                    }} />
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Largest position warning */}
          {riskMetrics.topWeight > 0.10 && (
            <div style={{ background: '#ef444410', border: '1px solid #ef444430', borderRadius: 12, padding: '14px 18px', display: 'flex', alignItems: 'center', gap: 12 }}>
              <span className="material-icons" style={{ color: 'var(--nd-red)', fontSize: 20 }}>warning</span>
              <div>
                <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--nd-red)' }}>High Concentration Warning</div>
                <div style={{ fontSize: 12, color: 'var(--nd-text-2)', marginTop: 2 }}>
                  {riskMetrics.topHolding?.symbol} represents {(riskMetrics.topWeight * 100).toFixed(1)}% of your portfolio.
                  Consider rebalancing — positions above 10% increase single-stock risk significantly.
                </div>
              </div>
            </div>
          )}
        </div>
      ) : (
        <div className="nd-card" style={{ textAlign: 'center', padding: 48 }}>
          <span className="material-icons" style={{ fontSize: 40, color: 'var(--nd-text-3)', display: 'block' }}>security</span>
          <div style={{ color: 'var(--nd-text-3)', marginTop: 8, fontSize: 13 }}>No portfolio data available for risk analysis</div>
        </div>
      )}

      {/* ── AI Risk Lab (merged under the Risk tab) ── */}
      <div style={{ padding: '18px 20px 0' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '4px 0 12px' }}>
          <span className="material-icons" style={{ fontSize: 18, color: 'var(--nd-purple)' }}>science</span>
          <span style={{ fontSize: 14, fontWeight: 700, color: 'var(--nd-text-1)' }}>AI Risk Lab</span>
          <span style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>— correlation, stress tests, smart exits &amp; dividends</span>
        </div>
        {!riskLab ? <div style={{ textAlign: 'center', padding: 40, color: 'var(--nd-text-3)', fontSize: 13 }}>Running risk analytics on your holdings…</div>
          : riskLab.note ? <div style={{ textAlign: 'center', padding: 40, color: 'var(--nd-text-3)', fontSize: 13 }}>{riskLab.note}</div>
          : (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 420px), 1fr))', gap: 16 }}>
              {/* Diversification */}
              <div className="nd-card" style={{ padding: '14px 18px', minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 6 }}>True diversification (correlation)</div>
                {riskLab.diversification?.score == null ? <div style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>{riskLab.diversification?.note}</div> : (
                  <>
                    <div style={{ fontSize: 28, fontWeight: 700, color: riskLab.diversification.score >= 60 ? 'var(--nd-green)' : riskLab.diversification.score >= 40 ? '#f59e0b' : 'var(--nd-red)' }}>{riskLab.diversification.score}<span style={{ fontSize: 13, color: 'var(--nd-text-3)' }}>/100</span></div>
                    <div style={{ fontSize: 11.5, color: 'var(--nd-text-3)', marginBottom: 8 }}>avg correlation {riskLab.diversification.avgCorrelation} · {riskLab.diversification.note}</div>
                    {(riskLab.diversification.correlatedPairs ?? []).length > 0 && <div style={{ fontSize: 11, color: 'var(--nd-text-3)', marginBottom: 3 }}>Move together (hidden concentration):</div>}
                    {(riskLab.diversification.correlatedPairs ?? []).map((p: any, i: number) => (
                      <div key={i} style={{ fontSize: 12, padding: '2px 0' }}><strong>{p.a}</strong> ↔ <strong>{p.b}</strong> <span style={{ color: '#f59e0b', fontWeight: 700 }}>{p.corr}</span></div>
                    ))}
                  </>
                )}
              </div>
              {/* Stress test */}
              <div className="nd-card" style={{ padding: '14px 18px', minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>Scenario stress-test</div>
                {(riskLab.stress?.scenarios ?? []).map((s: any, i: number) => (
                  <div key={i} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12.5, padding: '4px 0', borderBottom: '1px solid var(--nd-border)' }}>
                    <span style={{ color: 'var(--nd-text-2)' }}>{s.name}</span>
                    <span style={{ color: 'var(--nd-red)', fontWeight: 700 }}>{s.impactPct}% · ₹{inr(Math.abs(s.impactValue))}</span>
                  </div>
                ))}
                {(riskLab.stress?.fragile ?? []).length > 0 && (
                  <div style={{ fontSize: 11, color: 'var(--nd-text-3)', marginTop: 8 }}>Most fragile: {riskLab.stress.fragile.slice(0, 3).map((f: any) => `${f.symbol} (β${f.beta})`).join(', ')}</div>
                )}
              </div>
              {/* Smart exits */}
              <div className="nd-card" style={{ padding: '14px 18px', minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>AI smart exits (ATR-based)</div>
                <div style={{ overflowX: 'auto' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                    <thead><tr style={{ color: 'var(--nd-text-3)', fontSize: 10.5, textAlign: 'right' }}>
                      <th style={{ textAlign: 'left', padding: '4px 6px' }}>Stock</th><th>Now</th><th>Stop</th><th>Target</th><th>Trail</th>
                    </tr></thead>
                    <tbody>
                      {(riskLab.smartExits ?? []).map((e: any) => (
                        <tr key={e.symbol} style={{ borderTop: '1px solid var(--nd-border)', background: e.exitFlag ? 'rgba(239,68,68,0.10)' : 'transparent' }}>
                          <td style={{ padding: '4px 6px', fontWeight: 600 }}>{e.symbol}{e.exitFlag ? ' ⚠' : ''}</td>
                          <td style={{ textAlign: 'right' }}>₹{e.current}</td>
                          <td style={{ textAlign: 'right', color: 'var(--nd-red)' }}>₹{e.stop}</td>
                          <td style={{ textAlign: 'right', color: 'var(--nd-green)' }}>₹{e.target}</td>
                          <td style={{ textAlign: 'right', color: 'var(--nd-text-3)' }}>{e.trailPct}%</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div style={{ fontSize: 10.5, color: 'var(--nd-text-3)', marginTop: 6 }}>⚠ = AI scan flags a SELL/downgrade. Stops are 2×ATR below price.</div>
              </div>
              {/* Dividends */}
              <div className="nd-card" style={{ padding: '14px 18px', minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 6 }}>Dividend income forecast</div>
                <div style={{ display: 'flex', gap: 18, marginBottom: 8 }}>
                  <div><div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>Annual income</div><div style={{ fontSize: 18, fontWeight: 700, color: 'var(--nd-green)' }}>₹{inr(riskLab.dividends?.annualIncome ?? 0)}</div></div>
                  <div><div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>Portfolio yield</div><div style={{ fontSize: 18, fontWeight: 700 }}>{riskLab.dividends?.portfolioYieldPct ?? 0}%</div></div>
                </div>
                {(riskLab.dividends?.payers ?? []).slice(0, 6).map((p: any) => (
                  <div key={p.symbol} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, padding: '3px 0', borderBottom: '1px solid var(--nd-border)' }}>
                    <span style={{ fontWeight: 600 }}>{p.symbol}</span>
                    <span style={{ color: 'var(--nd-text-2)' }}>₹{inr(p.annualIncome)} · {p.yieldPct}% yld</span>
                  </div>
                ))}
                {!(riskLab.dividends?.payers ?? []).length && <div style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>No dividend payers in the book.</div>}
              </div>
            </div>
          )}
      </div>
    </>
  );
};

export default RiskTab;
