import React, { useEffect, useState, useMemo } from 'react';
import apiService from '../services/api';
import { Portfolio, Performance, PortfolioStock } from '../types';

type SortKey = keyof Pick<PortfolioStock, 'symbol' | 'quantity' | 'purchasePrice' | 'currentPrice' | 'value' | 'gain' | 'gainPercent'>;
type SortDir = 'asc' | 'desc';
type Tab = 'holdings' | 'performance' | 'risk';

const inr = (v: number) =>
  v.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const COLUMNS: { key: SortKey; label: string; align: 'left' | 'right' }[] = [
  { key: 'symbol',        label: 'Company',       align: 'left'  },
  { key: 'quantity',      label: 'Qty',           align: 'right' },
  { key: 'purchasePrice', label: 'Avg Price',     align: 'right' },
  { key: 'currentPrice',  label: 'Market Price',  align: 'right' },
  { key: 'value',         label: 'Current Value', align: 'right' },
  { key: 'gain',          label: 'P&L',           align: 'right' },
  { key: 'gainPercent',   label: 'Returns (%)',   align: 'right' },
];

// Herfindahl-Hirschman Index — 0 (perfectly diversified) to 1 (all in one stock)
function calcHHI(stocks: PortfolioStock[], totalValue: number): number {
  return stocks.reduce((sum, s) => {
    const w = s.value / totalValue;
    return sum + w * w;
  }, 0);
}

// Approximate 1-day 95% VaR using normal distribution assumption (1.645σ)
// σ derived from gainPercent as a rough daily volatility proxy
function calcVaR(stocks: PortfolioStock[], totalValue: number): number {
  const dailyVol = stocks.reduce((sum, s) => {
    const weight = s.value / totalValue;
    const vol = Math.abs(s.gainPercent / 100) * 0.1; // rough proxy
    return sum + weight * vol;
  }, 0);
  return totalValue * dailyVol * 1.645;
}

function RiskMeter({ score, label }: { score: 'LOW' | 'MEDIUM' | 'HIGH'; label: string }) {
  const color = score === 'LOW' ? 'var(--nd-green)' : score === 'MEDIUM' ? '#f59e0b' : 'var(--nd-red)';
  const pct   = score === 'LOW' ? 25 : score === 'MEDIUM' ? 60 : 90;
  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
        <span style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>{label}</span>
        <span style={{ fontSize: 12, fontWeight: 700, color }}>{score}</span>
      </div>
      <div style={{ height: 6, background: 'var(--nd-border)', borderRadius: 3, overflow: 'hidden' }}>
        <div style={{ height: '100%', width: `${pct}%`, background: color, borderRadius: 3, transition: 'width 0.5s' }} />
      </div>
    </div>
  );
}

