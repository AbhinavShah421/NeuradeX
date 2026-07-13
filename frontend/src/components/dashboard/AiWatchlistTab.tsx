import React, { useEffect, useState, useCallback } from 'react';
import apiService from '../../services/api';
import ScanControl from '../ScanControl';
import { WatchlistStock, WatchlistData, ScanEvaluation, ScanDiff, ScanDiffMove, ScanDiffEntry } from '../../types';
import { getErrorMessage } from '../../utils/errors';

// ── AI Watchlist tab (self-running scanner output + evidence) ──────────────────

const ACTION_BG: Record<string, string> = { BUY: '#22c55e', SELL: '#ef4444', HOLD: '#f59e0b' };
const GRADE_COLOR: Record<string, string> = { A: '#22c55e', B: '#3b82f6', C: '#f59e0b', D: '#94a3b8' };
// Hold-cap presets (minutes) for inline auto-trading of a watchlist stock
const HOLD_CAPS = [0, 15, 30, 60] as const;   // 0 = auto (system decides exits)

const SignalScorePanel: React.FC<{ ev: ScanEvaluation | null }> = ({ ev }) => {
  const [open, setOpen] = useState(false);
  const latest = ev?.latest;
  const overall = ev?.overall;
  const haveScore = latest || (overall && overall.accuracy != null);
  if (!haveScore) {
    return (
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8, background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 10, padding: '10px 12px', marginBottom: 12, fontSize: 12, color: 'var(--nd-text-3)', lineHeight: 1.5 }}>
        <span className="material-icons" style={{ fontSize: 17, color: 'var(--nd-blue)' }}>insights</span>
        <span>After the market closes, the scanner grades each morning pick against the actual day move and shows a <strong>signal score</strong> here — that accuracy then calibrates future scans so the system keeps learning.</span>
      </div>
    );
  }
  const acc = latest?.accuracy ?? overall?.accuracy ?? 0;
  const color = acc >= 0.6 ? 'var(--nd-green)' : acc >= 0.45 ? '#d98c00' : 'var(--nd-red)';
  const results = latest?.results ?? [];
  return (
    <div style={{ border: '1px solid var(--nd-border)', borderRadius: 12, marginBottom: 12, overflow: 'hidden', background: 'var(--nd-surface)' }}>
      <div onClick={() => setOpen(o => !o)} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '11px 14px', cursor: 'pointer' }}>
        <span className="material-icons" style={{ fontSize: 19, color }}>verified</span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--nd-text-1)' }}>
            Last signal score{latest?.date ? ` · ${latest.date}` : ''}
          </div>
          <div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>
            {(latest?.picks ?? overall?.picks ?? 0)} picks graded vs the actual day move
            {overall?.days ? ` · ${overall.days}-day avg ${((overall.accuracy ?? 0) * 100).toFixed(0)}%` : ''}
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={{ fontSize: 18, fontWeight: 800, color }}>{(acc * 100).toFixed(0)}%</div>
          <div style={{ fontSize: 10, color: 'var(--nd-text-3)' }}>accuracy</div>
        </div>
        {results.length > 0 && (
          <span className="material-icons" style={{ fontSize: 18, color: 'var(--nd-text-3)' }}>{open ? 'expand_less' : 'expand_more'}</span>
        )}
      </div>
      {open && results.length > 0 && (
        <div style={{ borderTop: '1px solid var(--nd-border)', padding: '8px 14px 12px' }}>
          {latest?.avgRealizedReturnPct != null && (
            <div style={{ fontSize: 11.5, color: 'var(--nd-text-3)', marginBottom: 8 }}>
              Avg realised return in the predicted direction:&nbsp;
              <strong style={{ color: latest.avgRealizedReturnPct >= 0 ? 'var(--nd-green)' : 'var(--nd-red)' }}>
                {latest.avgRealizedReturnPct >= 0 ? '+' : ''}{latest.avgRealizedReturnPct}%
              </strong>
            </div>
          )}
          {results.map((r) => (
            <div key={r.symbol} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 0', borderBottom: '1px solid var(--nd-border)', fontSize: 12 }}>
              <span className="material-icons" style={{ fontSize: 15, color: r.correct ? 'var(--nd-green)' : 'var(--nd-red)' }}>{r.correct ? 'check_circle' : 'cancel'}</span>
              <span style={{ fontWeight: 700, color: 'var(--nd-text-1)', width: 90 }}>{r.symbol}</span>
              <span style={{ fontSize: 10, fontWeight: 700, padding: '1px 7px', borderRadius: 4, background: `${ACTION_BG[r.action]}1a`, color: ACTION_BG[r.action] }}>{r.action}</span>
              <span style={{ flex: 1 }} />
              <span style={{ color: r.dayReturnPct >= 0 ? 'var(--nd-green)' : 'var(--nd-red)', fontWeight: 600 }}>
                day {r.dayReturnPct >= 0 ? '+' : ''}{r.dayReturnPct}%
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

// ── Grade badge (A/B/C/D win-probability quality) ─────────────────────────────
const GradeBadge: React.FC<{ grade?: string; winProb?: number }> = ({ grade, winProb }) => {
  if (!grade) return null;
  const c = GRADE_COLOR[grade] ?? '#94a3b8';
  return (
    <span title={winProb != null ? `Win probability ${(winProb * 100).toFixed(0)}%` : 'Quality grade'}
      style={{ fontSize: 10, fontWeight: 800, padding: '1px 6px', borderRadius: 4, background: `${c}1f`, color: c, border: `1px solid ${c}55`, letterSpacing: 0.3 }}>
      {grade}{winProb != null ? ` · ${(winProb * 100).toFixed(0)}%` : ''}
    </span>
  );
};

// ── Shared stock row used across all watchlist tabs ───────────────────────────
const WatchlistRow: React.FC<{ w: WatchlistStock; i: number; onClick: () => void; badge?: React.ReactNode; onAutoTrade?: (sym: string) => void; tradingSym?: string | null; onWatch?: (sym: string) => void; watchState?: string }> = ({ w, i, onClick, badge, onAutoTrade, tradingSym, onWatch, watchState }) => {
  const started = tradingSym === w.symbol;
  return (
  <div onClick={onClick}
    // flexWrap lets the controls group drop to its own line on narrow screens
    // instead of crushing the symbol/price blocks into each other.
    style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 12, rowGap: 8, padding: '12px 4px', borderTop: i ? '1px solid var(--nd-border)' : 'none', cursor: 'pointer' }}>
    <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--nd-text-3)', width: 20, flexShrink: 0 }}>#{i + 1}</span>
    <div style={{ flex: 1, minWidth: 150 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 14, fontWeight: 700, color: 'var(--nd-text-1)' }}>{w.symbol}</span>
        <GradeBadge grade={w.grade} winProb={w.winProbability} />
        {badge}
      </div>
      <div style={{ fontSize: 11, color: 'var(--nd-text-3)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{w.name}</div>
      {w.news && ((w.news.catalyst && w.news.catalyst !== 'none') || w.news.summary) && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginTop: 2, fontSize: 10.5, fontWeight: 600, color: ACTION_BG[w.news.action] ?? 'var(--nd-text-3)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          <span className="material-icons" style={{ fontSize: 12, flexShrink: 0 }}>article</span>
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {(w.news.catalyst && w.news.catalyst !== 'none') ? w.news.catalyst : w.news.summary}
          </span>
        </div>
      )}
    </div>
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0, marginLeft: 'auto' }}>
      <div style={{ textAlign: 'right' }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--nd-text-1)', whiteSpace: 'nowrap' }}>₹{w.price?.toLocaleString('en-IN')}</div>
        <div style={{ fontSize: 11, color: 'var(--nd-text-3)', whiteSpace: 'nowrap' }}>
          {w.winProbability != null ? `win ${(w.winProbability * 100).toFixed(0)}%` : `conf ${(w.confidence * 100).toFixed(0)}%`}
        </div>
      </div>
      <span style={{ fontSize: 11, fontWeight: 700, padding: '3px 9px', borderRadius: 5, background: `${ACTION_BG[w.action]}1a`, color: ACTION_BG[w.action] }}>{w.action}</span>
      {onWatch && (
        <button onClick={e => { e.stopPropagation(); if (!watchState) onWatch(w.symbol); }} disabled={!!watchState}
          title="Add to the live watcher (2nd-level scan) — promoted to paper trading only if it shows live confidence AND re-scores grade A"
          style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 3, padding: '6px 10px', minHeight: 34, borderRadius: 6, border: `1px solid ${watchState ? '#f59e0b' : 'var(--nd-border)'}`, background: watchState ? 'rgba(245,158,11,0.1)' : 'var(--nd-surface)', color: watchState ? '#f59e0b' : 'var(--nd-text-2)', cursor: watchState ? 'default' : 'pointer', fontSize: 11.5, fontWeight: 600, flexShrink: 0 }}>
          <span className="material-icons" style={{ fontSize: 13 }}>{watchState === 'promoted' ? 'trending_up' : watchState ? 'check' : 'visibility'}</span>
          {watchState === 'promoted' ? 'Promoted' : watchState ? 'Watching' : 'Watch'}
        </button>
      )}
      {onAutoTrade && (
        <button onClick={e => { e.stopPropagation(); onAutoTrade(w.symbol); }} disabled={started}
          title="Auto paper-trade this stock with the selected hold cap"
          // minHeight guarantees a real ~34px tap target — this starts a trade,
          // so it shouldn't be easier to mis-tap than to hit.
          style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 3, padding: '6px 10px', minHeight: 34, borderRadius: 6, border: `1px solid ${started ? 'var(--nd-green)' : 'var(--nd-border)'}`, background: started ? 'var(--nd-green-50)' : 'var(--nd-surface)', color: started ? 'var(--nd-green)' : 'var(--nd-text-2)', cursor: started ? 'default' : 'pointer', fontSize: 11.5, fontWeight: 600, flexShrink: 0 }}>
          <span className="material-icons" style={{ fontSize: 13 }}>{started ? 'check' : 'play_arrow'}</span>
          {started ? 'Started' : 'Auto'}
        </button>
      )}
      <span className="material-icons" style={{ fontSize: 16, color: 'var(--nd-text-3)' }}>chevron_right</span>
    </div>
  </div>
  );
};

