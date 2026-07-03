import React, { useCallback, useEffect, useRef, useState } from 'react';
import apiService from '../services/api';
import StockPicker from '../components/StockPicker';
import { getErrorMessage } from '../utils/errors';

// ── helpers ────────────────────────────────────────────────────────────────────

const inr = (v: number | null | undefined) =>
  v == null ? '—' : `₹${v.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const pct = (v: number | null | undefined) =>
  v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(2)}%`;
const green  = 'var(--nd-green)';
const red    = 'var(--nd-red)';
const amber  = '#f59e0b';
const pnlClr = (v: number) => (v > 0 ? green : v < 0 ? red : 'var(--nd-text-3)');

// ── styles ─────────────────────────────────────────────────────────────────────

const card: React.CSSProperties = {
  background: 'var(--nd-surface)', border: '1px solid var(--nd-border)',
  borderRadius: 12, padding: 16,
};
const row: React.CSSProperties = { display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' };
const btn = (c: string, bg: string, border = 'transparent'): React.CSSProperties => ({
  background: bg, color: c, border: `1px solid ${border}`, borderRadius: 8,
  padding: '8px 16px', fontSize: 13, fontWeight: 600, cursor: 'pointer',
  transition: 'opacity .15s',
});
const label: React.CSSProperties = { fontSize: 11, fontWeight: 500, color: 'var(--nd-text-3)', marginBottom: 4 };
const input: React.CSSProperties = {
  background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderRadius: 8,
  padding: '8px 12px', fontSize: 13, color: 'var(--nd-text-1)', width: '100%', boxSizing: 'border-box',
};

// ── types ──────────────────────────────────────────────────────────────────────

interface Position {
  symbol:      string;
  quantity:    number;
  entryPrice:  number;
  entryTime:   string;
  orderId:     string;
  confidence:  number;
  reason:      string;
}

interface HistoryTrade {
  symbol:     string;
  action:     string;
  quantity:   number;
  exitPrice:  number;
  exitTime:   string;
  orderId:    string;
  pnl:        number | null;
  pnlPct:     number | null;
  confidence: number;
  reason:     string;
}

interface LiveStatus {
  enabled:          boolean;
  autoExecute:      boolean;
  settings:         Record<string, number>;
  positions:        Position[];
  historyToday:     HistoryTrade[];
  realisedPnl:      number;
  tradeCountToday:  number;
  minsToSquareoff:  number;
  marketOpen:       boolean;
  istNow:           string;
}

interface Evaluation {
  gatePassed:       boolean;
  reason:           string;
  action:           string;
  confidence:       number;
  agentAgreement:   number;
  recommendedQty:   number;
  allocatedCapital: number;
  autoExecute:      boolean;
}

interface TickData {
  price:       number;
  signal:      string;
  signalInt:   number;
  confidence?: number;
  indicators:  Record<string, number>;
  candleTime:  string;
  istTime:     string;
}

// ── Enable confirmation modal ──────────────────────────────────────────────────

const ConfirmEnableModal: React.FC<{
  capital: string;
  autoExec: boolean;
  onCapitalChange: (v: string) => void;
  onAutoExecChange: (v: boolean) => void;
  onConfirm: () => void;
  onCancel: () => void;
}> = ({ capital, autoExec, onCapitalChange, onAutoExecChange, onConfirm, onCancel }) => (
  <div style={{
    position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.72)', zIndex: 1000,
    display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16,
  }}>
    <div style={{ ...card, maxWidth: 420, width: '100%', border: '1px solid #dc2626' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
        <span className="material-icons" style={{ color: red, fontSize: 28 }}>warning</span>
        <div>
          <div style={{ fontSize: 16, fontWeight: 700, color: red }}>Enable Live Trading</div>
          <div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>Real money · NSE MARKET orders · MIS intraday</div>
        </div>
      </div>

      <div style={{ background: '#dc262618', border: '1px solid #dc262640', borderRadius: 8, padding: 12, marginBottom: 16, fontSize: 12, color: 'var(--nd-text-2)', lineHeight: 1.7 }}>
        <strong style={{ color: red }}>⚠ This will place real Groww MIS orders using your actual money.</strong>
        <br />The AI engine will evaluate every signal and execute automatically if the conviction gate passes.
        All positions are auto-squared-off at 3:10 PM IST. You are solely responsible for trading losses.
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginBottom: 16 }}>
        <div>
          <div style={label}>Allocated Capital (₹)</div>
          <input
            style={input}
            type="number"
            min={5000}
            max={10000000}
            value={capital}
            onChange={e => onCapitalChange(e.target.value)}
          />
          <div style={{ fontSize: 11, color: 'var(--nd-text-3)', marginTop: 4 }}>
            Max 15% (₹{Math.round(parseFloat(capital || '0') * 0.15).toLocaleString('en-IN')}) deployed per trade
          </div>
        </div>

        <label style={{ display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer', fontSize: 13 }}>
          <input
            type="checkbox"
            checked={autoExec}
            onChange={e => onAutoExecChange(e.target.checked)}
            style={{ width: 16, height: 16, accentColor: red }}
          />
          <div>
            <span style={{ fontWeight: 600, color: 'var(--nd-text-1)' }}>Auto-execute</span>
            <span style={{ color: 'var(--nd-text-3)', fontSize: 11, marginLeft: 6 }}>
              (order fires without confirmation when gate passes)
            </span>
          </div>
        </label>
      </div>

      <div style={{ display: 'flex', gap: 8 }}>
        <button style={{ ...btn('var(--nd-text-2)', 'transparent', 'var(--nd-border)'), flex: 1 }} onClick={onCancel}>
          Cancel
        </button>
        <button style={{ ...btn('#fff', red), flex: 1 }} onClick={onConfirm}>
          I Understand — Enable Live Trading
        </button>
      </div>
    </div>
  </div>
);

// ── Order confirmation modal ───────────────────────────────────────────────────

const ConfirmOrderModal: React.FC<{
  evaluation: Evaluation;
  symbol: string;
  price: number;
  onConfirm: () => void;
  onCancel: () => void;
}> = ({ evaluation, symbol, price, onConfirm, onCancel }) => (
  <div style={{
    position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.72)', zIndex: 1000,
    display: 'flex', alignItems: 'flex-end', justifyContent: 'center',
  }}>
    <div style={{
      ...card, width: '100%', maxWidth: 480, borderRadius: '16px 16px 0 0',
      border: `1px solid ${evaluation.action === 'BUY' ? green : red}`,
      paddingBottom: `calc(20px + env(safe-area-inset-bottom, 0px))`,
    }}>
      <div style={{ width: 36, height: 4, background: 'var(--nd-border)', borderRadius: 2, margin: '0 auto 16px' }} />

      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
        <span className="material-icons" style={{ color: evaluation.action === 'BUY' ? green : red, fontSize: 28 }}>
          {evaluation.action === 'BUY' ? 'trending_up' : 'trending_down'}
        </span>
        <div>
          <div style={{ fontSize: 16, fontWeight: 700 }}>
            AI wants to {evaluation.action} {symbol}
          </div>
          <div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>
            {evaluation.recommendedQty} shares · {inr(price)} · {inr(evaluation.allocatedCapital)} deployed
          </div>
        </div>
      </div>

      {/* Conviction bar */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 16 }}>
        <div style={{ ...card, padding: 10, textAlign: 'center' }}>
          <div style={{ fontSize: 22, fontWeight: 700, color: evaluation.confidence >= 0.72 ? green : amber }}>
            {(evaluation.confidence * 100).toFixed(0)}%
          </div>
          <div style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>Conviction</div>
        </div>
        <div style={{ ...card, padding: 10, textAlign: 'center' }}>
          <div style={{ fontSize: 22, fontWeight: 700, color: evaluation.agentAgreement >= 0.55 ? green : amber }}>
            {(evaluation.agentAgreement * 100).toFixed(0)}%
          </div>
          <div style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>Agent Agreement</div>
        </div>
      </div>

      <div style={{ fontSize: 12, color: 'var(--nd-text-2)', marginBottom: 16 }}>{evaluation.reason}</div>

      <div style={{ display: 'flex', gap: 8 }}>
        <button style={{ ...btn('var(--nd-text-2)', 'transparent', 'var(--nd-border)'), flex: 1 }} onClick={onCancel}>
          Skip
        </button>
        <button
          style={{ ...btn('#fff', evaluation.action === 'BUY' ? green : red), flex: 2 }}
          onClick={onConfirm}
        >
          {evaluation.action === 'BUY' ? 'Buy' : 'Sell'} {evaluation.recommendedQty} × {inr(price)}
        </button>
      </div>
    </div>
  </div>
);

