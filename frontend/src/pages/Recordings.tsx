import React, { useCallback, useEffect, useMemo, useState } from 'react';
import apiService from '../services/api';
import MultiStockPicker from '../components/MultiStockPicker';
import TradingChart from '../components/TradingChart';

// NOTE: the axios interceptor camelCases all response keys, so the backend's
// snake_case (symbol_count, coverage_summary, total_ticks, full_day, first_time…)
// arrives here as symbolCount, coverageSummary, totalTicks, fullDay, firstTime, …

interface CoverageRow {
  symbol: string; date: string; ticks: number;
  firstTime: string | null; lastTime: string | null;
  fullDay: boolean; startClean: boolean; endClean: boolean;
}
interface Recording {
  id: string; name: string; date: string; symbols: string[];
  symbolCount: number; note: string; status: string;
  createdAt: string; updatedAt: string;
  coverage?: CoverageRow[];
  coverageSummary?: { symbols: number; symbolsWithData: number; fullDay: number; totalTicks: number };
}

const STATUS_META: Record<string, { label: string; color: string; bg: string; icon: string }> = {
  scheduled: { label: 'Scheduled', color: '#f59e0b', bg: 'rgba(245,158,11,0.12)', icon: 'schedule' },
  recording: { label: 'Recording', color: '#ef4444', bg: 'rgba(239,68,68,0.12)',  icon: 'fiber_manual_record' },
  completed: { label: 'Completed', color: 'var(--nd-green)', bg: 'rgba(0,179,134,0.12)', icon: 'check_circle' },
};

const fmtDate = (d: string) => {
  try {
    return new Date(d + 'T00:00:00').toLocaleDateString(undefined, { weekday: 'short', day: '2-digit', month: 'short' });
  } catch { return d; }
};

