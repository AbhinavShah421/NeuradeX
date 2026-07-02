import React from 'react';
import apiService from '../../services/api';
import { inr, ACTION_STYLE, CANCELLABLE } from './shared';

interface OptimizeTabProps {
  optimization: any;
  optimizing: boolean;
  orders: any[];
  cancellingId: string | null;
  runOptimization: () => void;
  loadOrders: () => void;
  cancelPendingOrder: (orderId: string, segment: string) => void;
  askOrder: (spec: any) => void;
  askSwap: (a: any, sig: any) => void;
  setOrderMsg: (msg: { ok: boolean; text: string } | null) => void;
}

const OptimizeTab: React.FC<OptimizeTabProps> = ({
  optimization, optimizing, orders, cancellingId,
  runOptimization, loadOrders, cancelPendingOrder, askOrder, askSwap, setOrderMsg,
}) => {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* Intro / run */}
      <div className="nd-card" style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
        <span className="material-icons" style={{ fontSize: 26, color: 'var(--nd-green)' }}>auto_awesome</span>
        <div style={{ flex: 1, minWidth: 220 }}>
          <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--nd-text-1)' }}>AI Portfolio Optimizer</div>
          <div style={{ fontSize: 12.5, color: 'var(--nd-text-2)', marginTop: 2 }}>
            Scores every holding with live AI signals, measures concentration &amp; sector risk, pulls higher-conviction picks from the AI scanner, and proposes a rebalancing plan.
          </div>
        </div>
        <button onClick={runOptimization} disabled={optimizing}
          style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 18px', borderRadius: 10, border: 'none',
            background: 'var(--nd-green)', color: '#fff', fontWeight: 600, fontSize: 14, cursor: optimizing ? 'wait' : 'pointer' }}>
          <span className={`material-icons${optimizing ? ' nd-spin' : ''}`} style={{ fontSize: 18 }}>{optimizing ? 'autorenew' : 'insights'}</span>
          {optimizing ? 'Optimizing…' : optimization ? 'Re-run' : 'Run optimization'}
        </button>
        {optimization && (
          <button onClick={async () => {
            try {
              const r: any = await apiService.paperTestOptimized(200000);
              const n = (r?.data?.positions ?? []).length;
              setOrderMsg({ ok: true, text: `Paper-test portfolio created from your optimized book (${n} stocks, ₹2L). Track it on Dashboard → Delivery Autopilot.` });
            } catch { setOrderMsg({ ok: false, text: 'Could not create the paper-test portfolio.' }); }
          }} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 16px', borderRadius: 10,
            border: '1px solid var(--nd-blue)', background: 'transparent', color: 'var(--nd-blue)', fontWeight: 600, fontSize: 13, cursor: 'pointer' }}>
            <span className="material-icons" style={{ fontSize: 18 }}>science</span>
            Paper-test this portfolio
          </button>
        )}
      </div>

      {/* Live Groww orders (today) with cancel */}
      {orders.length > 0 && (
        <div className="nd-card" style={{ padding: 0 }}>
          <div style={{ padding: '12px 18px', borderBottom: '1px solid var(--nd-border)', display: 'flex', alignItems: 'center', gap: 8 }}>
            <span className="material-icons" style={{ fontSize: 18, color: 'var(--nd-text-2)' }}>receipt_long</span>
            <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--nd-text-1)' }}>Today's Orders (Groww)</span>
            <span style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>{orders.length}</span>
            <button onClick={loadOrders} title="Refresh" style={{ marginLeft: 'auto', background: 'none', border: 'none', cursor: 'pointer', color: 'var(--nd-text-3)', display: 'flex' }}>
              <span className="material-icons" style={{ fontSize: 16 }}>refresh</span>
            </button>
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table className="nd-table">
              <thead><tr>
                <th>Symbol</th><th style={{ textAlign: 'center' }}>Side</th><th style={{ textAlign: 'right' }}>Qty</th>
                <th style={{ textAlign: 'center' }}>Type</th><th style={{ textAlign: 'center' }}>Status</th><th style={{ textAlign: 'center' }}>Action</th>
              </tr></thead>
              <tbody>
                {orders.map((o: any) => {
                  const status = String(o.status || '').toUpperCase();
                  const canCancel = CANCELLABLE.includes(status);
                  const stColor = ['EXECUTED', 'COMPLETE', 'FILLED'].includes(status) ? 'var(--nd-green)'
                    : ['FAILED', 'REJECTED', 'CANCELLED'].includes(status) ? 'var(--nd-red)' : 'var(--nd-orange, #f59e0b)';
                  return (
                    <tr key={o.orderId || o.referenceId}>
                      <td style={{ fontWeight: 700, fontFamily: 'monospace', color: 'var(--nd-text-1)' }}>{o.symbol}</td>
                      <td style={{ textAlign: 'center', color: o.transactionType === 'SELL' ? 'var(--nd-red)' : 'var(--nd-green)', fontWeight: 600, fontSize: 12 }}>{o.transactionType}</td>
                      <td style={{ textAlign: 'right', fontSize: 12.5 }}>{o.quantity}</td>
                      <td style={{ textAlign: 'center', fontSize: 11.5, color: 'var(--nd-text-2)' }}>{o.orderType}</td>
                      <td style={{ textAlign: 'center', fontSize: 11, fontWeight: 700, color: stColor }}>{status}</td>
                      <td style={{ textAlign: 'center' }}>
                        {canCancel ? (
                          <button onClick={() => cancelPendingOrder(o.orderId, o.segment)} disabled={cancellingId === o.orderId}
                            style={{ fontSize: 11, fontWeight: 700, padding: '3px 10px', borderRadius: 6, border: '1px solid #ef444455', background: '#ef44441a', color: '#ef4444', cursor: cancellingId === o.orderId ? 'wait' : 'pointer' }}>
                            {cancellingId === o.orderId ? '…' : 'Cancel'}
                          </button>
                        ) : (
                          <span style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>—</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {!optimization && !optimizing && (
        <div className="nd-card" style={{ textAlign: 'center', padding: 40, color: 'var(--nd-text-3)', fontSize: 13 }}>
          Click <strong>Run optimization</strong> to generate an AI-driven rebalancing plan for your live holdings.
        </div>
      )}

      {optimization && (() => {
        const plan = optimization.plan || {};
        const risk = optimization.risk || {};
        const signals = optimization.signals || {};
        const actions: any[] = plan.actions || [];
        return (
          <>
            {/* Summary */}
            <div className="nd-card" style={{ borderLeft: '3px solid var(--nd-green)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--nd-text-1)' }}>Recommendation</span>
                <span style={{ fontSize: 10, fontWeight: 600, padding: '2px 8px', borderRadius: 6, background: 'var(--nd-surface-2)', color: 'var(--nd-text-3)' }}>
                  {String(optimization.source || '').startsWith('ai') ? 'AI-generated' : 'rule-based'}
                </span>
                {optimization.scanAt && (
                  <span style={{ fontSize: 10.5, color: 'var(--nd-text-3)', marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 4 }}>
                    <span className="material-icons" style={{ fontSize: 12 }}>sync</span>
                    tracks AI scan · {new Date(optimization.scanAt).toLocaleString()}
                  </span>
                )}
              </div>
              <div style={{ fontSize: 13.5, color: 'var(--nd-text-1)', lineHeight: 1.6 }}>{plan.summary}</div>
              {plan.objective && <div style={{ fontSize: 12, color: 'var(--nd-text-2)', marginTop: 8 }}><strong>Objective:</strong> {plan.objective}</div>}
              {plan.expectedEffect && <div style={{ fontSize: 12, color: 'var(--nd-text-2)', marginTop: 4 }}><strong>Expected effect:</strong> {plan.expectedEffect}</div>}
            </div>

            {/* Risk snapshot */}
            <div className="nd-grid-3" style={{ gap: 12 }}>
              {[
                { label: 'Largest Position', value: `${risk.topSymbol ?? '—'} · ${risk.topWeightPct ?? 0}%`, icon: 'pie_chart' },
                { label: 'Top Sector',       value: `${risk.topSector ?? '—'} · ${risk.topSectorPct ?? 0}%`, icon: 'category' },
                { label: 'Effective Holdings', value: `${risk.effectiveHoldings ?? '—'} of ${risk.holdings ?? 0}`, icon: 'donut_large' },
              ].map(c => (
                <div key={c.label} className="nd-card" style={{ padding: '14px 18px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                    <span className="material-icons" style={{ fontSize: 16, color: 'var(--nd-text-3)' }}>{c.icon}</span>
                    <span style={{ fontSize: 11.5, color: 'var(--nd-text-3)' }}>{c.label}</span>
                  </div>
                  <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--nd-text-1)' }}>{c.value}</div>
                </div>
              ))}
            </div>

            {/* Per-holding action plan */}
            <div className="nd-card" style={{ padding: 0 }}>
              <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--nd-border)', fontSize: 14, fontWeight: 600, color: 'var(--nd-text-1)' }}>
                Rebalancing Plan
              </div>
              <div style={{ overflowX: 'auto' }}>
                <table className="nd-table">
                  <thead><tr>
                    <th>Symbol</th><th style={{ textAlign: 'center' }}>AI Signal</th><th style={{ textAlign: 'center' }}>Action</th>
                    <th style={{ textAlign: 'right' }}>Now</th><th style={{ textAlign: 'right' }}>Target</th>
                    <th>Reason / AI Alternative</th><th style={{ textAlign: 'center' }}>Execute</th>
                  </tr></thead>
                  <tbody>
                    {actions.map((a: any) => {
                      const st = ACTION_STYLE[a.action] || ACTION_STYLE.HOLD;
                      const sg = signals[a.symbol]?.signal;
                      const sgColor = sg === 'bullish' ? 'var(--nd-green)' : sg === 'bearish' ? 'var(--nd-red)' : 'var(--nd-text-3)';
                      const trade = a.trade;
                      const alt = a.alternative;
                      return (
                        <tr key={a.symbol}>
                          <td style={{ fontWeight: 700, color: 'var(--nd-text-1)', fontFamily: 'monospace' }}>{a.symbol}</td>
                          <td style={{ textAlign: 'center', color: sgColor, fontSize: 12, fontWeight: 600 }}>{sg ?? '—'}</td>
                          <td style={{ textAlign: 'center' }}>
                            <span style={{ fontSize: 11, fontWeight: 700, padding: '2px 9px', borderRadius: 6, background: st.bg, color: st.color }}>{a.action}</span>
                          </td>
                          <td style={{ textAlign: 'right', fontSize: 12.5 }}>{a.currentWeightPct?.toFixed?.(1) ?? a.currentWeightPct ?? 0}%</td>
                          <td style={{ textAlign: 'right', fontSize: 12.5, fontWeight: 600, color: 'var(--nd-accent)' }}>{a.targetWeightPct?.toFixed?.(1) ?? a.targetWeightPct ?? 0}%</td>
                          <td style={{ fontSize: 11.5, color: 'var(--nd-text-2)', maxWidth: 380 }}>
                            <div>{a.reason}</div>
                            {alt && (
                              <div style={{ marginTop: 6, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap',
                                background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 8, padding: '6px 10px' }}>
                                <span className="material-icons" style={{ fontSize: 14, color: 'var(--nd-green)' }}>swap_horiz</span>
                                <span style={{ fontSize: 11.5, color: 'var(--nd-text-3)' }}>Swap into</span>
                                <span style={{ fontWeight: 700, fontFamily: 'monospace', color: 'var(--nd-green)' }}>{alt.symbol}</span>
                                <span style={{ fontSize: 10.5, color: 'var(--nd-text-3)' }}>{alt.reason}</span>
                                {alt.buyQty > 0 && !(trade && trade.transactionType === 'SELL') && (
                                  <button onClick={() => askOrder({ symbol: alt.symbol, transactionType: 'BUY',
                                    quantity: alt.order?.quantity ?? alt.buyQty, orderType: alt.order?.orderType,
                                    price: alt.order?.limitPrice, exchange: alt.order?.exchange, product: alt.order?.product,
                                    estValue: alt.order?.estValue })}
                                    style={{ marginLeft: 'auto', fontSize: 11, fontWeight: 700, padding: '3px 10px', borderRadius: 6, border: '1px solid #22c55e55',
                                      background: '#22c55e1a', color: '#22c55e', cursor: 'pointer' }}>
                                    Buy {alt.buyQty}
                                  </button>
                                )}
                              </div>
                            )}
                          </td>
                          <td style={{ textAlign: 'center' }}>
                            {trade && trade.transactionType === 'SELL' && alt?.order ? (
                              <button onClick={() => askSwap(a, signals[a.symbol])}
                                title={`Sell ${trade.quantity} ${a.symbol} → Buy ${alt.order.quantity} ${alt.symbol}`}
                                style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11, fontWeight: 700, padding: '4px 12px', borderRadius: 6, cursor: 'pointer',
                                  border: '1px solid #8b5cf680', background: '#8b5cf61a', color: '#a78bfa' }}>
                                <span className="material-icons" style={{ fontSize: 13 }}>swap_horiz</span> Swap
                              </button>
                            ) : trade ? (
                              <button onClick={() => askOrder({ symbol: a.symbol, transactionType: trade.transactionType,
                                quantity: trade.quantity, orderType: trade.orderType, price: trade.limitPrice,
                                exchange: trade.exchange, product: trade.product, estValue: trade.estValue })}
                                style={{ fontSize: 11, fontWeight: 700, padding: '4px 12px', borderRadius: 6, cursor: 'pointer',
                                  border: `1px solid ${trade.transactionType === 'SELL' ? '#ef444455' : '#22c55e55'}`,
                                  background: trade.transactionType === 'SELL' ? '#ef44441a' : '#22c55e1a',
                                  color: trade.transactionType === 'SELL' ? '#ef4444' : '#22c55e' }}>
                                {trade.transactionType} {trade.quantity}
                              </button>
                            ) : (
                              <span style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>—</span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>

            {/* Add candidates */}
            {Array.isArray(plan.addCandidates) && plan.addCandidates.length > 0 && (
              <div className="nd-card">
                <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--nd-text-1)', marginBottom: 10 }}>Rotate Into — AI High-Conviction Picks</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {plan.addCandidates.map((c: any) => (
                    <div key={c.symbol} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 0', borderBottom: '1px solid var(--nd-border)' }}>
                      <span style={{ fontWeight: 700, fontFamily: 'monospace', color: 'var(--nd-green)', width: 110 }}>{c.symbol}</span>
                      <span style={{ fontSize: 11, color: 'var(--nd-accent)', width: 70 }}>~{c.suggestedWeightPct}%</span>
                      <span style={{ fontSize: 12, color: 'var(--nd-text-2)', flex: 1 }}>{c.reason}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Risk warnings */}
            {Array.isArray(plan.riskWarnings) && plan.riskWarnings.length > 0 && (
              <div style={{ background: '#f59e0b10', border: '1px solid #f59e0b30', borderRadius: 12, padding: '14px 18px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                  <span className="material-icons" style={{ color: '#f59e0b', fontSize: 18 }}>warning_amber</span>
                  <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--nd-text-1)' }}>Risk Warnings</span>
                </div>
                <ul style={{ margin: 0, paddingLeft: 22, color: 'var(--nd-text-2)', fontSize: 12.5, lineHeight: 1.7 }}>
                  {plan.riskWarnings.map((w: string, i: number) => <li key={i}>{w}</li>)}
                </ul>
              </div>
            )}

            <div style={{ fontSize: 11, color: 'var(--nd-text-3)', textAlign: 'center' }}>
              Advisory only — not investment advice. Source: {optimization.source}. Generated {optimization.asOf ? new Date(optimization.asOf).toLocaleString() : ''}.
            </div>
          </>
        );
      })()}
    </div>
  );
};

export default OptimizeTab;