// What changed between the last two completed scans — rank moves, new entrants,
// drop-offs, each with the reason derived from the scoring components.
const ScanDiffPanel: React.FC<{ diff: ScanDiff | null }> = ({ diff }) => {
  const [open, setOpen] = useState(false);
  if (!diff) return null;
  if (!diff.available) {
    return (
      <div className="nd-card" style={{ padding: '10px 14px', marginBottom: 12, fontSize: 12, color: 'var(--nd-text-3)' }}>
        {diff.message || 'No previous scan to compare yet.'}
      </div>
    );
  }
  const moved: ScanDiffMove[] = diff.moved ?? [];
  const entered: ScanDiffEntry[] = diff.entered ?? [];
  const dropped: ScanDiffEntry[] = diff.dropped ?? [];
  const ups = moved.filter(m => m.direction === 'up');
  const downs = moved.filter(m => m.direction === 'down');
  const c = diff.counts ?? { moved: moved.length, entered: entered.length, dropped: dropped.length };

  const Row: React.FC<{ m: ScanDiffMove | ScanDiffEntry; kind: 'up' | 'down' | 'in' | 'out' }> = ({ m, kind }) => {
    const color = kind === 'up' ? '#22c55e' : kind === 'down' ? '#ef4444' : kind === 'in' ? '#3b82f6' : '#94a3b8';
    const badge = kind === 'up' ? `▲ ${(m as ScanDiffMove).delta}` : kind === 'down' ? `▼ ${Math.abs((m as ScanDiffMove).delta)}` : kind === 'in' ? 'NEW' : 'OUT';
    return (
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, padding: '4px 0', borderBottom: '1px solid var(--nd-border)', flexWrap: 'wrap' }}>
        <span style={{ fontSize: 10, fontWeight: 700, color, minWidth: 38 }}>{badge}</span>
        <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--nd-text-1)', minWidth: 80 }}>{m.symbol}</span>
        <span style={{ fontSize: 11, color: 'var(--nd-text-3)', minWidth: 78 }}>
          {kind === 'out' ? `was #${m.prevRank}` : kind === 'in' ? `#${m.rank}` : `#${m.prevRank}→#${m.rank}`}
        </span>
        {/* No minWidth/flex-grow: with flexWrap on the row, this drops to its own
            line when the fixed columns above don't leave enough room (narrow
            screens) instead of squeezing into a few illegible px. */}
        <span style={{ fontSize: 11, color: 'var(--nd-text-2)' }}>{m.reason || ''}</span>
      </div>
    );
  };

  return (
    <div className="nd-card" style={{ padding: '12px 14px', marginBottom: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', cursor: 'pointer', gap: 8 }}
        onClick={() => setOpen(o => !o)}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--nd-text-1)' }}>What changed since the last scan</div>
          <div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>
            <span style={{ color: '#22c55e' }}>▲ {ups.length}</span> · <span style={{ color: '#ef4444' }}>▼ {downs.length}</span>
            {' · '}<span style={{ color: '#3b82f6' }}>{c.entered} new</span> · <span style={{ color: '#94a3b8' }}>{c.dropped} dropped</span>
          </div>
        </div>
        <span className="material-icons" style={{ color: 'var(--nd-text-3)' }}>{open ? 'expand_less' : 'expand_more'}</span>
      </div>
      {open && (
        <div style={{ marginTop: 10, display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 14 }}>
          <div>
            <div style={{ fontSize: 11, fontWeight: 700, color: '#22c55e', marginBottom: 4 }}>Climbed ({ups.length})</div>
            {ups.length ? ups.slice(0, 12).map((m, i) => <Row key={i} m={m} kind="up" />) : <div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>—</div>}
            <div style={{ fontSize: 11, fontWeight: 700, color: '#3b82f6', margin: '10px 0 4px' }}>Entered the board ({c.entered})</div>
            {entered.length ? entered.slice(0, 10).map((m, i) => <Row key={i} m={m} kind="in" />) : <div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>—</div>}
          </div>
          <div>
            <div style={{ fontSize: 11, fontWeight: 700, color: '#ef4444', marginBottom: 4 }}>Slipped ({downs.length})</div>
            {downs.length ? downs.slice(0, 12).map((m, i) => <Row key={i} m={m} kind="down" />) : <div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>—</div>}
            <div style={{ fontSize: 11, fontWeight: 700, color: '#94a3b8', margin: '10px 0 4px' }}>Dropped off ({c.dropped})</div>
            {dropped.length ? dropped.slice(0, 10).map((m, i) => <Row key={i} m={m} kind="out" />) : <div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>—</div>}
          </div>
        </div>
      )}
    </div>
  );
};

