import React from 'react';
import apiService from '../../services/api';
import { inr } from './shared';
import { TradeLeg, InvestPick, InvestableBasket } from '../../types';

type InvestSub = 'quick' | 'funds' | 'themes';

interface InvestTabProps {
  investSub: InvestSub;
  setInvestSub: (sub: InvestSub) => void;

  // Quick invest
  investAmount: string;
  setInvestAmount: (v: string) => void;
  investData: any;
  investLoading: boolean;
  generateInvestPlan: () => void;
  askInvestAll: (picks: InvestPick[]) => void;
  askOrder: (spec: TradeLeg) => void;

  // Baskets (shared amount input between themes & funds)
  basketAmt: string;
  setBasketAmt: (v: string) => void;
  askInvestBasket: (b: InvestableBasket) => void;

  // AI Themes
  themes: any[];
  themesLoading: boolean;
  openTheme: string | null;
  themeAnalytics: Record<string, any>;
  themeRebal: Record<string, any>;
  themeBusy: string | null;
  toggleTheme: (id: string) => void;
  loadRebalance: (id: string) => void;
  setThemeBusy: (id: string | null) => void;
  setOrderMsg: (msg: { ok: boolean; text: string } | null) => void;

  // AI Funds
  baskets: any[];
  openBasket: string | null;
  setOpenBasket: (id: string | null) => void;
}

