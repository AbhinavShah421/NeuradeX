import React, { useEffect, useState, useCallback, useRef } from 'react';
import { Link } from 'react-router-dom';
import apiService from '../services/api';

const inr = (v: number) =>
  `₹${v.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

// ── Types ─────────────────────────────────────────────────────────────────────

interface StockEntry {
  symbol: string;
  name: string;
  sector: string;
  exchange: string;
}

interface PriceInfo {
  price: number;
  changePct: number;
}

// ── Shared Tab Bar ─────────────────────────────────────────────────────────────

const TABS = [
  { id: 'watchlist', label: 'AI Watchlist',  icon: 'auto_awesome' },
  { id: 'directory', label: 'All Stocks',    icon: 'format_list_bulleted' },
] as const;
type TabId = typeof TABS[number]['id'];

// ── Exchange badge ─────────────────────────────────────────────────────────────

const ExBadge = ({ ex }: { ex: string }) => {
  const color = ex === 'NSE' ? '#3b82f6' : ex === 'BSE' ? '#f59e0b' : '#8b5cf6';
  return (
    <span style={{
      fontSize: 9, fontWeight: 700, padding: '2px 5px', borderRadius: 3,
      background: `${color}18`, color, border: `1px solid ${color}40`,
      letterSpacing: 0.4,
    }}>{ex}</span>
  );
};

// ── All Stocks Directory Tab ───────────────────────────────────────────────────

const EX_ALL = 'All';

const DirectoryTab: React.FC = () => {
  const [stocks, setStocks]           = useState<StockEntry[]>([]);
  const [total, setTotal]             = useState(0);
  const [pages, setPages]             = useState(1);
  const [sectors, setSectors]         = useState<string[]>([]);
  const [prices, setPrices]           = useState<Record<string, PriceInfo>>({});
  const [loading, setLoading]         = useState(false);
  const [priceLoading, setPriceLoading] = useState(false);
  const [query, setQuery]             = useState('');
  const [sector, setSector]           = useState('');
  const [exchange, setExchange]       = useState('');
  const [page, setPage]               = useState(1);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const fetchDirectory = useCallback(async (q: string, sec: string, ex: string, pg: number) => {
    setLoading(true);
    try {
      const json = await apiService.getDirectoryList({
        page: pg, limit: 50,
        ...(q   ? { q }        : {}),
        ...(sec ? { sector: sec } : {}),
        ...(ex && ex !== EX_ALL ? { exchange: ex } : {}),
      });
      setStocks(json.data ?? []);
      setTotal(json.total ?? 0);
      setPages(json.pages ?? 1);
      if (json.sectors?.length) setSectors(json.sectors);
      if (json.data?.length) {
        setPriceLoading(true);
        const syms = json.data.map((s: StockEntry) => s.symbol);
        const prJson = await apiService.getDirectoryPrices(syms);
        setPrices(prJson.data ?? {});
        setPriceLoading(false);
      }
    } catch { /* ignore */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setPage(1);
      fetchDirectory(query, sector, exchange, 1);
    }, query ? 300 : 0);
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [query, sector, exchange, fetchDirectory]);

  useEffect(() => {
    fetchDirectory(query, sector, exchange, page);
  }, [page]); // eslint-disable-line react-hooks/exhaustive-deps

  const chgColor = (v?: number) =>
    v == null ? 'var(--nd-text-3)' : v >= 0 ? 'var(--nd-green)' : 'var(--nd-red)';

  return (
    <div>
      {/* Filters row */}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 14 }}>
        {/* Search */}
        <div style={{ position: 'relative', flex: '1 1 220px', maxWidth: 280 }}>
          <span className="material-icons" style={{ position: 'absolute', left: 8, top: '50%', transform: 'translateY(-50%)', fontSize: 16, color: 'var(--nd-text-3)', pointerEvents: 'none' }}>search</span>
          <input
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="Search symbol or name…"
            style={{
              width: '100%', boxSizing: 'border-box',
              paddingLeft: 30, paddingRight: 10, paddingTop: 7, paddingBottom: 7,
              fontSize: 13, borderRadius: 8, border: '1px solid var(--nd-border)',
              background: 'var(--nd-surface)', color: 'var(--nd-text-1)', outline: 'none',
            }}
          />
        </div>

        {/* Sector */}
        <select
          value={sector}
          onChange={e => setSector(e.target.value)}
          style={{ fontSize: 12, padding: '7px 10px', borderRadius: 8, border: '1px solid var(--nd-border)', background: 'var(--nd-surface)', color: 'var(--nd-text-1)', cursor: 'pointer' }}
        >
          <option value="">All Sectors</option>
          {sectors.map(s => <option key={s} value={s}>{s}</option>)}
        </select>

        {/* Exchange toggle */}
        <div style={{ display: 'flex', borderRadius: 8, overflow: 'hidden', border: '1px solid var(--nd-border)' }}>
          {[EX_ALL, 'NSE', 'BSE'].map(ex => (
            <button
              key={ex}
              onClick={() => setExchange(ex === EX_ALL ? '' : ex)}
              style={{
                padding: '6px 14px', fontSize: 12, fontWeight: 500, cursor: 'pointer', border: 'none',
                background: (exchange === ex || (ex === EX_ALL && !exchange)) ? 'var(--nd-accent)' : 'var(--nd-surface)',
                color:      (exchange === ex || (ex === EX_ALL && !exchange)) ? '#fff' : 'var(--nd-text-2)',
                transition: 'all 0.15s',
              }}
            >{ex}</button>
          ))}
        </div>

        <span style={{ fontSize: 12, color: 'var(--nd-text-3)', marginLeft: 'auto' }}>
          {total} stocks
        </span>
      </div>

      {/* Table */}
      <div style={{ overflowX: 'auto' }}>
        <table className="nd-table">
          <thead>
            <tr>
              <th style={{ width: 40, textAlign: 'center' }}>#</th>
              <th>Symbol</th>
              <th>Company</th>
              <th>Sector</th>
              <th style={{ textAlign: 'center' }}>Exchange</th>
              <th style={{ textAlign: 'right' }}>Price</th>
              <th style={{ textAlign: 'right' }}>Change %</th>
              <th style={{ textAlign: 'center' }}>Action</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={8} style={{ textAlign: 'center', padding: 40, color: 'var(--nd-text-3)' }}>
                  <span className="material-icons nd-spin" style={{ fontSize: 20, verticalAlign: 'middle', marginRight: 6 }}>autorenew</span>
                  Loading stocks…
                </td>
              </tr>
            ) : stocks.length === 0 ? (
              <tr>
                <td colSpan={8} style={{ textAlign: 'center', padding: 40, color: 'var(--nd-text-3)' }}>
                  No stocks found. Try adjusting your filters.
                </td>
              </tr>
            ) : stocks.map((s, idx) => {
              const pi  = prices[s.symbol];
              const row = (page - 1) * 50 + idx + 1;
              return (
                <tr key={s.symbol}
                  onMouseEnter={e => (e.currentTarget.style.background = 'var(--nd-bg)')}
                  onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                  style={{ transition: 'background 0.1s' }}
                >
                  <td style={{ textAlign: 'center', color: 'var(--nd-text-3)', fontSize: 11 }}>{row}</td>
                  <td style={{ fontWeight: 700, fontSize: 13, color: 'var(--nd-accent)', fontFamily: 'monospace' }}>{s.symbol}</td>
                  <td style={{ fontSize: 13, color: 'var(--nd-text-1)', maxWidth: 220 }}>{s.name}</td>
                  <td style={{ fontSize: 11.5, color: 'var(--nd-text-3)' }}>{s.sector}</td>
                  <td style={{ textAlign: 'center' }}><ExBadge ex={s.exchange} /></td>
                  <td style={{ textAlign: 'right', fontWeight: 600, fontSize: 13 }}>
                    {priceLoading ? <span style={{ color: 'var(--nd-text-3)', fontSize: 11 }}>…</span> : pi ? inr(pi.price) : '—'}
                  </td>
                  <td style={{ textAlign: 'right', fontWeight: 600, fontSize: 13, color: chgColor(pi?.changePct) }}>
                    {pi != null ? `${pi.changePct >= 0 ? '+' : ''}${pi.changePct.toFixed(2)}%` : '—'}
                  </td>
                  <td style={{ textAlign: 'center' }}>
                    <Link to={`/stocks/${s.symbol}`} style={{
                      fontSize: 11, fontWeight: 600, padding: '3px 10px', borderRadius: 6,
                      background: 'var(--nd-accent)18', color: 'var(--nd-accent)',
                      border: '1px solid var(--nd-accent)40', textDecoration: 'none',
                    }}>View</Link>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {pages > 1 && (
        <div style={{ padding: '12px 0', display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 4 }}>
          <span style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>
            Page {page} of {pages} · {total} stocks
          </span>
          <div style={{ display: 'flex', gap: 4 }}>
            <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1}
              style={{ padding: '4px 12px', fontSize: 12, borderRadius: 6, cursor: page === 1 ? 'default' : 'pointer', border: '1px solid var(--nd-border)', background: 'var(--nd-surface)', color: page === 1 ? 'var(--nd-text-3)' : 'var(--nd-text-1)', opacity: page === 1 ? 0.5 : 1 }}>
              ← Prev
            </button>
            {Array.from({ length: Math.min(7, pages) }, (_, i) => {
              let p: number;
              if (pages <= 7)       p = i + 1;
              else if (page <= 4)   p = i + 1;
              else if (page >= pages - 3) p = pages - 6 + i;
              else                  p = page - 3 + i;
              return (
                <button key={p} onClick={() => setPage(p)}
                  style={{ width: 30, height: 28, fontSize: 12, borderRadius: 6, cursor: 'pointer', border: '1px solid var(--nd-border)', background: p === page ? 'var(--nd-accent)' : 'var(--nd-surface)', color: p === page ? '#fff' : 'var(--nd-text-2)', fontWeight: p === page ? 700 : 400 }}>
                  {p}
                </button>
              );
            })}
            <button onClick={() => setPage(p => Math.min(pages, p + 1))} disabled={page === pages}
              style={{ padding: '4px 12px', fontSize: 12, borderRadius: 6, cursor: page === pages ? 'default' : 'pointer', border: '1px solid var(--nd-border)', background: 'var(--nd-surface)', color: page === pages ? 'var(--nd-text-3)' : 'var(--nd-text-1)', opacity: page === pages ? 0.5 : 1 }}>
              Next →
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

// ── Metric evidence modal ─────────────────────────────────────────────────────
// Shows exactly how each headline number is derived from real stored data.

const MetricModal: React.FC<{ cardId: string; stats: any; onClose: () => void }> = ({ cardId, stats, onClose }) => {
  const pct = (v: number) => `${(v * 100).toFixed(1)}%`;
  const Row: React.FC<{ k: string; v: React.ReactNode; c?: string }> = ({ k, v, c }) => (
    <div style={{ display: 'flex', justifyContent: 'space-between', padding: '9px 0', borderBottom: '1px solid var(--nd-border)', fontSize: 13 }}>
      <span style={{ color: 'var(--nd-text-3)' }}>{k}</span>
      <span style={{ color: c || 'var(--nd-text-1)', fontWeight: 600, textAlign: 'right' }}>{v}</span>
    </div>
  );

  const META: Record<string, { title: string; icon: string; how: string; rows: () => React.ReactNode }> = {
    accuracy: {
      title: 'Model Accuracy', icon: 'psychology',
      how: 'Share of the AI engine’s recorded predictions whose outcome was correct (from the agent learning loop). It rises as the agents learn from each backtest, paper trade and live session.',
      rows: () => (<>
        <Row k="Correct predictions" v={stats.correctPredictions} c="var(--nd-green)" />
        <Row k="Total predictions" v={stats.totalPredictions} />
        <Row k="Accuracy" v={pct(stats.accuracyRate)} c="var(--nd-green)" />
      </>),
    },
    win: {
      title: 'Win Rate', icon: 'emoji_events',
      how: 'Share of closed trades that were profitable, computed from every recorded trade (Live, Paper, Backtest).',
      rows: () => (<>
        <Row k="Winning trades" v={stats.winningTrades} c="var(--nd-green)" />
        <Row k="Losing trades" v={stats.losingTrades} c="var(--nd-red)" />
        <Row k="Total closed trades" v={stats.totalTrades} />
        <Row k="Win rate" v={pct(stats.winRate)} c="var(--nd-green)" />
        {Array.isArray(stats.bySource) && stats.bySource.length > 0 && (
          <div style={{ marginTop: 10 }}>
            <div style={{ fontSize: 11, color: 'var(--nd-text-3)', marginBottom: 6 }}>By source</div>
            {stats.bySource.map((b: any) => (
              <Row key={b.source} k={`${b.source} (${b.trades})`} v={`${(b.winRate * 100).toFixed(1)}% · ${b.avgReturn >= 0 ? '+' : ''}${b.avgReturn}%`} />
            ))}
          </div>
        )}
      </>),
    },
    return: {
      title: 'Average Return', icon: 'trending_up',
      how: 'Mean P&L % across all closed trades. Best/worst show the spread the average is drawn from.',
      rows: () => (<>
        <Row k="Average return / trade" v={`${stats.averageReturn >= 0 ? '+' : ''}${stats.averageReturn}%`} c={stats.averageReturn >= 0 ? 'var(--nd-green)' : 'var(--nd-red)'} />
        <Row k="Std deviation" v={`${stats.returnStd}%`} />
        <Row k="Best trade" v={`+${stats.bestTradePct}%`} c="var(--nd-green)" />
        <Row k="Worst trade" v={`${stats.worstTradePct}%`} c="var(--nd-red)" />
        <Row k="Across" v={`${stats.totalTrades} trades`} />
      </>),
    },
    sharpe: {
      title: 'Sharpe Ratio', icon: 'analytics',
      how: 'Risk-adjusted return = mean return ÷ standard deviation of returns (per trade). Higher means more consistent returns for the risk taken.',
      rows: () => (<>
        <Row k="Sharpe (mean ÷ std)" v={stats.sharpeRatio} c="var(--nd-purple)" />
        <Row k="Mean return" v={`${stats.averageReturn}%`} />
        <Row k="Return std" v={`${stats.returnStd}%`} />
        <Row k="Max drawdown (₹)" v={`₹${stats.maxDrawdown?.toLocaleString('en-IN')}`} c="var(--nd-red)" />
      </>),
    },
  };
  const m = META[cardId];
  if (!m) return null;

  return (
    <div onClick={e => { if (e.target === e.currentTarget) onClose(); }}
      style={{ position: 'fixed', inset: 0, background: '#00000080', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 12 }}>
      <div style={{ background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderRadius: 16, width: '100%', maxWidth: 460, maxHeight: '88vh', overflow: 'auto', boxShadow: '0 24px 64px #00000060' }}>
        <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--nd-border)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span className="material-icons" style={{ color: 'var(--nd-green)' }}>{m.icon}</span>
            <span style={{ fontSize: 16, fontWeight: 700, color: 'var(--nd-text-1)' }}>{m.title}</span>
          </div>
          <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer' }}>
            <span className="material-icons" style={{ color: 'var(--nd-text-3)', fontSize: 20 }}>close</span>
          </button>
        </div>
        <div style={{ padding: '14px 20px' }}>
          <p style={{ margin: '0 0 14px', fontSize: 12.5, lineHeight: 1.6, color: 'var(--nd-text-2)' }}>{m.how}</p>
          {m.rows()}
          <div style={{ marginTop: 14, fontSize: 11, color: 'var(--nd-text-3)', background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 8, padding: '8px 12px' }}>
            Computed live from <strong>{stats.totalTrades}</strong> closed trades and <strong>{stats.totalPredictions}</strong> predictions — no hard-coded values.
            {!stats.hasData && ' Run a backtest or session to start populating these.'}
          </div>
        </div>
      </div>
    </div>
  );
};

// ── Autopilot banner ──────────────────────────────────────────────────────────

const APToggle: React.FC<{ on: boolean; busy: boolean; onClick: () => void }> = ({ on, busy, onClick }) => (
  <button onClick={onClick} disabled={busy}
    style={{ width: 46, height: 26, borderRadius: 14, border: 'none', cursor: busy ? 'wait' : 'pointer', position: 'relative', background: on ? 'var(--nd-green)' : 'var(--nd-border)', transition: 'background 0.2s', flexShrink: 0 }}>
    <span style={{ position: 'absolute', top: 3, left: on ? 23 : 3, width: 20, height: 20, borderRadius: '50%', background: '#fff', transition: 'left 0.2s', boxShadow: '0 1px 3px #0003' }} />
  </button>
);

const APRow: React.FC<{ icon: string; title: string; desc: string; on: boolean; busy: boolean; onToggle: () => void; first?: boolean }> =
  ({ icon, title, desc, on, busy, onToggle, first }) => (
  <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '9px 0', borderTop: first ? 'none' : '1px solid var(--nd-border)' }}>
    <span className="material-icons" style={{ fontSize: 18, color: on ? 'var(--nd-green)' : 'var(--nd-text-3)' }}>{icon}</span>
    <div style={{ flex: 1, minWidth: 0 }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--nd-text-1)', display: 'flex', alignItems: 'center', gap: 6 }}>
        {title}{on && <span style={{ fontSize: 9, fontWeight: 700, color: 'var(--nd-green)', border: '1px solid var(--nd-green)', borderRadius: 4, padding: '0 4px' }}>ON</span>}
      </div>
      <div style={{ fontSize: 11.5, color: 'var(--nd-text-3)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{desc}</div>
    </div>
    <APToggle on={on} busy={busy} onClick={onToggle} />
  </div>
);

const AutopilotBanner: React.FC = () => {
  const [ap, setAp] = useState<any>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const load = useCallback(async () => {
    try { const r = await apiService.getAutopilot(); setAp((r as any).data); } catch {}
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 10000); return () => clearInterval(t); }, [load]);
  const toggle = async (mode: 'paper' | 'backtest', next: boolean) => {
    setBusy(mode);
    try { const r = await apiService.setAutopilot(next, mode); setAp((r as any).data); } catch {} finally { setBusy(null); }
  };
  if (!ap) return null;
  const paper = ap.paper ?? {};
  const bt = ap.backtest ?? {};
  const anyOn = paper.enabled || bt.enabled;

  const paperDesc = paper.enabled
    ? (paper.marketOpen
        ? `Paper-trading ${paper.running ?? 0} of ${paper.watchlistSize ?? 0} watchlist stocks live`
        : `Market closed — will paper-trade all ${paper.watchlistSize ?? 0} watchlist stocks at open`)
    : 'Live paper-trade the whole watchlist during market hours';

  const btDesc = bt.enabled
    ? (bt.activeWindow === false
        ? `Paused for paper-trading hours — resumes after close · ${bt.completedDays ?? 0} days trained`
        : (bt.running ?? 0) > 0
          ? `Replaying ${bt.queueDate ?? bt.cursor} at ${bt.speed ?? 1}× · ${bt.queuePending ?? 0}/${bt.queueTotal ?? 0} sessions left · ${bt.completedDays ?? 0} days trained`
          : `Next day: ${bt.cursor ?? '—'} · ${bt.completedDays ?? 0} days trained so far`)
    : 'Replays past days (walking back) outside market hours to train on dense real data';

  return (
    <div className="nd-card" style={{ padding: '14px 18px', marginBottom: 20, borderLeft: `3px solid ${anyOn ? 'var(--nd-green)' : 'var(--nd-border)'}` }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 10 }}>
        <div className="nd-icon-chip" style={{ background: anyOn ? 'var(--nd-green-50)' : 'var(--nd-surface)' }}>
          <span className="material-icons" style={{ color: anyOn ? 'var(--nd-green)' : 'var(--nd-text-3)' }}>smart_toy</span>
        </div>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--nd-text-1)' }}>Autopilot</div>
          <div style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>Auto-trades the AI watchlist to keep training the agents</div>
        </div>
      </div>
      <APRow first icon="sync" title="Paper (live)" desc={paperDesc}
        on={!!paper.enabled} busy={busy === 'paper'} onToggle={() => toggle('paper', !paper.enabled)} />
      <APRow icon="history" title="Backtest (1× replay)" desc={btDesc}
        on={!!bt.enabled} busy={busy === 'backtest'} onToggle={() => toggle('backtest', !bt.enabled)} />
    </div>
  );
};

// ── Learning curve ────────────────────────────────────────────────────────────

const LearningCurveCard: React.FC = () => {
  const [data, setData] = useState<any>(null);
  useEffect(() => { apiService.learningCurve().then(r => setData((r as any).data)).catch(() => {}); }, []);
  const pts: any[] = data?.points ?? [];
  if (pts.length < 2) return null;

  const W = 600, H = 160, PL = 36, PR = 12, PT = 12, PB = 22;
  const xs = pts.map((_, i) => i);
  const ys = pts.map(p => p.cumWinRate * 100);
  const yMin = Math.max(0, Math.min(...ys) - 5), yMax = Math.min(100, Math.max(...ys) + 5);
  const sx = (i: number) => PL + (i / (xs.length - 1)) * (W - PL - PR);
  const sy = (v: number) => PT + (1 - (v - yMin) / (yMax - yMin || 1)) * (H - PT - PB);
  const line = pts.map((p, i) => `${sx(i).toFixed(1)},${sy(p.cumWinRate * 100).toFixed(1)}`).join(' ');
  const last = pts[pts.length - 1];

  return (
    <div className="nd-card" style={{ padding: '16px 18px', marginBottom: 20 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8, marginBottom: 8 }}>
        <div>
          <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--nd-text-1)' }}>System Learning Curve</div>
          <div style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>Cumulative win-rate as the AI accumulates experience from every trade</div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--nd-green)' }}>{(last.cumWinRate * 100).toFixed(1)}%</div>
          <div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>{data.totalTrades} trades</div>
        </div>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 150 }} preserveAspectRatio="none">
        {[yMin, (yMin + yMax) / 2, yMax].map((v, i) => (
          <g key={i}>
            <line x1={PL} y1={sy(v)} x2={W - PR} y2={sy(v)} stroke="var(--nd-border)" strokeWidth="0.5" />
            <text x={4} y={sy(v) + 3} fontSize="9" fill="var(--nd-text-3)">{v.toFixed(0)}%</text>
          </g>
        ))}
        <polyline points={line} fill="none" stroke="var(--nd-green)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx={sx(pts.length - 1)} cy={sy(last.cumWinRate * 100)} r="3.5" fill="var(--nd-green)" />
      </svg>
    </div>
  );
};

// ── AI Watchlist tab (self-running scanner output + evidence) ──────────────────

const ACTION_BG: Record<string, string> = { BUY: '#22c55e', SELL: '#ef4444', HOLD: '#f59e0b' };

const fmtDateTime = (s?: string): string => {
  if (!s) return '';
  const d = new Date(s);
  return `${d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' })}, ${d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })}`;
};

