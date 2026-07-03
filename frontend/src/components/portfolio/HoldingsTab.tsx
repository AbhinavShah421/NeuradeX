import React from 'react';
import { Portfolio, PortfolioStock } from '../../types';
import { inr, COLUMNS, SortKey, SortDir } from './shared';

interface HoldingsTabProps {
  portfolio: Portfolio;
  alerts: any[];
  sortKey: SortKey;
  sortDir: SortDir;
  sortedStocks: PortfolioStock[];
  onSort: (key: SortKey) => void;
}

const HoldingsTab: React.FC<HoldingsTabProps> = ({ portfolio, alerts, sortKey, sortDir, sortedStocks, onSort }) => {
  return (
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
                  <th key={col.key} onClick={() => onSort(col.key)}
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
  );
};

export default HoldingsTab;
