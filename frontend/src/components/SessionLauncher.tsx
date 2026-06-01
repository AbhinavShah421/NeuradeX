import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useAppStore } from '../stores/appStore';
import apiService from '../services/api';
import TradeChart, { TradeMarker } from './TradeChart';
import StockPicker from './StockPicker';

/**
 * A single, server-backed trading session that survives page refresh.
 * The session runs in the background on the server (advanced by the 7-agent
 * ensemble). This component just launches one and reconnects to it via a
 * session id persisted in localStorage — so refreshing never wipes it.
 */
interface Props {
  mode: 'replay' | 'paper';
  storageKey: string;   // localStorage key to remember the active session id
}

const inr = (v: number) => `₹${(v ?? 0).toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;
const pnlColor = (v: number) => (v > 0 ? 'var(--nd-green)' : v < 0 ? 'var(--nd-red)' : 'var(--nd-text-3)');
const fieldStyle: React.CSSProperties = { width: '100%', boxSizing: 'border-box' };

function lastWeekday(offset = 1): string {
  const d = new Date();
  d.setDate(d.getDate() - offset);
  while (d.getDay() === 0 || d.getDay() === 6) d.setDate(d.getDate() - 1);
  return d.toISOString().slice(0, 10);
}

const Field: React.FC<{ label: string; children: React.ReactNode; full?: boolean }> = ({ label, children, full }) => (
  <div style={{ display: 'flex', flexDirection: 'column', gap: 6, minWidth: 0, gridColumn: full ? '1 / -1' : 'auto' }}>
    <label style={{ fontSize: 11, fontWeight: 500, color: 'var(--nd-text-3)' }}>{label}</label>
    {children}
  </div>
);

const Stat: React.FC<{ label: string; value: string; color?: string }> = ({ label, value, color }) => (
  <div>
    <div style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>{label}</div>
    <div style={{ fontSize: 14, fontWeight: 600, color: color || 'var(--nd-text-1)' }}>{value}</div>
  </div>
);

const card: React.CSSProperties = { background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 12, padding: 16 };
const btnGhost: React.CSSProperties = { background: 'transparent', border: '1px solid var(--nd-border)', borderRadius: 6, padding: '5px 12px', fontSize: 12, fontWeight: 500, color: 'var(--nd-text-2)', cursor: 'pointer' };
const th: React.CSSProperties = { padding: '6px 8px', fontWeight: 500 };
const td: React.CSSProperties = { padding: '6px 8px', color: 'var(--nd-text-1)' };

const SessionLauncher: React.FC<Props> = ({ mode, storageKey }) => {
  const { theme } = useAppStore();
  const isDark = theme === 'dark';

  const [activeId, setActiveId] = useState<string | null>(() => localStorage.getItem(storageKey));
  const [detail, setDetail] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  const [symbol, setSymbol] = useState('SBIN');
  const [date, setDate] = useState(lastWeekday(1));
  const [startTime, setStartTime] = useState('09:15');
  const [capital, setCapital] = useState('50000');
  const [speed, setSpeed] = useState(5);

  const idRef = useRef<string | null>(activeId);
  idRef.current = activeId;

  const loadDetail = useCallback(async (id: string) => {
    try {
      const r = await apiService.sessionGet(id);
      setDetail((r as any).data);
    } catch (e: any) {
      // Session expired / not found → drop it so the launcher form returns
      if (e?.response?.status === 404) {
        localStorage.removeItem(storageKey);
        setActiveId(null);
        setDetail(null);
      }
    }
  }, [storageKey]);

  // Reconnect + live-poll the active session (this is what survives refresh)
  useEffect(() => {
    if (!activeId) { setDetail(null); return; }
    loadDetail(activeId);
    const t = setInterval(() => { if (idRef.current) loadDetail(idRef.current); }, 2500);
    return () => clearInterval(t);
  }, [activeId, loadDetail]);

  const start = async () => {
    setStarting(true); setError(null);
    try {
      const r = await apiService.sessionStart({
        mode, symbol: symbol.toUpperCase().trim(),
        date: mode === 'replay' ? date : undefined,
        start_time: startTime, capital: parseFloat(capital) || 50000, speed,
      });
      const d = (r as any).data;
      localStorage.setItem(storageKey, d.id);
      setActiveId(d.id);
      setDetail(d);
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || 'Failed to start session');
    } finally {
      setStarting(false);
    }
  };

  const newRun = () => { localStorage.removeItem(storageKey); setActiveId(null); setDetail(null); };
  const stop = async () => { if (activeId) { try { await apiService.sessionStop(activeId); loadDetail(activeId); } catch {} } };
  const changeSpeed = async (s: number) => { if (activeId) { try { await apiService.sessionSpeed(activeId, s); loadDetail(activeId); } catch {} } };

  // ── Launch form ─────────────────────────────────────────────────────────────
  if (!activeId) {
    return (
      <div style={card}>
        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--nd-text-1)', marginBottom: 6 }}>
          {mode === 'replay' ? 'Start an AI Live Trading session' : 'Start a paper trading session'}
        </div>
        <div style={{ fontSize: 12, color: 'var(--nd-text-3)', marginBottom: 14 }}>
          Runs on the server with the 7-agent ensemble — it keeps going in the background and survives a refresh.
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 12 }}>
          <Field label="Symbol" full><StockPicker value={symbol} onChange={(s) => setSymbol(s)} /></Field>
          {mode === 'replay' && (
            <Field label="Date"><input className="nd-input" type="date" value={date} onChange={e => setDate(e.target.value)} style={fieldStyle} /></Field>
          )}
          <Field label="Start time"><input className="nd-input" type="time" value={startTime} onChange={e => setStartTime(e.target.value)} style={fieldStyle} /></Field>
          <Field label="Capital (₹)"><input className="nd-input" type="number" inputMode="numeric" value={capital} onChange={e => setCapital(e.target.value)} style={fieldStyle} /></Field>
          <Field label="Speed">
            <select className="nd-select" value={speed} onChange={e => setSpeed(parseInt(e.target.value))} style={fieldStyle}>
              {[1, 2, 5, 10].map(s => <option key={s} value={s}>{s}×</option>)}
            </select>
          </Field>
        </div>
        <button className="nd-btn" onClick={start} disabled={starting}
          style={{ marginTop: 16, width: '100%', background: 'var(--nd-green)', color: '#fff', border: 'none', borderRadius: 8, padding: '12px 16px', fontSize: 14, fontWeight: 600, cursor: starting ? 'wait' : 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8 }}>
          <span className="material-icons" style={{ fontSize: 18 }}>play_arrow</span>
          {starting ? 'Starting…' : 'Start Session'}
        </button>
        {error && <div style={{ marginTop: 12, fontSize: 12, color: 'var(--nd-red)', background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderRadius: 8, padding: '8px 12px' }}>{error}</div>}
      </div>
    );
  }

  // ── Active session runner ───────────────────────────────────────────────────
  const pos = detail?.positionDetail ?? {};
  const dec = detail?.agentDecision ?? {};
  const markers: TradeMarker[] = (detail?.tradesList ?? []).map((t: any) => ({ timestamp: t.timestamp, action: t.action, price: t.price }));

  return (
    <div style={card}>
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: 10, marginBottom: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
          <span style={{ width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
            background: detail?.status === 'running' ? 'var(--nd-green)' : detail?.status === 'done' ? 'var(--nd-text-3)' : '#e0a800',
            boxShadow: detail?.status === 'running' ? '0 0 6px var(--nd-green)' : 'none' }} />
          <h3 style={{ margin: 0, fontSize: 15, fontWeight: 700, color: 'var(--nd-text-1)' }}>{detail?.symbol ?? '…'}</h3>
          <span style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>{detail?.date} · {detail?.currentTime} · {detail?.status}</span>
          {detail?.dataSource && <span style={{ fontSize: 10, padding: '2px 6px', borderRadius: 6, background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', color: 'var(--nd-text-3)' }}>{detail.dataSource}</span>}
        </div>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          {detail?.status === 'running' && [1, 2, 5, 10].map(sp => (
            <button key={sp} onClick={() => changeSpeed(sp)}
              style={{ ...btnGhost, padding: '5px 9px', background: detail?.speed === sp ? 'var(--nd-green)' : 'transparent', color: detail?.speed === sp ? '#fff' : 'var(--nd-text-2)', borderColor: detail?.speed === sp ? 'var(--nd-green)' : 'var(--nd-border)' }}>{sp}×</button>
          ))}
          {detail?.status === 'running' && <button onClick={stop} style={btnGhost}>Stop</button>}
          <button onClick={newRun} style={{ ...btnGhost, color: 'var(--nd-green)', borderColor: 'var(--nd-green)' }}>New run</button>
        </div>
      </div>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16, marginBottom: 12 }}>
        <Stat label="P&L" value={`${(detail?.pnl ?? 0) >= 0 ? '+' : ''}${inr(detail?.pnl ?? 0)} (${(detail?.pnlPct ?? 0) >= 0 ? '+' : ''}${(detail?.pnlPct ?? 0).toFixed(2)}%)`} color={pnlColor(detail?.pnl ?? 0)} />
        <Stat label="Cash" value={inr(detail?.cash ?? 0)} />
        <Stat label="Position" value={pos.status === 'LONG' ? `LONG ${pos.quantity} @ ${inr(pos.entryPrice)}` : 'FLAT'} color={pos.status === 'LONG' ? 'var(--nd-green)' : 'var(--nd-text-2)'} />
        <Stat label="Unrealised" value={inr(pos.currentPnl ?? 0)} color={pnlColor(pos.currentPnl ?? 0)} />
      </div>

      <div style={{ border: '1px solid var(--nd-border)', borderRadius: 10, overflow: 'hidden', marginBottom: 12 }}>
        <TradeChart candles={detail?.candles ?? []} prevDayCandles={detail?.prevDayCandles ?? []} markers={markers} height={380} isDark={isDark} />
      </div>

      {dec.action && (
        <div style={{ fontSize: 12, color: 'var(--nd-text-2)', background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderRadius: 8, padding: '8px 12px', marginBottom: 12 }}>
          <strong style={{ color: dec.action === 'BUY' ? 'var(--nd-green)' : dec.action === 'SELL' ? 'var(--nd-red)' : 'var(--nd-text-1)' }}>{dec.action}</strong>
          {dec.confidence != null && <span> · {(dec.confidence * 100).toFixed(0)}%</span>}
          {dec.reason && <span style={{ color: 'var(--nd-text-3)' }}> — {dec.reason}</span>}
        </div>
      )}

      {(detail?.tradesList ?? []).length > 0 && (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', minWidth: 480, borderCollapse: 'collapse', fontSize: 12 }}>
            <thead>
              <tr style={{ color: 'var(--nd-text-3)', textAlign: 'left' }}>
                <th style={th}>Time</th><th style={th}>Action</th><th style={th}>Price</th><th style={th}>Qty</th><th style={th}>P&L</th><th style={th}>Reason</th>
              </tr>
            </thead>
            <tbody>
              {detail.tradesList.map((t: any, i: number) => (
                <tr key={i} style={{ borderTop: '1px solid var(--nd-border)' }}>
                  <td style={td}>{t.time}</td>
                  <td style={{ ...td, color: t.action === 'BUY' ? 'var(--nd-green)' : 'var(--nd-red)', fontWeight: 600 }}>{t.action}</td>
                  <td style={td}>{inr(t.price)}</td>
                  <td style={td}>{t.quantity}</td>
                  <td style={{ ...td, color: pnlColor(t.pnl ?? 0) }}>{t.pnl != null ? inr(t.pnl) : '—'}</td>
                  <td style={{ ...td, color: 'var(--nd-text-3)', maxWidth: 240, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{t.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div style={{ marginTop: 12, fontSize: 11, color: 'var(--nd-text-3)' }}>
        This session runs on the server and keeps going even if you refresh or close the tab. Completed trades also appear in <strong>Orders</strong>.
      </div>
    </div>
  );
};

export default SessionLauncher;
