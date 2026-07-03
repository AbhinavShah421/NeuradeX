import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useAppStore } from '../stores/appStore';
import apiService from '../services/api';
import TradingChart, { TradeMarker, ChartCandle } from './TradingChart';
import StockPicker from './StockPicker';
import { getErrorMessage } from '../utils/errors';

// ── Session data shapes (server-side trading session state) ───────────────────
interface SessionSummary {
  id: string;
  status: string;
  mode?: 'replay' | 'paper';
  symbol: string;
  date?: string;
  currentTime?: string;
  pnl: number;
  trades?: number;
  position?: string;
}

interface SessionAgentVote { agent: string; action: string; reasoning?: string }

interface SessionDecision {
  action?: string;
  confidence?: number;
  executed?: boolean;
  time?: string;
  reason?: string;
  timingSignal?: number;
  timingLabel?: string;
  timingMode?: string;
  ensembleAction?: string;
  indicators?: { rsi?: number; momentumPct?: number; vwap?: number };
  agents?: SessionAgentVote[];
}

interface SessionTrade {
  time?: string;
  timestamp?: number;
  action: 'BUY' | 'SELL';
  price: number;
  quantity?: number;
  pnl?: number;
  reason?: string;
  executed?: boolean;
}

interface SessionDetail {
  id: string;
  status: string;
  symbol: string;
  date?: string;
  currentTime?: string;
  dataSource?: string;
  speed?: number;
  pnl: number;
  pnlPct?: number;
  cash: number;
  timingMode?: string;
  positionDetail?: { status: string; quantity?: number; entryPrice?: number; currentPnl?: number };
  agentDecision?: SessionDecision;
  lastDecision?: SessionDecision | null;
  decisionLog?: SessionTrade[];
  tradesList?: SessionTrade[];
  candles?: ChartCandle[];
  prevDayCandles?: ChartCandle[];
}

/**
 * Multi-session manager: start several server-side trading sessions (different
 * stocks), see a list of the running ones, and open any to watch its live chart.
 * Sessions run in the background and survive a refresh. Completed sessions drop
 * off the list — their trades live in Orders.
 *
 * `mode` fixes this view to one session type:
 *   - "replay" → AI Live Trading (historical replay)
 *   - "paper"  → live Paper Trading
 *   - undefined → all modes (with a Mode selector) — the unified Live Sessions page
 */
interface Props {
  mode?: 'replay' | 'paper';
}

const card: React.CSSProperties = {
  background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 12, padding: 16,
};
const fieldStyle: React.CSSProperties = { width: '100%', boxSizing: 'border-box' };
const btnGhost: React.CSSProperties = { background: 'transparent', border: '1px solid var(--nd-border)', borderRadius: 6, padding: '4px 10px', fontSize: 11, fontWeight: 500, color: 'var(--nd-text-2)', cursor: 'pointer' };
const th: React.CSSProperties = { padding: '6px 8px', fontWeight: 500 };
const td: React.CSSProperties = { padding: '6px 8px', color: 'var(--nd-text-1)' };