// ── Main component ─────────────────────────────────────────────────────────────

const LiveTrading: React.FC = () => {
  const [status, setStatus]           = useState<LiveStatus | null>(null);
  const [symbol, setSymbol]           = useState('RELIANCE');
  const [capital, setCapital]         = useState('50000');
  const [autoExec, setAutoExec]       = useState(false);
  const [tick, setTick]               = useState<TickData | null>(null);
  const [evaluation, setEvaluation]   = useState<Evaluation | null>(null);
  const [pendingOrder, setPendingOrder] = useState<Evaluation | null>(null);
  const [showEnableModal, setShowEnableModal] = useState(false);
  const [loading, setLoading]         = useState(false);
  const [orderLoading, setOrderLoading] = useState(false);
  const [error, setError]             = useState<string | null>(null);
  const [lastUpdate, setLastUpdate]   = useState('');
  const [currentPrices, setCurrentPrices] = useState<Record<string, number>>({});

  const tickRef    = useRef(tick);
  const statusRef  = useRef(status);
  tickRef.current   = tick;
  statusRef.current = status;

  // ── Fetch status ───────────────────────────────────────────────────────────

  const fetchStatus = useCallback(async () => {
    try {
      const r = await apiService.liveStatus();
      setStatus(r.data as LiveStatus);
    } catch { /* keep last */ }
  }, []);

  useEffect(() => {
    fetchStatus();
    const t = setInterval(fetchStatus, 15_000);
    return () => clearInterval(t);
  }, [fetchStatus]);

  // ── Tick polling — every 60 s during market hours ─────────────────────────

  const pollTick = useCallback(async () => {
    if (!symbol || !statusRef.current?.marketOpen) return;
    try {
      const r = await apiService.paperTradingTick(symbol, { position: 'NONE', entry_price: 0, quantity: 0 });
      const d = r.data as TickData;
      setTick(d);
      setLastUpdate(d.istTime || '');

      if (!statusRef.current?.enabled) return;

      // Only evaluate directional signals
      if (d.signalInt === 0) { setEvaluation(null); return; }

      const action = d.signalInt === 1 ? 'BUY' : 'SELL';
      // Use ensemble confidence from indicators if available, else fall back to a proxy
      const confidence    = d.indicators?.ensembleConfidence ?? d.indicators?.confidence ?? 0.65;
      const agentAgreement = d.indicators?.agentAgreement ?? 0.55;

      const evalR = await apiService.liveEvaluate({
        symbol,
        action,
        confidence,
        agent_agreement: agentAgreement,
        current_price:   d.price,
        reasoning:       `Signal: ${d.signal} at ${d.candleTime}`,
      });
      const ev = evalR.data as Evaluation;
      setEvaluation(ev);

      if (ev.gatePassed && ev.autoExecute) {
        // Auto-execute — fire order immediately
        await executeOrder(ev, d.price, action);
      } else if (ev.gatePassed && !ev.autoExecute) {
        // Manual — show confirmation modal
        setPendingOrder(ev);
      }
    } catch { /* non-fatal */ }
  }, [symbol]);

  useEffect(() => {
    pollTick();
    const t = setInterval(pollTick, 60_000);
    return () => clearInterval(t);
  }, [pollTick]);

  // ── Refresh current prices for positions ──────────────────────────────────

  useEffect(() => {
    if (!status?.positions?.length) return;
    const syms = status.positions.map(p => p.symbol);
    const refresh = async () => {
      const prices: Record<string, number> = {};
      await Promise.allSettled(
        syms.map(async sym => {
          try {
            const r = await apiService.paperTradingTick(sym, { position: 'LONG', entry_price: 0, quantity: 0 });
            prices[sym] = (r.data as TickData)?.price ?? 0;
          } catch {}
        })
      );
      setCurrentPrices(prices);
    };
    refresh();
    const t = setInterval(refresh, 30_000);
    return () => clearInterval(t);
  }, [status?.positions]);

  // ── Enable / disable ───────────────────────────────────────────────────────

  const handleEnable = async () => {
    setLoading(true); setError(null);
    try {
      await apiService.liveEnable({
        allocated_capital: parseFloat(capital) || 50_000,
        auto_execute: autoExec,
      });
      await fetchStatus();
    } catch (e: unknown) {
      setError(getErrorMessage(e, 'Failed to enable'));
    } finally { setLoading(false); setShowEnableModal(false); }
  };

  const handleDisable = async () => {
    setLoading(true); setError(null);
    try {
      await apiService.liveDisable();
      setEvaluation(null);
      setPendingOrder(null);
      await fetchStatus();
    } catch (e: unknown) {
      setError(getErrorMessage(e, 'Failed to disable'));
    } finally { setLoading(false); }
  };

  // ── Order execution ────────────────────────────────────────────────────────

  const executeOrder = async (ev: Evaluation, price: number, action?: string) => {
    setOrderLoading(true); setError(null);
    try {
      await apiService.livePlaceOrder({
        symbol,
        action:        action ?? ev.action,
        quantity:      ev.recommendedQty || 1,
        current_price: price,
        confidence:    ev.confidence,
        reason:        ev.reason,
      });
      setPendingOrder(null);
      setEvaluation(null);
      await fetchStatus();
    } catch (e: unknown) {
      setError(getErrorMessage(e, 'Order failed'));
    } finally { setOrderLoading(false); }
  };

  const handleSquareoff = async (sym?: string) => {
    setOrderLoading(true); setError(null);
    try {
      await apiService.liveSquareoff(sym ? { symbol: sym } : {});
      await fetchStatus();
    } catch (e: unknown) {
      setError(getErrorMessage(e, 'Squareoff failed'));
    } finally { setOrderLoading(false); }
  };

  // ── Render ─────────────────────────────────────────────────────────────────

  const enabled     = status?.enabled ?? false;
  const settings    = status?.settings ?? {};
  const positions   = status?.positions ?? [];
  const history     = status?.historyToday ?? [];
  const marketOpen  = status?.marketOpen ?? false;
  const minsLeft    = status?.minsToSquareoff ?? 0;

  const totalUnrealised = positions.reduce((s, p) => {
    const cur = currentPrices[p.symbol] ?? p.entryPrice;
    return s + (cur - p.entryPrice) * p.quantity;
  }, 0);

  return (
    <div style={{ maxWidth: 720, margin: '0 auto', padding: '16px 12px', display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* ── Header ── */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span className="material-icons" style={{ color: enabled ? red : 'var(--nd-text-3)', fontSize: 22 }}>
              bolt
            </span>
            <span style={{ fontSize: 18, fontWeight: 700 }}>Live Trading</span>
            {enabled && (
              <span style={{
                background: red, color: '#fff', fontSize: 10, fontWeight: 700,
                padding: '2px 7px', borderRadius: 4, letterSpacing: 1,
                animation: 'livePulse 2s ease-in-out infinite',
              }}>LIVE</span>
            )}
          </div>
          <div style={{ fontSize: 12, color: 'var(--nd-text-3)', marginTop: 2 }}>
            Real Groww MIS orders · Conviction-gated · Auto-squareoff 3:10 PM
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {marketOpen ? (
            <span style={{ fontSize: 11, color: green, display: 'flex', alignItems: 'center', gap: 4 }}>
              <span style={{ width: 6, height: 6, borderRadius: '50%', background: green }} />
              Market Open · {status?.istNow}
            </span>
          ) : (
            <span style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>Market Closed</span>
          )}
        </div>
      </div>

      {/* ── Error banner ── */}
      {error && (
        <div style={{ background: '#dc262618', border: '1px solid #dc262640', borderRadius: 8, padding: 12, fontSize: 13, color: red, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          {error}
          <button onClick={() => setError(null)} style={{ background: 'none', border: 'none', color: red, cursor: 'pointer', fontSize: 16 }}>×</button>
        </div>
      )}

      {/* ── Controls ── */}
      <div style={card}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 12 }}>
          <div>
            <div style={label}>Symbol</div>
            <div style={{ opacity: enabled ? 0.5 : 1, pointerEvents: enabled ? 'none' : 'auto' }}>
              <StockPicker value={symbol} onChange={setSymbol} />
            </div>
          </div>
          <div>
            <div style={label}>Allocated Capital</div>
            <input
              style={input}
              type="number"
              value={capital}
              min={5000}
              disabled={enabled}
              onChange={e => setCapital(e.target.value)}
            />
          </div>
        </div>

        {enabled ? (
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              style={{ ...btn('#fff', '#374151', 'var(--nd-border)'), flex: 1 }}
              onClick={handleDisable}
              disabled={loading}
            >
              {loading ? '...' : 'Disable Live Trading'}
            </button>
            <button
              style={{ ...btn('#fff', red), flex: 1 }}
              onClick={() => handleSquareoff()}
              disabled={orderLoading || !positions.length}
            >
              {orderLoading ? '...' : `Square Off All (${positions.length})`}
            </button>
          </div>
        ) : (
          <button
            style={{ ...btn('#fff', red), width: '100%' }}
            onClick={() => setShowEnableModal(true)}
            disabled={loading}
          >
            {loading ? 'Enabling...' : 'Enable Live Trading'}
          </button>
        )}
      </div>

      {/* ── Conviction gate settings display ── */}
      {enabled && (
        <div style={{ ...card, display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12 }}>
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--nd-accent)' }}>
              {((settings.convictionMin ?? 0.72) * 100).toFixed(0)}%
            </div>
            <div style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>Min Conviction</div>
          </div>
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--nd-accent)' }}>
              {((settings.agreementMin ?? 0.55) * 100).toFixed(0)}%
            </div>
            <div style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>Min Agreement</div>
          </div>
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 20, fontWeight: 700, color: amber }}>
              {minsLeft > 0 ? `${minsLeft}m` : 'Past'}
            </div>
            <div style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>To Auto-Squareoff</div>
          </div>
        </div>
      )}

      {/* ── Live signal monitor ── */}
      {enabled && (
        <div style={card}>
          <div style={{ ...row, marginBottom: 10 }}>
            <span className="material-icons" style={{ fontSize: 16, color: 'var(--nd-text-3)' }}>radar</span>
            <span style={{ fontSize: 13, fontWeight: 600 }}>Signal Monitor — {symbol}</span>
            {lastUpdate && <span style={{ fontSize: 10, color: 'var(--nd-text-3)', marginLeft: 'auto' }}>Updated {lastUpdate}</span>}
          </div>

          {tick ? (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12 }}>
              <div style={{ textAlign: 'center' }}>
                <div style={{ fontSize: 22, fontWeight: 700 }}>{inr(tick.price)}</div>
                <div style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>LTP</div>
              </div>
              <div style={{ textAlign: 'center' }}>
                <div style={{ fontSize: 18, fontWeight: 700, color: tick.signalInt > 0 ? green : tick.signalInt < 0 ? red : 'var(--nd-text-3)' }}>
                  {tick.signal}
                </div>
                <div style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>AI Signal</div>
              </div>
              <div style={{ textAlign: 'center' }}>
                <div style={{ fontSize: 18, fontWeight: 700, color: tick.signalInt !== 0 ? amber : 'var(--nd-text-3)' }}>
                  {tick.candleTime || '—'}
                </div>
                <div style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>Candle</div>
              </div>
            </div>
          ) : (
            <div style={{ textAlign: 'center', color: 'var(--nd-text-3)', fontSize: 13, padding: '20px 0' }}>
              {marketOpen ? 'Polling signal every 60 seconds…' : 'Market is closed'}
            </div>
          )}

          {evaluation && (
            <div style={{
              marginTop: 12, padding: 12, borderRadius: 8,
              background: evaluation.gatePassed ? '#16a34a18' : '#37415118',
              border: `1px solid ${evaluation.gatePassed ? '#16a34a40' : 'var(--nd-border)'}`,
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                <span className="material-icons" style={{ fontSize: 16, color: evaluation.gatePassed ? green : 'var(--nd-text-3)' }}>
                  {evaluation.gatePassed ? 'verified' : 'block'}
                </span>
                <span style={{ fontSize: 13, fontWeight: 600, color: evaluation.gatePassed ? green : 'var(--nd-text-2)' }}>
                  {evaluation.gatePassed ? 'Gate Passed — High Conviction Signal' : 'Gate Not Passed'}
                </span>
              </div>
              <div style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>{evaluation.reason}</div>
              {evaluation.gatePassed && !evaluation.autoExecute && tick && (
                <button
                  style={{ ...btn('#fff', red), marginTop: 10, width: '100%' }}
                  onClick={() => setPendingOrder(evaluation)}
                >
                  Review & Execute — {evaluation.action} {evaluation.recommendedQty}× {inr(tick.price)}
                </button>
              )}
            </div>
          )}
        </div>
      )}

      {/* ── Open positions ── */}
      {positions.length > 0 && (
        <div style={card}>
          <div style={{ ...row, marginBottom: 12 }}>
            <span className="material-icons" style={{ fontSize: 16, color: 'var(--nd-text-3)' }}>account_balance_wallet</span>
            <span style={{ fontSize: 13, fontWeight: 600 }}>Open Positions ({positions.length})</span>
            <span style={{ marginLeft: 'auto', fontSize: 13, fontWeight: 700, color: pnlClr(totalUnrealised) }}>
              {totalUnrealised >= 0 ? '+' : ''}{inr(totalUnrealised)} unrealised
            </span>
          </div>

          {positions.map((pos, i) => {
            const cur    = currentPrices[pos.symbol] ?? pos.entryPrice;
            const pnl    = (cur - pos.entryPrice) * pos.quantity;
            const pnlP   = pos.entryPrice > 0 ? pnl / (pos.entryPrice * pos.quantity) * 100 : 0;
            return (
              <div key={i} style={{
                display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
                padding: '10px 0', borderTop: i > 0 ? '1px solid var(--nd-border)' : 'none',
              }}>
                <div style={{ flex: 1, minWidth: 100 }}>
                  <div style={{ fontWeight: 700, fontSize: 14 }}>{pos.symbol}</div>
                  <div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>
                    {pos.quantity} × {inr(pos.entryPrice)} · {pos.entryTime}
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>
                    Conviction {(pos.confidence * 100).toFixed(0)}%
                  </div>
                </div>
                <div style={{ textAlign: 'right' }}>
                  <div style={{ fontSize: 16, fontWeight: 700 }}>{inr(cur)}</div>
                  <div style={{ fontSize: 12, fontWeight: 600, color: pnlClr(pnl) }}>
                    {pnl >= 0 ? '+' : ''}{inr(pnl)} ({pct(pnlP)})
                  </div>
                </div>
                <button
                  style={{ ...btn(red, 'transparent', red), fontSize: 11, padding: '6px 10px' }}
                  onClick={() => handleSquareoff(pos.symbol)}
                  disabled={orderLoading}
                >
                  Square Off
                </button>
              </div>
            );
          })}
        </div>
      )}

      {/* ── Day summary ── */}
      {(history.length > 0 || status?.realisedPnl !== undefined) && (
        <div style={{ ...card, display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, textAlign: 'center' }}>
          <div>
            <div style={{ fontSize: 18, fontWeight: 700, color: pnlClr(status?.realisedPnl ?? 0) }}>
              {status?.realisedPnl != null ? (status.realisedPnl >= 0 ? '+' : '') + inr(status.realisedPnl) : '—'}
            </div>
            <div style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>Realised P&L</div>
          </div>
          <div>
            <div style={{ fontSize: 18, fontWeight: 700 }}>{status?.tradeCountToday ?? 0}</div>
            <div style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>Trades Today</div>
          </div>
          <div>
            <div style={{ fontSize: 18, fontWeight: 700, color: pnlClr(totalUnrealised) }}>
              {totalUnrealised !== 0 ? (totalUnrealised >= 0 ? '+' : '') + inr(totalUnrealised) : '—'}
            </div>
            <div style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>Unrealised P&L</div>
          </div>
        </div>
      )}

      {/* ── Trade history ── */}
      {history.length > 0 && (
        <div style={card}>
          <div style={{ ...row, marginBottom: 12 }}>
            <span className="material-icons" style={{ fontSize: 16, color: 'var(--nd-text-3)' }}>history</span>
            <span style={{ fontSize: 13, fontWeight: 600 }}>Today's Trades</span>
          </div>
          {history.map((t, i) => (
            <div key={i} style={{
              display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
              padding: '8px 0', borderTop: i > 0 ? '1px solid var(--nd-border)' : 'none',
            }}>
              <span style={{
                fontSize: 10, fontWeight: 700, padding: '2px 6px', borderRadius: 4,
                background: t.action === 'BUY' ? '#16a34a22' : '#dc262622',
                color: t.action === 'BUY' ? green : red,
              }}>
                {t.action}
              </span>
              <div style={{ flex: 1 }}>
                <span style={{ fontWeight: 600, fontSize: 13 }}>{t.symbol}</span>
                <span style={{ fontSize: 11, color: 'var(--nd-text-3)', marginLeft: 6 }}>
                  {t.quantity}× {inr(t.exitPrice)} · {t.exitTime}
                </span>
              </div>
              {t.pnl != null && (
                <div style={{ textAlign: 'right' }}>
                  <div style={{ fontSize: 13, fontWeight: 700, color: pnlClr(t.pnl) }}>
                    {t.pnl >= 0 ? '+' : ''}{inr(t.pnl)}
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>{pct(t.pnlPct ?? null)}</div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* ── Empty state ── */}
      {!enabled && !history.length && (
        <div style={{ textAlign: 'center', padding: '40px 0', color: 'var(--nd-text-3)' }}>
          <span className="material-icons" style={{ fontSize: 48, opacity: 0.3 }}>bolt</span>
          <div style={{ fontSize: 14, marginTop: 8 }}>Enable Live Trading to start placing real orders</div>
          <div style={{ fontSize: 12, marginTop: 4 }}>
            The AI engine will evaluate every candle and only fire on signals above the conviction threshold
          </div>
        </div>
      )}

      {/* ── Modals ── */}
      {showEnableModal && (
        <ConfirmEnableModal
          capital={capital}
          autoExec={autoExec}
          onCapitalChange={setCapital}
          onAutoExecChange={setAutoExec}
          onConfirm={handleEnable}
          onCancel={() => setShowEnableModal(false)}
        />
      )}

      {pendingOrder && tick && (
        <ConfirmOrderModal
          evaluation={pendingOrder}
          symbol={symbol}
          price={tick.price}
          onConfirm={() => executeOrder(pendingOrder, tick.price)}
          onCancel={() => setPendingOrder(null)}
        />
      )}

      {/* LIVE pulse animation */}
      <style>{`
        @keyframes livePulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.6; }
        }
      `}</style>
    </div>
  );
};

export default LiveTrading;