const SignalScorePanel: React.FC<{ ev: any }> = ({ ev }) => {
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
  const acc = (latest?.accuracy ?? overall?.accuracy ?? 0) as number;
  const color = acc >= 0.6 ? 'var(--nd-green)' : acc >= 0.45 ? '#d98c00' : 'var(--nd-red)';
  const results: any[] = latest?.results ?? [];
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

const AiWatchlistTab: React.FC = () => {
  const [data, setData] = useState<any>(null);
  const [evalData, setEvalData] = useState<any>(null);
  const [sel, setSel] = useState<any>(null);
  const [scanning, setScanning] = useState(false);
  const load = useCallback(async () => {
    try { const r = await apiService.aiWatchlist(); setData((r as any).data); } catch {}
    try { const e = await apiService.scanEvaluation(); setEvalData((e as any).data); } catch {}
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 30000); return () => clearInterval(t); }, [load]);
  const rescan = async () => {
    setScanning(true);
    try { await apiService.scanWatchlist(); setTimeout(() => { load(); setScanning(false); }, 35000); }
    catch { setScanning(false); }
  };
  const items: any[] = data?.items ?? [];

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
        <div style={{ fontSize: 12, color: 'var(--nd-text-3)' }}>
          AI-selected from a live scan of {data?.scanned ?? 0}/{data?.universe ?? 0} stocks
          {data?.marketRegime ? ` · market ${data.marketRegime}` : ''}
          {data?.updatedAt ? ` · last updated ${fmtDateTime(data.updatedAt)} IST` : ''}
        </div>
        <button onClick={rescan} disabled={scanning}
          style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '5px 12px', borderRadius: 8, border: '1px solid var(--nd-border)', background: 'var(--nd-surface)', cursor: scanning ? 'wait' : 'pointer', fontSize: 12, color: 'var(--nd-text-2)' }}>
          <span className="material-icons" style={{ fontSize: 15 }}>{scanning ? 'hourglass_top' : 'refresh'}</span>
          {scanning ? 'Scanning…' : 'Rescan'}
        </button>
      </div>

      <SignalScorePanel ev={evalData} />

      {items.length === 0 ? (
        <div style={{ padding: 40, textAlign: 'center', color: 'var(--nd-text-3)', fontSize: 13 }}>
          The market scanner is warming up — the AI watchlist will appear shortly.
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          {items.map((w, i) => (
            <div key={w.symbol} onClick={() => setSel(w)}
              style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 4px', borderTop: i ? '1px solid var(--nd-border)' : 'none', cursor: 'pointer' }}>
              <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--nd-text-3)', width: 20 }}>#{i + 1}</span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--nd-text-1)' }}>{w.symbol}</div>
                <div style={{ fontSize: 11, color: 'var(--nd-text-3)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{w.name}</div>
                {w.news && ((w.news.catalyst && w.news.catalyst !== 'none') || w.news.summary) && (
                  <div title="LLM news sentiment" style={{ display: 'flex', alignItems: 'center', gap: 4, marginTop: 2, fontSize: 10.5, fontWeight: 600, color: ACTION_BG[w.news.action] ?? 'var(--nd-text-3)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    <span className="material-icons" style={{ fontSize: 12, flexShrink: 0 }}>article</span>
                    <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {(w.news.catalyst && w.news.catalyst !== 'none') ? w.news.catalyst : w.news.summary}
                    </span>
                  </div>
                )}
              </div>
              <div style={{ textAlign: 'right' }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--nd-text-1)' }}>₹{w.price?.toLocaleString('en-IN')}</div>
                <div style={{ fontSize: 11, color: 'var(--nd-text-3)' }}>
                  {w.signalScore != null ? `signal ${Math.round(w.signalScore)} · ` : ''}conf {(w.confidence * 100).toFixed(0)}%
                </div>
              </div>
              <span style={{ fontSize: 11, fontWeight: 700, padding: '3px 9px', borderRadius: 5, background: `${ACTION_BG[w.action]}1a`, color: ACTION_BG[w.action] }}>{w.action}</span>
              <span className="material-icons" style={{ fontSize: 16, color: 'var(--nd-text-3)' }}>chevron_right</span>
            </div>
          ))}
        </div>
      )}
      {sel && <WatchlistEvidence stock={sel} scannedAt={data?.updatedAt} onClose={() => setSel(null)} />}
    </div>
  );
};

