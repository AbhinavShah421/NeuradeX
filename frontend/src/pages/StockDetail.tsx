import React, { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import apiService from '../services/api';
import socketService from '../services/socket';
import { Stock, Prediction, SentimentData, OrderResponse } from '../types';

const inr = (v: number) =>
  `₹${v.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const Bar: React.FC<{ value: number; color: string }> = ({ value, color }) => (
  <div style={{ height: 6, background: 'var(--nd-surface-2)', borderRadius: 3, overflow: 'hidden', marginTop: 6 }}>
    <div style={{ width: `${Math.max(0, Math.min(100, value * 100))}%`, height: '100%', background: color, borderRadius: 3 }} />
  </div>
);

const StockDetail: React.FC = () => {
  const { symbol } = useParams<{ symbol: string }>();
  const navigate = useNavigate();

  const [stock, setStock] = useState<Stock | null>(null);
  const [prediction, setPrediction] = useState<Prediction | null>(null);
  const [sentiment, setSentiment] = useState<SentimentData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [predictionHistory, setPredictionHistory] = useState<any[]>([]);

  const [showOrderModal, setShowOrderModal] = useState(false);
  const [orderSide, setOrderSide] = useState<'BUY' | 'SELL'>('BUY');
  const [orderType, setOrderType] = useState<'MARKET' | 'LIMIT'>('MARKET');
  const [orderQty, setOrderQty] = useState(1);
  const [orderPrice, setOrderPrice] = useState('');
  const [orderProduct, setOrderProduct] = useState<'CNC' | 'INTRADAY'>('CNC');
  const [orderLoading, setOrderLoading] = useState(false);
  const [orderResult, setOrderResult] = useState<OrderResponse | null>(null);
  const [orderError, setOrderError] = useState<string | null>(null);

  useEffect(() => {
    if (!symbol) { navigate('/'); return; }
    fetchData();
    socketService.subscribeToStock(symbol);
    return () => { socketService.unsubscribeFromStock(symbol); };
  }, [symbol, navigate]);

  const fetchData = async () => {
    try {
      setLoading(true);
      const [stockRes, predRes, sentRes, histRes] = await Promise.all([
        apiService.getStock(symbol!),
        apiService.getPrediction(symbol!),
        apiService.getSentiment(symbol!),
        apiService.getPredictionHistory(symbol!, 20),
      ]);
      if (stockRes.data) setStock(stockRes.data);
      if (predRes.data) setPrediction(predRes.data);
      if (sentRes.data) setSentiment(sentRes.data);
      if (histRes.data) setPredictionHistory(histRes.data as any[]);
      setError(null);
    } catch {
      setError('Failed to fetch stock details');
    } finally {
      setLoading(false);
    }
  };

  const handlePlaceOrder = async () => {
    if (!symbol) return;
    setOrderLoading(true);
    setOrderError(null);
    setOrderResult(null);
    try {
      const res = await apiService.placeOrder({
        symbol: symbol.toUpperCase(),
        quantity: orderQty,
        transactionType: orderSide,
        orderType,
        price: orderType === 'LIMIT' ? parseFloat(orderPrice) : undefined,
        product: orderProduct,
        exchange: 'NSE',
      });
      if (res.data) setOrderResult(res.data);
    } catch (err: any) {
      setOrderError(err?.response?.data?.detail || 'Order failed. Please try again.');
    } finally {
      setOrderLoading(false);
    }
  };

  const closeModal = () => {
    setShowOrderModal(false);
    setOrderResult(null);
    setOrderError(null);
    setOrderQty(1);
    setOrderPrice('');
    setOrderType('MARKET');
    setOrderProduct('CNC');
  };

  if (loading) {
    return (
      <div className="nd-loading">
        <span className="material-icons nd-spin">autorenew</span>
        <span>Loading {symbol}...</span>
      </div>
    );
  }

  if (error || !stock) {
    return (
      <div className="nd-loading">
        <span className="material-icons" style={{ fontSize: 40, color: 'var(--nd-red)' }}>error_outline</span>
        <span style={{ color: 'var(--nd-text-2)' }}>{error || 'Stock not found'}</span>
        <button onClick={() => navigate('/')} className="nd-btn nd-btn-primary" style={{ marginTop: 8 }}>
          <span className="material-icons" style={{ fontSize: 16 }}>arrow_back</span>
          Back to Dashboard
        </button>
      </div>
    );
  }

  const isUp = stock.change >= 0;
  const estimatedValue = orderQty * (orderType === 'LIMIT' && orderPrice ? parseFloat(orderPrice) : stock.price);

  return (
    <div>
      {/* Back nav */}
      <button onClick={() => navigate('/')} style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 13, color: 'var(--nd-green)', background: 'none', border: 'none', cursor: 'pointer', marginBottom: 20, padding: 0, fontFamily: 'Roboto, sans-serif' }}>
        <span className="material-icons" style={{ fontSize: 16 }}>arrow_back</span>
        Back to Dashboard
      </button>

      {/* Stock header */}
      <div className="nd-card" style={{ marginBottom: 16, padding: '20px 24px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 16 }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 4 }}>
              <h1 style={{ fontSize: 28, fontWeight: 700, color: 'var(--nd-text-1)' }}>{stock.symbol}</h1>
              {stock.sector && <span className="nd-badge nd-badge-gray">{stock.sector}</span>}
            </div>
            <p style={{ fontSize: 13.5, color: 'var(--nd-text-2)' }}>{stock.name} · NSE</p>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 20, flexWrap: 'wrap' }}>
            <div style={{ textAlign: 'right' }}>
              <p style={{ fontSize: 32, fontWeight: 700, color: 'var(--nd-text-1)', lineHeight: 1.1 }}>{inr(stock.price)}</p>
              <p style={{ fontSize: 14, fontWeight: 500, color: isUp ? 'var(--nd-green)' : 'var(--nd-red)', marginTop: 4, display: 'flex', alignItems: 'center', gap: 4, justifyContent: 'flex-end' }}>
                <span className="material-icons" style={{ fontSize: 16 }}>{isUp ? 'arrow_drop_up' : 'arrow_drop_down'}</span>
                {inr(Math.abs(stock.change))} ({stock.change >= 0 ? '+' : ''}{stock.changePercent.toFixed(2)}%)
              </p>
            </div>
            <div style={{ display: 'flex', gap: 10 }}>
              <button onClick={() => { setOrderSide('BUY'); setShowOrderModal(true); }}
                className="nd-btn nd-btn-primary" style={{ padding: '10px 24px', fontSize: 14, fontWeight: 700 }}>
                BUY
              </button>
              <button onClick={() => { setOrderSide('SELL'); setShowOrderModal(true); }}
                className="nd-btn" style={{ padding: '10px 24px', fontSize: 14, fontWeight: 700, background: 'var(--nd-red)', color: '#fff' }}>
                SELL
              </button>
            </div>
          </div>
        </div>

        {/* Quick stats strip */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 0, marginTop: 20, paddingTop: 16, borderTop: '1px solid var(--nd-border)' }}>
          {[
            { label: 'Day High',  value: inr(stock.high),    icon: 'arrow_upward',   color: 'var(--nd-green)' },
            { label: 'Day Low',   value: inr(stock.low),     icon: 'arrow_downward', color: 'var(--nd-red)' },
            { label: 'Volume',    value: `${(stock.volume / 1_000_000).toFixed(2)}M`, icon: 'bar_chart', color: 'var(--nd-blue)' },
            { label: 'P/E Ratio', value: stock.peRatio ? stock.peRatio.toFixed(2) : '—', icon: 'analytics', color: 'var(--nd-purple)' },
          ].map((s, i) => (
            <div key={s.label} style={{ padding: '0 20px', borderLeft: i > 0 ? '1px solid var(--nd-border)' : 'none' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 5, marginBottom: 4 }}>
                <span className="material-icons" style={{ fontSize: 13, color: s.color }}>{s.icon}</span>
                <p className="nd-label" style={{ margin: 0 }}>{s.label}</p>
              </div>
              <p style={{ fontSize: 15, fontWeight: 600 }}>{s.value}</p>
            </div>
          ))}
        </div>
      </div>

      {/* AI Prediction */}
      {prediction && (
        <div className="nd-card" style={{ marginBottom: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 18 }}>
            <div className="nd-icon-chip" style={{ background: 'var(--nd-green-50)' }}>
              <span className="material-icons" style={{ color: 'var(--nd-green)' }}>psychology</span>
            </div>
            <h2 style={{ fontSize: 16, fontWeight: 600 }}>AI Prediction</h2>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 0 }}>
            {/* Signal */}
            <div style={{ padding: '0 24px 0 0', borderRight: '1px solid var(--nd-border)' }}>
              <p className="nd-label">Signal</p>
              <p style={{ fontSize: 32, fontWeight: 700, color: prediction.prediction === 'UP' ? 'var(--nd-green)' : prediction.prediction === 'DOWN' ? 'var(--nd-red)' : 'var(--nd-text-2)', display: 'flex', alignItems: 'center', gap: 8 }}>
                <span className="material-icons" style={{ fontSize: 32 }}>{prediction.prediction === 'UP' ? 'trending_up' : prediction.prediction === 'DOWN' ? 'trending_down' : 'trending_flat'}</span>
                {prediction.prediction}
              </p>
              <div style={{ display: 'flex', gap: 12, marginTop: 10 }}>
                <div>
                  <p style={{ fontSize: 11, color: 'var(--nd-text-3)', textTransform: 'uppercase', letterSpacing: '0.3px' }}>Stop Loss</p>
                  <p style={{ fontWeight: 600, fontSize: 13.5, color: 'var(--nd-red)' }}>{inr(prediction.stopLoss)}</p>
                </div>
                <div>
                  <p style={{ fontSize: 11, color: 'var(--nd-text-3)', textTransform: 'uppercase', letterSpacing: '0.3px' }}>R/R Ratio</p>
                  <p style={{ fontWeight: 600, fontSize: 13.5 }}>{prediction.riskRewardRatio.toFixed(2)}</p>
                </div>
              </div>
            </div>

            {/* Confidence */}
            <div style={{ padding: '0 24px', borderRight: '1px solid var(--nd-border)' }}>
              <p className="nd-label">Confidence</p>
              <p style={{ fontSize: 32, fontWeight: 700 }}>{(prediction.confidence * 100).toFixed(1)}%</p>
              <div style={{ height: 6, background: 'var(--nd-surface-2)', borderRadius: 3, overflow: 'hidden', marginTop: 10 }}>
                <div style={{ width: `${prediction.confidence * 100}%`, height: '100%', background: 'var(--nd-green)', borderRadius: 3 }} />
              </div>
              <p style={{ fontSize: 12, color: 'var(--nd-text-3)', marginTop: 6 }}>Timeframe: {prediction.timeframe}</p>
            </div>

            {/* Target */}
            <div style={{ padding: '0 0 0 24px' }}>
              <p className="nd-label">Target Price</p>
              <p style={{ fontSize: 32, fontWeight: 700, color: 'var(--nd-green)' }}>{inr(prediction.targetPrice)}</p>
              <p style={{ fontSize: 13, color: prediction.upsidePotential >= 0 ? 'var(--nd-green)' : 'var(--nd-red)', fontWeight: 500, marginTop: 4 }}>
                {prediction.upsidePotential >= 0 ? '+' : ''}{prediction.upsidePotential.toFixed(2)}% potential
              </p>
            </div>
          </div>

          {prediction.reasoning && (
            <div style={{ marginTop: 16, paddingTop: 16, borderTop: '1px solid var(--nd-border)' }}>
              <p style={{ fontSize: 13, color: 'var(--nd-text-2)', lineHeight: 1.6 }}>
                <strong style={{ color: 'var(--nd-text-1)' }}>Reasoning: </strong>{prediction.reasoning}
              </p>
              {prediction.factors?.length > 0 && (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 10 }}>
                  {prediction.factors.map((f: string, i: number) => (
                    <span key={i} className="nd-badge nd-badge-gray">{f}</span>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Sentiment */}
      {sentiment && (
        <div className="nd-card" style={{ marginBottom: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 18 }}>
            <div className="nd-icon-chip" style={{ background: '#fff7ed' }}>
              <span className="material-icons" style={{ color: 'var(--nd-orange)' }}>sentiment_satisfied</span>
            </div>
            <h2 style={{ fontSize: 16, fontWeight: 600 }}>Sentiment Analysis</h2>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 0 }}>
            {/* Overall score gauge */}
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: '0 24px 0 0', borderRight: '1px solid var(--nd-border)' }}>
              <div style={{
                width: 96, height: 96, borderRadius: '50%',
                border: `6px solid var(--nd-green)`,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                flexDirection: 'column', marginBottom: 8,
              }}>
                <p style={{ fontSize: 26, fontWeight: 700, lineHeight: 1 }}>{(sentiment.overallSentiment * 100).toFixed(0)}</p>
                <p style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>/ 100</p>
              </div>
              <p className="nd-label" style={{ textAlign: 'center' }}>Overall Sentiment</p>
            </div>

            {/* Bars */}
            <div style={{ padding: '0 24px', borderRight: '1px solid var(--nd-border)', display: 'flex', flexDirection: 'column', gap: 14 }}>
              {[
                { label: 'News Sentiment',   value: sentiment.newsSentiment,         color: 'var(--nd-green)' },
                { label: 'Social Media',     value: sentiment.socialMediaSentiment,  color: '#3b82f6' },
              ].map(s => (
                <div key={s.label}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, marginBottom: 4 }}>
                    <span style={{ color: 'var(--nd-text-2)' }}>{s.label}</span>
                    <strong>{(Math.max(0, s.value) * 100).toFixed(0)}%</strong>
                  </div>
                  <Bar value={s.value} color={s.color} />
                </div>
              ))}
            </div>

            {/* Analyst ratings */}
            <div style={{ padding: '0 0 0 24px' }}>
              <p className="nd-label">Analyst Rating</p>
              <p style={{ fontSize: 28, fontWeight: 700, marginBottom: 10 }}>{sentiment.analystRating.toFixed(1)} <span style={{ fontSize: 16, color: 'var(--nd-text-3)', fontWeight: 400 }}>/ 5.0</span></p>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {[
                  { label: 'Buy',  count: sentiment.buyCount,  color: 'var(--nd-green)' },
                  { label: 'Hold', count: sentiment.holdCount, color: '#ca8a04' },
                  { label: 'Sell', count: sentiment.sellCount, color: 'var(--nd-red)' },
                ].map(r => {
                  const total = sentiment.buyCount + sentiment.holdCount + sentiment.sellCount || 1;
                  return (
                    <div key={r.label} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
                      <span style={{ width: 30, color: 'var(--nd-text-2)' }}>{r.label}</span>
                      <div style={{ flex: 1, height: 6, background: 'var(--nd-surface-2)', borderRadius: 3, overflow: 'hidden' }}>
                        <div style={{ width: `${(r.count / total) * 100}%`, height: '100%', background: r.color, borderRadius: 3 }} />
                      </div>
                      <span style={{ width: 20, textAlign: 'right', fontWeight: 600 }}>{r.count}</span>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Prediction History */}
      {predictionHistory.length > 0 && (
        <div className="nd-card" style={{ padding: 0 }}>
          <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--nd-border)', display: 'flex', alignItems: 'center', gap: 8 }}>
            <span className="material-icons" style={{ fontSize: 18, color: 'var(--nd-text-2)' }}>history</span>
            <h2 style={{ fontSize: 15, fontWeight: 600 }}>
              Prediction History
              <span style={{ fontWeight: 400, color: 'var(--nd-text-2)', marginLeft: 6 }}>({predictionHistory.length})</span>
            </h2>
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table className="nd-table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Signal</th>
                  <th className="text-right">Confidence</th>
                  <th className="text-right">Target</th>
                  <th className="text-right">Actual</th>
                  <th style={{ textAlign: 'center' }}>Result</th>
                </tr>
              </thead>
              <tbody>
                {predictionHistory.map((item, idx) => (
                  <tr key={idx}>
                    <td style={{ fontSize: 12, color: 'var(--nd-text-2)' }}>{new Date(item.timestamp).toLocaleString('en-IN')}</td>
                    <td>
                      <span className={`nd-badge ${item.prediction === 'UP' ? 'nd-badge-green' : item.prediction === 'DOWN' ? 'nd-badge-red' : 'nd-badge-gray'}`}>
                        {item.prediction === 'UP' ? '▲' : item.prediction === 'DOWN' ? '▼' : '—'} {item.prediction}
                      </span>
                    </td>
                    <td className="text-right">{(item.confidence * 100).toFixed(0)}%</td>
                    <td className="text-right">{inr(item.targetPrice ?? item.target_price ?? 0)}</td>
                    <td className="text-right">{inr(item.actualPrice ?? item.actual_price ?? 0)}</td>
                    <td style={{ textAlign: 'center' }}>
                      <span style={{ fontSize: 16 }}>{item.accuracy}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Order Modal */}
      {showOrderModal && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 50, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(0,0,0,0.55)' }}>
          <div style={{ width: '100%', maxWidth: 420, margin: '0 16px', borderRadius: 14, background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', boxShadow: 'var(--nd-shadow-md)', padding: 24 }}>

            {orderResult ? (
              <div style={{ textAlign: 'center', padding: '8px 0' }}>
                <span className="material-icons" style={{ fontSize: 52, color: 'var(--nd-green)', marginBottom: 12 }}>
                  {orderResult.status === 'SIMULATED' ? 'science' : 'check_circle'}
                </span>
                <h3 style={{ fontSize: 18, fontWeight: 700, marginBottom: 4 }}>
                  {orderResult.status === 'SIMULATED' ? 'Simulated Order' : 'Order Placed!'}
                </h3>
                <div style={{ background: 'var(--nd-surface)', borderRadius: 8, padding: 16, marginTop: 16, marginBottom: 16, textAlign: 'left', fontSize: 13, display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {[
                    { l: 'Order ID', v: orderResult.orderId, mono: true },
                    { l: 'Symbol',   v: orderResult.symbol },
                    { l: 'Side',     v: orderResult.transactionType, color: orderResult.transactionType === 'BUY' ? 'var(--nd-green)' : 'var(--nd-red)' },
                    { l: 'Qty',      v: String(orderResult.quantity) },
                    { l: 'Type',     v: orderResult.orderType },
                    { l: 'Status',   v: orderResult.status, color: 'var(--nd-blue)' },
                  ].map(r => (
                    <div key={r.l} style={{ display: 'flex', justifyContent: 'space-between' }}>
                      <span style={{ color: 'var(--nd-text-2)' }}>{r.l}</span>
                      <span style={{ fontWeight: 600, color: r.color, fontFamily: r.mono ? 'monospace' : undefined }}>{r.v}</span>
                    </div>
                  ))}
                </div>
                {orderResult.status === 'SIMULATED' && (
                  <p style={{ fontSize: 12, color: 'var(--nd-text-3)', marginBottom: 16 }}>Groww API not connected — simulated order for testing.</p>
                )}
                <button onClick={closeModal} className="nd-btn nd-btn-primary" style={{ width: '100%', justifyContent: 'center' }}>Done</button>
              </div>
            ) : (
              <>
                {/* Header */}
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 18 }}>
                  <h3 style={{ fontSize: 16, fontWeight: 700 }}>Place Order · {symbol}</h3>
                  <button onClick={closeModal} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--nd-text-2)', display: 'flex' }}>
                    <span className="material-icons">close</span>
                  </button>
                </div>

                {/* LTP strip */}
                <div style={{ background: 'var(--nd-surface)', borderRadius: 8, padding: '10px 16px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                  <span style={{ fontSize: 12, color: 'var(--nd-text-2)' }}>LTP</span>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ fontSize: 17, fontWeight: 700 }}>{inr(stock.price)}</span>
                    <span style={{ fontSize: 12.5, color: isUp ? 'var(--nd-green)' : 'var(--nd-red)', fontWeight: 500 }}>
                      {isUp ? '+' : ''}{stock.changePercent.toFixed(2)}%
                    </span>
                  </div>
                </div>

                {/* BUY / SELL toggle */}
                <div style={{ display: 'flex', border: '1px solid var(--nd-border)', borderRadius: 8, overflow: 'hidden', marginBottom: 14 }}>
                  {(['BUY', 'SELL'] as const).map(side => (
                    <button key={side} onClick={() => setOrderSide(side)} style={{
                      flex: 1, padding: '9px', fontSize: 13.5, fontWeight: 700, border: 'none', cursor: 'pointer', fontFamily: 'Roboto, sans-serif',
                      background: orderSide === side ? (side === 'BUY' ? 'var(--nd-green)' : 'var(--nd-red)') : 'var(--nd-bg)',
                      color: orderSide === side ? '#fff' : 'var(--nd-text-2)',
                      transition: 'background 0.15s, color 0.15s',
                    }}>{side}</button>
                  ))}
                </div>

                {/* Order type */}
                <div style={{ display: 'flex', gap: 8, marginBottom: 14 }}>
                  {(['MARKET', 'LIMIT'] as const).map(t => (
                    <button key={t} onClick={() => setOrderType(t)} style={{
                      flex: 1, padding: '7px', fontSize: 12, fontWeight: 600, borderRadius: 6, cursor: 'pointer', fontFamily: 'Roboto, sans-serif',
                      border: `1px solid ${orderType === t ? 'var(--nd-green)' : 'var(--nd-border)'}`,
                      background: orderType === t ? 'var(--nd-green-50)' : 'var(--nd-bg)',
                      color: orderType === t ? 'var(--nd-green)' : 'var(--nd-text-2)',
                    }}>{t}</button>
                  ))}
                </div>

                {/* Quantity */}
                <div style={{ marginBottom: 12 }}>
                  <label className="nd-field-label">Quantity</label>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <button onClick={() => setOrderQty(q => Math.max(1, q - 1))}
                      style={{ width: 34, height: 34, border: '1px solid var(--nd-border)', borderRadius: 6, background: 'var(--nd-bg)', cursor: 'pointer', fontSize: 18, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--nd-text-1)' }}>−</button>
                    <input type="number" min={1} value={orderQty}
                      onChange={e => setOrderQty(Math.max(1, parseInt(e.target.value) || 1))}
                      className="nd-input" style={{ textAlign: 'center', flex: 1 }} />
                    <button onClick={() => setOrderQty(q => q + 1)}
                      style={{ width: 34, height: 34, border: '1px solid var(--nd-border)', borderRadius: 6, background: 'var(--nd-bg)', cursor: 'pointer', fontSize: 18, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--nd-text-1)' }}>+</button>
                  </div>
                </div>

                {/* Limit price */}
                {orderType === 'LIMIT' && (
                  <div style={{ marginBottom: 12 }}>
                    <label className="nd-field-label">Limit Price (₹)</label>
                    <input type="number" min={0.01} step={0.05} value={orderPrice}
                      onChange={e => setOrderPrice(e.target.value)} placeholder={stock.price.toFixed(2)}
                      className="nd-input" />
                  </div>
                )}

                {/* Product type */}
                <div style={{ display: 'flex', gap: 8, marginBottom: 14 }}>
                  {(['CNC', 'INTRADAY'] as const).map(p => (
                    <button key={p} onClick={() => setOrderProduct(p)} style={{
                      flex: 1, padding: '7px', fontSize: 12, fontWeight: 600, borderRadius: 6, cursor: 'pointer', fontFamily: 'Roboto, sans-serif',
                      border: `1px solid ${orderProduct === p ? 'var(--nd-green)' : 'var(--nd-border)'}`,
                      background: orderProduct === p ? 'var(--nd-green-50)' : 'var(--nd-bg)',
                      color: orderProduct === p ? 'var(--nd-green)' : 'var(--nd-text-2)',
                    }}>{p === 'CNC' ? 'CNC (Delivery)' : 'Intraday (MIS)'}</button>
                  ))}
                </div>

                {/* Estimated value */}
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, padding: '10px 14px', background: 'var(--nd-surface)', borderRadius: 8, marginBottom: 14 }}>
                  <span style={{ color: 'var(--nd-text-2)' }}>Estimated {orderSide === 'BUY' ? 'Cost' : 'Value'}</span>
                  <strong>{inr(estimatedValue)}</strong>
                </div>

                {orderError && (
                  <div className="nd-alert nd-alert-error" style={{ marginBottom: 12 }}>
                    <span className="material-icons">error_outline</span>
                    {orderError}
                  </div>
                )}

                <button onClick={handlePlaceOrder} disabled={orderLoading || (orderType === 'LIMIT' && !orderPrice)}
                  className="nd-btn" style={{
                    width: '100%', justifyContent: 'center', padding: '11px', fontSize: 14, fontWeight: 700,
                    background: orderSide === 'BUY' ? 'var(--nd-green)' : 'var(--nd-red)', color: '#fff',
                    opacity: (orderLoading || (orderType === 'LIMIT' && !orderPrice)) ? 0.55 : 1,
                    cursor: (orderLoading || (orderType === 'LIMIT' && !orderPrice)) ? 'not-allowed' : 'pointer',
                  }}>
                  {orderLoading
                    ? 'Placing Order...'
                    : `${orderSide} ${orderQty} × ${symbol}`}
                </button>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

export default StockDetail;