const AiWatchlistTab: React.FC = () => {
  const [data, setData]         = useState<WatchlistData | null>(null);
  const [evalData, setEvalData] = useState<ScanEvaluation | null>(null);
  const [diff, setDiff]         = useState<ScanDiff | null>(null);
  const [agrade, setAgrade]     = useState<any | null>(null);   // A-grade live watcher snapshot + promotions
  const [sel, setSel]           = useState<WatchlistStock | null>(null);
  const [tab, setTab]           = useState<'intraday' | 'delivery' | 'fno'>('intraday');
  const [holdCap, setHoldCap]   = useState<number>(0);      // per-trade hold cap (min) —
  // 0 = Auto (default): the system decides — trend-intact winners ride past the
  // policy review point, everything else exits there. A number = manual hard cap.
  const [tradingSym, setTradingSym] = useState<string | null>(null);
  const [autoMsg, setAutoMsg]   = useState<string | null>(null);

  const startAuto = useCallback(async (sym: string) => {
    setAutoMsg(null);
    try {
      await apiService.sessionStart({ mode: 'paper', symbol: sym, capital: 50000, max_hold_minutes: holdCap });
      setTradingSym(sym);
      setAutoMsg(`Auto paper-trading ${sym} — exits ${holdCap ? `hard-capped at ${holdCap}m (manual)` : 'decided by the system (auto)'}.`);
      setTimeout(() => setAutoMsg(null), 6000);
    } catch (e: unknown) {
      setAutoMsg(`Could not start auto-trade for ${sym}: ${getErrorMessage(e, 'error')}`);
      setTimeout(() => setAutoMsg(null), 6000);
    }
  }, [holdCap]);

  // Manually watched symbols (Watch button) — optimistic local set so the
  // button flips immediately; the scanner's snapshot confirms within a cycle.
  const [manualWatch, setManualWatch] = useState<Set<string>>(new Set());
  const addWatch = useCallback(async (sym: string) => {
    setManualWatch(prev => new Set(prev).add(sym));
    try { await apiService.addAgradeWatch(sym); } catch {
      setManualWatch(prev => { const n = new Set(prev); n.delete(sym); return n; });
    }
  }, []);

  const [viewN, setViewN]   = useState<number>(15);   // how many intraday stocks to display
  const [ranked, setRanked] = useState<WatchlistStock[]>([]);     // full ranked board (top viewN)
  const [wlSaving, setWlSaving] = useState(false);
  const load = useCallback(async () => {
    try { const r = await apiService.aiWatchlist(); setData(r.data as WatchlistData); } catch {}
    try { const e = await apiService.scanEvaluation(); setEvalData(e.data as ScanEvaluation); } catch {}
    try { const d = await apiService.scanDiff(); setDiff(d.data as ScanDiff); } catch {}
    try { const a = await apiService.getAgradeWatch(); setAgrade(a.data); } catch {}
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 30000); return () => clearInterval(t); }, [load]);

  // Full ranked intraday board (lets the user view far more than the high-conviction
  // tier). Refetches when the chosen count changes + on a 30s interval.
  const loadRanked = useCallback(async (n: number) => {
    try {
      const r = await apiService.getRanked(n);
      setRanked((r.data as { items?: WatchlistStock[] })?.items ?? []);
    } catch {}
  }, []);
  useEffect(() => { loadRanked(viewN); const t = setInterval(() => loadRanked(viewN), 30000); return () => clearInterval(t); }, [viewN, loadRanked]);

  const changeViewN = async (n: number) => {
    setViewN(n); setWlSaving(true);
    // Keep the high-conviction grading tier in step for small selections (its
    // backend cap is 25); larger views just pull more of the ranked board.
    try { if (n <= 25) { await apiService.setWatchlistConfig(n); await apiService.scanWatchlist(); } } catch {}
    await loadRanked(n);
    setTimeout(() => setWlSaving(false), 1500);
  };

  // A-grade live watcher: per-symbol status + today's promotions (camelCased by
  // the axios interceptor — watch symbols carry status/promotedAt etc.).
  const watchStatus = new Map<string, string>();
  (agrade?.watch?.symbols ?? []).forEach((s: any) => { if (s?.symbol) watchStatus.set(s.symbol, s.status); });
  const promotedSet = new Set<string>((agrade?.promotions ?? []).map((p: any) => p?.symbol).filter(Boolean));

  // Intraday view: the top-N ranked scan (falls back to the high-conviction list).
  const intraday: WatchlistStock[] = (ranked.length ? ranked : (data?.intraday ?? data?.items ?? [])).slice(0, viewN);
  const delivery: WatchlistStock[] = data?.delivery ?? [];
  const fno: WatchlistStock[]      = data?.fno ?? [];

  const tabs = [
    { key: 'intraday', label: 'Best Intraday',  icon: 'bolt',       count: intraday.length },
    { key: 'delivery', label: 'Best Delivery',  icon: 'calendar_month', count: delivery.length },
    { key: 'fno',      label: 'Best F&O',        icon: 'auto_graph', count: fno.length },
  ] as const;

  const activeItems = tab === 'intraday' ? intraday : tab === 'delivery' ? delivery : fno;

  return (
    <div>
      {/* Header — watchlist size + centralized scan status + rescan */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--nd-text-3)' }}>
          <span className="material-icons" style={{ fontSize: 15 }}>tune</span>
          Show top
          <select value={viewN} onChange={e => changeViewN(Number(e.target.value))} className="nd-input"
            style={{ width: 72, padding: '4px 6px' }}>
            {[10, 15, 25, 50, 100, 200, 250].map(n => <option key={n} value={n}>{n}</option>)}
          </select>
          scanned{wlSaving ? ' · rescanning…' : ''}
          {viewN > 25 && <span style={{ fontSize: 10.5, color: 'var(--nd-text-3)' }}> · top {Math.min(viewN, 25)} are the graded high-conviction tier</span>}
        </div>
        <div style={{ marginLeft: 'auto' }}><ScanControl align="right" /></div>
      </div>

      <SignalScorePanel ev={evalData} />
      <ScanDiffPanel diff={diff} />

      {/* Category tabs — scrolls horizontally instead of overflowing the page
          on narrow screens (three tabs + icon + count badge don't fit 390px). */}
      <div style={{
        display: 'flex', gap: 6, marginBottom: 14, borderBottom: '1px solid var(--nd-border)', paddingBottom: 0,
        overflowX: 'auto', scrollbarWidth: 'none', WebkitOverflowScrolling: 'touch',
      }}>
        {tabs.map(t => (
          <button key={t.key} onClick={() => setTab(t.key)}
            style={{
              display: 'flex', alignItems: 'center', gap: 5,
              padding: '8px 14px', borderRadius: '8px 8px 0 0',
              border: '1px solid var(--nd-border)', borderBottom: tab === t.key ? '2px solid var(--nd-green)' : '1px solid var(--nd-border)',
              background: tab === t.key ? 'var(--nd-surface)' : 'transparent',
              cursor: 'pointer', fontSize: 12, fontWeight: tab === t.key ? 700 : 500,
              color: tab === t.key ? 'var(--nd-green)' : 'var(--nd-text-2)',
              marginBottom: -1, whiteSpace: 'nowrap', flexShrink: 0,
            }}>
            <span className="material-icons" style={{ fontSize: 14 }}>{t.icon}</span>
            {t.label}
            {t.count > 0 && (
              <span style={{ fontSize: 10, fontWeight: 700, padding: '1px 5px', borderRadius: 8, background: tab === t.key ? 'var(--nd-green)' : 'var(--nd-border)', color: tab === t.key ? '#fff' : 'var(--nd-text-3)' }}>{t.count}</span>
            )}
          </button>
        ))}
      </div>

      {/* Tab descriptions */}
      {tab === 'intraday' && (
        <div style={{ fontSize: 11, color: 'var(--nd-text-3)', marginBottom: 10, padding: '6px 10px', background: 'var(--nd-bg)', borderRadius: 6, borderLeft: '3px solid var(--nd-green)' }}>
          High-momentum stocks with above-average volume — suitable for same-day trades. Grades A/B are the highest win-probability setups. Click for full evidence.
        </div>
      )}

      {/* Top Conviction Picks + auto-trade controls (intraday only) */}
      {tab === 'intraday' && intraday.length > 0 && (() => {
        const top = intraday.filter((w) => w.grade === 'A' || w.grade === 'B').slice(0, 5);
        return (
          <div style={{ border: '1px solid var(--nd-border)', borderRadius: 12, padding: '12px 14px', marginBottom: 12, background: 'linear-gradient(135deg, rgba(34,197,94,0.06), rgba(59,130,246,0.05))' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: top.length ? 10 : 0 }}>
              <span className="material-icons" style={{ fontSize: 18, color: 'var(--nd-green)' }}>workspace_premium</span>
              <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--nd-text-1)' }}>Top Conviction Picks</span>
              {data?.gradeCounts && (
                <span style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>
                  {data.gradeCounts.A ?? 0}×A · {data.gradeCounts.B ?? 0}×B · {data.gradeCounts.C ?? 0}×C
                </span>
              )}
              {agrade?.watch && (
                <span style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>
                  · Live watch: {(agrade.watch.symbols ?? []).filter((s: any) => s?.status !== 'promoted').length} watching
                  · {agrade.watch.promotionsToday ?? 0}/{agrade.watch.cap ?? 5} promoted
                  {agrade.watch.feedOk === false && <span style={{ color: 'var(--nd-red, #ef4444)' }}> · live feed offline</span>}
                </span>
              )}
              <span style={{ flex: 1 }} />
              {/* Hold-cap selector */}
              <span style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>Hold cap</span>
              <div style={{ display: 'flex', gap: 2, background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 8, padding: 2 }}>
                {HOLD_CAPS.map(h => (
                  <button key={h} onClick={() => setHoldCap(h)}
                    style={{ padding: '3px 9px', borderRadius: 6, border: 'none', cursor: 'pointer', fontSize: 11, fontWeight: 600,
                      background: holdCap === h ? 'var(--nd-green)' : 'transparent', color: holdCap === h ? '#fff' : 'var(--nd-text-2)' }}>
                    {h === 0 ? 'Auto' : `${h}m`}
                  </button>
                ))}
              </div>
            </div>
            {top.length > 0 ? (
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                {top.map((w) => (
                  <div key={w.symbol} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 10px', background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 8 }}>
                    <GradeBadge grade={w.grade} winProb={w.winProbability} />
                    <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--nd-text-1)' }}>{w.symbol}</span>
                    <span style={{ fontSize: 11, fontWeight: 700, color: ACTION_BG[w.action] }}>{w.action}</span>
                    <button onClick={() => startAuto(w.symbol)} disabled={tradingSym === w.symbol}
                      style={{ display: 'flex', alignItems: 'center', gap: 3, padding: '3px 8px', borderRadius: 6, border: `1px solid ${tradingSym === w.symbol ? 'var(--nd-green)' : 'var(--nd-border)'}`, background: tradingSym === w.symbol ? 'var(--nd-green-50)' : 'var(--nd-bg)', color: tradingSym === w.symbol ? 'var(--nd-green)' : 'var(--nd-text-2)', cursor: tradingSym === w.symbol ? 'default' : 'pointer', fontSize: 11, fontWeight: 600 }}>
                      <span className="material-icons" style={{ fontSize: 13 }}>{tradingSym === w.symbol ? 'check' : 'play_arrow'}</span>
                      {tradingSym === w.symbol ? 'Started' : 'Auto'}
                    </button>
                  </div>
                ))}
              </div>
            ) : (
              <div style={{ fontSize: 11.5, color: 'var(--nd-text-3)' }}>No A/B-grade setups right now — only high win-probability names appear here.</div>
            )}
            {autoMsg && (
              <div style={{ marginTop: 10, fontSize: 11.5, color: 'var(--nd-text-2)', background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderRadius: 8, padding: '7px 10px' }}>
                {autoMsg}
              </div>
            )}
          </div>
        );
      })()}
      {tab === 'delivery' && (
        <div style={{ fontSize: 11, color: 'var(--nd-text-3)', marginBottom: 10, padding: '6px 10px', background: 'var(--nd-bg)', borderRadius: 6, borderLeft: '3px solid var(--nd-blue)' }}>
          Stocks in a confirmed uptrend with moderate volatility — suitable for multi-week holding. The <strong style={{ color: 'var(--nd-text-2)' }}>Safe ~X wks</strong> badge is the AI's estimated safe holding window.
        </div>
      )}
      {tab === 'fno' && (
        <div style={{ fontSize: 11, color: 'var(--nd-text-3)', marginBottom: 10, padding: '6px 10px', background: 'var(--nd-bg)', borderRadius: 6, borderLeft: '3px solid #a78bfa' }}>
          F&O-eligible stocks with a directional signal. Each row shows the recommended option (CE/PE), strike, expiry, and estimated safe holding days.
        </div>
      )}

      {/* Stock list */}
      {activeItems.length === 0 ? (
        <div style={{ padding: 40, textAlign: 'center', color: 'var(--nd-text-3)', fontSize: 13 }}>
          {data ? 'No qualifying stocks in this category right now.' : 'The market scanner is warming up — results will appear shortly.'}
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          {activeItems.map((w, i: number) => {
            // ── Delivery badge ──────────────────────────────────────────────
            const deliveryBadge = tab === 'delivery' && (w.deliveryWeeks ?? 0) > 0 ? (
              <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 8, background: 'rgba(59,130,246,0.12)', color: 'var(--nd-blue)', flexShrink: 0 }}>
                Safe ~{w.deliveryWeeks} wk{(w.deliveryWeeks ?? 0) > 1 ? 's' : ''}
              </span>
            ) : null;

            // ── FNO badge ───────────────────────────────────────────────────
            const rec = w.fnoRecommendation;
            const fnoBadge = tab === 'fno' && rec ? (
              <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 8, background: 'rgba(167,139,250,0.15)', color: '#a78bfa', flexShrink: 0 }}>
                {rec.optionType} {rec.strike} · {rec.expiry} · {rec.safeDays}d
              </span>
            ) : null;

            // ── A-grade live-watch state (intraday only) — shown on the Watch
            // button itself; no extra badge, it crowds narrow screens.
            const liveStatus = tab === 'intraday'
              ? (promotedSet.has(w.symbol) ? 'promoted'
                 : watchStatus.get(w.symbol) ?? (manualWatch.has(w.symbol) ? 'watching' : undefined))
              : undefined;

            return (
              <div key={w.symbol}>
                <WatchlistRow w={w} i={i} onClick={() => setSel(w)} badge={deliveryBadge ?? fnoBadge}
                  onAutoTrade={tab === 'intraday' ? startAuto : undefined} tradingSym={tradingSym}
                  onWatch={tab === 'intraday' ? addWatch : undefined} watchState={liveStatus} />
                {/* FNO rationale row */}
                {tab === 'fno' && rec && (
                  <div style={{ fontSize: 10.5, color: 'var(--nd-text-3)', padding: '0 4px 8px 52px', lineHeight: 1.5 }}>
                    {rec.rationale}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Metrics summary row for active tab */}
      {activeItems.length > 0 && tab === 'delivery' && (
        <div style={{ marginTop: 12, padding: '8px 12px', background: 'var(--nd-bg)', borderRadius: 8, fontSize: 11, color: 'var(--nd-text-3)', display: 'flex', gap: 16, flexWrap: 'wrap' }}>
          <span>Avg holding: <strong style={{ color: 'var(--nd-text-2)' }}>
            {Math.round(activeItems.reduce((s: number, i) => s + (i.deliveryWeeks || 0), 0) / activeItems.length)} wks
          </strong></span>
          <span>All in uptrend: <strong style={{ color: 'var(--nd-green)' }}>
            {activeItems.filter((i) => i.metrics?.smaTrend === 'up').length}/{activeItems.length}
          </strong></span>
        </div>
      )}

      {sel && <WatchlistEvidence stock={sel} scannedAt={data?.updatedAt} onClose={() => setSel(null)} />}
    </div>
  );
};

const WatchlistEvidence: React.FC<{ stock: WatchlistStock; scannedAt?: string; onClose: () => void }> = ({ stock, scannedAt, onClose }) => (
  <div onClick={e => { if (e.target === e.currentTarget) onClose(); }}
    style={{ position: 'fixed', inset: 0, background: '#00000080', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 12 }}>
    <div style={{ background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderRadius: 16, width: '100%', maxWidth: 500, maxHeight: '88vh', overflow: 'auto', boxShadow: '0 24px 64px #00000060' }}>
      <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--nd-border)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 16, fontWeight: 700, color: 'var(--nd-text-1)' }}>{stock.symbol}</span>
          <span style={{ fontSize: 11, fontWeight: 700, padding: '2px 9px', borderRadius: 5, background: `${ACTION_BG[stock.action]}1a`, color: ACTION_BG[stock.action] }}>{stock.action}</span>
          <GradeBadge grade={stock.grade} winProb={stock.winProbability} />
        </div>
        <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer' }}><span className="material-icons" style={{ color: 'var(--nd-text-3)', fontSize: 20 }}>close</span></button>
      </div>
      <div style={{ padding: '14px 20px' }}>
        <p style={{ margin: '0 0 12px', fontSize: 12.5, color: 'var(--nd-text-2)', lineHeight: 1.6 }}>
          Why the AI picked this for <strong>intraday</strong>: the scanner judged a
          <strong> {stock.action}</strong> view at <strong>{(stock.confidence * 100).toFixed(0)}%</strong> confidence
          {stock.signalScore != null ? <> (signal score <strong>{Math.round(stock.signalScore)}</strong>)</> : null}.
          It weighs every indicator that moves price — liquidity, volatility, trend, momentum, MACD, RSI, the opening gap and the broader market — and only stocks that clear the liquidity + volatility bar make the list.
        </p>
        <div style={{ fontSize: 12, color: 'var(--nd-text-3)', background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 8, padding: '8px 12px', marginBottom: 12 }}>{stock.reasoning}</div>
        {stock.news && (
          <div style={{ border: '1px solid var(--nd-border)', borderRadius: 10, padding: '10px 12px', marginBottom: 12, background: 'var(--nd-surface)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
              <span className="material-icons" style={{ fontSize: 15, color: ACTION_BG[stock.news.action] ?? 'var(--nd-text-3)' }}>article</span>
              <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--nd-text-3)', letterSpacing: 0.3 }}>NEWS SENTIMENT · LLM</span>
              <span style={{ flex: 1 }} />
              <span style={{ fontSize: 10, fontWeight: 700, padding: '1px 7px', borderRadius: 4, background: `${ACTION_BG[stock.news.action]}1a`, color: ACTION_BG[stock.news.action] }}>{stock.news.action}</span>
              {stock.news.confidence != null && <span style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>{(stock.news.confidence * 100).toFixed(0)}%</span>}
            </div>
            {stock.news.summary && <div style={{ fontSize: 12, color: 'var(--nd-text-2)', lineHeight: 1.5, marginBottom: 6 }}>{stock.news.summary}</div>}
            {stock.news.catalyst && stock.news.catalyst !== 'none' && (
              <div style={{ fontSize: 11.5, marginBottom: 6 }}>
                <span style={{ color: 'var(--nd-text-3)' }}>Catalyst: </span>
                <strong style={{ color: ACTION_BG[stock.news.action] ?? 'var(--nd-text-1)' }}>{stock.news.catalyst}</strong>
              </div>
            )}
            {Array.isArray(stock.news.topHeadlines) && stock.news.topHeadlines.length > 0 && (
              <div style={{ marginTop: 4 }}>
                {stock.news.topHeadlines.map((h: string, i: number) => (
                  <div key={i} style={{ display: 'flex', gap: 6, fontSize: 11, color: 'var(--nd-text-3)', padding: '3px 0' }}>
                    <span style={{ color: 'var(--nd-text-4, var(--nd-text-3))' }}>•</span>
                    <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{h}</span>
                  </div>
                ))}
              </div>
            )}
            <div style={{ fontSize: 10, color: 'var(--nd-text-3)', marginTop: 6 }}>
              {stock.news.headlinesCount} headlines{stock.news.updatedAt ? ` · ${new Date(stock.news.updatedAt).toLocaleString()}` : ''}
            </div>
          </div>
        )}
        {stock.metrics && (<>
          <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--nd-text-3)', marginBottom: 8 }}>MARKET-INDICATOR EVIDENCE</div>
          {[
            ['Avg daily volume', `${(stock.metrics.avgVolume / 1e6).toFixed(2)}M`],
            ['Relative volume', stock.metrics.relVolume != null ? `${stock.metrics.relVolume}×` : '—'],
            ['Volatility (ATR)', `${stock.metrics.atrPct}%`],
            ['Avg daily range', `${stock.metrics.rangePct}%`],
            ['Trend (SMA20/50)', stock.metrics.smaTrend ? `${stock.metrics.smaTrend}${stock.metrics.sma20 ? ` · 20: ${stock.metrics.sma20}` : ''}` : '—'],
            ['MACD histogram', stock.metrics.macdHist != null ? `${stock.metrics.macdHist >= 0 ? '+' : ''}${stock.metrics.macdHist}` : '—'],
            ['RSI (14)', `${stock.metrics.rsi}`],
            ['Momentum (10d)', `${stock.metrics.momentumPct >= 0 ? '+' : ''}${stock.metrics.momentumPct}%`],
            ['Opening gap', stock.metrics.gapPct != null ? `${stock.metrics.gapPct >= 0 ? '+' : ''}${stock.metrics.gapPct}%` : '—'],
            ['Room to 20d high', stock.metrics.distFromHighPct != null ? `${stock.metrics.distFromHighPct}%` : '—'],
            ['Market regime', stock.metrics.marketRegime ?? '—'],
            ['Liquidity score', `${(stock.metrics.liquidityScore * 100).toFixed(0)}%`],
            ['Volatility score', `${(stock.metrics.volatilityScore * 100).toFixed(0)}%`],
          ].map(([k, v]) => (
            <div key={k} style={{ display: 'flex', justifyContent: 'space-between', padding: '7px 0', borderBottom: '1px solid var(--nd-border)', fontSize: 12 }}>
              <span style={{ color: 'var(--nd-text-3)' }}>{k}</span>
              <span style={{ color: 'var(--nd-text-1)', fontWeight: 600 }}>{v}</span>
            </div>
          ))}
        </>)}
        {Array.isArray(stock.agents) && stock.agents.length > 0 && (<>
          <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--nd-text-3)', margin: '12px 0 8px' }}>AGENT BREAKDOWN</div>
          {stock.agents.map((a) => (
            <div key={a.agent} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 0', borderBottom: '1px solid var(--nd-border)' }}>
              <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--nd-text-1)', textTransform: 'capitalize', width: 84 }}>{a.agent}</span>
              <span style={{ fontSize: 11, fontWeight: 700, color: ACTION_BG[a.action] ?? 'var(--nd-text-2)', width: 40 }}>{a.action}</span>
              <span style={{ fontSize: 11, color: 'var(--nd-text-3)', flex: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{a.reasoning}</span>
            </div>
          ))}
        </>)}
        <div style={{ marginTop: 12, fontSize: 11, color: 'var(--nd-text-3)' }}>
          Live scan from the stock-scanner service · {scannedAt ? new Date(scannedAt).toLocaleString() : ''}. No hard-coded values.
        </div>
      </div>
    </div>
  </div>
);

export default AiWatchlistTab;