const WatchlistEvidence: React.FC<{ stock: any; scannedAt?: string; onClose: () => void }> = ({ stock, scannedAt, onClose }) => (
  <div onClick={e => { if (e.target === e.currentTarget) onClose(); }}
    style={{ position: 'fixed', inset: 0, background: '#00000080', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 12 }}>
    <div style={{ background: 'var(--nd-bg)', border: '1px solid var(--nd-border)', borderRadius: 16, width: '100%', maxWidth: 500, maxHeight: '88vh', overflow: 'auto', boxShadow: '0 24px 64px #00000060' }}>
      <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--nd-border)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 16, fontWeight: 700, color: 'var(--nd-text-1)' }}>{stock.symbol}</span>
          <span style={{ fontSize: 11, fontWeight: 700, padding: '2px 9px', borderRadius: 5, background: `${ACTION_BG[stock.action]}1a`, color: ACTION_BG[stock.action] }}>{stock.action}</span>
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
          {stock.agents.map((a: any) => (
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

// ── Dashboard Page ────────────────────────────────────────────────────────────

const Dashboard: React.FC = () => {
  const [activeTab, setActiveTab]     = useState<TabId>('watchlist');
  const [accuracyStats, setAccuracyStats] = useState<any>(null);
  const [selectedCard, setSelectedCard] = useState<string | null>(null);

  useEffect(() => {
    apiService.getAccuracyStats().then(r => { if (r.data) setAccuracyStats(r.data); }).catch(() => {});
  }, []);

  const STAT_CARDS = accuracyStats ? [
    { id: 'accuracy', label: 'Model Accuracy', value: `${(accuracyStats.accuracyRate * 100).toFixed(1)}%`, icon: 'psychology',    color: 'var(--nd-green)',  bg: 'var(--nd-green-50)' },
    { id: 'win',      label: 'Win Rate',       value: `${(accuracyStats.winRate * 100).toFixed(1)}%`,        icon: 'emoji_events', color: 'var(--nd-green)',  bg: 'var(--nd-green-50)' },
    { id: 'return',   label: 'Avg Return',     value: `${accuracyStats.averageReturn?.toFixed(2)}%`,          icon: 'trending_up',  color: 'var(--nd-blue)',   bg: '#e3f2fd'            },
    { id: 'sharpe',   label: 'Sharpe Ratio',   value: accuracyStats.sharpeRatio?.toFixed(2),                  icon: 'analytics',    color: 'var(--nd-purple)', bg: '#f5f3ff'            },
  ] : [];

  return (
    <div>
      {/* Page heading */}
      <div style={{ marginBottom: 20 }}>
        <h1 className="nd-page-title">Market Overview</h1>
        <p className="nd-page-sub">Real-time NSE · BSE stock data with AI-generated predictions</p>
      </div>

      {/* Accuracy stat cards — click any to see the evidence */}
      {STAT_CARDS.length > 0 && (
        <div className="nd-grid-4" style={{ gap: 12, marginBottom: 20 }}>
          {STAT_CARDS.map(s => (
            <div key={s.label} className="nd-card" onClick={() => setSelectedCard(s.id)}
              style={{ position: 'relative', display: 'flex', alignItems: 'center', gap: 14, padding: '14px 18px', cursor: 'pointer', transition: 'box-shadow 0.15s' }}
              onMouseEnter={e => (e.currentTarget.style.boxShadow = 'var(--nd-shadow-md)')}
              onMouseLeave={e => (e.currentTarget.style.boxShadow = 'none')}>
              <span className="material-icons" title="See how this is calculated"
                style={{ position: 'absolute', top: 8, right: 8, fontSize: 16, color: 'var(--nd-text-3)' }}>info</span>
              <div className="nd-icon-chip" style={{ background: s.bg }}>
                <span className="material-icons" style={{ color: s.color }}>{s.icon}</span>
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <p className="nd-label">{s.label}</p>
                <p style={{ fontSize: 18, fontWeight: 700, color: s.color }}>{s.value}</p>
              </div>
            </div>
          ))}
        </div>
      )}

      {selectedCard && accuracyStats && (
        <MetricModal cardId={selectedCard} stats={accuracyStats} onClose={() => setSelectedCard(null)} />
      )}

      {/* Self-running autopilot + the system's learning curve */}
      <AutopilotBanner />
      <LearningCurveCard />

      {/* Tabbed card */}
      <div className="nd-card" style={{ padding: 0 }}>

        {/* Tab bar */}
        <div style={{ display: 'flex', borderBottom: '1px solid var(--nd-border)', padding: '0 20px' }}>
          {TABS.map(tab => {
            const active = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                style={{
                  display: 'flex', alignItems: 'center', gap: 6,
                  padding: '13px 18px', fontSize: 13, fontWeight: active ? 700 : 500,
                  cursor: 'pointer', border: 'none', background: 'transparent',
                  color: active ? 'var(--nd-accent)' : 'var(--nd-text-3)',
                  borderBottom: active ? '2px solid var(--nd-accent)' : '2px solid transparent',
                  marginBottom: -1, transition: 'color 0.15s',
                }}
              >
                <span className="material-icons" style={{ fontSize: 16 }}>{tab.icon}</span>
                {tab.label}
                {tab.id === 'watchlist' && (
                  <span style={{ fontSize: 11, background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 10, padding: '1px 7px', color: 'var(--nd-text-3)', fontWeight: 400 }}>
                    AI
                  </span>
                )}
              </button>
            );
          })}
        </div>

        {/* Tab content */}
        <div style={{ padding: '16px 20px 20px' }}>
          {activeTab === 'watchlist' && <AiWatchlistTab />}
          {activeTab === 'directory' && <DirectoryTab />}
        </div>
      </div>
    </div>
  );
};

export default Dashboard;