const PortfolioPage: React.FC = () => {
  const [portfolio,   setPortfolio]   = useState<Portfolio | null>(null);
  const [performance, setPerformance] = useState<Performance | null>(null);
  const [loading,     setLoading]     = useState(true);
  const [alerts,      setAlerts]      = useState<any[]>([]);
  const [sortKey,     setSortKey]     = useState<SortKey>('value');
  const [sortDir,     setSortDir]     = useState<SortDir>('desc');
  const [activeTab,   setActiveTab]   = useState<Tab>('holdings');

  useEffect(() => { fetchPortfolioData(); }, []);

  const fetchPortfolioData = async () => {
    try {
      setLoading(true);
      const [portRes, perfRes, alertRes] = await Promise.all([
        apiService.getPortfolio(),
        apiService.getPerformance(),
        apiService.getAlerts(),
      ]);
      if (portRes.data)  setPortfolio(portRes.data);
      if (perfRes.data)  setPerformance(perfRes.data);
      if (alertRes.data) setAlerts(alertRes.data as any[]);
    } catch (err) {
      console.error('Error fetching portfolio:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleSort = (key: SortKey) => {
    if (key === sortKey) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    else { setSortKey(key); setSortDir(key === 'symbol' ? 'asc' : 'desc'); }
  };

  const sortedStocks = useMemo(() => {
    if (!portfolio) return [];
    return [...portfolio.stocks].sort((a, b) => {
      const av = a[sortKey], bv = b[sortKey];
      const cmp = typeof av === 'string' ? av.localeCompare(bv as string) : (av as number) - (bv as number);
      return sortDir === 'asc' ? cmp : -cmp;
    });
  }, [portfolio, sortKey, sortDir]);

  // Risk metrics computed from portfolio
  const riskMetrics = useMemo(() => {
    if (!portfolio || !portfolio.stocks.length) return null;
    const tv = portfolio.totalValue;
    const hhi = calcHHI(portfolio.stocks, tv);
    const varAmount = calcVaR(portfolio.stocks, tv);
    const topHolding = [...portfolio.stocks].sort((a, b) => b.value - a.value)[0];
    const topWeight  = topHolding ? topHolding.value / tv : 0;
    const negCount   = portfolio.stocks.filter(s => s.gainPercent < 0).length;
    const worstStock = [...portfolio.stocks].sort((a, b) => a.gainPercent - b.gainPercent)[0];

    const concentrationRisk: 'LOW' | 'MEDIUM' | 'HIGH' =
      hhi > 0.25 ? 'HIGH' : hhi > 0.12 ? 'MEDIUM' : 'LOW';
    const diversificationRisk: 'LOW' | 'MEDIUM' | 'HIGH' =
      portfolio.stocks.length < 5 ? 'HIGH' : portfolio.stocks.length < 10 ? 'MEDIUM' : 'LOW';
    const drawdownRisk: 'LOW' | 'MEDIUM' | 'HIGH' =
      portfolio.gainPercent < -15 ? 'HIGH' : portfolio.gainPercent < -5 ? 'MEDIUM' : 'LOW';

    return {
      hhi, varAmount, topHolding, topWeight, negCount, worstStock,
      concentrationRisk, diversificationRisk, drawdownRisk,
      stockWeights: portfolio.stocks.map(s => ({
        symbol: s.symbol,
        pct: (s.value / tv) * 100,
        value: s.value,
        gain: s.gainPercent,
        isConcentrated: s.value / tv > 0.10,
      })).sort((a, b) => b.pct - a.pct),
    };
  }, [portfolio]);

  if (loading) {
    return (
      <div className="nd-loading">
        <span className="material-icons nd-spin">autorenew</span>
        <span>Loading portfolio...</span>
      </div>
    );
  }

  const tabs: { id: Tab; label: string; icon: string }[] = [
    { id: 'holdings',    label: 'Holdings',    icon: 'account_balance_wallet' },
    { id: 'performance', label: 'Performance', icon: 'trending_up' },
    { id: 'risk',        label: 'Risk',        icon: 'security' },
  ];

  return (
    <div>
      {/* Page header */}
      <div style={{ marginBottom: 20 }}>
        <h1 className="nd-page-title">Portfolio</h1>
        <p className="nd-page-sub">Holdings, performance & risk from your Groww account</p>
      </div>

      {portfolio && (
        <>
          {/* Summary strip */}
          <div className="nd-card" style={{ padding: '20px 24px', marginBottom: 16 }}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 0 }}>
              {[
                { label: 'Current Value',   value: `₹${inr(portfolio.totalValue)}`,    color: 'var(--nd-text-1)', icon: 'account_balance_wallet' },
                { label: 'Invested Value',  value: `₹${inr(portfolio.totalInvested)}`, color: 'var(--nd-text-2)', icon: 'savings' },
                { label: '1D Returns',
                  value: `${portfolio.totalGain >= 0 ? '+' : '-'}₹${inr(Math.abs(portfolio.totalGain))}`,
                  color: portfolio.totalGain >= 0 ? 'var(--nd-green)' : 'var(--nd-red)', icon: 'show_chart' },
                { label: 'Total Returns',
                  value: `${portfolio.gainPercent >= 0 ? '+' : ''}${portfolio.gainPercent.toFixed(2)}%`,
                  color: portfolio.gainPercent >= 0 ? 'var(--nd-green)' : 'var(--nd-red)', icon: 'trending_up' },
              ].map((c, i) => (
                <div key={c.label} style={{ padding: '0 20px', borderLeft: i > 0 ? '1px solid var(--nd-border)' : 'none' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                    <span className="material-icons" style={{ fontSize: 14, color: 'var(--nd-text-3)' }}>{c.icon}</span>
                    <p className="nd-label" style={{ margin: 0 }}>{c.label}</p>
                  </div>
                  <p style={{ fontSize: 22, fontWeight: 700, color: c.color, margin: 0 }}>{c.value}</p>
                </div>
              ))}
            </div>
          </div>

          {/* Tab strip */}
          <div style={{ display: 'flex', gap: 4, marginBottom: 16, background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 10, padding: 4, width: 'fit-content' }}>
            {tabs.map(t => (
              <button key={t.id} onClick={() => setActiveTab(t.id)}
                style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '7px 16px', borderRadius: 7, border: 'none', cursor: 'pointer', fontSize: 13, fontWeight: 500, transition: 'all 0.15s',
                  background: activeTab === t.id ? 'var(--nd-accent)' : 'transparent',
                  color: activeTab === t.id ? '#fff' : 'var(--nd-text-2)' }}>
                <span className="material-icons" style={{ fontSize: 15 }}>{t.icon}</span>
                {t.label}
              </button>
            ))}
          </div>

          {/* ── Holdings Tab ─────────────────────────────────────────────────────── */}
          {activeTab === 'holdings' && (
            <>
              <div className="nd-card" style={{ padding: 0, marginBottom: 16 }}>
                <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--nd-border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <h2 className="nd-section-title" style={{ margin: 0 }}>
                    Holdings
                    <span style={{ fontWeight: 400, color: 'var(--nd-text-2)', marginLeft: 6 }}>({portfolio.stocks.length})</span>
                  </h2>
                  <span style={{ fontSize: 11.5, color: 'var(--nd-text-3)' }}>Click column to sort</span>
                </div>
                <div style={{ overflowX: 'auto' }}>
                  <table className="nd-table">
                    <thead>
                      <tr>
                        {COLUMNS.map(col => (
                          <th key={col.key} onClick={() => handleSort(col.key)}
                            className={sortKey === col.key ? 'active-sort' : ''}
                            style={{ textAlign: col.align, cursor: 'pointer' }}>
                            {col.label}
                            {sortKey === col.key && (
                              <span className="material-icons" style={{ fontSize: 12, verticalAlign: 'middle', marginLeft: 3 }}>
                                {sortDir === 'asc' ? 'arrow_upward' : 'arrow_downward'}
                              </span>
                            )}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {sortedStocks.map(stock => (
                        <tr key={stock.symbol}>
                          <td><p style={{ fontWeight: 600, color: 'var(--nd-green)' }}>{stock.symbol}</p></td>
                          <td className="text-right">{stock.quantity}</td>
                          <td className="text-right">₹{inr(stock.purchasePrice)}</td>
                          <td className="text-right" style={{ fontWeight: 600 }}>₹{inr(stock.currentPrice)}</td>
                          <td className="text-right" style={{ fontWeight: 600 }}>₹{inr(stock.value)}</td>
                          <td className="text-right" style={{ fontWeight: 600, color: stock.gain >= 0 ? 'var(--nd-green)' : 'var(--nd-red)' }}>
                            {stock.gain >= 0 ? '+' : '-'}₹{inr(Math.abs(stock.gain))}
                          </td>
                          <td className="text-right">
                            <span className={`nd-badge ${stock.gainPercent >= 0 ? 'nd-badge-green' : 'nd-badge-red'}`}>
                              {stock.gainPercent >= 0 ? '+' : ''}{stock.gainPercent.toFixed(2)}%
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              {alerts.length > 0 && (
                <div className="nd-card">
                  <h2 className="nd-section-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span className="material-icons" style={{ fontSize: 18, color: 'var(--nd-orange)' }}>notifications_active</span>
                    Active Alerts
                  </h2>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                    {alerts.map(alert => (
                      <div key={alert.id} style={{ padding: '12px 16px', borderRadius: 8, border: `1px solid ${alert.enabled ? 'var(--nd-green)' : 'var(--nd-border)'}`, background: alert.enabled ? 'var(--nd-green-50)' : 'var(--nd-surface)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', opacity: alert.enabled ? 1 : 0.6 }}>
                        <div>
                          <p style={{ fontWeight: 600, fontSize: 13.5 }}>{alert.symbol} — {alert.alertType}</p>
                          <p style={{ fontSize: 12, color: 'var(--nd-text-2)', marginTop: 2 }}>{alert.condition}</p>
                        </div>
                        <span className={`nd-badge ${alert.enabled ? 'nd-badge-green' : 'nd-badge-gray'}`}>
                          {alert.enabled ? 'Active' : 'Inactive'}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}

          {/* ── Performance Tab ──────────────────────────────────────────────────── */}
          {activeTab === 'performance' && performance && (
            <div className="nd-card">
              <h2 className="nd-section-title">Performance Metrics</h2>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
                {[
                  { label: 'Daily Return',   value: `${performance.dailyReturn > 0 ? '+' : ''}${performance.dailyReturn.toFixed(2)}%`,   color: performance.dailyReturn >= 0 ? 'var(--nd-green)' : 'var(--nd-red)' },
                  { label: 'Monthly Return', value: `${performance.monthlyReturn > 0 ? '+' : ''}${performance.monthlyReturn.toFixed(2)}%`, color: performance.monthlyReturn >= 0 ? 'var(--nd-green)' : 'var(--nd-red)' },
                  { label: 'Sharpe Ratio',   value: performance.sharpeRatio.toFixed(2),                                                   color: 'var(--nd-text-1)' },
                  { label: 'Win Rate',       value: `${(performance.winRate * 100).toFixed(1)}%`,                                         color: 'var(--nd-text-1)' },
                ].map(m => (
                  <div key={m.label} className="nd-metric">
                    <p className="nd-metric-label">{m.label}</p>
                    <p className="nd-metric-value" style={{ color: m.color }}>{m.value}</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* ── Risk Tab ─────────────────────────────────────────────────────────── */}
          {activeTab === 'risk' && (
            riskMetrics ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

                {/* Risk summary cards */}
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12 }}>
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
            )
          )}
        </>
      )}
    </div>
  );
};

export default PortfolioPage;
