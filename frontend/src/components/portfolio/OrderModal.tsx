import React from 'react';
import { inr } from './shared';

interface OrderModalProps {
  orderMsg: { ok: boolean; text: string } | null;
  setOrderMsg: (msg: { ok: boolean; text: string } | null) => void;
  pendingOrder: any;
  setPendingOrder: (order: any) => void;
  placing: boolean;
  confirmOrder: () => void;
}

const OrderModal: React.FC<OrderModalProps> = ({ orderMsg, setOrderMsg, pendingOrder, setPendingOrder, placing, confirmOrder }) => {
  return (
    <>
      {/* ── Order result toast ── */}
      {orderMsg && (
        <div onClick={() => setOrderMsg(null)}
          style={{ position: 'fixed', bottom: 20, right: 20, zIndex: 1100, maxWidth: 360, cursor: 'pointer',
            background: orderMsg.ok ? 'var(--nd-green-50)' : 'var(--nd-red-50)',
            border: `1px solid ${orderMsg.ok ? '#22c55e55' : '#ef444455'}`, borderRadius: 10, padding: '12px 16px',
            display: 'flex', alignItems: 'center', gap: 10, boxShadow: 'var(--nd-shadow-md)' }}>
          <span className="material-icons" style={{ color: orderMsg.ok ? 'var(--nd-green)' : 'var(--nd-red)', fontSize: 20 }}>
            {orderMsg.ok ? 'check_circle' : 'error_outline'}
          </span>
          <span style={{ fontSize: 13, color: orderMsg.ok ? '#065f46' : 'var(--nd-red)' }}>{orderMsg.text}</span>
        </div>
      )}

      {/* ── Order confirmation modal ── */}
      {pendingOrder && (
        <div onClick={e => { if (e.target === e.currentTarget && !placing) setPendingOrder(null); }}
          style={{ position: 'fixed', inset: 0, background: '#00000080', zIndex: 1200, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}>
          <div style={{ background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderRadius: 14, width: '100%', maxWidth: 460, maxHeight: '88vh', overflow: 'auto', padding: 22 }}>
            {pendingOrder.kind === 'basket' ? (
              <>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
                  <span className="material-icons" style={{ color: 'var(--nd-green)' }}>shopping_cart_checkout</span>
                  <span style={{ fontSize: 16, fontWeight: 700, color: 'var(--nd-text-1)' }}>Confirm investment</span>
                </div>
                <div style={{ fontSize: 12.5, color: 'var(--nd-text-2)', marginBottom: 10 }}>
                  Placing <strong style={{ color: 'var(--nd-text-1)' }}>{pendingOrder.legs.length}</strong> protective LIMIT buy orders
                  {' '}(~₹{inr(pendingOrder.legs.reduce((s: number, l: any) => s + (l.estValue || 0), 0))} total):
                </div>
                <div style={{ maxHeight: 240, overflow: 'auto', marginBottom: 12 }}>
                  {pendingOrder.legs.map((l: any) => (
                    <div key={l.symbol} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 0', borderBottom: '1px solid var(--nd-border)', fontSize: 12.5 }}>
                      <strong style={{ color: 'var(--nd-green)' }}>BUY {l.quantity}</strong>
                      <strong style={{ fontFamily: 'monospace', color: 'var(--nd-text-1)' }}>{l.symbol}</strong>
                      <span style={{ marginLeft: 'auto', color: 'var(--nd-text-3)' }}>{l.orderType === 'LIMIT' && l.price ? `LIMIT @ ₹${l.price}` : 'MARKET'} · ~₹{inr(l.estValue || 0)}</span>
                    </div>
                  ))}
                </div>
                <div style={{ fontSize: 11.5, color: 'var(--nd-red)', background: 'var(--nd-red-50)', borderRadius: 8, padding: '8px 10px', marginBottom: 14 }}>
                  Places <strong>{pendingOrder.legs.length} real orders on your Groww account</strong>, one after another. Executes during market hours; needs sufficient funds.
                </div>
              </>
            ) : pendingOrder.kind === 'swap' ? (() => {
              const { sell, buy } = pendingOrder;
              const Leg = ({ leg, n }: { leg: any; n: string }) => (
                <div style={{ border: '1px solid var(--nd-border)', borderRadius: 10, padding: '10px 12px', marginBottom: 8 }}>
                  <div style={{ fontSize: 10.5, color: 'var(--nd-text-3)', fontWeight: 700, letterSpacing: 0.5, marginBottom: 3 }}>{n}</div>
                  <div style={{ fontSize: 13.5 }}>
                    <strong style={{ color: leg.transactionType === 'SELL' ? 'var(--nd-red)' : 'var(--nd-green)' }}>{leg.transactionType} {leg.quantity}</strong>
                    {' '}<strong style={{ color: 'var(--nd-text-1)', fontFamily: 'monospace' }}>{leg.symbol}</strong>
                  </div>
                  <div style={{ fontSize: 11.5, color: 'var(--nd-text-2)', marginTop: 2 }}>
                    {leg.orderType === 'LIMIT' && leg.price ? `LIMIT @ ₹${leg.price}` : 'MARKET'} · {leg.exchange || 'NSE'} · CNC
                    {leg.estValue ? ` · ~₹${inr(leg.estValue)}` : ''}
                  </div>
                </div>
              );
              return (
                <>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
                    <span className="material-icons" style={{ color: '#a78bfa' }}>swap_horiz</span>
                    <span style={{ fontSize: 16, fontWeight: 700, color: 'var(--nd-text-1)' }}>Confirm swap</span>
                  </div>
                  <Leg leg={sell} n="STEP 1 — SELL FIRST" />
                  <div style={{ textAlign: 'center', color: 'var(--nd-text-3)', margin: '-2px 0 6px' }}>
                    <span className="material-icons" style={{ fontSize: 18 }}>south</span>
                  </div>
                  <Leg leg={buy} n="STEP 2 — THEN BUY" />

                  {/* Why the AI recommends this swap */}
                  {pendingOrder.basis && (() => {
                    const b = pendingOrder.basis;
                    const sc = b.sell.signal === 'bullish' ? 'var(--nd-green)' : b.sell.signal === 'bearish' ? 'var(--nd-red)' : 'var(--nd-text-2)';
                    const Fact = ({ k, v, c }: { k: string; v: any; c?: string }) => (v === undefined || v === null || v === '') ? null : (
                      <span style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>{k} <strong style={{ color: c || 'var(--nd-text-1)' }}>{v}</strong></span>
                    );
                    return (
                      <div style={{ background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 10, padding: '10px 12px', marginBottom: 10 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
                          <span className="material-icons" style={{ fontSize: 14, color: 'var(--nd-accent)' }}>insights</span>
                          <span style={{ fontSize: 11.5, fontWeight: 700, color: 'var(--nd-text-1)' }}>Why the AI suggests this swap</span>
                        </div>
                        <div style={{ fontSize: 11.5, color: 'var(--nd-text-2)', marginBottom: 4 }}>
                          <strong style={{ color: 'var(--nd-red)' }}>Out — {sell.symbol}:</strong> {b.sell.reason}
                        </div>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginBottom: 8 }}>
                          <Fact k="AI signal" v={b.sell.signal} c={sc} />
                          <Fact k="health" v={b.sell.health != null ? `${b.sell.health}/100` : null} />
                          <Fact k="RSI" v={b.sell.rsi} />
                          <Fact k="momentum" v={b.sell.momentum != null ? `${b.sell.momentum > 0 ? '+' : ''}${b.sell.momentum}%` : null} />
                          <Fact k="trend" v={b.sell.trend} />
                          <Fact k="P&L" v={b.sell.pnl != null ? `${b.sell.pnl > 0 ? '+' : ''}${b.sell.pnl}%` : null} c={(b.sell.pnl ?? 0) >= 0 ? 'var(--nd-green)' : 'var(--nd-red)'} />
                          <Fact k="weight" v={`${b.sell.weightNow ?? '–'}% → ${b.sell.weightTarget ?? '–'}%`} />
                        </div>
                        <div style={{ fontSize: 11.5, color: 'var(--nd-text-2)', marginBottom: 4 }}>
                          <strong style={{ color: 'var(--nd-green)' }}>In — {buy.symbol}:</strong>{' '}
                          AI grade <strong style={{ color: 'var(--nd-text-1)' }}>{b.buy.grade}</strong>
                          {b.buy.winProb != null ? <> · win prob <strong style={{ color: 'var(--nd-green)' }}>{Math.round(b.buy.winProb * 100)}%</strong></> : null}
                          {b.buy.sameSector ? <> · <span style={{ color: 'var(--nd-accent)' }}>same sector ({b.buy.sector})</span></> : (b.buy.sector ? <> · {b.buy.sector}</> : null)}
                        </div>
                        {b.buy.reasoning && (
                          <div style={{ fontSize: 11, color: 'var(--nd-text-3)', lineHeight: 1.5 }}>{b.buy.reasoning}</div>
                        )}
                      </div>
                    );
                  })()}

                  <div style={{ fontSize: 11.5, color: 'var(--nd-red)', background: 'var(--nd-red-50)', borderRadius: 8, padding: '8px 10px', margin: '6px 0 14px' }}>
                    Places <strong>two real orders on Groww</strong> — the SELL first, then the BUY (only if the sell is accepted). Executes during market hours.
                  </div>
                </>
              );
            })() : (
              <>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
                  <span className="material-icons" style={{ color: pendingOrder.transactionType === 'SELL' ? 'var(--nd-red)' : 'var(--nd-green)' }}>
                    {pendingOrder.transactionType === 'SELL' ? 'sell' : 'shopping_cart'}
                  </span>
                  <span style={{ fontSize: 16, fontWeight: 700, color: 'var(--nd-text-1)' }}>Confirm {pendingOrder.transactionType} order</span>
                </div>
                <div style={{ fontSize: 13.5, color: 'var(--nd-text-2)', lineHeight: 1.7, marginBottom: 8 }}>
                  <div><strong style={{ color: 'var(--nd-text-1)' }}>{pendingOrder.transactionType} {pendingOrder.quantity}</strong> × <strong style={{ color: 'var(--nd-text-1)', fontFamily: 'monospace' }}>{pendingOrder.symbol}</strong></div>
                  <div>
                    {pendingOrder.orderType === 'LIMIT' && pendingOrder.price
                      ? <>Type: <strong style={{ color: 'var(--nd-text-1)' }}>LIMIT @ ₹{pendingOrder.price}</strong> (protective collar)</>
                      : 'Type: MARKET'} · Product: CNC · {pendingOrder.exchange || 'NSE'}
                  </div>
                  {(pendingOrder.estValue || (pendingOrder.price && pendingOrder.quantity)) ? (
                    <div>Est. value: ₹{inr(pendingOrder.estValue ?? pendingOrder.quantity * pendingOrder.price)}</div>
                  ) : null}
                </div>
                <div style={{ fontSize: 11.5, color: 'var(--nd-red)', background: 'var(--nd-red-50)', borderRadius: 8, padding: '8px 10px', marginBottom: 14 }}>
                  This places a <strong>real order on your Groww account</strong>. Executes only during market hours.
                </div>
              </>
            )}
            <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
              <button onClick={() => setPendingOrder(null)} disabled={placing}
                style={{ padding: '8px 16px', borderRadius: 8, border: '1px solid var(--nd-border)', background: 'var(--nd-surface)', color: 'var(--nd-text-2)', cursor: 'pointer', fontSize: 13, fontWeight: 600 }}>
                Cancel
              </button>
              <button onClick={confirmOrder} disabled={placing}
                style={{ padding: '8px 18px', borderRadius: 8, border: 'none', cursor: placing ? 'wait' : 'pointer', fontSize: 13, fontWeight: 700, color: '#fff',
                  background: pendingOrder.kind === 'swap' ? '#8b5cf6'
                    : pendingOrder.kind === 'basket' ? 'var(--nd-green)'
                    : (pendingOrder.transactionType === 'SELL' ? 'var(--nd-red)' : 'var(--nd-green)') }}>
                {placing ? 'Placing…'
                  : pendingOrder.kind === 'swap' ? 'Confirm swap'
                  : pendingOrder.kind === 'basket' ? `Invest in ${pendingOrder.legs.length} stocks`
                  : `Confirm ${pendingOrder.transactionType}`}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
};

export default OrderModal;