const Recordings: React.FC = () => {
  const [recordings, setRecordings] = useState<Recording[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  // Create form
  const [name, setName] = useState('');
  const [picked, setPicked] = useState<string[]>([]);
  const [creating, setCreating] = useState(false);

  // Expanded detail per recording id
  const [expanded, setExpanded] = useState<Record<string, Recording>>({});
  const [busy, setBusy] = useState<Record<string, boolean>>({});

  // Chart viewer
  const [chart, setChart] = useState<{ recId: string; symbol: string; candles: any[]; coverage: CoverageRow } | null>(null);
  const [chartLoading, setChartLoading] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await apiService.listRecordings();
      setRecordings((r?.data ?? []) as Recording[]);
      setErr(null);
    } catch (e: any) {
      setErr(e?.response?.data?.detail || 'Failed to load recordings');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 20000);   // refresh coverage/status periodically
    return () => clearInterval(t);
  }, [load]);

  const removeSymbol = (sym: string) => setPicked(prev => prev.filter(s => s !== sym));

  const create = async () => {
    if (picked.length === 0) { setErr('Add at least one stock to record.'); return; }
    setCreating(true); setErr(null); setMsg(null);
    try {
      const r = await apiService.createRecording({ name: name.trim(), symbols: picked });
      setMsg(`Recording scheduled for ${fmtDate(r.data.date)} — ${picked.length} stock(s) armed for capture.`);
      setName(''); setPicked([]);
      await load();
    } catch (e: any) {
      setErr(e?.response?.data?.detail || 'Failed to create recording');
    } finally {
      setCreating(false);
    }
  };

  const toggleExpand = async (rec: Recording) => {
    if (expanded[rec.id]) { setExpanded(prev => { const n = { ...prev }; delete n[rec.id]; return n; }); return; }
    try {
      const r = await apiService.getRecording(rec.id);
      setExpanded(prev => ({ ...prev, [rec.id]: r.data as Recording }));
    } catch (e: any) {
      setErr(e?.response?.data?.detail || 'Failed to load recording detail');
    }
  };

  const remove = async (rec: Recording) => {
    if (!window.confirm(`Delete recording "${rec.name}"? Captured data already in the dataset is kept.`)) return;
    setBusy(p => ({ ...p, [rec.id]: true }));
    try {
      await apiService.deleteRecording(rec.id);
      setExpanded(prev => { const n = { ...prev }; delete n[rec.id]; return n; });
      await load();
    } catch (e: any) {
      setErr(e?.response?.data?.detail || 'Failed to delete');
    } finally {
      setBusy(p => ({ ...p, [rec.id]: false }));
    }
  };

  const runBacktest = async (rec: Recording, symbols?: string[]) => {
    setBusy(p => ({ ...p, [rec.id]: true })); setErr(null); setMsg(null);
    try {
      const r = await apiService.backtestRecording(rec.id, { symbols });
      const started = r.data?.started ?? [];
      const skipped = r.data?.skipped ?? [];
      setMsg(`Launched ${started.length} backtest session(s)${skipped.length ? `, skipped ${skipped.length} (no data)` : ''}. Open Live Trading to watch them.`);
    } catch (e: any) {
      setErr(e?.response?.data?.detail || 'Backtest could not start');
    } finally {
      setBusy(p => ({ ...p, [rec.id]: false }));
    }
  };

  const openChart = async (recId: string, symbol: string) => {
    setChartLoading(true); setChart(null);
    try {
      const r = await apiService.getRecordingChart(recId, symbol, 60);
      setChart({ recId, symbol, candles: r.data?.candles ?? [], coverage: r.data?.coverage });
    } catch (e: any) {
      setErr(e?.response?.data?.detail || 'Failed to load chart');
    } finally {
      setChartLoading(false);
    }
  };

  const card: React.CSSProperties = {
    background: 'var(--nd-surface)', border: '1px solid var(--nd-border)',
    borderRadius: 14, padding: 18, marginBottom: 16,
  };
  const chip: React.CSSProperties = {
    display: 'inline-flex', alignItems: 'center', gap: 4, padding: '3px 8px',
    background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderRadius: 20,
    fontSize: 12, fontWeight: 600, color: 'var(--nd-text-1)',
  };

  const totalArmed = useMemo(
    () => recordings.filter(r => r.status !== 'completed').reduce((a, r) => a + (r.symbolCount || 0), 0),
    [recordings],
  );

  return (
    <div>
      {/* Intro */}
      <div style={{ ...card, borderLeft: '3px solid #0ea5e9' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
          <span className="material-icons" style={{ fontSize: 20, color: '#0ea5e9' }}>radio_button_checked</span>
          <h2 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: 'var(--nd-text-1)' }}>Data Recordings</h2>
          {totalArmed > 0 && (
            <span style={{ ...chip, color: '#0ea5e9', borderColor: '#0ea5e9' }}>{totalArmed} armed for capture</span>
          )}
        </div>
        <p style={{ margin: 0, fontSize: 12.5, color: 'var(--nd-text-3)', lineHeight: 1.6 }}>
          Pick any number of stocks to record for the next trading day. When the market opens, the Groww live
          stream is captured tick-by-tick into the 1-second dataset — the full day, from the open, with the
          least possible gap. Recorded days can be charted and backtested straight from the list below.
        </p>
      </div>

      {(err || msg) && (
        <div style={{
          ...card, marginBottom: 16, padding: '12px 14px', display: 'flex', alignItems: 'center', gap: 8,
          borderLeft: `3px solid ${err ? '#ef4444' : 'var(--nd-green)'}`,
          background: err ? 'rgba(239,68,68,0.08)' : 'rgba(0,179,134,0.08)',
        }}>
          <span className="material-icons" style={{ fontSize: 16, color: err ? '#ef4444' : 'var(--nd-green)' }}>
            {err ? 'error_outline' : 'check_circle'}
          </span>
          <span style={{ fontSize: 12.5, color: 'var(--nd-text-1)' }}>{err || msg}</span>
          <button onClick={() => { setErr(null); setMsg(null); }} style={{ marginLeft: 'auto', background: 'none', border: 'none', cursor: 'pointer', color: 'var(--nd-text-3)', display: 'flex' }}>
            <span className="material-icons" style={{ fontSize: 15 }}>close</span>
          </button>
        </div>
      )}

      {/* Create form */}
      <div style={card}>
        <h3 style={{ margin: '0 0 14px', fontSize: 14, fontWeight: 700, color: 'var(--nd-text-1)' }}>New recording</h3>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div>
            <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--nd-text-3)', textTransform: 'uppercase', letterSpacing: '0.4px' }}>Name (optional)</label>
            <input className="nd-input" value={name} onChange={e => setName(e.target.value)}
              placeholder="e.g. Nifty50 large caps" style={{ width: '100%', boxSizing: 'border-box', marginTop: 5 }} />
          </div>
          <div>
            <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--nd-text-3)', textTransform: 'uppercase', letterSpacing: '0.4px' }}>
              Stocks to record — no limit ({picked.length} added)
            </label>
            <div style={{ marginTop: 5 }}>
              <MultiStockPicker selected={picked} onChange={setPicked} placeholder="Search, then tick stocks to record…" />
            </div>
            {picked.length > 0 && (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 10 }}>
                {picked.map(s => (
                  <span key={s} style={chip}>
                    {s}
                    <button onClick={() => removeSymbol(s)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--nd-text-3)', display: 'flex', padding: 0 }}>
                      <span className="material-icons" style={{ fontSize: 14 }}>close</span>
                    </button>
                  </span>
                ))}
                <button onClick={() => setPicked([])} style={{ ...chip, cursor: 'pointer', color: 'var(--nd-text-3)' }}>Clear all</button>
              </div>
            )}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            <button className="nd-btn nd-btn-primary" disabled={creating || picked.length === 0} onClick={create}
              style={{ opacity: creating || picked.length === 0 ? 0.6 : 1 }}>
              {creating ? 'Scheduling…' : 'Schedule recording'}
            </button>
            <span style={{ fontSize: 11.5, color: 'var(--nd-text-3)' }}>
              Targets the next clean market open (today if before 09:15 IST, otherwise the next trading day) — never a mid-day start.
            </span>
          </div>
        </div>
      </div>

      {/* List */}
      {loading ? (
        <div style={{ ...card, textAlign: 'center', color: 'var(--nd-text-3)', fontSize: 13 }}>Loading recordings…</div>
      ) : recordings.length === 0 ? (
        <div style={{ ...card, textAlign: 'center', color: 'var(--nd-text-3)', fontSize: 13 }}>
          No recordings yet. Schedule one above to start capturing a trading day.
        </div>
      ) : recordings.map(rec => {
        const meta = STATUS_META[rec.status] ?? STATUS_META.completed;
        const sum = rec.coverageSummary;
        const det = expanded[rec.id];
        return (
          <div key={rec.id} style={card}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
              <span style={{
                display: 'inline-flex', alignItems: 'center', gap: 5, padding: '3px 10px', borderRadius: 20,
                fontSize: 11, fontWeight: 700, color: meta.color, background: meta.bg,
              }}>
                <span className="material-icons" style={{ fontSize: 13 }}>{meta.icon}</span>{meta.label}
              </span>
              <span style={{ fontWeight: 700, fontSize: 14, color: 'var(--nd-text-1)' }}>{rec.name}</span>
              <span style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>{fmtDate(rec.date)}</span>
              <span style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>· {rec.symbolCount} stock{rec.symbolCount === 1 ? '' : 's'}</span>
              {sum && sum.totalTicks > 0 && (
                <span style={{ fontSize: 12, color: 'var(--nd-text-2)' }}>
                  · {sum.totalTicks.toLocaleString()} ticks · {sum.symbolsWithData}/{sum.symbols} captured
                  {sum.fullDay > 0 && <span style={{ color: 'var(--nd-green)' }}> · {sum.fullDay} full-day</span>}
                </span>
              )}
              <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
                {rec.status === 'completed' && (
                  <button className="nd-btn" disabled={busy[rec.id]} onClick={() => runBacktest(rec)}
                    title="Run intraday backtest for every recorded symbol">
                    <span className="material-icons" style={{ fontSize: 14, verticalAlign: 'middle', marginRight: 4 }}>history_edu</span>
                    Backtest all
                  </button>
                )}
                <button className="nd-btn" onClick={() => toggleExpand(rec)}>
                  {det ? 'Hide' : 'Details'}
                </button>
                <button className="nd-btn" disabled={busy[rec.id]} onClick={() => remove(rec)} title="Delete recording">
                  <span className="material-icons" style={{ fontSize: 15, color: '#ef4444' }}>delete_outline</span>
                </button>
              </div>
            </div>

            {det && (
              <div style={{ marginTop: 14, borderTop: '1px solid var(--nd-border)', paddingTop: 12 }}>
                {(det.coverage ?? []).length === 0 ? (
                  <div style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>No symbols.</div>
                ) : (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr 1fr 0.8fr auto', gap: 8, fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.4px', color: 'var(--nd-text-3)', padding: '0 4px' }}>
                      <span>Symbol</span><span>Captured</span><span>Window</span><span>Coverage</span><span></span>
                    </div>
                    {(det.coverage ?? []).map(c => (
                      <div key={c.symbol} style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr 1fr 0.8fr auto', gap: 8, alignItems: 'center', fontSize: 12, background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderRadius: 8, padding: '8px 10px' }}>
                        <span style={{ fontWeight: 700, color: 'var(--nd-text-1)' }}>{c.symbol}</span>
                        <span style={{ color: 'var(--nd-text-2)' }}>{c.ticks.toLocaleString()} ticks</span>
                        <span style={{ color: 'var(--nd-text-3)' }}>{c.firstTime && c.lastTime ? `${c.firstTime}–${c.lastTime}` : '—'}</span>
                        <span>
                          {c.ticks === 0 ? <span style={{ color: 'var(--nd-text-3)' }}>—</span>
                            : c.fullDay ? <span style={{ color: 'var(--nd-green)', fontWeight: 600 }}>Full day</span>
                            : <span style={{ color: '#f59e0b', fontWeight: 600 }}>Partial</span>}
                        </span>
                        <span style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
                          <button className="nd-btn" disabled={c.ticks === 0} onClick={() => openChart(rec.id, c.symbol)}
                            style={{ padding: '3px 8px', fontSize: 11, opacity: c.ticks === 0 ? 0.5 : 1 }}>Chart</button>
                          {rec.status === 'completed' && (
                            <button className="nd-btn" disabled={c.ticks === 0 || busy[rec.id]} onClick={() => runBacktest(rec, [c.symbol])}
                              style={{ padding: '3px 8px', fontSize: 11, opacity: c.ticks === 0 ? 0.5 : 1 }}>Backtest</button>
                          )}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
                {rec.status !== 'completed' && (
                  <p style={{ margin: '10px 0 0', fontSize: 11.5, color: 'var(--nd-text-3)' }}>
                    These symbols are armed. Capture runs automatically during market hours (09:15–15:30 IST) on {fmtDate(rec.date)}.
                  </p>
                )}
              </div>
            )}
          </div>
        );
      })}

      {/* Chart modal */}
      {(chart || chartLoading) && (
        <div onClick={() => setChart(null)} style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', zIndex: 200,
          display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20,
        }}>
          <div onClick={e => e.stopPropagation()} style={{
            background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 14,
            padding: 18, width: 'min(960px, 96vw)', maxHeight: '92vh', overflowY: 'auto',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
              <span className="material-icons" style={{ fontSize: 18, color: '#0ea5e9' }}>candlestick_chart</span>
              <h3 style={{ margin: 0, fontSize: 15, fontWeight: 700, color: 'var(--nd-text-1)' }}>
                {chart ? `${chart.symbol} · recorded 1-min bars` : 'Loading chart…'}
              </h3>
              <button onClick={() => setChart(null)} style={{ marginLeft: 'auto', background: 'none', border: 'none', cursor: 'pointer', color: 'var(--nd-text-3)', display: 'flex' }}>
                <span className="material-icons">close</span>
              </button>
            </div>
            {chartLoading ? (
              <div style={{ padding: 40, textAlign: 'center', color: 'var(--nd-text-3)' }}>Loading…</div>
            ) : chart && chart.candles.length === 0 ? (
              <div style={{ padding: 40, textAlign: 'center', color: 'var(--nd-text-3)' }}>No captured candles for this symbol/day yet.</div>
            ) : chart ? (
              <>
                {chart.coverage && (
                  <div style={{ fontSize: 12, color: 'var(--nd-text-3)', marginBottom: 10 }}>
                    {chart.candles.length} bars · {chart.coverage.ticks.toLocaleString()} ticks ·
                    {chart.coverage.firstTime}–{chart.coverage.lastTime} ·
                    {chart.coverage.fullDay ? <span style={{ color: 'var(--nd-green)' }}> full day</span> : <span style={{ color: '#f59e0b' }}> partial</span>}
                  </div>
                )}
                <TradingChart candles={chart.candles} height={460} />
              </>
            ) : null}
          </div>
        </div>
      )}
    </div>
  );
};

export default Recordings;