const InvestTab: React.FC<InvestTabProps> = ({
  investSub, setInvestSub,
  investAmount, setInvestAmount, investData, investLoading, generateInvestPlan, askInvestAll, askOrder,
  basketAmt, setBasketAmt, askInvestBasket,
  themes, themesLoading, openTheme, themeAnalytics, themeRebal, themeBusy, toggleTheme, loadRebalance, setThemeBusy, setOrderMsg,
  baskets, openBasket, setOpenBasket,
}) => {
  return (
    <>
      {/* ── AI Invest Tab — sub-nav: Quick Invest / AI Funds / AI Themes ─────── */}
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', padding: '14px 20px 0' }}>
        {([
          { id: 'quick', label: 'Quick Invest', icon: 'savings' },
          { id: 'funds', label: 'AI Funds', icon: 'inventory_2' },
          { id: 'themes', label: 'AI Themes', icon: 'category' },
        ] as const).map(s => {
          const on = investSub === s.id;
          return (
            <button key={s.id} onClick={() => setInvestSub(s.id)}
              style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '7px 14px', borderRadius: 9, cursor: 'pointer',
                border: `1px solid ${on ? 'var(--nd-green)' : 'var(--nd-border)'}`,
                background: on ? 'var(--nd-green)' : 'transparent',
                color: on ? '#fff' : 'var(--nd-text-2)', fontWeight: 700, fontSize: 12.5 }}>
              <span className="material-icons" style={{ fontSize: 16 }}>{s.icon}</span>{s.label}
            </button>
          );
        })}
      </div>

      {/* ── Quick Invest (amount → AI-split across A/B scan picks) ─────────────── */}
      {investSub === 'quick' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div className="nd-card" style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
            <span className="material-icons" style={{ fontSize: 26, color: 'var(--nd-green)' }}>savings</span>
            <div style={{ flex: 1, minWidth: 220 }}>
              <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--nd-text-1)' }}>AI Invest</div>
              <div style={{ fontSize: 12.5, color: 'var(--nd-text-2)', marginTop: 2 }}>
                Enter an amount and the AI divides it across the best-performing stocks from its live scan (graded A/B, conviction-weighted) — then place them all in one go.
              </div>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <div style={{ position: 'relative' }}>
                <span style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: 'var(--nd-text-3)', fontSize: 14 }}>₹</span>
                <input type="number" min={0} value={investAmount} onChange={e => setInvestAmount(e.target.value)}
                  placeholder="Amount" className="nd-input" style={{ width: 140, paddingLeft: 22 }} />
              </div>
              <button onClick={generateInvestPlan} disabled={investLoading}
                style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '9px 16px', borderRadius: 10, border: 'none',
                  background: 'var(--nd-green)', color: '#fff', fontWeight: 600, fontSize: 14, cursor: investLoading ? 'wait' : 'pointer' }}>
                <span className={`material-icons${investLoading ? ' nd-spin' : ''}`} style={{ fontSize: 18 }}>{investLoading ? 'autorenew' : 'auto_awesome'}</span>
                {investLoading ? 'Building…' : 'Build plan'}
              </button>
            </div>
          </div>

          {investData && investData.picks?.length > 0 && (
            <>
              <div className="nd-card" style={{ display: 'flex', gap: 24, flexWrap: 'wrap', alignItems: 'center' }}>
                <div><div className="nd-label" style={{ margin: 0 }}>Amount</div><div style={{ fontSize: 18, fontWeight: 700 }}>₹{inr(investData.amount)}</div></div>
                <div><div className="nd-label" style={{ margin: 0 }}>Deploying</div><div style={{ fontSize: 18, fontWeight: 700, color: 'var(--nd-green)' }}>₹{inr(investData.deployed)}</div></div>
                <div><div className="nd-label" style={{ margin: 0 }}>Leftover</div><div style={{ fontSize: 18, fontWeight: 700, color: 'var(--nd-text-2)' }}>₹{inr(investData.leftover)}</div></div>
                <div><div className="nd-label" style={{ margin: 0 }}>Stocks</div><div style={{ fontSize: 18, fontWeight: 700 }}>{investData.count}</div></div>
                <button onClick={() => askInvestAll(investData.picks)}
                  style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 6, padding: '10px 18px', borderRadius: 10, border: 'none',
                    background: 'var(--nd-green)', color: '#fff', fontWeight: 700, fontSize: 14, cursor: 'pointer' }}>
                  <span className="material-icons" style={{ fontSize: 18 }}>shopping_cart_checkout</span>
                  Invest all
                </button>
              </div>

              <div className="nd-card" style={{ padding: 0 }}>
                <div style={{ overflowX: 'auto' }}>
                  <table className="nd-table">
                    <thead><tr>
                      <th>Stock</th><th style={{ textAlign: 'center' }}>Grade</th><th style={{ textAlign: 'right' }}>Win%</th>
                      <th style={{ textAlign: 'right' }}>Price</th><th style={{ textAlign: 'right' }}>Alloc</th>
                      <th style={{ textAlign: 'right' }}>Qty</th><th style={{ textAlign: 'right' }}>Cost</th>
                      <th>Why (AI)</th><th style={{ textAlign: 'center' }}>Buy</th>
                    </tr></thead>
                    <tbody>
                      {investData.picks.map((p: any) => (
                        <tr key={p.symbol}>
                          <td><div style={{ fontWeight: 700, fontFamily: 'monospace', color: 'var(--nd-text-1)' }}>{p.symbol}</div>
                            <div style={{ fontSize: 10.5, color: 'var(--nd-text-3)' }}>{p.name} · {p.sector}</div></td>
                          <td style={{ textAlign: 'center', fontWeight: 700, color: p.grade === 'A' ? 'var(--nd-green)' : 'var(--nd-blue)' }}>{p.grade}</td>
                          <td style={{ textAlign: 'right', fontSize: 12.5, color: 'var(--nd-green)' }}>{p.winProbability != null ? `${Math.round(p.winProbability * 100)}%` : '—'}</td>
                          <td style={{ textAlign: 'right', fontSize: 12.5 }}>₹{inr(p.price)}</td>
                          <td style={{ textAlign: 'right', fontSize: 12.5, fontWeight: 600, color: 'var(--nd-accent)' }}>{p.weightPct}%</td>
                          <td style={{ textAlign: 'right', fontSize: 12.5 }}>{p.quantity}</td>
                          <td style={{ textAlign: 'right', fontSize: 12.5 }}>₹{inr(p.estCost)}</td>
                          <td style={{ fontSize: 11, color: 'var(--nd-text-3)', maxWidth: 320, lineHeight: 1.5 }}>{p.reasoning}</td>
                          <td style={{ textAlign: 'center' }}>
                            <button onClick={() => askOrder({ symbol: p.symbol, transactionType: 'BUY', quantity: p.order.quantity,
                              orderType: p.order.orderType, price: p.order.limitPrice, exchange: p.order.exchange, product: p.order.product, estValue: p.order.estValue })}
                              style={{ fontSize: 11, fontWeight: 700, padding: '4px 12px', borderRadius: 6, border: '1px solid #22c55e55', background: '#22c55e1a', color: '#22c55e', cursor: 'pointer' }}>
                              Buy {p.quantity}
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
              <div style={{ fontSize: 11, color: 'var(--nd-text-3)', textAlign: 'center' }}>
                Conviction-weighted (win probability, capped 35%/stock) protective LIMIT buys. Advisory only — not investment advice.
              </div>
            </>
          )}

          {investData && (!investData.picks || investData.picks.length === 0) && (
            <div className="nd-card" style={{ textAlign: 'center', padding: 40, color: 'var(--nd-text-3)', fontSize: 13 }}>
              {investData.note || 'No qualifying picks right now — try after the next AI scan, or increase the amount.'}
            </div>
          )}

          {!investData && !investLoading && (
            <div className="nd-card" style={{ textAlign: 'center', padding: 40, color: 'var(--nd-text-3)', fontSize: 13 }}>
              Enter the amount from your Groww wallet and click <strong>Build plan</strong> — the AI will split it across its best current picks.
            </div>
          )}
        </div>
      )}

      {/* ── AI Themes (thematic baskets) ── */}
      {investSub === 'themes' && (
        <div style={{ padding: '18px 20px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 4 }}>
            <span style={{ fontSize: 12.5, color: 'var(--nd-text-2)' }}>Invest amount ₹</span>
            <input value={basketAmt} onChange={e => setBasketAmt(e.target.value.replace(/[^0-9]/g, ''))}
              className="nd-input" style={{ width: 140 }} placeholder="25000" />
          </div>
          <div style={{ fontSize: 11.5, color: 'var(--nd-text-3)', marginBottom: 14 }}>
            AI-curated thematic baskets — each tracks a real-world theme, populated &amp; conviction-weighted from the live scan. <strong>Buy</strong> = the AI endorses it now; <strong>Watch</strong> = on the theme's radar. Open one for backtested risk/return vs NIFTY and a rebalance proposal.
          </div>
          {themesLoading && themes.length === 0 ? (
            <div className="nd-card" style={{ textAlign: 'center', padding: 40, color: 'var(--nd-text-3)', fontSize: 13 }}>Building thematic baskets from the live scan…</div>
          ) : (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 360px), 1fr))', gap: 14 }}>
              {themes.map((t: any) => {
                const open = openTheme === t.id;
                const an = themeAnalytics[t.id];
                const rb = themeRebal[t.id];
                const shown = open ? t.holdings : t.holdings.slice(0, 4);
                return (
                  <div key={t.id} className="nd-card" style={{ padding: '14px 16px', minWidth: 0 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8 }}>
                      <div style={{ fontSize: 14.5, fontWeight: 700, color: 'var(--nd-text-1)' }}>{t.emoji} {t.name}</div>
                      <span style={{ fontSize: 10, fontWeight: 700, color: 'var(--nd-purple)', border: '1px solid var(--nd-purple)', borderRadius: 5, padding: '1px 6px', whiteSpace: 'nowrap' }}>{t.risk} risk</span>
                    </div>
                    <div style={{ fontSize: 11.5, color: 'var(--nd-text-3)', margin: '4px 0 8px' }}>{t.thesis}</div>
                    <div style={{ fontSize: 11, color: 'var(--nd-text-2)', marginBottom: 8 }}>
                      {t.stats.size} stocks · <span style={{ color: 'var(--nd-green)' }}>{t.stats.buys} buy</span> · {t.stats.watch} watch · avg win-prob {(t.stats.avgWinProbability * 100).toFixed(0)}%
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 3, marginBottom: 8 }}>
                      {shown.map((h: any) => (
                        <div key={h.symbol} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
                          <span style={{ fontFamily: 'monospace', fontWeight: 600, color: 'var(--nd-text-1)', minWidth: 92 }}>{h.symbol}</span>
                          <span style={{ fontSize: 9.5, fontWeight: 700, borderRadius: 4, padding: '0 5px',
                            background: h.stance === 'buy' ? 'rgba(34,197,94,0.14)' : 'var(--nd-surface-2)',
                            color: h.stance === 'buy' ? 'var(--nd-green)' : 'var(--nd-text-3)' }}>{h.stance === 'buy' ? `BUY ${h.grade}` : 'WATCH'}</span>
                          <span style={{ color: 'var(--nd-text-3)', fontSize: 10.5, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{open ? (h.rationale || h.sector) : h.sector}</span>
                          <span style={{ color: 'var(--nd-green)', fontWeight: 700 }}>{h.weightPct}%</span>
                        </div>
                      ))}
                      {t.holdings.length > 4 && (
                        <button onClick={() => toggleTheme(t.id)} style={{ alignSelf: 'flex-start', background: 'none', border: 'none', color: 'var(--nd-blue)', cursor: 'pointer', fontSize: 11, padding: 0 }}>
                          {open ? 'show less' : `+${t.holdings.length - 4} more · view analytics`}
                        </button>
                      )}
                    </div>

                    {open && (
                      <div style={{ borderTop: '1px solid var(--nd-border)', paddingTop: 8, marginBottom: 8 }}>
                        {themeBusy === t.id ? (
                          <div style={{ fontSize: 11.5, color: 'var(--nd-text-3)' }}>Backtesting the basket vs NIFTY…</div>
                        ) : an && an.ok ? (
                          <>
                            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 6, marginBottom: 6 }}>
                              {[
                                { l: 'CAGR (1y)', v: `${an.cagrPct}%`, c: an.cagrPct >= 0 ? 'var(--nd-green)' : 'var(--nd-red)' },
                                { l: 'Volatility', v: `${an.annVolatilityPct}%`, c: 'var(--nd-text-1)' },
                                { l: 'Max drawdown', v: `${an.maxDrawdownPct}%`, c: 'var(--nd-red)' },
                                { l: 'Sharpe', v: `${an.sharpe}`, c: 'var(--nd-text-1)' },
                                { l: 'Beta', v: `${an.beta ?? '—'}`, c: 'var(--nd-text-1)' },
                                { l: 'Alpha vs NIFTY', v: an.alphaPct != null ? `${an.alphaPct > 0 ? '+' : ''}${an.alphaPct}%` : '—', c: (an.alphaPct ?? 0) >= 0 ? 'var(--nd-green)' : 'var(--nd-red)' },
                              ].map(m => (
                                <div key={m.l} style={{ background: 'var(--nd-surface-2)', borderRadius: 6, padding: '5px 7px' }}>
                                  <div style={{ fontSize: 9.5, color: 'var(--nd-text-3)' }}>{m.l}</div>
                                  <div style={{ fontSize: 13, fontWeight: 700, color: m.c }}>{m.v}</div>
                                </div>
                              ))}
                            </div>
                            <div style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>
                              Basket vs NIFTY: {an.totalReturnPct}% vs {an.niftyReturnPct}% over {an.windowDays}d · {an.volatilityLabel} volatility · coverage {an.coverage}
                            </div>
                          </>
                        ) : (
                          <div style={{ fontSize: 11.5, color: 'var(--nd-text-3)' }}>Backtest unavailable (not enough price history).</div>
                        )}
                      </div>
                    )}

                    {open && rb && (
                      <div style={{ borderTop: '1px solid var(--nd-border)', paddingTop: 8, marginBottom: 8, fontSize: 11.5 }}>
                        <div style={{ fontWeight: 700, color: 'var(--nd-text-1)', marginBottom: 3 }}>
                          Rebalance update · drift {rb.driftPct}% {rb.needsRebalance ? '' : '· already aligned'}
                        </div>
                        {rb.add?.length > 0 && <div style={{ color: 'var(--nd-green)' }}>Add: {rb.add.join(', ')}</div>}
                        {rb.drop?.length > 0 && <div style={{ color: 'var(--nd-red)' }}>Drop: {rb.drop.join(', ')}</div>}
                        {(!rb.add?.length && !rb.drop?.length) && <div style={{ color: 'var(--nd-text-3)' }}>No changes — the AI still endorses this basket as-is.</div>}
                      </div>
                    )}

                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                      {open && (
                        <button onClick={() => loadRebalance(t.id)} disabled={themeBusy === t.id + ':rb'}
                          style={{ flex: '0 0 auto', background: 'var(--nd-surface-2)', color: 'var(--nd-text-1)', border: '1px solid var(--nd-border)', borderRadius: 8, padding: '8px 12px', fontSize: 12, fontWeight: 700, cursor: 'pointer' }}>
                          {themeBusy === t.id + ':rb' ? '…' : 'Rebalance'}
                        </button>
                      )}
                      <button onClick={async () => {
                        setThemeBusy(t.id + ':pt');
                        try {
                          const r: any = await apiService.paperTestTheme(t.id, 200000);
                          const n = (r?.data?.positions ?? []).length;
                          setOrderMsg({ ok: true, text: `Paper-test portfolio created from "${t.name}" (${n} stocks, ₹2L). Track it on Dashboard → Delivery Autopilot.` });
                        } catch { setOrderMsg({ ok: false, text: 'Could not create the paper-test portfolio.' }); }
                        finally { setThemeBusy(null); }
                      }} disabled={themeBusy === t.id + ':pt'}
                        style={{ flex: '0 0 auto', display: 'flex', alignItems: 'center', gap: 5, background: 'transparent', color: 'var(--nd-blue)', border: '1px solid var(--nd-blue)', borderRadius: 8, padding: '8px 12px', fontSize: 12, fontWeight: 700, cursor: 'pointer' }}>
                        <span className="material-icons" style={{ fontSize: 15 }}>science</span>
                        {themeBusy === t.id + ':pt' ? '…' : 'Paper-test'}
                      </button>
                      <button onClick={() => askInvestBasket(t)} className="nd-btn"
                        style={{ flex: 1, minWidth: 130, background: 'var(--nd-green)', color: '#fff', border: 'none', borderRadius: 8, padding: '8px 0', fontSize: 12.5, fontWeight: 700, cursor: 'pointer' }}>
                        Invest ₹{Number(basketAmt || 0).toLocaleString('en-IN')}
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* ── AI Funds (quant baskets) ── */}
      {investSub === 'funds' && (
        <div style={{ padding: '18px 20px 0' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 14 }}>
            <span style={{ fontSize: 12.5, color: 'var(--nd-text-2)' }}>Invest amount ₹</span>
            <input value={basketAmt} onChange={e => setBasketAmt(e.target.value.replace(/[^0-9]/g, ''))}
              className="nd-input" style={{ width: 140 }} placeholder="25000" />
            <span style={{ fontSize: 11.5, color: 'var(--nd-text-3)' }}>AI-built, mutual-fund-style stock baskets from the live scan — pick one and invest across it in one click.</span>
          </div>
          {baskets.length === 0 ? (
            <div className="nd-card" style={{ textAlign: 'center', padding: 40, color: 'var(--nd-text-3)', fontSize: 13 }}>Building baskets from the latest AI scan…</div>
          ) : (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 340px), 1fr))', gap: 14 }}>
              {baskets.map((b: any) => {
                const open = openBasket === b.id;
                return (
                  <div key={b.id} className="nd-card" style={{ padding: '14px 16px', minWidth: 0 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8 }}>
                      <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--nd-text-1)' }}>{b.name}</div>
                      <span style={{ fontSize: 10, fontWeight: 700, color: 'var(--nd-purple)', border: '1px solid var(--nd-purple)', borderRadius: 5, padding: '1px 6px' }}>{b.risk}</span>
                    </div>
                    <div style={{ fontSize: 11.5, color: 'var(--nd-text-3)', margin: '4px 0 8px' }}>{b.description}</div>
                    <div style={{ fontSize: 11, color: 'var(--nd-text-2)', marginBottom: 8 }}>
                      {b.stats.size} stocks · {b.stats.sectors} sectors · avg win-prob {(b.stats.avgWinProbability * 100).toFixed(0)}%
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 3, marginBottom: 8 }}>
                      {(open ? b.holdings : b.holdings.slice(0, 4)).map((h: any) => (
                        <div key={h.symbol} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
                          <span style={{ fontFamily: 'monospace', fontWeight: 600, color: 'var(--nd-text-1)', minWidth: 96 }}>{h.symbol}</span>
                          <span style={{ color: 'var(--nd-text-3)', fontSize: 10.5, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{h.sector}</span>
                          <span style={{ color: 'var(--nd-green)', fontWeight: 700 }}>{h.weightPct}%</span>
                        </div>
                      ))}
                      {b.holdings.length > 4 && (
                        <button onClick={() => setOpenBasket(open ? null : b.id)} style={{ alignSelf: 'flex-start', background: 'none', border: 'none', color: 'var(--nd-blue)', cursor: 'pointer', fontSize: 11, padding: 0 }}>
                          {open ? 'show less' : `+${b.holdings.length - 4} more`}
                        </button>
                      )}
                    </div>
                    <button onClick={() => askInvestBasket(b)} className="nd-btn"
                      style={{ width: '100%', background: 'var(--nd-green)', color: '#fff', border: 'none', borderRadius: 8, padding: '8px 0', fontSize: 12.5, fontWeight: 700, cursor: 'pointer' }}>
                      Invest ₹{Number(basketAmt || 0).toLocaleString('en-IN')}
                    </button>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </>
  );
};

export default InvestTab;