const inr = (v: number) => `₹${(v ?? 0).toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;
const pnlColor = (v: number) => (v > 0 ? 'var(--nd-green)' : v < 0 ? 'var(--nd-red)' : 'var(--nd-text-3)');

function lastWeekday(offset = 1): string {
  const d = new Date();
  d.setDate(d.getDate() - offset);
  while (d.getDay() === 0 || d.getDay() === 6) d.setDate(d.getDate() - 1);
  return d.toISOString().slice(0, 10);
}

const HEADERS: Record<string, { icon: string; title: string; blurb: string }> = {
  replay: { icon: 'show_chart', title: 'AI Live Trading', blurb: 'Replay a past trading day candle-by-candle with the 7-agent ensemble. Sessions run on the server, keep going in the background, survive a refresh, and you can run several stocks at once.' },
  paper:  { icon: 'receipt_long', title: 'Paper Trading', blurb: 'Practice on the live market with no real money. Sessions run on the server during NSE hours (09:15–15:30 IST), survive a refresh, and run several at once.' },
  all:    { icon: 'monitoring', title: 'Live Sessions', blurb: 'Sessions run on the server with the full 7-agent ensemble. They keep advancing in the background, survive a refresh, run several at once, and can be reopened any time.' },
};

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
const StatusDot: React.FC<{ status: string }> = ({ status }) => {
  const c = status === 'running' ? 'var(--nd-green)' : status === 'done' ? 'var(--nd-text-3)' : status === 'error' ? 'var(--nd-red)' : '#e0a800';
  return <span style={{ width: 8, height: 8, borderRadius: '50%', background: c, flexShrink: 0, boxShadow: status === 'running' ? `0 0 6px ${c}` : 'none' }} />;
};

const SessionManager: React.FC<Props> = ({ mode: fixedMode }) => {
  const { theme } = useAppStore();
  const isDark = theme === 'dark';
  const header = HEADERS[fixedMode ?? 'all'];

  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  // New-session form
  const [mode, setMode] = useState<'replay' | 'paper'>(fixedMode ?? 'replay');
  const [symbol, setSymbol] = useState('SBIN');
  const [date, setDate] = useState(lastWeekday(1));
  const [autoDate, setAutoDate] = useState(true);   // Auto = last trading day
  const [startTime, setStartTime] = useState('09:15');
  const [capital, setCapital] = useState('50000');
  const [speed, setSpeed] = useState(1);
  const [timingMode, setTimingMode] = useState<'normal' | 'aggressive'>('normal');

  const effectiveMode = fixedMode ?? mode;

  const selectedRef = useRef<string | null>(null);
  selectedRef.current = selectedId;
  const detailCache = useRef<Map<string, SessionDetail>>(new Map());

  const loadList = useCallback(async () => {
    try {
      const r = await apiService.sessionList();
      setSessions((r.data as SessionSummary[]) ?? []);
    } catch (e: unknown) {
      const status = (e as { response?: { status?: number } })?.response?.status;
      if (status === 401 || status === 403) {
        setError('Session expired — please log in again.');
      }
      /* on other errors keep last known list */
    }
  }, []);
  const loadDetail = useCallback(async (id: string) => {
    try {
      const r = await apiService.sessionGet(id);
      const data = r.data as SessionDetail;
      detailCache.current.set(id, data);
      if (selectedRef.current === id) setDetail(data);
    } catch { if (selectedRef.current === id) setDetail(null); }
  }, []);

  useEffect(() => { loadList(); const t = setInterval(loadList, 3000); return () => clearInterval(t); }, [loadList]);
  useEffect(() => {
    if (!selectedId) { setDetail(null); return; }
    // Show cached data instantly, then fetch fresh in background
    const cached = detailCache.current.get(selectedId);
    if (cached) setDetail(cached);
    loadDetail(selectedId);
    const t = setInterval(() => { if (selectedRef.current) loadDetail(selectedRef.current); }, 2500);
    return () => clearInterval(t);
  }, [selectedId, loadDetail]);

  const startSession = async () => {
    setStarting(true); setError(null);
    try {
      const r = await apiService.sessionStart({
        mode: effectiveMode, symbol: symbol.toUpperCase().trim(),
        date: effectiveMode === 'replay' ? (autoDate ? lastWeekday(1) : date) : undefined,
        start_time: startTime, capital: parseFloat(capital) || 50000, speed,
        timing_mode: timingMode,
      });
      const d = r.data as { id: string };
      await loadList();
      setSelectedId(d.id);
    } catch (e: unknown) {
      setError(getErrorMessage(e, 'Failed to start session'));
    } finally {
      setStarting(false);
    }
  };

  const stopSession = async (id: string) => { try { await apiService.sessionStop(id); await loadList(); if (selectedId === id) loadDetail(id); } catch {} };
  const deleteSession = async (id: string) => { try { await apiService.sessionDelete(id); if (selectedId === id) { setSelectedId(null); setDetail(null); } await loadList(); } catch {} };
  const changeSpeed = async (id: string, s: number) => { try { await apiService.sessionSpeed(id, s); loadDetail(id); } catch {} };

  // Only in-progress sessions of this mode are listed; finished ones live in Orders.
  const running = sessions.filter(s => s.status === 'running' && (!fixedMode || s.mode === fixedMode));

  const markers: TradeMarker[] = (detail?.tradesList ?? []).map((t) => ({ timestamp: t.timestamp ?? 0, action: t.action, price: t.price }));
  const pos: NonNullable<SessionDetail['positionDetail']> = detail?.positionDetail ?? { status: 'FLAT' };
  const dec: SessionDecision = detail?.agentDecision ?? {};
  const ld  = detail?.lastDecision ?? null;
  const decLog: SessionTrade[] = detail?.decisionLog ?? [];
  const actColor = (a?: string) => a === 'BUY' ? 'var(--nd-green)' : a === 'SELL' ? 'var(--nd-red)' : 'var(--nd-text-2)';

  return (
    <div>
      {/* Intro */}
      <div style={{ ...card, marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
          <span className="material-icons" style={{ color: 'var(--nd-green)', fontSize: 20, lineHeight: 1 }}>{header.icon}</span>
          <h2 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: 'var(--nd-text-1)' }}>{header.title}</h2>
        </div>
        <p style={{ margin: 0, fontSize: 13, lineHeight: 1.6, color: 'var(--nd-text-2)' }}>{header.blurb}</p>
      </div>

      {/* New session */}
      <div style={{ ...card, marginBottom: 16 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--nd-text-1)', marginBottom: 14 }}>Start a new session</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 12 }}>
          {!fixedMode && (
            <Field label="Mode" full>
              <select className="nd-select" value={mode} onChange={e => setMode(e.target.value as 'replay' | 'paper')} style={fieldStyle}>
                <option value="replay">AI Live Trading (replay)</option>
                <option value="paper">Paper Trading (live)</option>
              </select>
            </Field>
          )}
          <Field label="Symbol" full><StockPicker value={symbol} onChange={(sym) => setSymbol(sym)} /></Field>
          {effectiveMode === 'replay' && (
            <Field label="Date">
              <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                <button type="button"
                  onClick={() => { const next = !autoDate; setAutoDate(next); if (next) setDate(lastWeekday(1)); }}
                  title="Auto = last trading day"
                  style={{ flexShrink: 0, padding: '6px 10px', borderRadius: 6, border: `1px solid ${autoDate ? 'var(--nd-green)' : 'var(--nd-border)'}`, background: autoDate ? 'var(--nd-green)' : 'var(--nd-surface)', color: autoDate ? '#fff' : 'var(--nd-text-2)', fontSize: 11, fontWeight: 600, cursor: 'pointer' }}>
                  Auto
                </button>
                <input className="nd-input" type="date" value={date} disabled={autoDate}
                  onChange={e => setDate(e.target.value)} style={{ ...fieldStyle, opacity: autoDate ? 0.55 : 1 }} />
              </div>
            </Field>
          )}
          <Field label="Start time"><input className="nd-input" type="time" value={startTime} onChange={e => setStartTime(e.target.value)} style={fieldStyle} /></Field>
          <Field label="Capital (₹)"><input className="nd-input" type="number" inputMode="numeric" value={capital} onChange={e => setCapital(e.target.value)} style={fieldStyle} /></Field>
          <Field label="Speed">
            <select className="nd-select" value={speed} onChange={e => setSpeed(parseInt(e.target.value))} style={fieldStyle}>
              {[1, 2, 5, 10].map(s => <option key={s} value={s}>{s}×</option>)}
            </select>
          </Field>
          <Field label="Entry timing">
            <select className="nd-select" value={timingMode} onChange={e => setTimingMode(e.target.value as 'normal' | 'aggressive')} style={fieldStyle}
              title="Aggressive loosens the entry triggers (wider RSI/momentum bands) so the session takes more trades">
              <option value="normal">Normal</option>
              <option value="aggressive">Aggressive (more trades)</option>
            </select>
          </Field>
        </div>
        <button className="nd-btn" onClick={startSession} disabled={starting}
          style={{ marginTop: 16, width: '100%', background: 'var(--nd-green)', color: '#fff', border: 'none', borderRadius: 8, padding: '12px 16px', fontSize: 14, fontWeight: 600, cursor: starting ? 'wait' : 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8 }}>
          <span className="material-icons" style={{ fontSize: 18 }}>play_arrow</span>
          {starting ? 'Starting…' : 'New Run'}
        </button>
        {error && <div style={{ marginTop: 12, fontSize: 12, color: 'var(--nd-red)', background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderRadius: 8, padding: '8px 12px' }}>{error}</div>}
      </div>

      {/* Running sessions list */}
      <div style={{ ...card, marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
          <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: 'var(--nd-text-1)' }}>
            Running Sessions <span style={{ color: 'var(--nd-text-3)', fontWeight: 400 }}>({running.length})</span>
          </h3>
          <a href="/neuradex/orders" style={{ fontSize: 12, color: 'var(--nd-green)', textDecoration: 'none' }}>Completed → Orders</a>
        </div>
        {running.length === 0 ? (
          <div style={{ fontSize: 13, color: 'var(--nd-text-3)' }}>No running sessions — start one (or several) above. They keep running here even if you refresh; once finished, their trades appear in <strong>Orders</strong>.</div>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(240px,1fr))', gap: 10 }}>
            {running.map(s => (
              <div key={s.id} onClick={() => setSelectedId(s.id)}
                style={{ background: 'var(--nd-bg)', border: `1px solid ${selectedId === s.id ? 'var(--nd-green)' : 'var(--nd-border)'}`, borderRadius: 10, padding: 12, cursor: 'pointer' }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
                    <StatusDot status={s.status} />
                    <span style={{ fontWeight: 700, color: 'var(--nd-text-1)' }}>{s.symbol}</span>
                    {!fixedMode && <span style={{ fontSize: 10, color: 'var(--nd-text-3)', textTransform: 'uppercase' }}>{s.mode}</span>}
                  </div>
                  <span style={{ fontSize: 12, fontWeight: 700, color: pnlColor(s.pnl) }}>{s.pnl >= 0 ? '+' : ''}{inr(s.pnl)}</span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 8, fontSize: 11, color: 'var(--nd-text-3)' }}>
                  <span>{s.date} · {s.currentTime}</span>
                  <span>{s.trades} trades · {s.position}</span>
                </div>
                <div style={{ display: 'flex', gap: 6, marginTop: 10 }}>
                  <button onClick={(e) => { e.stopPropagation(); setSelectedId(s.id); }} style={{ ...btnGhost, color: 'var(--nd-green)', borderColor: 'var(--nd-green)' }}>View chart</button>
                  <button onClick={(e) => { e.stopPropagation(); stopSession(s.id); }} style={btnGhost}>Stop</button>
                  <button onClick={(e) => { e.stopPropagation(); deleteSession(s.id); }} style={btnGhost}>Remove</button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Selected session live detail */}
      {detail && (
        <div style={card}>
          <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: 10, marginBottom: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <StatusDot status={detail.status} />
              <h3 style={{ margin: 0, fontSize: 15, fontWeight: 700, color: 'var(--nd-text-1)' }}>{detail.symbol}</h3>
              <span style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>{detail.date} · {detail.currentTime} · {detail.status}</span>
              {detail.dataSource && <span style={{ fontSize: 10, padding: '2px 6px', borderRadius: 6, background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', color: 'var(--nd-text-3)' }}>{detail.dataSource}</span>}
            </div>
            <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
              {detail.status === 'running' && [1, 2, 5, 10].map(sp => (
                <button key={sp} onClick={() => changeSpeed(detail.id, sp)}
                  style={{ ...btnGhost, background: detail.speed === sp ? 'var(--nd-green)' : 'transparent', color: detail.speed === sp ? '#fff' : 'var(--nd-text-2)', borderColor: detail.speed === sp ? 'var(--nd-green)' : 'var(--nd-border)' }}>{sp}×</button>
              ))}
              <button onClick={() => { setSelectedId(null); setDetail(null); }} style={btnGhost}>Close</button>
            </div>
          </div>

          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16, marginBottom: 12 }}>
            <Stat label="P&L" value={`${detail.pnl >= 0 ? '+' : ''}${inr(detail.pnl)} (${(detail.pnlPct ?? 0) >= 0 ? '+' : ''}${(detail.pnlPct ?? 0).toFixed(2)}%)`} color={pnlColor(detail.pnl)} />
            <Stat label="Cash" value={inr(detail.cash)} />
            <Stat label="Position" value={pos.status === 'LONG' ? `LONG ${pos.quantity} @ ${inr(pos.entryPrice ?? 0)}` : 'FLAT'} color={pos.status === 'LONG' ? 'var(--nd-green)' : 'var(--nd-text-2)'} />
            <Stat label="Unrealised" value={inr(pos.currentPnl ?? 0)} color={pnlColor(pos.currentPnl ?? 0)} />
          </div>

          <div style={{ border: '1px solid var(--nd-border)', borderRadius: 10, overflow: 'hidden', marginBottom: 12 }}>
            <TradingChart candles={detail.candles ?? []} prevDayCandles={detail.prevDayCandles ?? []} markers={markers} height={400} isDark={isDark} />
          </div>

          {/* ── Live AI decision — why it is / isn't trading right now ── */}
          {(ld || dec.action) && (() => {
            const d = ld || dec;
            const ind = d.indicators || {};
            const tcolor = d.timingSignal === 1 ? 'var(--nd-green)' : d.timingSignal === -1 ? 'var(--nd-red)' : 'var(--nd-text-3)';
            return (
              <div style={{ background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderRadius: 10, padding: '10px 12px', marginBottom: 12 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 6 }}>
                  <span className="material-icons" style={{ fontSize: 16, color: 'var(--nd-accent)' }}>psychology</span>
                  <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--nd-text-1)' }}>Live AI Decision</span>
                  {(detail.timingMode === 'aggressive' || d.timingMode === 'aggressive') && (
                    <span style={{ fontSize: 9.5, fontWeight: 700, padding: '1px 6px', borderRadius: 4, background: '#f59e0b22', color: '#f59e0b', border: '1px solid #f59e0b55' }}>AGGRESSIVE</span>
                  )}
                  <span style={{ fontSize: 11, fontWeight: 700, padding: '2px 9px', borderRadius: 5, background: `${actColor(d.action)}1a`, color: actColor(d.action) }}>{d.action}</span>
                  {d.confidence != null && <span style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>{(d.confidence * 100).toFixed(0)}%</span>}
                  {d.executed
                    ? <span style={{ fontSize: 10, fontWeight: 700, color: 'var(--nd-green)', border: '1px solid var(--nd-green)', borderRadius: 4, padding: '0 5px' }}>TRADE PLACED</span>
                    : <span style={{ fontSize: 10, fontWeight: 700, color: 'var(--nd-text-3)', border: '1px solid var(--nd-border)', borderRadius: 4, padding: '0 5px' }}>NO TRADE</span>}
                  {d.time && <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--nd-text-3)' }}>{d.time}</span>}
                </div>
                <div style={{ fontSize: 12, color: 'var(--nd-text-2)', lineHeight: 1.5, marginBottom: ld ? 8 : 0 }}>{d.reason}</div>
                {ld && (
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, fontSize: 11, color: 'var(--nd-text-3)' }}>
                    <span>Timing: <strong style={{ color: tcolor }}>{d.timingLabel}</strong></span>
                    <span>Ensemble: <strong style={{ color: actColor(d.ensembleAction) }}>{d.ensembleAction}</strong></span>
                    {ind.rsi != null && <span>RSI <strong style={{ color: 'var(--nd-text-1)' }}>{ind.rsi}</strong></span>}
                    {ind.momentumPct != null && <span>Mom <strong style={{ color: ind.momentumPct >= 0 ? 'var(--nd-green)' : 'var(--nd-red)' }}>{ind.momentumPct >= 0 ? '+' : ''}{ind.momentumPct}%</strong></span>}
                    {ind.vwap != null && <span>VWAP <strong style={{ color: 'var(--nd-text-1)' }}>{inr(ind.vwap)}</strong></span>}
                    {Array.isArray(d.agents) && d.agents.length > 0 && (
                      <span style={{ flexBasis: '100%', marginTop: 2 }}>
                        Agents: {d.agents.map((a, i: number) => (
                          <span key={i} style={{ marginRight: 8 }}>{a.agent} <strong style={{ color: actColor(a.action) }}>{a.action}</strong></span>
                        ))}
                      </span>
                    )}
                  </div>
                )}
              </div>
            );
          })()}

          {/* ── Decision log — the running "why" each candle ── */}
          {decLog.length > 0 && (
            <details style={{ marginBottom: 12 }}>
              <summary style={{ cursor: 'pointer', fontSize: 12, fontWeight: 600, color: 'var(--nd-text-2)', padding: '6px 0' }}>
                Decision log ({decLog.length} candles)
              </summary>
              <div style={{ maxHeight: 240, overflow: 'auto', border: '1px solid var(--nd-border)', borderRadius: 8, marginTop: 6 }}>
                {[...decLog].reverse().map((d, i: number) => (
                  <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'baseline', padding: '5px 10px', borderBottom: '1px solid var(--nd-border)', fontSize: 11 }}>
                    <span style={{ color: 'var(--nd-text-3)', width: 42, flexShrink: 0 }}>{d.time}</span>
                    <span style={{ fontWeight: 700, color: actColor(d.action), width: 38, flexShrink: 0 }}>{d.action}</span>
                    {d.executed && <span className="material-icons" style={{ fontSize: 13, color: 'var(--nd-green)' }}>check_circle</span>}
                    <span style={{ color: 'var(--nd-text-3)', flex: 1 }}>{d.reason}</span>
                  </div>
                ))}
              </div>
            </details>
          )}

          {(detail.tradesList ?? []).length > 0 && (
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', minWidth: 480, borderCollapse: 'collapse', fontSize: 12 }}>
                <thead>
                  <tr style={{ color: 'var(--nd-text-3)', textAlign: 'left' }}>
                    <th style={th}>Time</th><th style={th}>Action</th><th style={th}>Price</th><th style={th}>Qty</th><th style={th}>P&L</th><th style={th}>Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {(detail.tradesList ?? []).map((t, i: number) => (
                    <tr key={i} style={{ borderTop: '1px solid var(--nd-border)' }}>
                      <td style={td}>{t.time}</td>
                      <td style={{ ...td, color: t.action === 'BUY' ? 'var(--nd-green)' : 'var(--nd-red)', fontWeight: 600 }}>{t.action}</td>
                      <td style={td}>{inr(t.price)}</td>
                      <td style={td}>{t.quantity}</td>
                      <td style={{ ...td, color: pnlColor(t.pnl ?? 0) }}>{t.pnl != null ? inr(t.pnl) : '—'}</td>
                      <td style={{ ...td, color: 'var(--nd-text-3)', maxWidth: 260, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{t.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default SessionManager;
